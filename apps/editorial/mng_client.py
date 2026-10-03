"""Signed HTTP client for mng's read-only APID queue endpoints.

Every call is timestamped and HMAC-signed with the shared secret. The same
scheme APID uses in `apps.editorial.apply` for the intake — one client, two
directions.

Loopback calls to mng need the `Host` and `X-Forwarded-Proto` headers or the
Django app rejects them (400 from ALLOWED_HOSTS or 301 from SSL redirect). Both
are set here so nothing else has to remember.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


MNG_BASE_URL = os.environ.get("MNG_BOARD_INTAKE_URL",
                              "http://127.0.0.1:8110/api/v1/apid/board-application/")
# Everything derived from the base URL, so switching to a different host or
# port needs one env change and not five.
_HOST = "manuscript-engine.celnet.in"
_ROOT = MNG_BASE_URL.rsplit("/apid/", 1)[0] + "/apid"


def _secret() -> str:
    return os.environ.get("APID_LOOKUP_SECRET", "").strip()


def _signed(path: str, params: dict | None = None, timeout: int = 10):
    """GET a signed URL, returning parsed JSON. Raises on non-2xx."""
    if not _secret():
        raise RuntimeError("APID_LOOKUP_SECRET not set")
    url = f"{_ROOT}{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    ts = str(int(time.time()))
    body = b""
    sig = hmac.new(_secret().encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, headers={
        "Host": _HOST,
        "X-Forwarded-Proto": "https",
        "X-APID-Timestamp": ts,
        "X-APID-Signature": sig,
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def queue_summary(journal_ids: list[int] | None = None) -> dict:
    # wisp 2026-10-02 (option-B): native decisions
    # wisp 2026-10-03: optional journal_ids filter so a journal manager only
    # sees counts for their own journals.
    from apps.editorial.models import Application, Decision
    qs = Application.objects.all()
    if journal_ids is not None:
        qs = qs.filter(journals__journal_id__in=journal_ids).distinct()
    return {
        "new": qs.filter(decision=Decision.PENDING).count(),
        "under_review": 0,
        "accepted": qs.filter(decision=Decision.ACCEPTED).count(),
        "declined": qs.filter(decision=Decision.DECLINED).count(),
    }


def _queue_summary_legacy_mng() -> dict:
    return _signed("/queue/summary/").get("counts", {}) or {}


def queue(*, status: str | None = None, journal: str | None = None,
          q: str | None = None, page: int = 1, page_size: int = 50,
          journal_ids: list[int] | None = None) -> dict:
    # wisp 2026-10-02 (option-B): native queue from local DB
    # wisp 2026-10-03: optional journal_ids filter so a journal manager only
    # sees applications naming at least one of their journals.
    from apps.editorial.models import Application, Decision
    from django.db.models import Q
    qs = (Application.objects
          .select_related("member", "member__profile", "decided_by")
          .prefetch_related("journals__journal")
          .order_by("-id"))
    if status in ("new", "pending"):
        qs = qs.filter(decision=Decision.PENDING)
    elif status == "accepted":
        qs = qs.filter(decision=Decision.ACCEPTED)
    elif status == "declined":
        qs = qs.filter(decision=Decision.DECLINED)
    elif status == "withdrawn":
        qs = qs.filter(decision=Decision.WITHDRAWN)
    if journal_ids is not None:
        qs = qs.filter(journals__journal_id__in=journal_ids).distinct()
    if q:
        qs = qs.filter(Q(member__full_name__icontains=q)
                       | Q(member__email__icontains=q)
                       | Q(subject__icontains=q)
                       | Q(applying_for__icontains=q))
    total = qs.count()
    sliced = qs[(page - 1) * page_size: page * page_size]
    rows = []
    for a in sliced:
        js = list(a.journals.all())
        rows.append({
            "id": str(a.pk),
            "pk": a.pk,
            "application_id": a.pk,
            "legacy_apid_application_id": a.pk,
            "applicant": a.member.display_name if a.member else "-",
            "email": getattr(a.member, "email", "") if a.member else "",
            "role": a.applying_for or "",
            "subject": a.subject or "",
            "journals": ", ".join([(j.journal.title if j.journal else j.stated_title) for j in js if (j.journal or j.stated_title)]),
            "status": a.decision or "pending",
            "status_display": a.get_decision_display(),
            "applied_at": a.applied_at,
            "decided_at": a.decided_at,
            "cv_url": f"/office/application/{a.pk}/cv" if a.cv else "",
            "cv_name": a.cv.name.rsplit("/", 1)[-1] if a.cv else "",
        })
    return {"rows": rows, "total": total, "page": page, "page_size": page_size}


def application_detail(pk: str) -> dict:
    """Local-DB variant: build the same payload shape from APID's Application."""
    from apps.editorial.models import Application
    try:
        a = (Application.objects.select_related("member", "member__profile", "decided_by")
             .prefetch_related("journals__journal").get(pk=int(pk)))
    except (ValueError, Application.DoesNotExist):
        return None
    # wisp 2026-10-02 pm: per-choice decision data.
    # wisp 2026-10-03: find the appointment per accepted choice so the UI can
    # offer certificate + empanelment-letter PDFs for approvals.
    from apps.editorial.models import Appointment
    appts_by_journal = {
        appt.journal_id: appt.pk
        for appt in Appointment.objects.filter(application=a).only("pk", "journal_id")
    }
    choices = []
    for j in a.journals.all():
        choices.append({
            "choice_id": j.pk,
            "journal_title": (j.journal.title if j.journal else j.stated_title) or "(untitled)",
            "journal_id": j.journal.pk if j.journal else None,
            "role": a.applying_for or "",
            "role_display": a.applying_for or "—",
            "role_appointed": j.role_appointed or (a.applying_for or ""),
            "status": j.decision or "pending",
            "status_display": j.get_decision_display(),
            "preference": j.preference,
            "decided_at": j.decided_at,
            "decided_by_name": j.decided_by.display_name if j.decided_by else "",
            "note": j.note or "",
            "appointment_id": appts_by_journal.get(j.journal_id) if j.decision == "accepted" else None,
        })
    app_data = {
        "id": a.pk,
        "legacy_apid_application_id": a.pk,
        "email": getattr(a.member, "email", ""),
        "name": a.member.display_name if a.member else "",
        "applying_for": a.applying_for,
        "subject": a.subject,
        "stated_designation": a.stated_designation,
        "stated_department": a.stated_department,
        "stated_affiliation": a.stated_affiliation,
        "statement": a.note or "",
        "status": a.decision,
        "status_display": a.get_decision_display(),
        "applied_at": a.applied_at,
        "decided_at": a.decided_at,
        "decided_by": a.decided_by.display_name if a.decided_by else "",
        "cv_url": (f"/office/application/{a.pk}/cv") if a.cv else "",
        "cv_name": a.cv.name.rsplit("/", 1)[-1] if a.cv else "",
    }
    return {"application": app_data, "choices": choices}


