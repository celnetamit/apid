"""Aggregate APID stats for the ops cockpit.

A read-only JSON endpoint, shared-key gated, that returns counts and
pending-pile shape — no PII, no per-row data. The VPS cockpit cron pulls
it every 15 minutes and writes the body to /var/www/dashboard/apid.json
so dashboard.nolege.in/app/ can render tiles alongside leads and news.

Auth: `X-APID-Stats-Key` header must equal the APID_STATS_KEY env var.
If the env var is unset or empty the endpoint 404s — a missing secret is
not the same as an open endpoint.
"""

from __future__ import annotations

import hmac
import os
from datetime import timedelta

from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.editorial.mng_client import queue_summary
from apps.editorial.models import Application, Appointment, Decision


def _authorized(request) -> bool:
    expected = (os.environ.get("APID_STATS_KEY") or "").strip()
    if not expected:
        return False
    provided = (request.headers.get("X-APID-Stats-Key") or "").strip()
    if not provided:
        return False
    return hmac.compare_digest(expected, provided)


@require_GET
def stats_json(request):
    if not _authorized(request):
        # 404 (not 401) so an unauth probe can't tell the endpoint exists.
        raise Http404()

    now = timezone.now()
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    summary = queue_summary(with_pending_stats=True)
    pending = summary.get("pending_stats") or {}

    totals = {
        "total": Application.objects.count(),
        "pending": summary.get("new", 0),
        "accepted": summary.get("accepted", 0),
        "declined": summary.get("declined", 0),
        "withdrawn": Application.objects.filter(
            decision=Decision.WITHDRAWN).count(),
    }

    applied = Application.objects
    activity = {
        "applied_last_7d": applied.filter(applied_at__gte=seven_days_ago).count(),
        "applied_last_30d": applied.filter(applied_at__gte=thirty_days_ago).count(),
        "decided_last_7d": applied.filter(decided_at__gte=seven_days_ago).count(),
        "decided_last_30d": applied.filter(decided_at__gte=thirty_days_ago).count(),
    }

    # Members: cheap aggregates kept here so the cockpit can show the
    # registry's shape without a second feed.
    from apps.identity.models import Member
    from apps.profiles.models import Profile
    members = {
        "total": Member.objects.count(),
        "with_profile": Profile.objects.exclude(affiliation="").count(),
    }

    appointments = {
        "active": Appointment.objects.filter(ended_on__isnull=True).count(),
        "created_last_30d": Appointment.objects.filter(
            started_on__gte=thirty_days_ago.date()).count(),
    }

    return JsonResponse({
        "generated_at": now.isoformat(),
        "totals": totals,
        "pending": pending,
        "activity": activity,
        "members": members,
        "appointments": appointments,
    })
