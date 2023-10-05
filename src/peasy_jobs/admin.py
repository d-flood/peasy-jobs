from django.contrib import admin

from peasy_jobs.models import PeasyJobQueue


admin.site.register(PeasyJobQueue)
