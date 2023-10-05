from django.db import models
from django.conf import settings


class PeasyJobQueue(models.Model):
    class Meta:
        verbose_name_plural = 'Enqueud Jobs'
        ordering = ['-created']
    
    job_name = models.CharField(max_length=255, null=False)
    pickled_args = models.BinaryField(null=True)
    pickled_kwargs = models.BinaryField(null=True)
    title = models.CharField(max_length=255, null=False)
    doing_now = models.CharField(max_length=255, null=False)
    progress = models.IntegerField(null=False)
    started = models.BooleanField(default=False)
    complete = models.BooleanField(default=False)
    failed = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        completed = PeasyJobQueue.objects.filter(complete=True)
        failed = PeasyJobQueue.objects.filter(failed=True)
        if completed.count() > settings.PEASY_MAX_COMPLETED:
            completed.last().delete()
        if failed.count() > settings.PEASY_MAX_FAILED:
            failed.last().delete()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.title}, {self.created}'
