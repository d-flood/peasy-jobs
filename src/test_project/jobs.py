import logging
from time import sleep

from peasy_jobs.peasy_jobs import peasy

logger = logging.getLogger("test_project")


@peasy.job("test_job")
def job_should_succeed(job_pk: int):
    logger.info("test_job is running")
    logger.info(f"job_pk: {job_pk}")
    sleep(2)
    logger.info("test_job is done")
    return "test_job is done"


@peasy.job("test_job_will_fail")
def job_should_fail(job_pk: int):
    logger.info(f"job_pk: {job_pk}")
    logger.info("test_job_will_fail is starting")
    raise Exception("This job will fail")
