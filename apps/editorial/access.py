"""Who may work the editorial queue.

A plain function returning a plain bool, and a decorator built on it. Both deliberately:
a permission helper that returns a *response* looks fine at the call site
(`if denied: return`) and never fires, because the response is truthy-shaped but the
caller's check is on `None`. That exact bug let a member on the social dashboard make
themselves an admin after being sent a 403.

Two tiers can work the queue:

  1. **Office** — staff / superuser / administrator / tech_support / commissioning_editor.
     They see every application across every journal.

  2. **Journal manager** — someone with an active Editor-in-Chief (or equivalent) appointment
     for one or more journals. They see only applications that name one of their journals,
     and can decide those applications.  They cannot see or decide applications for journals
     they do not manage.

Editors and section editors are *not* in either tier — they serve on boards, they do not
decide who joins one.
"""

from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied

#: Counted on the live capability rows: administrator 6, tech support 7, commissioning
#: editor 24.
OFFICE_ROLES = {"administrator", "tech_support", "commissioning_editor"}

#: Appointment roles that give someone journal-level application management access.
#: Counted in live data: Editor-in-Chief 344, Associate Editor-in-chief 382, etc.
#: Case-sensitive match against the Appointment.role strings as imported.
# # wisp 2026-10-02: CE as journal-manager
MANAGER_ROLES = frozenset({
    "Editor-in-Chief",
    "Associate Editor-in-chief",
    "Associate Editor-in-Chief",
    "Commissioning Editor",
    "journal_manager",
})


def is_office(user) -> bool:
    """True or False. Never a response, never None."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.roles.filter(role__in=OFFICE_ROLES).exists()


def manager_journals(user):
    """Queryset of Journal objects this user actively manages (EIC or equivalent).
    Returns an empty queryset for anyone not authenticated or not a manager."""
    from apps.editorial.models import Journal
    if not getattr(user, "is_authenticated", False):
        return Journal.objects.none()
    return Journal.objects.filter(
        appointments__member=user,
        appointments__role__in=MANAGER_ROLES,
        appointments__ended_on__isnull=True,
    ).distinct()


def is_journal_manager(user) -> bool:
    """True if the user holds at least one active manager-tier appointment."""
    return manager_journals(user).exists()


def can_work_queue(user) -> bool:
    """Office sees all; journal managers may see their slice."""
    return is_office(user) or is_journal_manager(user)


def can_see_application(user, application) -> bool:
    """Office sees any application; journal managers see only applications that name
    one of their journals.  Returns True or False — never a response."""
    if is_office(user):
        return True
    managed_ids = manager_journals(user).values_list("id", flat=True)
    return application.journals.filter(journal_id__in=managed_ids).exists()


def office_only(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not is_office(request.user):
            raise PermissionDenied("This is the editorial office's queue.")
        return view(request, *args, **kwargs)
    return guarded


def queue_or_manager(view):
    """Allows both office and journal managers. Views must scope their own querysets."""
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not can_work_queue(request.user):
            raise PermissionDenied("This is the editorial office's queue.")
        return view(request, *args, **kwargs)
    return guarded
