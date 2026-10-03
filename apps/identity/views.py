"""The member's own pages: sign in, and change what the registry says about you.

The whole reason this exists is that on the live site a member cannot correct their own
record. They submit the profile form again and a new entry appears beside the old one;
the site shows whichever its view happens to pick. Here there is one record per member,
they own it, and a change is a change.
"""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.identity import google_login, google_oauth, mail
from apps.identity.registration import RegistrationForm

from apps.identity.forms import PictureForm, ProfileForm, PublicationForm
from apps.profiles.models import Profile
from apps.works import orcid
from apps.works.models import Publication

logger = logging.getLogger(__name__)


def _profile_of(member) -> Profile:
    """Every member has a profile here, even the 8,126 who never filled the form in.
    An empty profile is a page they can complete; a missing one is an error page."""
    profile, _ = Profile.objects.get_or_create(member=member)
    return profile


@login_required
def dashboard(request):
    from apps.editorial.access import is_journal_manager
    from apps.editorial.mng_client import my_applications
    member = request.user
    profile = _profile_of(member)

    # Applications live in mng now — every /me/ view pulls the member's own list
    # so status stays truthful even after a mng-side decision.
    from apps.editorial.mng_client import invitations_for
    mng_choices = []
    try:
        mng_choices = my_applications(member.email or "")
    except Exception as exc:                                     # noqa: BLE001
        # Never break the whole dashboard if mng is briefly unreachable — the
        # apps section will just be empty with a hint.
        mng_choices = []

    invitations = []
    try:
        invitations = invitations_for(member.email or "")
    except Exception:                                            # noqa: BLE001
        invitations = []
    STATE_LABELS = {
        "new": "Pending", "under_review": "Under review",
        "accepted": "Accepted", "declined": "Not accepted",
        "withdrawn": "Withdrawn", "transferred": "Moved",
    }
    for c in mng_choices:
        c["state_label"] = STATE_LABELS.get(c["status"], c["status_display"])

    # wisp 2026-10-02 pm: share / invite feature.
    profile_url = request.build_absolute_uri(f"/profiles/{member.apid}/")
    invite_url = request.build_absolute_uri(f"/r/{member.apid}/")
    display = member.display_name or member.username or "a colleague"
    share_tagline = (
        f"I'm on APID — my permanent academic profile is at {profile_url}."
    )
    invite_tagline = (
        f"Hi, I'm on APID — the Academic Publishing & Information Database. "
        f"Join me and build your permanent academic profile: {invite_url}"
    )

    return render(request, "members/dashboard.html", {
        "member": member,
        "profile": profile,
        "publications": member.publications.all()[:10],
        "publication_count": member.publications.count(),
        "appointments": member.appointments.select_related("journal").filter(
            ended_on__isnull=True),
        "applications": mng_choices,
        "invitations": invitations,
        "is_journal_manager": is_journal_manager(member),
        # Share / invite context.
        "profile_url": profile_url,
        "invite_url": invite_url,
        "share_tagline": share_tagline,
        "invite_tagline": invite_tagline,
        "referral_count": member.referrals.count(),
    })


@login_required
def edit_profile(request):
    profile = _profile_of(request.user)
    form = ProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your profile has been updated.")
        return redirect("dashboard")
    return render(request, "members/edit_profile.html",
                  {"form": form, "profile": profile})


@login_required
def edit_picture(request):
    profile = _profile_of(request.user)
    form = PictureForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        profile.picture = form.save_for(request.user)
        profile.save(update_fields=["picture", "updated_at"])
        messages.success(request, "Your photograph has been updated.")
        return redirect("dashboard")
    return render(request, "members/edit_picture.html",
                  {"form": form, "profile": profile})


@login_required
def publications(request):
    return render(request, "members/publications.html", {
        "publications": request.user.publications.all(),
    })


@login_required
def edit_publication(request, pk: int | None = None):
    """Add one, or change one of your own.

    `member=request.user` in the lookup rather than a check afterwards: an ownership
    test that can be forgotten in one branch is an ownership test that will be.
    """
    instance = (get_object_or_404(Publication, pk=pk, member=request.user)
                if pk else None)
    form = PublicationForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        publication = form.save(commit=False)
        publication.member = request.user
        publication.save()
        messages.success(request, "Saved.")
        return redirect("publications")
    return render(request, "members/edit_publication.html",
                  {"form": form, "publication": instance})


@login_required
def delete_publication(request, pk: int):
    publication = get_object_or_404(Publication, pk=pk, member=request.user)
    if request.method == "POST":
        publication.delete()
        messages.success(request, "Removed.")
        return redirect("publications")
    return render(request, "members/delete_publication.html",
                  {"publication": publication})


# ----------------------------------------------------------------- signing in with Google

def google_is_ready() -> bool:
    """Whether to draw the button, asked at render time.

    Django templates call a callable in the context, so this is passed as a function
    rather than a value — the credentials come from the environment and a restart is
    not a redeploy. It looks the function up on the module each call instead of holding
    a reference: a reference is bound when the URLs are imported, which made the button
    impossible to switch and, first, made a test of it silently pass.
    """
    return google_oauth.configured()


