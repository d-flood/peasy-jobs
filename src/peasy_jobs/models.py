from django.db import models
from django.utils import dateformat


class PeasyJobQueue(models.Model):
    class Meta:
        verbose_name_plural = "Peasy Jobs"
        ordering = ("-created",)

    ENQUEUED = "Q"
    ONGOING = "O"
    COMPLETED = "C"
    FAILED = "F"
    CANCELLED = "X"

    STATUS_CHOICES = (
        (ENQUEUED, "Enqueued"),
        (ONGOING, "Ongoing"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    )

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
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.title}, {dateformat.format(self.created, 'N j, Y, P')}"
