"""APID side of the mng → APID reflection: appointment created / ended.

mng owns the editorial-board decision. When an application is accepted, mng
POSTs one call to this endpoint so APID's local `Appointment` table stays in
sync — which is what `/profiles/<apid>/` renders. Signed with the shared
`APID_LOOKUP_SECRET`.

Idempotent: a repeated call for the same (member, journal, role, started_on)
does not create a duplicate row. Ending an appointment sets `ended_on` on
existing rows and does not error if none match.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import time

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.editorial.models import Appointment, Journal
from apps.identity.models import Member

MAX_AGE_SECONDS = 300


def _secret() -> str:
    return os.environ.get("APID_LOOKUP_SECRET", "").strip()


def _verify(request) -> bool:
    if not _secret():
        return False
    timestamp = request.headers.get("X-APID-Timestamp", "")
    signature = request.headers.get("X-APID-Signature", "")
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > MAX_AGE_SECONDS:
        return False
    expected = hmac.new(
        _secret().encode(),
        timestamp.encode() + b"." + request.body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@csrf_exempt
@require_POST
def appointment(request):
    """Create or end an APID Appointment to mirror a mng decision.

    Body (JSON):
      email:        member's email (join key — APID uses email as identity)
      journal_code: mng journal.code (APID uses journal.abbreviation)
      role:         short role token ("associate", "eic", ...)
      started_on:   ISO date (optional; defaults to today)
      ended_on:     ISO date; when set, the matching Appointment is closed
    """
    if not _verify(request):
        return JsonResponse({"error": "bad signature"}, status=401)

    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "bad json"}, status=400)

    email = (data.get("email") or "").strip().lower()
    code = (data.get("journal_code") or "").strip()
    role = (data.get("role") or "editor").strip()[:60]
    if not email or not code:
        return JsonResponse({"error": "email and journal_code required"}, status=400)

    member = Member.objects.filter(email__iexact=email).first()
    if member is None:
        # No matching APID member — the applicant came in through a path that did
        # not create one (e.g. straight into mng). Materialise a shell profile from
        # the payload so the appointment has somewhere to live and /profiles/ works
        # the moment the applicant signs in with the same email.
        from apps.profiles.models import Profile
        # apid is a CharField; the legacy import used numeric strings so we do the
        # same, take the max numeric value and add one.
        highest = 0
        for v in Member.objects.values_list("apid", flat=True):
            try:
                n = int(v)
                if n > highest:
                    highest = n
            except (TypeError, ValueError):
                continue
        new_apid = str(highest + 1)
        member = Member.objects.create(
            username=(email.split("@")[0][:24] + "_" + new_apid)[:30],
            email=email,
            apid=new_apid,
            full_name=(data.get("full_name") or "").strip()[:200],
            contact_number=(data.get("phone") or "").strip()[:40],
            is_active=False,   # Cannot sign in until they set a password.
        )
        Profile.objects.get_or_create(
            member=member,
            defaults={"affiliation": (data.get("affiliation") or "").strip()[:255]},
        )

    # APID's Journal join key is `abbreviation` (mng's `code`). We match
    # case-insensitively — codes are typed by hand and can drift by case.
    journal = Journal.objects.filter(abbreviation__iexact=code).first()
    if journal is None:
        return JsonResponse({"error": f"journal {code} not in APID"}, status=404)

    ended_raw = (data.get("ended_on") or "").strip()
    started_raw = (data.get("started_on") or "").strip()

    if ended_raw:
        try:
            ended_on = dt.date.fromisoformat(ended_raw)
        except ValueError:
            return JsonResponse({"error": "bad ended_on"}, status=400)
        rows = Appointment.objects.filter(
            member=member, journal=journal, role=role, ended_on__isnull=True,
        )
        updated = rows.update(ended_on=ended_on)
        return JsonResponse({"ok": True, "closed": updated})

    try:
        started_on = (dt.date.fromisoformat(started_raw) if started_raw
                      else dt.date.today())
    except ValueError:
        return JsonResponse({"error": "bad started_on"}, status=400)

    row, created = Appointment.objects.get_or_create(
        member=member, journal=journal, role=role, started_on=started_on,
    )
    return JsonResponse({"ok": True, "appointment_id": row.pk, "created": created})
