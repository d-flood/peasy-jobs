from django.core.management.base import BaseCommand

from peasy_jobs.peasy_jobs import peasy


class Command(BaseCommand):
    help = "Starts the Peasy Job Runner."

    def handle(self, *args, **options):
        peasy.run()
        self.stdout.write("Exited Peasy Job Runner.")
