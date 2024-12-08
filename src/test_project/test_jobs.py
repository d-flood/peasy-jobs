import pytest
from django.core.management import call_command

from peasy_jobs.models import PeasyJobQueue
from peasy_jobs.peasy_jobs import peasy
from test_project.jobs import job_should_fail, job_should_succeed

peasy.test_mode = True


@pytest.fixture(autouse=True)
def reset_peasy():
    """Reset the peasy job runner state between tests."""
    yield
    peasy.running = True
    peasy.shutting_down = False
    peasy._shutdown_start_time = None
    peasy._active_processes.clear()


@pytest.mark.django_db
def test_job_registration():
    """Test that jobs are properly registered with peasy."""
    assert "test_project.jobs.job_should_succeed" in peasy.job_definitions
    assert "test_project.jobs.job_should_fail" in peasy.job_definitions


@pytest.mark.django_db
def test_job_queueing():
    """Test that calling the job function adds it to the queue."""
    job_should_succeed()

    queued_job = PeasyJobQueue.objects.first()
    assert queued_job is not None
    assert queued_job.job_name == "test_project.jobs.job_should_succeed"
    assert queued_job.status == PeasyJobQueue.ENQUEUED
    assert queued_job.title == "test_job"
    assert queued_job.started is None
    assert queued_job.completed is None


@pytest.mark.django_db
def test_failed_job_queueing():
    """Test that calling the failing job function adds it to the queue."""
    job_should_fail()

    queued_job = PeasyJobQueue.objects.first()
    assert queued_job is not None
    assert queued_job.job_name == "test_project.jobs.job_should_fail"
    assert queued_job.status == PeasyJobQueue.ENQUEUED
    assert queued_job.title == "test_job_will_fail"
    assert queued_job.started is None
    assert queued_job.completed is None


@pytest.mark.django_db(transaction=True)
def test_successful_job_execution():
    """Test that the job runner properly executes a successful job."""
    job_should_succeed()

    try:
        call_command("run_peasy", "--exit-on-empty")
    except SystemExit:
        pass  # Command may exit with sys.exit()

    completed_job = PeasyJobQueue.objects.first()
    assert completed_job is not None
    assert completed_job.status == PeasyJobQueue.COMPLETED
    assert completed_job.started is not None
    assert completed_job.completed is not None


@pytest.mark.django_db(transaction=True)
def test_failed_job_execution():
    """Test that the job runner properly handles a failed job."""
    job_should_fail()

    try:
        call_command("run_peasy", "--exit-on-empty")
    except SystemExit:
        pass  # Command may exit with sys.exit()

    failed_job = PeasyJobQueue.objects.first()
    assert failed_job is not None
    assert failed_job.status == PeasyJobQueue.FAILED
    assert "This job will fail" in failed_job.status_msg
    assert failed_job.started is not None
    assert failed_job.completed is not None
