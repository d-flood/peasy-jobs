import logging
import os
import pickle
import signal
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import Manager
from time import sleep

from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from peasy_jobs.models import PeasyJobQueue

logger = logging.getLogger(__name__)

# Add console handler if none exists
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.setLevel(logging.INFO)

manager = Manager()
pids_map = manager.dict()

MINIMUM_ALLOWED_POLLING_INTERVAL = 0.01


class PeasyJob:
    """A class for collecting and executing asynchronous jobs."""

    def __init__(self):
        if hasattr(settings, "PEASY_MAX_COMPLETED"):
            if not isinstance(settings.PEASY_MAX_COMPLETED, int):
                raise TypeError("PEASY_MAX_COMPLETED must be an integer.")
            elif settings.PEASY_MAX_COMPLETED < 0:
                raise ValueError("PEASY_MAX_COMPLETED must be greater than or equal to 0.")
            self.max_completed = settings.PEASY_MAX_COMPLETED
        else:
            self.max_completed = 10

        if hasattr(settings, "PEASY_MAX_FAILED"):
            if not isinstance(settings.PEASY_MAX_FAILED, int):
                raise TypeError("PEASY_MAX_FAILED must be an integer.")
            elif settings.PEASY_MAX_FAILED < 0:
                raise ValueError("PEASY_MAX_FAILED must be greater than or equal to 0.")
            self.max_failed = settings.PEASY_MAX_FAILED
        else:
            self.max_failed = 10

        if hasattr(settings, "PEASY_MAX_CANCELLED"):
            if not isinstance(settings.PEASY_MAX_CANCELLED, int):
                raise TypeError("PEASY_MAX_CANCELLED must be an integer.")
            elif settings.PEASY_MAX_CANCELLED < 0:
                raise ValueError("PEASY_MAX_CANCELLED must be greater than or equal to 0.")
            self.max_cancelled = settings.PEASY_MAX_CANCELLED
        else:
            self.max_cancelled = 10

        if hasattr(settings, "PEASY_POLLING_INTERVAL"):
            if not isinstance(settings.PEASY_POLLING_INTERVAL, int | float):
                raise TypeError("PEASY_POLLING_INTERVAL must be a float (or integer) representing seconds.")
            elif settings.PEASY_POLLING_INTERVAL < MINIMUM_ALLOWED_POLLING_INTERVAL:
                raise ValueError("PEASY_POLLING_INTERVAL must be greater than or equal to 0.01")
            self.polling_interval = settings.PEASY_POLLING_INTERVAL
        else:
            self.polling_interval = 2

        if hasattr(settings, "PEASY_CONCURRENCY"):
            if not isinstance(settings.PEASY_MAX_CONCURRENCY, int):
                raise TypeError("PEASY_CONCURRENCY must be an integer.")
            elif settings.PEASY_MAX_CONCURRENCY < 1:
                raise ValueError("PEASY_CONCURRENCY must be greater than or equal to 1.")
            self.concurrency = settings.PEASY_MAX_CONCURRENCY
        else:
            self.concurrency = 1

        if hasattr(settings, "PEASY_WORKER_TYPE"):
            if settings.PEASY_WORKER_TYPE not in ("thread", "process"):
                raise ValueError('PEASY_WORKER_TYPE must be either "thread" or "process".')
            self.worker_type = settings.PEASY_WORKER_TYPE
        else:
            self.worker_type = "process"

        if hasattr(settings, "PEASY_SHUTDOWN_TIMEOUT"):
            if not isinstance(settings.PEASY_SHUTDOWN_TIMEOUT, int | float):
                raise TypeError("PEASY_SHUTDOWN_TIMEOUT must be a number representing seconds.")
            elif settings.PEASY_SHUTDOWN_TIMEOUT < 0:
                raise ValueError("PEASY_SHUTDOWN_TIMEOUT must be greater than or equal to 0")
            self.shutdown_timeout = settings.PEASY_SHUTDOWN_TIMEOUT
        else:
            self.shutdown_timeout = 30  # default 30 seconds wait

        self.job_definitions = {}
        self.running = True
        self.shutting_down = False
        self._shutdown_start_time = None
        self._active_processes = manager.dict()

    def register_job_definition(self, func):
        """Add a callable to the job dictionary."""
        job_name = f"{func.__module__}.{func.__name__}"
        if job_name in self.job_definitions.keys():
            raise ValueError(f'Job name "{job_name}" already exists in job definitions.')
        self.job_definitions[job_name] = func
        logger.info(f"Registered job: {job_name}")

    def job(self, title: str):
        """A decorator to add a callable to the job dictionary
        at startup, then enqueues jobs during runtime.
        Decorator takes a title argument."""

        def decorator(func):
            self.register_job_definition(func)

            def wrapper(*args, **kwargs):
                job_name = f"{func.__module__}.{func.__name__}"
                self.enqueue_job(job_name, title, args, kwargs)

            return wrapper

        return decorator

    def enqueue_job(self, job_name: str, title, args: tuple, kwargs: dict | None = None):
        """Add a job to the db queue."""
        if job_name not in self.job_definitions.keys():
            raise ValueError(f'Job name "{job_name}" not found in job definitions.')
        try:
            args = pickle.dumps(args)
        except TypeError as e:
            raise TypeError("Job arguments must be pickleable.") from e
        if kwargs is not None:
            try:
                kwargs = pickle.dumps(kwargs)
            except TypeError as e:
                raise TypeError("Job keyword arguments must be pickleable.") from e

        PeasyJobQueue.objects.create(
            job_name=job_name,
            pickled_args=args,
            pickled_kwargs=kwargs,
            title=title,
            status_msg="Enqueued",
            status=PeasyJobQueue.ENQUEUED,
        )

    def execute_job(self, job_pk: int):
        """Execute a job from the db queue."""
        job = PeasyJobQueue.objects.get(pk=job_pk)
        logger.info(f"executing {job.title}")
        job_name = job.job_name
        args: tuple = pickle.loads(job.pickled_args)  # noqa
        if job.pickled_kwargs:
            kwargs: dict[str] = pickle.loads(job.pickled_kwargs)  # noqa
        else:
            kwargs = {}
        try:
            result = None
            try:
                result = self.job_definitions[job_name](*args, job_pk=job_pk, **kwargs)
            except TypeError:
                result = self.job_definitions[job_name](*args, **kwargs)
        except Exception as e:
            logger.exception(e)
            PeasyJobQueue.objects.filter(pk=job_pk).update(
                status_msg=f"Failed: {e}",
                completed=timezone.now(),
                status=PeasyJobQueue.FAILED,
            )
        else:
            try:
                pickled_result = pickle.dumps(result)
                status_msg = "Complete"
            except TypeError:
                pickled_result = None
                status_msg = "Complete (result not pickleable)"

            PeasyJobQueue.objects.filter(pk=job_pk).update(
                status_msg=status_msg,
                result=pickled_result,
                status=PeasyJobQueue.COMPLETED,
                completed=timezone.now(),
            )

    def cancel_job(self, job_pk: int):
        PeasyJobQueue.objects.filter(pk=job_pk).update(
            status=PeasyJobQueue.CANCELLED,
            status_msg="Cancelled",
        )

    def run_job_command(self, job_pk: int):
        try:
            call_command("execute_job", job_pk)
        except Exception as e:
            logger.exception(e)
            PeasyJobQueue.objects.filter(pk=job_pk).update(
                status=PeasyJobQueue.FAILED,
                status_msg=f"Failed: {e}",
            )
        finally:
            connection.close()

    def run_job_command_with_pid_tracking(self, job_id):
        pid = os.getpid()
        pids_map[job_id] = pid
        self._active_processes[pid] = job_id
        try:
            self.run_job_command(job_id)
        finally:
            if pid in self._active_processes:
                del self._active_processes[pid]
            if job_id in pids_map:
                del pids_map[job_id]
            connection.close()

    def terminate_child_process(self, job_id):
        """Gracefully terminate a child process."""
        pid = pids_map.get(job_id)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                # Give the process some time to cleanup
                sleep(0.5)
                # Force kill if still running
                if pid in self._active_processes:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # Process already terminated
            finally:
                if job_id in pids_map:
                    del pids_map[job_id]

    @staticmethod
    def update_status(
        job_pk: int,
        status_msg: str,
        extra: dict | None = None,
    ):
        if extra:
            PeasyJobQueue.objects.filter(pk=job_pk).update(
                status_msg=status_msg,
                extra=extra,
            )
        else:
            PeasyJobQueue.objects.filter(pk=job_pk).update(
                status_msg=status_msg,
            )

    def sigint_handler(self, signum, frame):  # noqa
        if not self.shutting_down:
            logger.info("SIGINT received. Initiating graceful shutdown...")
            self.shutting_down = True
            self._shutdown_start_time = timezone.now()

    def run(self, *, exit_when_queue_empty: bool):
        signal.signal(signal.SIGINT, self.sigint_handler)
        signal.signal(signal.SIGTERM, self.sigint_handler)

        executor_class = ThreadPoolExecutor if self.worker_type == "thread" else ProcessPoolExecutor

        with executor_class(max_workers=self.concurrency) as executor:
            while self.running:
                try:
                    # Check shutdown timeout
                    if self.shutting_down:
                        if not self._active_processes:
                            logger.info("All jobs completed. Shutting down...")
                            self.running = False
                            break

                        elapsed = (timezone.now() - self._shutdown_start_time).total_seconds()
                        if elapsed >= self.shutdown_timeout:
                            logger.info("Shutdown timeout reached. Terminating remaining jobs...")
                            # Cleanup and update status of remaining jobs
                            to_terminate = PeasyJobQueue.objects.filter(status=PeasyJobQueue.ONGOING)
                            if to_terminate.exists():
                                logger.info(f"Terminating {to_terminate.count()} remaining jobs...")
                                to_terminate.update(
                                    status=PeasyJobQueue.FAILED,
                                    status_msg="Job interrupted - shutdown timeout reached",
                                    completed=timezone.now(),
                                )
                                for job_pid in list(pids_map.keys()):
                                    self.terminate_child_process(job_pid)
                            self.running = False
                            break

                        sleep(0.5)  # Short sleep while waiting for jobs to complete
                        continue

                    # Handle cancelled jobs
                    cancelled_ongoing_jobs = PeasyJobQueue.objects.filter(
                        status=PeasyJobQueue.CANCELLED, started__isnull=False, completed__isnull=True
                    )
                    for job in cancelled_ongoing_jobs:
                        self.terminate_child_process(job.pk)
                        with transaction.atomic():
                            job.completed = timezone.now()
                            job.status_msg = "Cancelled"
                            job.save()

                    # Only process new jobs if not shutting down
                    if not self.shutting_down:
                        # Check if we should exit due to empty queue
                        if exit_when_queue_empty:
                            if not PeasyJobQueue.objects.filter(
                                status__in=(PeasyJobQueue.ENQUEUED, PeasyJobQueue.ONGOING)
                            ).exists():
                                logger.info("Queue is empty. Exiting as requested...")
                                self.running = False
                                break

                        with transaction.atomic():
                            jobs = PeasyJobQueue.objects.select_for_update().filter(status=PeasyJobQueue.ENQUEUED)
                            job_ids = list(jobs.values_list("pk", flat=True)[: self.concurrency])
                            if job_ids:
                                PeasyJobQueue.objects.filter(pk__in=job_ids).update(
                                    status=PeasyJobQueue.ONGOING, started=timezone.now(), status_msg="Starting..."
                                )

                        if job_ids:
                            executor.map(self.run_job_command_with_pid_tracking, job_ids)
                        else:
                            sleep(self.polling_interval)

                except Exception as e:
                    logger.exception(e)
                    sleep(self.polling_interval)

            # Only handle remaining ongoing jobs if we didn't already do it in timeout
            if not self.shutting_down:
                # Cleanup on normal shutdown
                for job_pid in list(pids_map.keys()):
                    self.terminate_child_process(job_pid)

                PeasyJobQueue.objects.filter(status=PeasyJobQueue.ONGOING).update(
                    status=PeasyJobQueue.FAILED, status_msg="Job interrupted during shutdown", completed=timezone.now()
                )


peasy = PeasyJob()
