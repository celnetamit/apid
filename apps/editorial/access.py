"""Who may work the editorial queue.

A plain function returning a plain bool, and a decorator built on it. Both deliberately:
a permission helper that returns a *response* looks fine at the call site
(`if denied: return`) and never fires, because the response is truthy-shaped but the
caller's check is on `None`. That exact bug let a member on the social dashboard make
themselves an admin after being sent a 403.

Two tiers can work the queue:

  1. **Office** — staff / superuser / administrator / tech_support.
     They see every application across every journal.

  2. **Journal manager** — an internal EditorialStaff member (our own employee)
     set as `Journal.commissioning_editor` on one or more journals. The access
     link is the signatory link: whichever journals have your EditorialStaff row
     set as their commissioning editor are the journals whose applications you
     can decide. Nothing else.

Deliberately out of scope: external academic appointments.  Even an active
Editor-in-Chief, Associate EIC, or Commissioning-Editor Appointment on a journal
does not grant queue access — board members serve on boards, they do not decide
who joins one. The academic board is advisory; application decisions belong to
the editorial office (internal staff + their delegated commissioning editors).
"""

from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied

#: Counted on the live capability rows: administrator 6, tech support 7.
#:
#: wisp 2026-10-03: "commissioning_editor" was in this set because the legacy WP role
#: implied "office tier". With the new EditorialStaff model that scopes a commissioning
#: editor to specific journals via Journal.commissioning_editor, giving the role
#: office-wide access would show every editor every subject's applications — which
#: defeats the point. They now reach the queue as journal managers for their mapped
#: journals (see `manager_journals`).
OFFICE_ROLES = {"administrator", "tech_support"}


def is_office(user) -> bool:
    """True or False. Never a response, never None."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.roles.filter(role__in=OFFICE_ROLES).exists()


def manager_journals(user):
    """Queryset of Journal objects this user actively manages.

    One source: Journal.commissioning_editor pointing at an active EditorialStaff
    whose email matches the user's email. Internal employees only — the whole
    point of the correction (2026-10-05) is that external academic appointments
    do not grant queue power.
    """
    from apps.editorial.models import Journal
    if not getattr(user, "is_authenticated", False):
        return Journal.objects.none()
    email = (getattr(user, "email", "") or "").lower()
    if not email:
        return Journal.objects.none()
    return Journal.objects.filter(
        commissioning_editor__email__iexact=email,
        commissioning_editor__active=True,
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
