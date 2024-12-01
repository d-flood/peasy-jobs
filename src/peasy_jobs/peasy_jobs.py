import logging
import os
import pickle
import signal
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import Manager, Pool
from time import sleep

from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from peasy_jobs.models import PeasyJobQueue

logger = logging.getLogger(__name__)
manager = Manager()
pids_map = manager.dict()


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
            elif settings.PEASY_POLLING_INTERVAL < 0.01:
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

        self.job_definitions = {}
        self.running = True

    def register_job_definition(self, func, *args, **kwargs):
        """Add a callable to the job dictionary."""
        job_name = f"{func.__module__}.{func.__name__}"
        if job_name in self.job_definitions.keys():
            raise ValueError(f'Job name "{job_name}" already exists in job definitions.')
        self.job_definitions[job_name] = func
        if os.getenv("PEASY_RUNNER", False):
            logger.info(f"registered job: {job_name}")

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

    def enqueue_job(self, job_name: str, title, args: tuple, kwargs: dict = None):
        """Add a job to the db queue."""
        if job_name not in self.job_definitions.keys():
            raise ValueError(f'Job name "{job_name}" not found in job definitions.')
        try:
            args = pickle.dumps(args)
        except TypeError:
            raise TypeError("Job arguments must be pickleable.")
        if kwargs is not None:
            try:
                kwargs = pickle.dumps(kwargs)
            except TypeError:
                raise TypeError("Job keyword arguments must be pickleable.")

        PeasyJobQueue.objects.create(
            job_name=job_name,
            pickled_args=args,
            pickled_kwargs=kwargs,
            title=title,
            status_msg="Enqueued",
            progress=0,
            started=False,
            complete=False,
            failed=False,
        )

    def execute_job(self, job_pk: int):
        """Execute a job from the db queue."""
        job = PeasyJobQueue.objects.get(pk=job_pk)
        logger.info(f"executing {job.title}")
        job_name = job.job_name
        args: tuple = pickle.loads(job.pickled_args)
        if job.pickled_kwargs:
            kwargs: dict[str] = pickle.loads(job.pickled_kwargs)
        else:
            kwargs = {}
        try:
            PeasyJobQueue.objects.filter(pk=job_pk).update(
                status_msg="Starting...",
                started=True,
            )
            try:
                result = self.job_definitions[job_name](*args, job_pk=job_pk, **kwargs)
            except TypeError as e:
                self.job_definitions[job_name](*args, **kwargs)
        except Exception as e:
            logger.exception(e)
            PeasyJobQueue.objects.filter(pk=job_pk).update(
                status_msg=f"Failed: {e}",
                complete=False,
                failed=True,
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
        try:
            self.run_job_command(job_id)
        finally:
            del pids_map[job_id]
            connection.close()

    def terminate_child_process(job_id):
        pid = pids_map.get(job_id)
        if pid:
            os.kill(pid, signal.SIGTERM)

    def update_status(
        job_pk: int,
        status_msg: str,
        extra: dict = None,
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

    def sigint_handler(self, signum, frame):
        logger.info("SIGINT received. Exiting.")
        self.running = False

    def run(self):
        signal.signal(signal.SIGINT, self.sigint_handler)

        executor_class = ThreadPoolExecutor if self.worker_type == "thread" else ProcessPoolExecutor

        with executor_class(max_workers=self.concurrency) as executor:
            while self.running:
                try:
                    cancelled_ongoing_jobs = PeasyJobQueue.objects.filter(
                        status=PeasyJobQueue.CANCELLED, started__isnull=False, completed__isnull=True
                    )
                    for job in cancelled_ongoing_jobs:
                        self.terminate_child_process(job.pk)
                        job.completed = timezone.now()
                        job.status_msg = "Cancelled"
                        job.save()
                    with transaction.atomic():
                        jobs = PeasyJobQueue.objects.select_for_update().filter(status=PeasyJobQueue.ENQUEUED)
                        job_ids = list(jobs.values_list("pk", flat=True)[: self.concurrency])
                        if job_ids:
                            jobs.update(status=PeasyJobQueue.ONGOING)
                    if job_ids:
                        executor.map(self.run_job_command_with_pid_tracking, job_ids)
                    else:
                        sleep(self.polling_interval)
                except Exception as e:
                    logger.exception(e)
                    sleep(self.polling_interval)


peasy = PeasyJob()
