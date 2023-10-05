from django.core.management.base import BaseCommand
from peasy_jobs.peasy_jobs import peasy



class Command(BaseCommand):
    help = 'Executes a peasy job; this should only be used by the peasy_jobs app.'

    def add_arguments(self, parser):
        parser.add_argument('job_pk', type=int)

    def handle(self, *args, **options):
        job_pk: int = options['job_pk']
        peasy.execute_job(job_pk)
        self.stdout.write(self.style.SUCCESS('Completed Job.'))