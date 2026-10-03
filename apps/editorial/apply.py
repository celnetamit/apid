"""Applying for an editorial board, from here, decided there.

Amit, 17 Sep 2026, choosing between two designs: **A** — APID is the front door and
manuscript-ngine is where the office decides. One place decides, so the two can never
quietly disagree, which is the failure this estate has already had with journal lists
and board data living in two systems at once.

**The point is not that a member can apply from here. It is that they barely have to.**
manuscript-ngine's own form asks for twenty-two fields. This registry already holds
eleven of them — affiliation for 4,194 members, designation for 4,146, department for
4,013, expertise for 4,170, ORCID for 2,259, phone for 4,672, country, photograph,
institution URL — and two it holds *better* than a form can ask:

* **publications.** A claim on the form; 6,409 real records here, fetched from ORCID or
  entered by the member.
* **prior board service.** A free-text box there; 3,896 real appointments here.

So the applicant confirms what the registry already knows and writes the two things only
they can write: which journals, and why. A twenty-two-field form becomes a page.

**Nothing is decided here.** APID's own application table is the imported history — 2,439
of them — and stays read-only history. A new application is created in manuscript-ngine
and answered in manuscript-ngine.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

from django.core.cache import cache

from apps.editorial.models import Appointment, Journal
from apps.works.orcid import normalise_id

TIMEOUT = 20

#: What manuscript-ngine calls the roles. Sent as its vocabulary, not ours — the two
#: systems having different words for the same role is exactly how a mapping rots.
ROLES = [("associate", "Associate Editor"),
         ("section", "Section Editor"),
         ("eic", "Editor-in-Chief"),
         ("deputy", "Deputy Editor"),
         ("commissioning", "Commissioning Editor"),
         ("guest", "Guest Editor"),
         ("assistant", "Assistant Editor"),
         ("manager", "Journal Manager")]


def _secret() -> str:
    return os.environ.get("APID_LOOKUP_SECRET", "")


def prefill(member) -> Dict[str, Any]:
    """Everything the registry can answer on the applicant's behalf."""
    profile = getattr(member, "profile", None)
    serving = [f"{a.role} — {a.journal.title if a.journal else a.stated_journal}"
               + (f" (since {a.started_on:%b %Y})" if a.started_on else "")
               for a in Appointment.objects.filter(member=member)
               .select_related("journal")[:12]]
    return {
        "full_name": member.full_name or member.display_name,
        "email": member.email,
        "phone": member.contact_number or "",
        "country": (member.country or "")[:2].upper(),
        "affiliation": getattr(profile, "affiliation", "") or "",
        "designation": getattr(profile, "designation", "") or "",
        "department": getattr(profile, "department", "") or "",
        # Normalised, and dropped if it is not an ORCID at all. 142 profiles hold
        # something else in that field — one of them the old site's own URL — and
        # manuscript-ngine's column is nineteen characters, so an unchecked value
        # arrives there truncated into nonsense that looks like data.
        "orcid": normalise_id(getattr(profile, "orcid", "") or ""),
        "highest_degree": getattr(profile, "academic_qualification", "") or "",
        "subject_areas": (getattr(profile, "expertise", "") or "")[:255],
        "institution_url": getattr(profile, "affiliation_url", "") or "",
        "years_of_experience": _years(getattr(profile, "experience_years", "")),
        # Counted, not claimed.
        "publications": member.publications.count(),
        # Remembered, not retyped.
        "prior_board_service": "\n".join(serving),
        "profile_url": f"https://apid.celnet.in/profiles/{member.apid}/",
    }


def _years(value: str) -> int:
    """`"12 years"`, `"12"`, `""` → an integer, because the other side wants one."""
    import re
    m = re.search(r"\d{1,2}", str(value or ""))
    return int(m.group(0)) if m else 0


def send(payload: Dict[str, Any], url: str = "") -> Tuple[bool, str, List[str]]:
    # wisp 2026-10-02 (option-B): native decisions
    """`(sent, message, journals_not_accepted)`. Native implementation: creates a local
    Application record (and ApplicationJournal rows) in APID's own database.
    Decisions are made on apid.celnet.in now; mng is not called.
    """
    from apps.identity.models import Member
    from apps.editorial.models import Application, ApplicationJournal, Decision, Journal

    email = (payload.get("email") or "").strip()
    if not email:
        return False, "Missing applicant email.", []
    member = Member.objects.filter(email__iexact=email).first()
    if not member:
        return False, "No APID account found for this email.", []

    picks = payload.get("journals") or []
    if not picks:
        return False, "Choose at least one journal.", []

    not_matched: List[str] = []
    create_kwargs = dict(
        member=member,
        applying_for=(picks[0].get("role") or "").strip()[:120] if picks else "",
        subject=(payload.get("subject") or "").strip()[:200],
        decision=Decision.PENDING,
        stated_designation=(payload.get("designation") or "").strip()[:200],
        stated_department=(payload.get("department") or "").strip()[:200],
        stated_affiliation=(payload.get("affiliation") or "").strip()[:255],
        note=(payload.get("statement") or "").strip(),
    )
    cv = payload.get("cv_file")
    if cv is not None:
        create_kwargs["cv"] = cv
    app = Application.objects.create(**create_kwargs)
    for i, pick in enumerate(picks, start=1):
        title = (pick.get("journal") or "").strip()
        j = Journal.objects.filter(title__iexact=title).first()
        if title and not j:
            not_matched.append(title)
        ApplicationJournal.objects.create(
            application=app, journal=j, stated_title=title[:300], preference=i)
    msg = f"Application #{app.pk} received. The editorial office has been notified."
    return True, msg, not_matched


