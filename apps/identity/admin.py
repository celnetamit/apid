"""The admin, registered so that "administrator" means something.

Making somebody a superuser of an admin with nothing in it is a promise that is not
kept. Every model that holds migrated data is here, searchable by the things an office
actually has to hand — an APID, an email address, a person's name.

The WordPress ids and hashes are read-only everywhere. They are the thread back to where
each row came from, and a row whose origin has been edited cannot be traced or re-imported.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.identity.models import Member, MemberRole


class RoleInline(admin.TabularInline):
    """Roles are a choice on the row, not a table: `Role` is a TextChoices."""
    model = MemberRole
    extra = 0


@admin.register(Member)
class MemberAdmin(UserAdmin):
    list_display = ("apid", "username", "full_name", "email", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "is_superuser", "roles__role")
    search_fields = ("apid", "username", "full_name", "email", "wp_user_id")
    ordering = ("apid",)
    inlines = [RoleInline]
    readonly_fields = ("wp_user_id", "legacy_password", "last_login", "date_joined",
                       "registered_at", "imported_at")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Identity", {"fields": ("apid", "title", "full_name", "email",
                                 "contact_number", "country")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser",
                                    "groups", "user_permissions")}),
        ("Where it came from", {"classes": ("collapse",),
                                "fields": ("wp_user_id", "legacy_password",
                                           "registered_at", "imported_at",
                                           "last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("username", "apid", "full_name", "email",
                           "password1", "password2")}),
    )
