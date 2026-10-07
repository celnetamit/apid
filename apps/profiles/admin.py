from django.contrib import admin

from apps.profiles.models import (Award, CareerPosition, Conference, LegacyProfileLink,
                                  Profile, Project, Qualification)


class QualificationInline(admin.TabularInline):
    model = Qualification
    extra = 0


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("member", "affiliation", "designation", "country", "orcid")
    list_filter = ("country", "profession")
    search_fields = ("member__apid", "member__full_name", "member__email",
                     "affiliation", "department", "orcid", "expertise")
    autocomplete_fields = ["member"]
    readonly_fields = ("wp_entry_id", "source_updated_at", "updated_at")
    inlines = [QualificationInline]


@admin.register(LegacyProfileLink)
class LegacyProfileLinkAdmin(admin.ModelAdmin):
    """Read-only on purpose: these are the addresses printed in published papers. A
    changed one is a link that stops working, with nothing to say it ever existed."""
    list_display = ("wp_entry_id", "member", "form_id", "superseded")
    search_fields = ("wp_entry_id", "member__apid", "member__full_name")
    readonly_fields = ("member", "wp_entry_id", "form_id", "superseded")

    def has_add_permission(self, request):
        return False


class _CvAdmin(admin.ModelAdmin):
    search_fields = ("member__apid", "member__full_name")
    autocomplete_fields = ["member"]
    readonly_fields = ("wp_entry_id",)


@admin.register(Award)
class AwardAdmin(_CvAdmin):
    list_display = ("name", "member", "institution", "awarded_on")
    search_fields = _CvAdmin.search_fields + ("name", "institution")


@admin.register(Conference)
class ConferenceAdmin(_CvAdmin):
    list_display = ("name", "member", "organizer", "starts_on", "location")
    search_fields = _CvAdmin.search_fields + ("name", "organizer")


@admin.register(Project)
class ProjectAdmin(_CvAdmin):
    list_display = ("title", "member", "sponsor", "stage", "started_on")
    search_fields = _CvAdmin.search_fields + ("title", "sponsor")


@admin.register(CareerPosition)
class CareerPositionAdmin(_CvAdmin):
    list_display = ("role", "organisation", "member", "started_on", "is_current")
    search_fields = _CvAdmin.search_fields + ("organisation", "role")
