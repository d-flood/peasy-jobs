import logging
from time import sleep

from peasy_jobs.peasy_jobs import peasy

logger = logging.getLogger("test_project")


@peasy.job("test_job")
def job_should_succeed():
    logger.info("test_job is running")
    sleep(2)
    logger.info("test_job is done")
    return "test_job is done"


@peasy.job("test_job_will_fail")
def job_should_fail():
    logger.info("test_job_will_fail is starting")
    raise Exception("This job will fail")
