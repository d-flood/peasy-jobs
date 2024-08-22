import os
import pytest
import django
from django.conf import settings
from django.test.utils import override_settings
from peasy_jobs.peasy_jobs import PeasyJob

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Ensure Django is configured before modifying settings
if not settings.configured:
    django.setup()

# Test the __init__ method with default settings
@pytest.mark.django_db
def test_peasy_job_init_default():
    with override_settings(
        PEASY_MAX_COMPLETED=None,
        PEASY_MAX_FAILED=None,
        PEASY_MAX_CANCELLED=None,
        PEASY_POLLING_INTERVAL=None,
        PEASY_MAX_CONCURRENCY=None,
        PEASY_WORKER_TYPE=None
    ):
        job = PeasyJob()

        assert job.max_completed == 10
        assert job.max_failed == 10
        assert job.max_cancelled == 10
        assert job.polling_interval == 2
        assert job.concurrency == 1
        assert job.worker_type == 'process'

# Test the __init__ method with custom settings
@pytest.mark.django_db
def test_peasy_job_init_custom():
    with override_settings(
        PEASY_MAX_COMPLETED=5,
        PEASY_MAX_FAILED=5,
        PEASY_MAX_CANCELLED=5,
        PEASY_POLLING_INTERVAL=1.0,
        PEASY_MAX_CONCURRENCY=4,
        PEASY_WORKER_TYPE='thread'
    ):
        job = PeasyJob()

        assert job.max_completed == 5
        assert job.max_failed == 5
        assert job.max_cancelled == 5
        assert job.polling_interval == 1.0
        assert job.concurrency == 4
        assert job.worker_type == 'thread'