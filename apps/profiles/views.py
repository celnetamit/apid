"""The public side: a member's profile, and the search that finds it.

Server-rendered rather than a React app, and that is a decision rather than a shortcut.
An academic profile page exists to be found — by a colleague, by a search engine, by
whoever follows the link printed in a paper — and a page that is empty until JavaScript
runs is a page Google indexes as empty. The member and editorial dashboards, which
nobody needs to index and which are all interaction, are the other way round.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.editorial.models import Appointment
from apps.identity.models import Member
from apps.profiles.models import LegacyProfileLink, Profile
from apps.social.models import Follow, SocialCounts


def profile(request, apid: str):
    """One member's academic profile.

    Two views of the same URL: an anonymous visitor (and Google) sees the safe,
    public academic identity — name, affiliation, ORCID, biography, expertise,
    publications — with a sign-in CTA. Signed-in visitors additionally see the
    social layer (follower counts, follow button, recent posts) and the editorial
    board appointments, which per the privacy policy are visible only to
    logged-in users and the CELNET editorial office.

    The URL is the same in both cases so links printed in papers and pasted into
    CVs never break, and Google's index carries the meta description and
    academic identity for every profile.
    """
    member = get_object_or_404(
        Member.objects.select_related("profile")
        .prefetch_related(
            "publications",
            "roles",
            Prefetch("appointments",
                     queryset=Appointment.objects.select_related("journal")
                     .filter(ended_on__isnull=True))),
        apid=apid)
    full_view = request.user.is_authenticated
    # wisp 2026-10-03: share widget belongs to the profile owner, not the public.
    # Anyone else wanting the link can copy it from the browser bar.
    is_owner = full_view and request.user.pk == member.pk
    counts = SocialCounts.objects.filter(member=member).first() if full_view else None
    i_follow = bool(
        full_view and request.user != member
        and Follow.objects.filter(follower=request.user, following=member).exists())
    from apps.editorial.access import is_office, is_journal_manager
    can_invite = full_view and (is_office(request.user) or is_journal_manager(request.user))
    # wisp 2026-10-03: "Login as" button for office/superuser.
    can_impersonate = (full_view and request.user != member and member.is_active
                       and (request.user.is_superuser or is_office(request.user))
                       and not request.session.get("impersonator_id"))

    invite_journals = []
    if can_invite:
        # Journals the caller can staff (via manager, or all if office). Kept small
        # for the office too — the form gets a searchable dropdown, not a 276-item list.
        from apps.editorial.access import manager_journals
        from apps.editorial.models import Journal
        if is_office(request.user):
            invite_journals = list(Journal.objects.order_by("title")
                                   .values("id", "title", "abbreviation"))
        else:
            invite_journals = list(manager_journals(request.user).order_by("title")
                                   .values("id", "title", "abbreviation"))

    # wisp 2026-10-02 pm: share widget + OpenGraph context.
    profile_url = request.build_absolute_uri(f"/profiles/{member.apid}/")
    card_url = request.build_absolute_uri(f"/profiles/{member.apid}/card.png")
    invite_url = request.build_absolute_uri(f"/r/{member.apid}/")
    share_tagline = (
        f"{member.display_name or 'An APID scholar'} on APID — the Academic "
        f"Publishing & Information Database."
    )

    # wisp 2026-10-03: Person JSON-LD. Built in the view rather than the template
    # because comma-sensitive JSON construction in Django templates is a trap —
    # one empty field and the parser trips.
    import json as _json
    _profile = getattr(member, "profile", None)
    _person = {
        "@context": "https://schema.org",
        "@type": "Person",
        "@id": f"{profile_url}#person",
        "name": member.display_name or member.get_full_name() or member.username,
        "identifier": member.apid,
        "url": profile_url,
        "memberOf": {"@id": "https://apid.journalslibrary.com/#org"},
    }
    if _profile and _profile.picture:
        _person["image"] = request.build_absolute_uri(f"/media/{_profile.picture}")
    else:
        _person["image"] = card_url
    if _profile and _profile.affiliation:
        _person["affiliation"] = {"@type": "Organization", "name": _profile.affiliation}
    if _profile and _profile.designation:
        _person["jobTitle"] = _profile.designation
    if _profile and _profile.country:
        _person["nationality"] = _profile.country
    if _profile and _profile.orcid:
        _person["sameAs"] = [f"https://orcid.org/{_profile.orcid}"]
    if _profile and _profile.biography:
        _person["description"] = _profile.biography[:500]
    person_ld_json = _json.dumps(_person, ensure_ascii=False)

    return render(request, "profiles/profile.html", {
        "member": member,
        "profile": getattr(member, "profile", None),
        "appointments": member.appointments.all() if full_view else None,
        "publications": member.publications.all()[:20],
        "counts": counts,
        "i_follow": i_follow,
        "posts": member.posts.filter(hidden=False)[:5] if full_view else None,
        "full_view": full_view,
        "is_owner": is_owner,
        "can_invite": can_invite,
        "can_impersonate": can_impersonate,
        "invite_journals": invite_journals,
        # Share / invite.
        "profile_url": profile_url,
        "card_url": card_url,
        "invite_url": invite_url,
        "share_tagline": share_tagline,
        "person_ld_json": person_ld_json,
    })


def legacy_profile(request, entry_id: int):
    """`/apid-profiles/apid/<entry>/` — every address the old site ever gave out.

    Permanent, because these are printed in papers and pasted into CVs: 5,114 of them,
    37 pointing at profile entries the old site itself stopped showing. A redirect
    costs nothing and a 404 costs somebody their link.
    """
    link = LegacyProfileLink.objects.filter(wp_entry_id=entry_id).select_related(
        "member").first()
    if link is None:
        raise Http404("No profile has ever had that address.")
    return redirect("profile", apid=link.member.apid, permanent=True)


def page(request, slug):
    """Static informational pages: about, privacy, terms."""
    allowed = {"about", "privacy", "terms"}
    if slug not in allowed:
        raise Http404
    return render(request, f"profiles/pages/{slug}.html", {
        "total": Member.objects.count(),
    })


def home(request):
    """Public landing page: a random showcase of members with complete profiles."""
    showcase = (Profile.objects
                .select_related("member")
                .exclude(affiliation="")
                .order_by("?")[:12])
    return render(request, "profiles/home.html", {"showcase": showcase})


def directory(request):
    """Search the registry: APID, name, email, affiliation, expertise or ORCID."""
    query = (request.GET.get("q") or "").strip()
    results = Profile.objects.none()
    if query:
        # APID is minted as the WordPress user id (digits), so a plain number
        # should land on a single member rather than fuzzy-matching 13,500 ORCIDs
        # that happen to contain those digits. iexact on apid, icontains on the
        # text fields.
        results = (Profile.objects.select_related("member")
                   .filter(Q(member__apid__iexact=query)
                           | Q(member__full_name__icontains=query)
                           | Q(member__email__icontains=query)
                           | Q(affiliation__icontains=query)
                           | Q(expertise__icontains=query)
                           | Q(areas_of_interest__icontains=query)
                           | Q(department__icontains=query)
                           | Q(orcid__iexact=query))
                   .order_by("member__full_name")[:60])
    return render(request, "profiles/directory.html", {
        "query": query,
        "results": results,
        "total": Member.objects.count(),
        "with_profiles": Profile.objects.exclude(affiliation="").count(),
    })