def my_applications(email: str) -> list[dict]:
    # wisp 2026-10-02 (option-B): native my-apps from local DB
    from apps.identity.models import Member
    from apps.editorial.models import Application
    if not email:
        return []
    m = Member.objects.filter(email__iexact=email).first()
    if not m:
        return []
    out = []
    for a in (Application.objects.filter(member=m).prefetch_related("journals__journal")
              .order_by("-id")):
        js = list(a.journals.all())
        title = ""
        if js:
            title = (js[0].journal.title if js[0].journal else js[0].stated_title)
        out.append({
            "id": a.pk,
            "status": a.decision or "pending",
            "status_display": a.get_decision_display(),
            "journal_title": title,
            "applying_for": a.applying_for,
            "role": a.applying_for,
            "subject": a.subject,
            "submitted_at": None,
        })
    return out


# ---------------------------------------------------------------------------
# The reverse direction: mng calls APID when a board application is accepted
# so the appointment shows up on the member's public /profiles/<apid>/ page.
# The endpoint is `apps.editorial.reflect.appointment` — signed with the same
# APID_LOOKUP_SECRET.
# ---------------------------------------------------------------------------


def invite_create(*, apid, email, name, journal_code, role, note=""):
    """Ask mng to open an invitation for this member. Returns the invitation id."""
    if not _secret():
        raise RuntimeError("APID_LOOKUP_SECRET not set")
    ts = str(int(time.time()))
    body = json.dumps({
        "apid": apid, "email": email, "name": name,
        "journal": journal_code, "role": role, "note": note,
    }).encode()
    sig = hmac.new(_secret().encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{_ROOT}/invite/", data=body, method="POST",
        headers={"Host": _HOST, "X-Forwarded-Proto": "https",
                 "Content-Type": "application/json",
                 "X-APID-Timestamp": ts, "X-APID-Signature": sig})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def invitations_for(email):
    """Every invitation addressed to one email address."""
    if not email:
        return []
    try:
        return _signed("/invite/mine/", {"email": email}).get("rows", [])
    except Exception:                                            # noqa: BLE001
        return []


def invite_respond(token, response, note=""):
    """Accept or decline an invitation. Response is 'accept' or 'decline'."""
    if not _secret():
        raise RuntimeError("APID_LOOKUP_SECRET not set")
    ts = str(int(time.time()))
    body = json.dumps({"token": token, "response": response, "note": note}).encode()
    sig = hmac.new(_secret().encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{_ROOT}/invite/respond/", data=body, method="POST",
        headers={"Host": _HOST, "X-Forwarded-Proto": "https",
                 "Content-Type": "application/json",
                 "X-APID-Timestamp": ts, "X-APID-Signature": sig})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def recommend(*, journal_code, role="associate", limit=20):
    """Rank APID members for a journal + role. See apid_recommend.py."""
    return _signed("/recommend/", {
        "journal": journal_code, "role": role, "limit": limit,
    }).get("candidates", [])
