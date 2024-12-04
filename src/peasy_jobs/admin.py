from django.contrib import admin
from django.utils.html import format_html

from peasy_jobs.models import PeasyJobQueue


@admin.register(PeasyJobQueue)
class PeasyJobQueueAdmin(admin.ModelAdmin):
    list_display = ("job_name", "title", "created", "started", "completed", "status_emoji")

    def status_emoji(self, obj):
        status_to_emoji = {
            "Q": ("📥", "Enqueued"),
            "O": ("🔄", "Ongoing"),
            "C": ("✅", "Completed"),
            "F": ("❌", "Failed"),
            "X": ("🚫", "Cancelled"),
        }
        emoji, status_text = status_to_emoji.get(obj.status, ("❓", "Unknown"))
        return format_html('<span title="{}">{}</span>', status_text, emoji)

    status_emoji.short_description = "Status"
    status_emoji.allow_tags = True
