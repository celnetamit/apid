from django.contrib import admin

from apps.works.models import Publication


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ("short_title", "member", "journal", "year", "doi")
    list_filter = ("year", "wp_form_id")
    search_fields = ("title", "journal", "doi", "member__apid", "member__full_name")
    autocomplete_fields = ["member"]
    readonly_fields = ("wp_form_id", "wp_entry_id", "created_at")

    @admin.display(description="title")
    def short_title(self, obj):
        return obj.title[:90]
