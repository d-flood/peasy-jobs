"""
URL configuration for test_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path

from test_project.jobs import job_should_fail, job_should_succeed


def job_view_successful(request):  # noqa
    job_should_succeed()
    return HttpResponse("Test job was queued")


def job_view_failed(request):  # noqa
    job_should_fail()
    return HttpResponse("Test job was queued")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("test_job/", job_view_successful),
    path("test_job_fail/", job_view_failed),
]
