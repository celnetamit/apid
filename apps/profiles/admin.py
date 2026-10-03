from django.contrib import admin

from apps.profiles.models import LegacyProfileLink, Profile, Qualification


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
