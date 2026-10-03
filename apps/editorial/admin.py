from django.contrib import admin

from apps.editorial.models import (Application, ApplicationJournal, Appointment,
                                   Journal)


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = ("title", "abbreviation", "subject", "status")
    list_filter = ("status", "publisher")
    search_fields = ("title", "abbreviation", "subject")
    readonly_fields = ("wp_entry_id",)


class ApplicationJournalInline(admin.TabularInline):
    model = ApplicationJournal
    extra = 0
    autocomplete_fields = ["journal"]


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("member", "applying_for", "subject", "decision", "applied_at")
    list_filter = ("decision", "applying_for")
    search_fields = ("member__apid", "member__full_name", "member__email",
                     "stated_affiliation", "subject")
    autocomplete_fields = ["member"]
    date_hierarchy = "applied_at"
    inlines = [ApplicationJournalInline]
    readonly_fields = ("wp_entry_id", "applied_at")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("member", "journal", "role", "started_on", "ended_on")
    list_filter = ("role",)
    search_fields = ("member__apid", "member__full_name", "journal__title",
                     "stated_journal")
    autocomplete_fields = ["member", "journal"]
    readonly_fields = ("wp_entry_id",)
