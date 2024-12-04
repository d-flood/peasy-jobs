import logging
from time import sleep

from peasy_jobs.peasy_jobs import peasy

logger = logging.getLogger("test_project")


@peasy.job("test_job")
def test_job():
    logger.info("test_job is running")
    sleep(20)
    logger.info("test_job is done")
    return "test_job is done"


@peasy.job("test_job_will_fail")
def test_job_will_fail():
    logger.info("test_job_will_fail is starting")
    raise Exception("This job will fail")
