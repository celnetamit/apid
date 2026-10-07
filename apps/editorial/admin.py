from django.contrib import admin

from apps.editorial.models import (Application, ApplicationJournal, Appointment,
                                   EditorialStaff, Journal, PublisherImprint)


@admin.register(EditorialStaff)
class EditorialStaffAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "designation", "active", "journal_count")
    list_filter = ("active", "designation")
    search_fields = ("name", "email", "phone")
    readonly_fields = ("created_at", "updated_at")

    def journal_count(self, obj):
        return obj.journals.count()
    journal_count.short_description = "Journals"


@admin.register(PublisherImprint)
class PublisherImprintAdmin(admin.ModelAdmin):
    list_display = ("publisher", "signatory_name", "signatory_title", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("publisher", "signatory_name")
    readonly_fields = ("updated_at",)


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = ("title", "abbreviation", "subject", "commissioning_editor", "status")
    list_filter = ("status", "publisher", "commissioning_editor")
    search_fields = ("title", "abbreviation", "subject")
    readonly_fields = ("wp_entry_id",)
    autocomplete_fields = ["commissioning_editor"]


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