def _send_legacy_mng(payload: Dict[str, Any], url: str = "") -> Tuple[bool, str, List[str]]:
    """Pre-2026-10-02 mng-POST implementation, kept for reference only."""
    """`(sent, message, journals_not_accepted)`.

    Never raises — an application the member cannot see fail is an application they will
    send four times.

    The third value is the part that is easy to drop. The editorial platform creates the
    application if *any* chosen journal matched, and names the ones that did not. Saying
    only "sent" there would tell somebody who chose three journals that all three went,
    when one was quietly left out — the same silent-skip that has cost this estate a day
    of posts and a batch of mail before.
    """
    secret = _secret()
    target = url or os.environ.get(
        "MNG_BOARD_INTAKE_URL",
        "http://127.0.0.1:8110/api/v1/apid/board-application/")
    if not secret:
        return False, "The link to the editorial platform is not configured.", []

    body = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b"." + body,
                         hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        target, data=body,
        headers={"Content-Type": "application/json",
                 "X-APID-Timestamp": stamp, "X-APID-Signature": signature,
                 # The call goes over the loopback, straight past Caddy, so it has to
                 # say what Caddy would have said. Without the host it is refused by
                 # ALLOWED_HOSTS as a 400, and without the protocol the SSL redirect
                 # answers 301 and the application is never delivered — both of which
                 # look like "the platform is down" rather than a missing header.
                 "Host": os.environ.get("MNG_HOST", "manuscript-engine.celnet.in"),
                 "X-Forwarded-Proto": "https"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            answer = json.loads(response.read().decode())
        return (True, str(answer.get("application") or ""),
                [str(t) for t in (answer.get("unknown") or [])])
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("error") or exc.reason
        except Exception:                                        # noqa: BLE001
            detail = exc.reason
        return False, f"The editorial platform refused it: {detail}", []
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"The editorial platform could not be reached: {exc!r}", []


#: Ten minutes. The two lists change when somebody launches a journal, not by the hour.
TITLES_CACHE_SECONDS = 600


def _signed_get(url: str) -> Dict[str, Any]:
    """A signed, bodiless call to the editorial platform. `{}` if it cannot be had."""
    secret = _secret()
    if not secret:
        return {}
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b".",
                         hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        url, headers={"X-APID-Timestamp": stamp, "X-APID-Signature": signature,
                      "Host": os.environ.get("MNG_HOST", "manuscript-engine.celnet.in"),
                      "X-Forwarded-Proto": "https"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return {}


def journal_signatory(journal_slug: str) -> dict:
    """Signatory (name, title, imprint) for one journal, from the editorial platform.

    Used by the letter and certificate templates so the signature block matches the
    real imprint director rather than the generic 'Editorial Office'. Returns an empty
    dict if the platform is unreachable or the journal is unknown — the templates fall
    back to 'Editorial Office / CELNET' in that case.
    """
    cache_key = f"mng-signatory-{journal_slug}"
    cached = cache.get(cache_key)
    if cached is None:
        base = os.environ.get("MNG_JOURNALS_URL",
                              "http://127.0.0.1:8110/api/v1/apid/journals/")
        # base ends with /journals/ — replace with the signatory sub-path
        url = base.rstrip("/").rsplit("/journals", 1)[0] + f"/journals/{journal_slug}/signatory/"
        cached = _signed_get(url) or {}
        cache.set(cache_key, cached, TITLES_CACHE_SECONDS)
    return cached


def accepted_titles() -> set:
    """What the editorial platform will accept, loosely keyed. Empty if unreachable."""
    cached = cache.get("mng-journal-titles")
    if cached is None:
        url = os.environ.get("MNG_JOURNALS_URL",
                             "http://127.0.0.1:8110/api/v1/apid/journals/")
        cached = {_loose(t) for t in (_signed_get(url).get("titles") or [])}
        # A failed call is cached briefly too, so a platform that is down does not turn
        # every page view of this form into a twenty-second wait.
        cache.set("mng-journal-titles", cached, 60 if not cached else TITLES_CACHE_SECONDS)
    return cached


def journals_to_offer() -> List[Journal]:
    """Only journals the editorial platform has.

    28 of this registry's 278 do not exist there — `International Journal of Mineral`,
    `Journal of Polymer and Composites` and 26 others, most of them a near-miss of a
    real title. Offering them would let somebody fill in the whole page for a journal
    that cannot receive it.

    If the platform cannot be reached the full list is offered rather than an empty one:
    a form that says "no journals" reads as the registry being broken, and the intake
    still names anything it does not recognise.
    """
    known = accepted_titles()
    everything = list(Journal.objects.order_by("title"))
    if not known:
        return everything
    return [j for j in everything if _loose(j.title) in known]


def _loose(title: str) -> str:
    """Matched the same way the other side matches it — punctuation and case aside.

    & and 'and' are treated as equivalent so 'Journal of Polymer & Composites'
    and 'Journal of Polymer and Composites' resolve to the same key.
    """
    normalised = re.sub(r"\s*&\s*", " and ", (title or "").lower())
    return re.sub(r"[^a-z0-9]", "", normalised)
