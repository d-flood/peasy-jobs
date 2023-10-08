import logging
from venv import logger

from django.conf import settings
from django.core.management import call_command

from time import sleep
import os
import pickle
from peasy_jobs.models import PeasyJobQueue



logger = logging.getLogger(__name__)

class PeasyJob:
    """A class for collecting and executing asynchronous jobs."""

    def __init__(self):
        if hasattr(settings, 'PEASY_MAX_COMPLETED'):
            if not isinstance(settings.PEASY_MAX_COMPLETED, int):
                raise TypeError('PEASY_MAX_COMPLETED must be an integer.')
            elif settings.PEASY_MAX_COMPLETED < 0:
                raise ValueError('PEASY_MAX_COMPLETED must be greater than or equal to 0.')
            self.max_completed = settings.PEASY_MAX_COMPLETED
        else:
            self.max_completed = 10

        if hasattr(settings, 'PEASY_MAX_FAILED'):
            if not isinstance(settings.PEASY_MAX_FAILED, int):
                raise TypeError('PEASY_MAX_FAILED must be an integer.')
            elif settings.PEASY_MAX_FAILED < 0:
                raise ValueError('PEASY_MAX_FAILED must be greater than or equal to 0.')
            self.max_failed = settings.PEASY_MAX_FAILED
        else:
            self.max_failed = 5
        
        if hasattr(settings, 'PEASY_POLLING_INTERVAL'):
            if not isinstance(settings.PEASY_POLLING_INTERVAL, (int, float)):
                raise TypeError('PEASY_POLLING_INTERVAL must be a float (or integer) representing seconds.')
            elif settings.PEASY_POLLING_INTERVAL < 0.01:
                raise ValueError('PEASY_POLLING_INTERVAL must be greater than or equal to 0.01')
            self.polling_interval = settings.PEASY_POLLING_INTERVAL
        else:
            self.polling_interval = 2
        
        self.max_failed = settings.PEASY_MAX_FAILED
        self.max_failed = settings.PEASY_MAX_FAILED
        self.polling_interval = settings.PEASY_POLLING_INTERVAL
        self.job_definitions = {}

    def register_job_definition(self, func, *args, **kwargs):
        """Add a callable to the job dictionary."""
        job_name = f'{func.__module__}.{func.__name__}'
        if job_name in self.job_definitions.keys():
            raise ValueError(f'Job name "{job_name}" already exists in job definitions.')
        self.job_definitions[job_name] = func
        if os.getenv('PEASY_RUNNER', False):
            logger.info(f'registered job: {job_name}')

    def job(self, title: str):
        """A decorator to add a callable to the job dictionary
        at startup, then enques jobs during runtime.
        Decorator takes a title argument."""
        def decorator(func):
            self.register_job_definition(func)
            def wrapper(*args, **kwargs):
                job_name = f'{func.__module__}.{func.__name__}'
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
            raise TypeError('Job arguments must be pickleable.')
        if kwargs is not None:
            try:
                kwargs = pickle.dumps(kwargs)
            except TypeError:
                raise TypeError('Job keyword arguments must be pickleable.')
            
        PeasyJobQueue.objects.create(
            job_name=job_name,
            pickled_args=args,
            pickled_kwargs=kwargs,
            title=title,
            doing_now='Enqueued',
            progress=0,
            started=False,
            complete=False,
            failed=False,
        )

    def execute_job(self, job_pk: int):
        """Execute a job from the db queue."""
        job = PeasyJobQueue.objects.get(pk=job_pk)
        logger.info(f'executing {job.title}')
        job_name = job.job_name
        args: tuple = pickle.loads(job.pickled_args)
        if job.pickled_kwargs:
            kwargs: dict[str] = pickle.loads(job.pickled_kwargs)
        else:
            kwargs = {}
        try:
            PeasyJobQueue.objects.filter(pk=job_pk).update(
                doing_now='Starting...',
            )
            try:
                self.job_definitions[job_name](*args, job_pk=job_pk, **kwargs)
            except TypeError as e:
                self.job_definitions[job_name](*args, **kwargs)
        except Exception as e:
            PeasyJobQueue.objects.filter(pk=job_pk).update(
                doing_now='Failed',
                complete=False,
                failed=True,
            )
            raise e
        else:
            PeasyJobQueue.objects.filter(pk=job_pk).update(
                doing_now='Complete',
                progress=100,
                complete=True,
            )

    def update_status(
        job_pk: int, doing_now: str, progress: int,
        started: bool, complete: bool, failed: bool,
    ):
        PeasyJobQueue.objects.filter(pk=job_pk).update(
            doing_now=doing_now,
            progress=progress,
            started=started,
            complete=complete,
            failed=failed,
        )

    def run(self):
        """Run the job queue."""
        while True:
            if PeasyJobQueue.objects.filter(started=False).count() > 0:
                job = PeasyJobQueue.objects.filter(started=False).first()
                job.started = True
                job.save()
                call_command('execute_job', job.pk)
            else:
                sleep(self.polling_interval)


peasy = PeasyJob()
