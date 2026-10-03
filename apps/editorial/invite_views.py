"""APID-side views for the invitation flow.

Three views only:

- `invite_from_profile` — office/manager POSTs from a member's /profiles/ page
  to invite them to a specific journal + role. Renders the profile page with a
  banner if it worked, or with the form re-populated if it did not.

- `respond_to_invitation` — the invitee (or anyone the token was mailed to)
  accepts or declines. GET shows a confirmation page; POST records the answer.

- Inbox rendering lives on the dashboard view, which pulls the list via
  `mng_client.invitations_for` and passes it to the template.

Every write is proxied to mng — the source of truth lives there.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.editorial import mng_client
from apps.editorial.access import is_office, is_journal_manager
from apps.editorial.models import Journal
from apps.identity.models import Member


def _can_invite(user) -> bool:
    """Office staff invites for the portfolio; managers invite for their journals only."""
    return is_office(user) or is_journal_manager(user)


@login_required
@require_POST
def invite_from_profile(request, apid: str):
    """Send an invitation to this APID member for a specific journal + role."""
    if not _can_invite(request.user):
        messages.error(request, "Only office staff or journal managers can invite.")
        return redirect("profile", apid=apid)

    member = get_object_or_404(Member, apid=apid)
    code = (request.POST.get("journal_code") or "").strip()
    role = (request.POST.get("role") or "").strip()
    note = (request.POST.get("note") or "").strip()

    if not code or not role:
        messages.error(request, "Pick a journal and a role.")
        return redirect("profile", apid=apid)
    if not member.email:
        messages.error(request,
                       f"{member.display_name} has no email address on record.")
        return redirect("profile", apid=apid)

    try:
        result = mng_client.invite_create(
            apid=member.apid, email=member.email, name=member.display_name,
            journal_code=code, role=role, note=note,
        )
    except Exception as exc:                                     # noqa: BLE001
        messages.error(request, f"Could not send the invitation: {exc}")
        return redirect("profile", apid=apid)

    if result.get("existing"):
        messages.info(request,
                      f"There is already an open invitation to {code} for this member.")
    else:
        messages.success(request,
                         f"Invitation to {code} sent to {member.display_name}.")
    return redirect("profile", apid=apid)


def respond_to_invitation(request, token: str):
    """Accept or decline an invitation via its opaque token.

    A member does not have to be signed in to respond: the token is unguessable
    and lives inside a link mailed only to the invitee.
    """
    if request.method == "POST":
        response = (request.POST.get("response") or "").strip().lower()
        note = (request.POST.get("note") or "").strip()
        if response not in ("accept", "decline"):
            messages.error(request, "Choose accept or decline.")
        else:
            try:
                res = mng_client.invite_respond(token, response, note)
                if res.get("ok"):
                    messages.success(request,
                                     f"Recorded — status is now {res['status']}.")
                    return redirect("dashboard" if request.user.is_authenticated
                                    else "home")
                messages.error(request, res.get("error") or "Could not record response.")
            except Exception as exc:                             # noqa: BLE001
                messages.error(request, f"Could not reach the decisions system: {exc}")

    # Show the confirmation page. Fetch invitation detail via a targeted list call —
    # cheap and doesn't need a new endpoint. If the token doesn't match any invitation
    # visible for the signed-in user, we render a minimal "check the link" page.
    invitation = None
    if request.user.is_authenticated and request.user.email:
        for row in mng_client.invitations_for(request.user.email):
            if row.get("token") == token:
                invitation = row
                break
    return render(request, "editorial/invite_respond.html", {
        "invitation": invitation, "token": token,
    })
