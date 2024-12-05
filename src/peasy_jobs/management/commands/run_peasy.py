from django.core.management.base import BaseCommand

from peasy_jobs.peasy_jobs import peasy


class Command(BaseCommand):
    help = "Starts the Peasy Job Runner."

    def add_arguments(self, parser):
        parser.add_argument("--exit-on-empty", action="store_true", help="Exit when the job queue is empty")

    def handle(self, *args, **options):  # noqa
        peasy.run(exit_when_queue_empty=options["exit_on_empty"])
        self.stdout.write("Exited Peasy Job Runner.")
