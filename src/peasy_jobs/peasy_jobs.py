import inspect
import logging
import os
import pickle
import signal
from multiprocessing import Manager, Process
from time import sleep

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from peasy_jobs.models import PeasyJobQueue

logger = logging.getLogger(__name__)

manager = Manager()
pids_map = manager.dict()

# Default settings
MINIMUM_ALLOWED_POLLING_INTERVAL = 0.01
PEASY_MAX_COMPLETED = 10
PEASY_MAX_FAILED = 10
PEASY_MAX_CANCELLED = 10
PEASY_POLLING_INTERVAL = 2
PEASY_MAX_CONCURRENCY = 1
PEASY_WORKER_TYPE = "process"
PEASY_SHUTDOWN_TIMEOUT = 30


class PeasyJob:
    """A class for collecting and executing asynchronous jobs."""

    def __init__(self):
        # max number of completed jobs to keep in the db
        if hasattr(settings, "PEASY_MAX_COMPLETED"):
            if not isinstance(settings.PEASY_MAX_COMPLETED, int):
                raise TypeError("PEASY_MAX_COMPLETED must be an integer.")
            elif settings.PEASY_MAX_COMPLETED < 0:
                raise ValueError("PEASY_MAX_COMPLETED must be greater than or equal to 0.")
            self.max_completed = settings.PEASY_MAX_COMPLETED
        else:
            self.max_completed = PEASY_MAX_COMPLETED
        # max number of failed jobs to keep in the db
        if hasattr(settings, "PEASY_MAX_FAILED"):
            if not isinstance(settings.PEASY_MAX_FAILED, int):
                raise TypeError("PEASY_MAX_FAILED must be an integer.")
            elif settings.PEASY_MAX_FAILED < 0:
                raise ValueError("PEASY_MAX_FAILED must be greater than or equal to 0.")
            self.max_failed = settings.PEASY_MAX_FAILED
        else:
            self.max_failed = PEASY_MAX_FAILED
        # max number of cancelled jobs to keep in the db
        if hasattr(settings, "PEASY_MAX_CANCELLED"):
            if not isinstance(settings.PEASY_MAX_CANCELLED, int):
                raise TypeError("PEASY_MAX_CANCELLED must be an integer.")
            elif settings.PEASY_MAX_CANCELLED < 0:
                raise ValueError("PEASY_MAX_CANCELLED must be greater than or equal to 0.")
            self.max_cancelled = settings.PEASY_MAX_CANCELLED
        else:
            self.max_cancelled = PEASY_MAX_CANCELLED
        # how long to wait between checking for new jobs
        if hasattr(settings, "PEASY_POLLING_INTERVAL"):
            if not isinstance(settings.PEASY_POLLING_INTERVAL, int | float):
                raise TypeError("PEASY_POLLING_INTERVAL must be a float (or integer) representing seconds.")
            elif settings.PEASY_POLLING_INTERVAL < MINIMUM_ALLOWED_POLLING_INTERVAL:
                raise ValueError("PEASY_POLLING_INTERVAL must be greater than or equal to 0.01")
            self.polling_interval = settings.PEASY_POLLING_INTERVAL
        else:
            self.polling_interval = PEASY_POLLING_INTERVAL
        # max number of jobs to run concurrently
        if hasattr(settings, "PEASY_CONCURRENCY"):
            if not isinstance(settings.PEASY_MAX_CONCURRENCY, int):
                raise TypeError("PEASY_CONCURRENCY must be an integer.")
            elif settings.PEASY_MAX_CONCURRENCY < 1:
                raise ValueError("PEASY_CONCURRENCY must be greater than or equal to 1.")
            self.concurrency = settings.PEASY_MAX_CONCURRENCY
        else:
            self.concurrency = PEASY_MAX_CONCURRENCY
        # whether to use threads or processes for concurrent job execution
        if hasattr(settings, "PEASY_WORKER_TYPE"):
            if settings.PEASY_WORKER_TYPE not in ("thread", "process"):
                raise ValueError('PEASY_WORKER_TYPE must be either "thread" or "process".')
            self.worker_type = settings.PEASY_WORKER_TYPE
        else:
            self.worker_type = PEASY_WORKER_TYPE
        # on a sigint, how long to wait for jobs to complete before terminating them
        if hasattr(settings, "PEASY_SHUTDOWN_TIMEOUT"):
            if not isinstance(settings.PEASY_SHUTDOWN_TIMEOUT, int | float):
                raise TypeError("PEASY_SHUTDOWN_TIMEOUT must be a number representing seconds.")
            elif settings.PEASY_SHUTDOWN_TIMEOUT < 0:
                raise ValueError("PEASY_SHUTDOWN_TIMEOUT must be greater than or equal to 0")
            self.shutdown_timeout = settings.PEASY_SHUTDOWN_TIMEOUT
        else:
            self.shutdown_timeout = PEASY_SHUTDOWN_TIMEOUT

        self.job_definitions = {}
        self.running = True
        self.shutting_down = False
        self._shutdown_start_time = None
        self._active_processes = manager.dict()
        self.test_mode = False

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
            job_func = self.job_definitions[job_name]
            signature = inspect.signature(job_func)
            if "job_pk" in signature.parameters:  # user has chosen to accept the injected job_pk
                result = job_func(*args, job_pk=job_pk, **kwargs)
            else:  # user has chosen not to accept the injected job_pk
                result = job_func(*args, **kwargs)
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
        logger.info("Starting PeasyJob...")
        signal.signal(signal.SIGINT, self.sigint_handler)
        signal.signal(signal.SIGTERM, self.sigint_handler)

        running_processes = {}  # job_id -> Process mapping

        while self.running:
            try:
                # Cleanup finished processes
                finished = []
                for job_id, process in running_processes.items():
                    if not process.is_alive():  # Process has finished
                        process.join()  # Clean up the process
                        finished.append(job_id)
                for job_id in finished:
                    del running_processes[job_id]

                # Handle shutdown
                if self.shutting_down:
                    if not running_processes:
                        logger.info("All jobs completed. Shutting down...")
                        self.running = False
                        break

                    elapsed = (timezone.now() - self._shutdown_start_time).total_seconds()
                    if elapsed >= self.shutdown_timeout:
                        logger.info("Shutdown timeout reached. Terminating remaining jobs...")
                        for job_id in running_processes:
                            self.terminate_child_process(job_id)
                        self.running = False
                        break

                    sleep(0.5)
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

                # Check if we should exit due to empty queue
                if exit_when_queue_empty:
                    if not PeasyJobQueue.objects.filter(
                        status__in=(PeasyJobQueue.ENQUEUED, PeasyJobQueue.ONGOING)
                    ).exists():
                        logger.info("Queue is empty. Exiting as requested...")
                        self.running = False
                        break

                # Start new jobs if capacity available
                if len(running_processes) < self.concurrency:
                    jobs = PeasyJobQueue.objects.filter(status=PeasyJobQueue.ENQUEUED)
                    available_slots = self.concurrency - len(running_processes)
                    for job in jobs[:available_slots]:
                        logger.info(f"Starting job {job.pk}")
                        job.status = PeasyJobQueue.ONGOING
                        job.started = timezone.now()
                        job.status_msg = "Starting..."
                        job.save()

                        if self.test_mode:
                            # Run job directly in same process for tests
                            self.execute_job(job.pk)
                        else:
                            # Create and start a new process for production
                            process = Process(target=call_command, args=("execute_job", job.pk), name=f"job-{job.pk}")
                            process.start()
                            running_processes[job.pk] = process
                            pids_map[job.pk] = process.pid
                            self._active_processes[process.pid] = job.pk

                sleep(self.polling_interval)

            except Exception as e:
                logger.exception(e)
                sleep(self.polling_interval)

        # Cleanup remaining processes
        for job_id, process in running_processes.items():
            self.terminate_child_process(job_id)
            process.join()  # Wait for process to finish cleanup


peasy = PeasyJob()