def google_start(request):
    """Send the member to Google, having first given this browser a nonce."""
    if not google_oauth.configured():
        messages.error(request, "Google sign-in is not set up on this site yet.")
        return redirect("login")
    nonce = google_oauth.new_nonce()
    next_path = request.GET.get("next") or "/me/"
    # Only our own paths. `next` arrives from a query string, and a redirect to
    # wherever it says is an open redirect wearing a sign-in page's clothes.
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/me/"
    # Apache's default ErrorDocument paths (`/403.shtml`, `/404.shtml`, `/500.shtml`)
    # are not Django routes. A stale `next` pointing at one of them signs the user
    # in and then 404s the dashboard. Treat them as "wherever people go after login".
    if next_path.endswith(".shtml"):
        next_path = "/me/"
    state = google_oauth.make_state(nonce, next_path)
    response = redirect(google_oauth.authorise_url(
        google_oauth.redirect_uri(request), state))
    response.set_cookie(google_oauth.NONCE_COOKIE, nonce, max_age=600,
                        httponly=True, secure=request.is_secure(), samesite="Lax")
    return response


def google_callback(request):
    """Google's answer: verify it is ours, then find the one account it belongs to."""
    response = redirect("login")
    state = google_oauth.read_state(request.GET.get("state", ""),
                                    request.COOKIES.get(google_oauth.NONCE_COOKIE, ""))
    if state is None:
        messages.error(request, "That sign-in link has expired. Please try again.")
        return response
    if request.GET.get("error") or not request.GET.get("code"):
        messages.error(request, "Google did not complete the sign-in.")
        return response

    try:
        claims = google_oauth.claims(request.GET["code"],
                                     google_oauth.redirect_uri(request))
    except Exception:                                            # noqa: BLE001
        # Logged without the code or any token: this branch is a network or
        # configuration failure, and neither is worth writing a credential down for.
        logger.exception("google sign-in: token exchange failed")
        messages.error(request, "Google could not be reached. Please try again.")
        return response

    member, reason = google_login.member_for(claims, google_oauth.domain_allowed)
    if member is None:
        messages.error(request, google_login.MESSAGES[reason])
        return response

    # `backend` named explicitly: this member was found by email rather than by
    # authenticate(), so Django has no backend to record on the session.
    login(request, member,
          backend="apps.identity.backends.UsernameOrEmailBackend")
    destination = state.get("p") or "/me/"
    done = redirect(destination)
    done.delete_cookie(google_oauth.NONCE_COOKIE)
    return done


# ------------------------------------------------------------------- joining the registry

def register(request):
    """Make an account and mint an APID.

    Open to anyone, as the live site is: this registry's members are academics
    worldwide and there is nobody to vet them against. What being a member gets you is
    a profile and a place to apply from — an editorial role is an application with a
    decision behind it, and that is decided in the office.
    """
    # `next` ko yahan tak pahunchana zaroori hai. 23 Sep 2026 ko naapa: /apply/ bina
    # login 302 deta hai login?next=/apply/ par, par wahan se "Create an account" par
    # jaate hi next gir jaata tha aur register hamesha dashboard par chhodta tha — yaani
    # editorial board ke liye aaya naya aadmi form tak pahunchta hi nahi tha, aur
    # dashboard par use wo darwaza dobara dhoondhna padta. Amit: "APID wala asli hai."
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        nxt = ""
    if request.user.is_authenticated:
        return redirect(nxt or "dashboard")
    # wisp 2026-10-02 pm: capture referral from `?ref=<APID>` into the session,
    # so the referrer survives the GET → POST round-trip. Session is wiped on login,
    # so stash the apid itself, not the Member object.
    ref_apid = (request.GET.get("ref") or "").strip()
    if ref_apid and Member.objects.filter(apid=ref_apid).exists():
        request.session["referral_apid"] = ref_apid

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        member = form.create_member()
        # Apply the referral (if captured earlier in this browser).
        ref_apid = request.session.pop("referral_apid", "")
        if ref_apid:
            inviter = Member.objects.filter(apid=ref_apid).first()
            if inviter and inviter.pk != member.pk:
                member.referred_by = inviter
                member.save(update_fields=["referred_by"])
        sent = mail.welcome(member, request)
        login(request, member,
              backend="apps.identity.backends.UsernameOrEmailBackend")
        messages.success(
            request,
            f"Welcome — your APID is {member.apid}."
            + (" A confirmation has been emailed to you." if sent else
               " Please make a note of it."))
        return redirect(nxt or "dashboard")
    return render(request, "registration/register.html",
                  {"form": form, "next": nxt})


# ------------------------------------------------------------------- ORCID

@login_required
def orcid_sync(request):
    """Show what ORCID has that this profile does not, and add what is ticked.

    Offered rather than imported: an ORCID record holds everything any publisher ever
    deposited against that iD, duplicates and mis-keyed deposits included. A profile is
    a list its owner stands behind.
    """
    profile = _profile_of(request.user)
    if not profile.orcid:
        messages.error(request, "Add your ORCID iD to your profile first.")
        return redirect("edit-profile")

    if request.method == "POST":
        chosen = request.POST.getlist("work")
        added = 0
        for raw in chosen:
            try:
                work = json.loads(raw)
            except ValueError:
                continue
            if not (work.get("title") or "").strip():
                continue
            Publication.objects.create(
                member=request.user, title=work["title"][:2000],
                journal=(work.get("journal") or "")[:255],
                year=(work.get("year") or "")[:10],
                doi=(work.get("doi") or "")[:120],
                link=(work.get("link") or "")[:1000],
                created_at=timezone.now())
            added += 1
        messages.success(request, f"{added} publication(s) added from ORCID."
                         if added else "Nothing was ticked, so nothing was added.")
        return redirect("publications")

    works = orcid.fetch(profile.orcid)
    fresh = orcid.new_for(request.user, works)
    return render(request, "members/orcid.html", {
        "orcid": profile.orcid,
        "works": [(json.dumps(w), w) for w in fresh],
        "found": len(works),
        "already": len(works) - len(fresh),
        "unreachable": not works,
    })
