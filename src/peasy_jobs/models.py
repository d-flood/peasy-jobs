from tkinter.messagebox import CANCEL
from django.db import models
from django.conf import settings



class PeasyJobQueue(models.Model):
    class Meta:
        verbose_name_plural = 'Peasy Jobs'
        ordering = ['-created']

    ENQUEUED = 'Q'
    ONGOING = 'O'
    COMPLETED = 'C'
    FAILED = 'F'
    CANCELLED = 'X'
    STATUS_CHOICES = [
        (ENQUEUED, 'Enqueued'),
        (ONGOING, 'Ongoing'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
        (CANCELLED, 'Cancelled'),
    ]

    job_name = models.CharField(max_length=255, null=False)
    pickled_args = models.BinaryField(null=True)
    pickled_kwargs = models.BinaryField(null=True)
    result = models.BinaryField(null=True)
    title = models.CharField(max_length=255, null=False)
    status_msg = models.CharField(max_length=255, null=False)
    extra = models.JSONField(null=True)
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=ENQUEUED)

    created = models.DateTimeField(auto_now_add=True)
    started = models.DateTimeField(null=True)
    completed = models.DateTimeField(null=True)

    def save(self, *args, **kwargs):
        completed = PeasyJobQueue.objects.filter(status=self.COMPLETED)
        failed = PeasyJobQueue.objects.filter(status=self.FAILED)
        cancelled = PeasyJobQueue.objects.filter(status=self.CANCELLED)
        if completed.count() > settings.PEASY_MAX_COMPLETED:
            completed.last().delete()
        if failed.count() > settings.PEASY_MAX_FAILED:
            failed.last().delete()
        if cancelled.count() > settings.PEASY_MAX_CANCELLED:
            cancelled.last().delete()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.title}, {self.created}'
