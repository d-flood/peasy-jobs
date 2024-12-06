# Peasy Jobs

[![PyPI - Version](https://img.shields.io/pypi/v/peasy-jobs.svg)](https://pypi.org/project/peasy-jobs)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/peasy-jobs.svg)](https://pypi.org/project/peasy-jobs)
![tests status badge](https://github.com/d-flood/peasy-jobs/actions/workflows/tests.yml/badge.svg?branch=main)

---

An _incredibly_ simple database queue and background worker for Django. It is an "easy peasy" option for those who do not have a very busy task queue.

**Table of Contents**

- [Installation](#installation)
- [Quickstart](#quickstart)
- [Django Optional Settings](#django-optional-settings)
- [Tracking a Job's Status](#tracking-a-jobs-status)
- [Cancelling Jobs and Gracefully Shutting Down the Job Runner](#cancelling-jobs-and-gracefully-shutting-down-the-job-runner)

- [License](#license)

## Quickstart

Like other task queue runners (e.g., [Celery](https://docs.celeryq.dev/en/stable/index.html), [Huey](https://huey.readthedocs.io/en/latest/), etc.), Peasy runs in a separate process from your main Django application and processes jobs from a queue. It runs the same code as the application but with a different startup command.

1. Install then add `peasy_jobs` to `installed_apps`
   ```console
   > pip install peasy-jobs
   ```
   ```Python
   # settings.py
   INSTALLED_APPS = [
        ...
        "peasy_jobs",
        ...
    ]
   ```
2. Run migrations `python manage.py migrate`. This adds the job queue table/result backend to your database.
3. Decorate functions that should be run in the background and outside of the request-response cycle:

   ```Python
   # jobs.py
   from peasy_jobs.peasy_jobs import peasy

   @peasy.job("export data to s3")
   def generate_large_data_export():
       data = gather_data()
       upload_data_to_s3(data)
   ```

   Calling this function from your Django application (likely during the handling of a request) will add the function to the queue, along with its positional and keyword arguments. It will remain in the queue until it is processed by the job runner.

4. Start your Django application, e.g. `./manage.py runserver`, `gunicorn myproject.wsgi:application`, etc.
5. Start the Peasy job runner `python manage.py run_peasy`. Peasy will read jobs from the database queue table, execute them, and store both the status and return value in the same object.

## Django Optional settings

These are optional settings and their defaults if not defined.

```Python
# settings.py
PEASY_MAX_COMPLETED = 10 # max number of completed job objects to keep in the db
PEASY_MAX_FAILED = 10 # max number of failed job objects to keep in the db
PEASY_MAX_CANCELLED = 10 # max number of cancelled job objects to keep in the db
PEASY_POLLING_INTERVAL = 2 # seconds to wait between checks for enqueued jobs in the db
PEASY_CONCURRENCY = 1 # max number of worker threads or processes
PEASY_WORKER_TYPE = "process" # "thread" or "process"
PEASY_SHUTDOWN_TIMEOUT = 30 # grace seconds to wait for jobs to complete after receiving a sigint before terminating them
```

## Tracking a Job's Status

When a job is called by the main Django application, a `PeasyJobQueue` object is created and added to the database. You can query this object both to track whether a background job was successful _and_ to manually add status updates to it (progress percentage, for example).

Peasy will update the status of the job on the edges of its work, i.e., the beginning and ending of the job. This includes whether the job succeeded, failed, or was cancelled. However, you can use `PeasyJobQueue.status_msg` and `PeasyJobQueue.extra` to store a string and arbitrary dictionary (respectively) for updating the job status throughout a background task.

Peasy will conveniently inject the `PeasyJobQueue` `pk` as an argument to your job function _if_ you add `job_pk` as an argument (and obviously, don't supply a value for it yourself from the calling code).

```Python
# jobs.py
from peasy_jobs.models import PeasyJobQueue
from peasy_jobs.peasy_jobs import peasy

@peasy.job("export data to s3")
def generate_large_data_export(job_pk: int): # add job_pk here and it will automatically be injected.
    data = gather_data()
    peasy.update_status( # a convenience method for updating a peasy job status
        job_pk,
        status_msg="Succesfully gathered data. Now uploading data to s3.",
        extra={"progress": 50}) # use the `extra` field to optionally store an arbitrary dictionary.
    upload_data_to_s3(data)
```

If your job returns a value, it will be stored in `PeasyJobQueue.result`.

You can also view job statuses in the admin with no additional configuration:
![PeasyJobQueue job status Django admin list view showing a tabular layout with job completions and failures clearly indicated.](./.github/images/admin_listview.png)

## Cancelling Jobs and Gracefully Shutting Down the Job Runner

### How to Cancel an Enqueued Job

To cancel an enqueued job before it has been read by the job runner, simply set its status to cancelled and it will not be processed. **Note:** Peasy Jobs does not provide a way to target and cancel a specific _running_ job.

```Python
PeasyJobQueue.objects.filter(pk=job_pk).update(status=PeasyJobQueue.CANCELLED)
```

### Gracefully Shutting Down

To gracefully shutdown the Peasy Job runner, send its main process a `sigint` (e.g. CTRL+C). It will not immediately shutdown, rather, it will stop scheduling enqueued jobs for executing and wait for ongoing jobs to complete. Once there are no running jobs it will promptly exit. This grace period in which ongoing jobs are given a chance to complete is controlled by the `PEASY_SHUTDOWN_TIMEOUT` [setting variable](#django-optional-settings). If not defined, the grace period will last 30 seconds.

The graceful shutdown is intended to make it easier to update application without killing in-process jobs. This pairs well with Docker and Docker Compose since it also uses `sigint` when stopping a running service.

## Arguments and Result Data Gotcha

There is only one restriction concerning the positional arguments, keyword arguments, and return values: They must all be pickleable. The arguments and return values are stored in the database, which cannot store arbitrary Python objects _except_ as far as we can get with pickling. It is recommended that you try to keep arguments and return values JSON serializable since security concerns could force Peasy Jobs into using JSON, JSONB, or other safer serialization protocols.

## License

`peasy-jobs` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
