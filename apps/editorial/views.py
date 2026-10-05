"""The editorial office's queue: applications in, decisions out.

On the live site this is three Formidable forms that only a person knows are related.
An application is entry 91,204 in form 159; the office's answer is a *different* entry in
form 200 carrying "Application ID" typed into a box; the appointment is a third entry in
form 219. Nothing joins them, so nothing can be asked: not "who is waiting", not "how
long have they waited", not "which journals have nobody".

Here the decision is a field on the application, the appointment points at the
application it came from, and all three questions are a query. That is the whole reason
for the rebuild, and this is the page where it shows.
"""

from __future__ import annotations

import datetime as dt

import weasyprint

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from apps.editorial import apply as apply_bridge
from apps.editorial.access import (
    can_see_application, is_journal_manager, is_office, manager_journals,
    office_only, queue_or_manager,
)
from apps.editorial.models import (Application, ApplicationJournal, Appointment,
                                   Decision, Journal)

#: What the office can do to an application, and what each one means afterwards.
ACTIONS = {
    "accept": Decision.ACCEPTED,
    "decline": Decision.DECLINED,
    "hold": Decision.PENDING,
}


STATUS_LABELS = {
    "new":         ("Pending",     "warn"),
    "under_review":("Under review","info"),
    "accepted":    ("Accepted",    "good"),
    "declined":    ("Declined",    "muted"),
    "withdrawn":   ("Withdrawn",   "muted"),
    "transferred": ("Transferred", "info"),
}


def _csv_response(filename: str, rows):
    """Stream a list[dict] as a CSV download. The first row's keys form the
    header, so caller is responsible for consistent dict shape."""
    import csv
    from django.http import StreamingHttpResponse
    class _Echo:
        def write(self, value): return value
    writer = csv.writer(_Echo())
    rows = iter(rows)
    try:
        first = next(rows)
    except StopIteration:
        first = {}
    header = list(first.keys()) if first else []
    def gen():
        if header:
            yield writer.writerow(header)
            yield writer.writerow([first.get(h, "") for h in header])
            for r in rows:
                yield writer.writerow([r.get(h, "") for h in header])
    resp = StreamingHttpResponse(gen(), content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@queue_or_manager
def queue(request):
    """Local-DB application queue.

    Office sees every application; a journal manager (EIC/Associate EIC, or the
    commissioning editor set on the journal via EditorialStaff) sees only
    applications naming at least one of their journals. Journal + date filters
    narrow it further; per-tab counts respect those filters so the user sees
    numbers that match the table in front of them.
    """
    from apps.editorial.mng_client import queue as mng_queue, queue_summary
    from apps.editorial.models import Journal
    state = request.GET.get("state", "new")
    query = (request.GET.get("q") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip() or None
    date_to = (request.GET.get("date_to") or "").strip() or None
    try:
        journal_id = int(request.GET.get("journal") or 0) or None
    except ValueError:
        journal_id = None
    # wisp 2026-10-05: pending-age bucket filter (fresh/week/twoweek/older).
    # Only meaningful on the Pending tab; silently ignored elsewhere.
    age = (request.GET.get("age") or "").strip() or None
    if state not in ("new", "pending"):
        age = None

    user_is_office = is_office(request.user)
    journal_ids = None
    if not user_is_office:
        journal_ids = list(manager_journals(request.user).values_list("id", flat=True))
        if not journal_ids:
            journal_ids = [-1]  # match nothing

    # CSV export streams every matching row (bypassing pagination); a Boss who
    # filtered down to one journal + a date range wants the whole slice, not
    # a page of it.
    want_csv = request.GET.get("export") == "csv"
    try:
        page_num = max(1, int(request.GET.get("page") or 1))
    except (TypeError, ValueError):
        page_num = 1
    page_size = 10_000 if want_csv else 50
    try:
        summary = queue_summary(journal_ids=journal_ids, journal_id=journal_id,
                                date_from=date_from, date_to=date_to,
                                with_pending_stats=(state in ("new", "pending")))
        counts = {k: v for k, v in summary.items() if k != "pending_stats"}
        pending_stats = summary.get("pending_stats")
        page_data = mng_queue(
            status=state if state and state != "all" else None,
            q=query or None, page=page_num, page_size=page_size,
            journal_ids=journal_ids, journal_id=journal_id,
            date_from=date_from, date_to=date_to, age=age,
        )
    except Exception as exc:                                     # noqa: BLE001
        messages.error(request, f"Could not reach the decisions system: {exc}")
        counts, pending_stats, page_data = {}, None, {"rows": [], "total": 0}

    rows = page_data.get("rows", [])
    for r in rows:
        label, tone = STATUS_LABELS.get(r["status"], (r["status_display"], "muted"))
        r["state_label"], r["state_tone"] = label, tone

    if want_csv:
        from django.utils import timezone as _tz
        def _csv_rows():
            for r in rows:
                yield {
                    "APID": r.get("pk"),
                    "Applicant": r.get("applicant", ""),
                    "Email": r.get("email", ""),
                    "Role": r.get("role", ""),
                    "Subject": r.get("subject", ""),
                    "Journals": r.get("journals", ""),
                    "Status": r.get("state_label", r.get("status", "")),
                    "Applied": r.get("applied_at", "").isoformat() if r.get("applied_at") else "",
                    "Decided": r.get("decided_at", "").isoformat() if r.get("decided_at") else "",
                }
        return _csv_response(
            f"apid-queue-{state}-{_tz.now().strftime('%Y%m%d')}.csv", _csv_rows())

    total = page_data.get("total", 0)
    import math as _math
    total_pages = max(1, _math.ceil(total / page_size)) if page_size else 1

    tabs = [
        ("new",         "Pending",      counts.get("new", 0)),
        ("under_review","Under review", counts.get("under_review", 0)),
        ("accepted",    "Accepted",     counts.get("accepted", 0)),
        ("declined",    "Declined",     counts.get("declined", 0)),
    ]

    # Journal dropdown counts: Applications on each journal matching the
    # current state tab + date range + search, so "jomme (12)" tells the user
    # how many rows picking that journal would reveal. One grouped query over
    # ApplicationJournal, then a Counter.
    from collections import Counter as _Counter
    from django.db.models import Q as _Q
    from apps.editorial.models import ApplicationJournal, Decision as _Dec
    base = Journal.objects.all() if user_is_office else manager_journals(request.user)
    aj_qs = ApplicationJournal.objects.all()
    if not user_is_office:
        aj_qs = aj_qs.filter(journal__in=base)
    if state in ("new", "pending"):
        aj_qs = aj_qs.filter(application__decision=_Dec.PENDING)
    elif state == "accepted":
        aj_qs = aj_qs.filter(application__decision=_Dec.ACCEPTED)
    elif state == "declined":
        aj_qs = aj_qs.filter(application__decision=_Dec.DECLINED)
    elif state == "withdrawn":
        aj_qs = aj_qs.filter(application__decision=_Dec.WITHDRAWN)
    if date_from:
        aj_qs = aj_qs.filter(application__applied_at__date__gte=date_from)
    if date_to:
        aj_qs = aj_qs.filter(application__applied_at__date__lte=date_to)
    if query:
        aj_qs = aj_qs.filter(_Q(application__member__full_name__icontains=query)
                             | _Q(application__member__email__icontains=query)
                             | _Q(application__subject__icontains=query)
                             | _Q(application__applying_for__icontains=query))
    # Distinct application-ids per journal (one application can list the same
    # journal twice but should count once).
    counts_by_j = _Counter()
    for jid in aj_qs.values_list("journal_id", "application_id").distinct():
        if jid[0] is not None:
            counts_by_j[jid[0]] += 1
    journal_choices = [
        {**j, "app_count": counts_by_j.get(j["id"], 0)}
        for j in base.order_by("title").values("id", "title", "abbreviation")
    ]

    return render(request, "editorial/queue.html", {
        "rows": rows,
        "shown": len(rows),
        "total": total,
        "page_num": page_num,
        "total_pages": total_pages,
        "page_size": page_size,
        "state": state,
        "query": query,
        "counts": counts,
        "pending_stats": pending_stats,
        "age": age or "",
        "tabs": tabs,
        "journal_choices": journal_choices,
        "selected_journal": journal_id,
        "date_from": date_from or "",
        "date_to": date_to or "",
    })


@queue_or_manager
def approved_profiles(request):
    """A directory of every member with an active editorial appointment.

    One row per appointment, laid out as a table so a column sort or an eye
    scan can find a journal, a role or a date. Journal + since-date filters
    narrow the table; the count under the toolbar reflects the filtered view.
    """
    from django.db.models import Q
    from apps.editorial.models import Appointment, Journal
    user_is_office = is_office(request.user)
    qs = (Appointment.objects.filter(ended_on__isnull=True)
          .select_related("member", "member__profile", "journal", "application"))
    if not user_is_office:
        managed_ids = list(manager_journals(request.user).values_list("id", flat=True))
        qs = qs.filter(journal_id__in=managed_ids)

    # Suppress role-mailbox contamination: an appointment on a journal whose
    # commissioning editor shares the Member's email is a legacy artefact,
    # not that person's actual role. Same reason as journal_manage.
    from django.db.models import F
    from django.db.models.functions import Lower
    qs = qs.annotate(_mem_email_l=Lower("member__email"),
                     _ce_email_l=Lower("journal__commissioning_editor__email"))\
           .exclude(_mem_email_l=F("_ce_email_l"))

    q = (request.GET.get("q") or "").strip()
    try:
        journal_id = int(request.GET.get("journal") or 0) or None
    except ValueError:
        journal_id = None
    date_from = (request.GET.get("date_from") or "").strip() or None
    date_to = (request.GET.get("date_to") or "").strip() or None

    if q:
        qs = qs.filter(
            Q(member__full_name__icontains=q)
            | Q(member__email__icontains=q)
            | Q(member__apid__iexact=q)
            | Q(journal__title__icontains=q)
            | Q(role__icontains=q))
    if journal_id:
        qs = qs.filter(journal_id=journal_id)
    if date_from:
        qs = qs.filter(started_on__gte=date_from)
    if date_to:
        qs = qs.filter(started_on__lte=date_to)
    qs = qs.order_by("-started_on", "member__full_name", "role")

    # CSV export dumps everything matching the filters; the table pages 50.
    want_csv = request.GET.get("export") == "csv"
    total_matches = qs.count()
    distinct_members = qs.values("member_id").distinct().count()

    if want_csv:
        from django.utils import timezone as _tz
        def _csv_rows():
            for a in qs.iterator(chunk_size=500):
                yield {
                    "APID": a.member.apid,
                    "Member": a.member.display_name or a.member.username,
                    "Email": a.member.email,
                    "Journal": (a.journal.title if a.journal else ""),
                    "Journal abbreviation": (a.journal.abbreviation if a.journal else ""),
                    "Role": a.role,
                    "Started": a.started_on.isoformat() if a.started_on else "",
                }
        return _csv_response(
            f"apid-approved-{_tz.now().strftime('%Y%m%d')}.csv", _csv_rows())

    import math as _math
    try:
        page_num = max(1, int(request.GET.get("page") or 1))
    except (TypeError, ValueError):
        page_num = 1
    page_size = 50
    total_pages = max(1, _math.ceil(total_matches / page_size))
    page_num = min(page_num, total_pages)
    rows = list(qs[(page_num - 1) * page_size: page_num * page_size])

    # Journal dropdown counts: active appointments per journal, honouring the
    # same date filter and role-mailbox strip as the main table, so the number
    # next to each option matches what picking it would show. One grouped
    # query + a Counter beats 274 point counts.
    from collections import Counter as _Counter
    from django.db.models import F as _F
    from django.db.models.functions import Lower as _Lower
    base = Journal.objects.all() if user_is_office else manager_journals(request.user)
    appt_qs = Appointment.objects.filter(ended_on__isnull=True)
    if not user_is_office:
        appt_qs = appt_qs.filter(journal__in=base)
    if date_from:
        appt_qs = appt_qs.filter(started_on__gte=date_from)
    if date_to:
        appt_qs = appt_qs.filter(started_on__lte=date_to)
    appt_qs = appt_qs.annotate(_ml=_Lower("member__email"),
                               _cl=_Lower("journal__commissioning_editor__email"))\
                     .exclude(_ml=_F("_cl"))
    counts_by_j = _Counter(appt_qs.values_list("journal_id", flat=True))
    journal_choices = [
        {**j, "appt_count": counts_by_j.get(j["id"], 0)}
        for j in base.order_by("title").values("id", "title", "abbreviation")
    ]

    return render(request, "editorial/approved.html", {
        "rows": rows,
        "shown": len(rows),
        "total_appointments": total_matches,
        "total_members": distinct_members,
        "page_num": page_num,
        "total_pages": total_pages,
        "query": q,
        "journal_choices": journal_choices,
        "selected_journal": journal_id,
        "date_from": date_from or "",
        "date_to": date_to or "",
        "user_is_office": user_is_office,
    })


@login_required
def impersonate(request, apid):
    """Sign in as another member (support/debugging). Office or superuser only.

    The original user id is parked on the session so a banner can offer "Return
    to admin" on every page. A warning is logged for the audit trail.
    """
    from apps.identity.models import Member
    from django.contrib.auth import login
    if not (request.user.is_superuser or is_office(request.user)):
        raise PermissionDenied("Only the editorial office or an admin may impersonate.")
    target = get_object_or_404(Member, apid=apid)
    if not target.is_active:
        messages.error(request, "That account is deactivated — impersonation refused.")
        return redirect("profile", apid=apid)
    if target.pk == request.user.pk:
        messages.info(request, "You are already signed in as yourself.")
        return redirect("profile", apid=apid)
    # Preserve original on the session across the login() rotation.
    original_pk = request.user.pk
    original_display = (getattr(request.user, "display_name", None)
                        or request.user.get_full_name() or request.user.username)
    import logging as _lg
    _lg.getLogger(__name__).warning(
        "impersonation: %s (pk=%s) -> %s (pk=%s, apid=%s)",
        request.user.username, original_pk, target.username, target.pk, target.apid)
    login(request, target, backend="apps.identity.backends.UsernameOrEmailBackend")
    request.session["impersonator_id"] = original_pk
    request.session["impersonator_display"] = original_display
    messages.info(request,
        f"Signed in as {target.display_name}. Use the red banner on top to return.")
    return redirect("dashboard")


@login_required
def stop_impersonating(request):
    """Exit impersonation — swap session back to the original admin."""
    from apps.identity.models import Member
    from django.contrib.auth import login, logout
    orig_pk = request.session.get("impersonator_id")
    if not orig_pk:
        return redirect("dashboard")
    orig = Member.objects.filter(pk=orig_pk, is_active=True).first()
    if orig is None:
        # Original account vanished or deactivated — fully sign out.
        logout(request)
        return redirect("login")
    import logging as _lg
    _lg.getLogger(__name__).warning(
        "impersonation-stop: back to %s (pk=%s)", orig.username, orig.pk)
    login(request, orig, backend="apps.identity.backends.UsernameOrEmailBackend")
    request.session.pop("impersonator_id", None)
    request.session.pop("impersonator_display", None)
    messages.success(request, "Back to your own account.")
    return redirect("dashboard")


@queue_or_manager
def application_cv(request, pk):
    """Serve the CV attached to one Application. Office or managing editor only."""
    from django.http import FileResponse
    from apps.editorial.models import Application
    try:
        app = Application.objects.get(pk=int(pk))
    except (ValueError, Application.DoesNotExist):
        raise Http404()
    if not app.cv:
        raise Http404("No CV on this application.")
    if not is_office(request.user):
        # A journal manager may see the CV for their own journals.
        app_journal_ids = set(app.journals.values_list("journal_id", flat=True))
        if not manager_journals(request.user).filter(pk__in=app_journal_ids).exists():
            raise PermissionDenied()
    return FileResponse(app.cv.open("rb"), as_attachment=False,
                        filename=app.cv.name.rsplit("/", 1)[-1])


@queue_or_manager
def application(request, pk):
    """One legacy application, everything known about the person, plus every
    journal choice on it and its per-journal status.

    The `pk` in the URL is the APID Application id (an int, kept so old
    bookmarks still work). It maps to a mng BoardApplication via the
    `legacy_apid_application_id` field.
    """
    from apps.editorial.mng_client import application_detail
    try:
        payload = application_detail(str(pk))
    except Exception as exc:                                     # noqa: BLE001
        messages.error(request, f"Could not reach the decisions system: {exc}")
        payload = None
    if payload is None:
        raise Http404("This application is not on the queue.")

    app_data = payload["application"]
    choices = payload["choices"]
    # wisp 2026-10-02 pm: which choices this user can decide on.
    user_is_office = is_office(request.user)
    managed_ids = set(manager_journals(request.user).values_list("id", flat=True))
    # wisp 2026-10-03: non-office users may only view the page when at least one
    # choice is on a journal they manage. Otherwise the detail page would leak
    # application contents (identity, CV link, affiliations) across journals.
    if not user_is_office:
        choice_journal_ids = {c.get("journal_id") for c in choices if c.get("journal_id")}
        if not (choice_journal_ids & managed_ids):
            raise PermissionDenied("This application is not for one of your journals.")
    for c in choices:
        label, tone = STATUS_LABELS.get(c["status"], (c["status_display"], "muted"))
        c["state_label"], c["state_tone"] = label, tone
        c["can_decide"] = bool(
            user_is_office
            or (c.get("journal_id") and c["journal_id"] in managed_ids)
        )

    # Local member for the profile sidebar (photo, appointments, publications).
    from apps.identity.models import Member
    member = Member.objects.filter(email__iexact=app_data["email"]).first()

    from apps.editorial.mng_client import CANONICAL_ROLES
    return render(request, "editorial/application.html", {
        "app": app_data,
        "choices": choices,
        "member": member,
        "profile": getattr(member, "profile", None) if member else None,
        "publications": member.publications.all()[:10] if member else [],
        "appointments": (member.appointments.select_related("journal").all()
                         if member else []),
        "user_is_office": user_is_office,
        "canonical_roles": CANONICAL_ROLES,
    })


@queue_or_manager
def decide(request, pk):
    # wisp 2026-10-02 pm: per-journal-choice decisions.
    """Decide one ApplicationJournal (one journal choice on an application).

    Each journal on the application is decided independently by its own
    manager or commissioning editor — accepting on one does not accept
    the others. The application's overall `decision` is derived afterwards.

    POST params:
      choice_id: ApplicationJournal.pk (required — which journal this decision is on)
      action: "accept" | "decline" | "withdraw"
      role: role string on the Appointment (accept only; defaults to applying_for)
      note: free-text decision note (optional)
    """
    from django.utils import timezone
    from apps.editorial.models import (
        Application, ApplicationJournal, Appointment, Decision)
    app = get_object_or_404(Application.objects.select_related("member"), pk=pk)

    if request.method != "POST":
        return redirect("application", pk=pk)

    choice_id = request.POST.get("choice_id")
    try:
        choice = ApplicationJournal.objects.select_related("journal").get(
            pk=int(choice_id or 0), application=app)
    except (ValueError, ApplicationJournal.DoesNotExist):
        messages.error(request, "Pick a journal choice before deciding.")
        return redirect("application", pk=pk)

    if not choice.journal_id:
        messages.error(request,
            "This choice is not linked to a journal in the registry yet — "
            "set the journal on the choice before deciding.")
        return redirect("application", pk=pk)

    # Permission: office OR this specific journal's manager/CE.
    managed_ids = set(manager_journals(request.user).values_list("id", flat=True))
    if not (is_office(request.user) or choice.journal_id in managed_ids):
        raise PermissionDenied(
            "Only this journal's manager or the editorial office can decide this choice.")

    action = (request.POST.get("action") or "").strip()
    note = (request.POST.get("note") or "").strip()

    if action == "accept":
        role = (request.POST.get("role")
                or choice.role_appointed
                or app.applying_for
                or "Reviewer").strip()
        choice.decision = Decision.ACCEPTED
        choice.decided_at = timezone.now()
        choice.decided_by = request.user
        choice.role_appointed = role
        if note:
            choice.note = note
        choice.save()
        appt, _ = Appointment.objects.get_or_create(
            member=app.member, journal=choice.journal, application=app,
            defaults={"role": role, "started_on": timezone.now().date()})
        messages.success(request,
            f"Accepted. {app.member.display_name} is now a {appt.role} on {choice.journal.title}.")
    elif action == "decline":
        choice.decision = Decision.DECLINED
        choice.decided_at = timezone.now()
        choice.decided_by = request.user
        if note:
            choice.note = note
        choice.save()
        messages.success(request,
            f"Declined on {choice.journal.title}.")
    elif action == "withdraw":
        choice.decision = Decision.WITHDRAWN
        choice.decided_at = timezone.now()
        choice.decided_by = request.user
        choice.save()
        messages.info(request, f"Withdrawn from {choice.journal.title}.")
    else:
        messages.error(request, "Unknown action.")
        return redirect("application", pk=pk)

    # Re-derive the application's overall decision from the choice decisions.
    _sync_application_decision(app)

    return redirect("application", pk=pk)


def _sync_application_decision(app):
    """Derived application state from the per-journal choices.

    - If any journal choice is ACCEPTED → application is ACCEPTED.
    - Else if there's any PENDING choice → application stays PENDING.
    - Else all choices are DECLINED/WITHDRAWN → application is DECLINED.
    Keeps `decided_at` / `decided_by` as the most recent choice decision.
    """
    from django.utils import timezone
    from apps.editorial.models import Decision
    choices = list(app.journals.all())
    if not choices:
        return
    accepted = [c for c in choices if c.decision == Decision.ACCEPTED]
    pending = [c for c in choices if c.decision == Decision.PENDING]
    if accepted:
        new_state = Decision.ACCEPTED
        last = max(accepted, key=lambda c: c.decided_at or timezone.now())
    elif pending:
        new_state = Decision.PENDING
        last = None
    else:
        new_state = Decision.DECLINED
        last = max(choices, key=lambda c: c.decided_at or timezone.now())
    if app.decision != new_state or (last and app.decided_at != last.decided_at):
        app.decision = new_state
        app.decided_at = last.decided_at if last else None
        app.decided_by = last.decided_by if last else None
        app.save(update_fields=["decision", "decided_at", "decided_by"])

@office_only
def boards(request):
    """Every journal and who serves on it — including the ones with nobody.

    The empty ones are the point. A list of journals that have editors can be had from
    the appointments alone; the question worth asking is which of the 278 have none.
    """
    journals = (Journal.objects.annotate(
        serving=Count("appointments", filter=Q(appointments__ended_on__isnull=True)))
        .order_by("serving", "title"))
    return render(request, "editorial/boards.html", {
        "journals": journals,
        "empty": sum(1 for j in journals if not j.serving),
        "total": journals.count(),
    })


@office_only
def board(request, pk: int):
    journal = get_object_or_404(Journal, pk=pk)
    return render(request, "editorial/board.html", {
        "journal": journal,
        "serving": journal.appointments.select_related("member", "member__profile")
                          .filter(ended_on__isnull=True).order_by("role",
                                                                 "member__full_name"),
        "past": journal.appointments.select_related("member")
                       .filter(ended_on__isnull=False).order_by("-ended_on"),
    })


def office_link(request):
    """Whether to show the office link in the header. A bool, asked at render time."""
    return is_office(request.user)


def _pdf_response(template, context, filename):
    """Render an HTML template to PDF and return it as a download."""
    html_string = render_to_string(template, context)
    pdf_bytes = weasyprint.HTML(string=html_string).write_pdf()
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _qr_data_url(payload: str) -> str:
    """Return a PNG data: URL of a QR code encoding `payload`.

    Used on certificate/letter PDFs so a scanner can jump to the verification
    page without ever typing the URL. Embedded as base64 so WeasyPrint does
    not need network access.
    """
    import base64 as _b64
    import io as _io
    import qrcode as _qr
    img = _qr.make(payload, box_size=10, border=2)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()


def _journal_signatory_native(journal):
    """Pick a signatory for a journal's letter/certificate from the local DB.

    Priority: journal.commissioning_editor (EditorialStaff, our internal office)
    → Chief Editor → Editor-in-Chief → Managing Editor (Appointment-based board roles).
    If nothing matches, the templates fall back to "Editorial Office".
    """
    if not journal:
        return {}
    ce = getattr(journal, "commissioning_editor", None)
    if ce and ce.active:
        return {
            "signatory_name": ce.name,
            "signatory_title": ce.designation or "Commissioning Editor",
            "imprint_name": "Consortium e-Learning Network Pvt Ltd",
        }
    from apps.editorial.models import Appointment
    priority = ["Chief Editor", "Editor-in-Chief", "Managing Editor"]
    appts = {a.role: a for a in
             Appointment.objects.filter(journal=journal, ended_on__isnull=True,
                                        role__in=priority)
             .select_related("member")}
    for role in priority:
        a = appts.get(role)
        if a:
            return {
                "signatory_name": a.member.display_name or a.member.username,
                "signatory_title": role,
                "imprint_name": "Consortium e-Learning Network Pvt Ltd",
            }
    return {}


def _logo_data_url():
    """Base64 the APID logo once per process — WeasyPrint has no network."""
    import base64 as _b64
    from django.contrib.staticfiles import finders
    path = finders.find("apid/apid-logo.png")
    if not path:
        return ""
    with open(path, "rb") as fp:
        return "data:image/png;base64," + _b64.b64encode(fp.read()).decode()


_LOGO_URL_CACHE = None

def _appointment_context(request, appt):
    """Shared context for certificate + letter + verify pages."""
    global _LOGO_URL_CACHE
    if _LOGO_URL_CACHE is None:
        _LOGO_URL_CACHE = _logo_data_url()
    verify_url = request.build_absolute_uri(
        f"/verify/appointment/{appt.pk}/")
    return {
        "appointment": appt,
        "member": appt.member,
        "profile": getattr(appt.member, "profile", None),
        "journal": appt.journal,
        "today": timezone.now().date(),
        "sig": _journal_signatory_native(appt.journal),
        "verify_url": verify_url,
        "qr_data_url": _qr_data_url(verify_url),
        "logo_data_url": _LOGO_URL_CACHE,
    }


@login_required
def resume_pdf(request, pk: int):
    """Download the applicant's CV, generated from their registry profile.

    Accessible by: the applicant themselves, office staff, and journal managers
    who can see this application.
    """
    item = get_object_or_404(
        Application.objects.select_related("member", "member__profile"), pk=pk)
    is_own = request.user.pk == item.member.pk
    if not (is_own or can_see_application(request.user, item)):
        raise PermissionDenied()
    member = item.member
    return _pdf_response("editorial/resume.html", {
        "member": member,
        "profile": getattr(member, "profile", None),
        "publications": member.publications.order_by("-year")[:20],
        "appointments": (member.appointments.select_related("journal")
                         .filter(ended_on__isnull=True)),
        "today": timezone.now().date(),
    }, filename=f"cv-{member.apid}.pdf")


@login_required
def empanelment_letter(request, pk: int):
    """Download the empanelment letter for an appointment.

    Accessible by: the appointee, office staff, and managers of the same journal.
    """
    appt = get_object_or_404(
        Appointment.objects.select_related("member", "member__profile", "journal"), pk=pk)
    is_own = request.user.pk == appt.member.pk
    is_mgr = (is_office(request.user)
              or manager_journals(request.user).filter(pk=appt.journal_id).exists())
    if not (is_own or is_mgr):
        raise PermissionDenied()
    return _pdf_response("editorial/letter.html",
        _appointment_context(request, appt),
        filename=f"empanelment-{appt.member.apid}-{appt.pk}.pdf")


@login_required
def editorial_certificate(request, pk: int):
    """Download the editorial certificate for an appointment.

    Accessible by: the appointee, office staff, and managers of the same journal.
    """
    appt = get_object_or_404(
        Appointment.objects.select_related("member", "member__profile", "journal"), pk=pk)
    is_own = request.user.pk == appt.member.pk
    is_mgr = (is_office(request.user)
              or manager_journals(request.user).filter(pk=appt.journal_id).exists())
    if not (is_own or is_mgr):
        raise PermissionDenied()
    return _pdf_response("editorial/certificate.html",
        _appointment_context(request, appt),
        filename=f"editorial-certificate-{appt.member.apid}-{appt.pk}.pdf")


@login_required
def verify_appointment(request, pk: int):
    """Public-ish verification page for a scanned certificate QR.

    Login is required (SignedInOnly middleware enforces it), so a scanner lands
    on sign-in first and then here. We never confirm an appointment to an
    anonymous caller — that is the point of gating the URL.
    """
    appt = get_object_or_404(
        Appointment.objects.select_related("member", "member__profile", "journal"), pk=pk)
    return render(request, "editorial/verify_appointment.html", {
        "appointment": appt,
        "member": appt.member,
        "profile": getattr(appt.member, "profile", None),
        "journal": appt.journal,
        "today": timezone.now().date(),
        "is_current": appt.ended_on is None,
    })


# ------------------------------------------------------- applying, from here

@login_required
def apply(request):
    """Apply for an editorial board.

    The registry answers most of the form from the member's existing Profile,
    but asks for anything missing (photo, affiliation link, institutional
    profile URL, years of experience, CV) because the office cannot evaluate
    a half-filled application. Anything typed here is mirrored back onto the
    Profile, so the correction updates the registry too.
    """
    known = apply_bridge.prefill(request.user)
    journals = apply_bridge.journals_to_offer()
    subjects = sorted({(j.subject or "").strip() for j in journals if j.subject})

    if request.method == "POST":
        picked = []
        for value in request.POST.getlist("journal"):
            title, _, role = value.partition("|")
            if title.strip():
                picked.append({"journal": title.strip(),
                               "role": (role or "associate").strip()})
        statement = (request.POST.get("statement") or "").strip()
        full_name = (request.POST.get("full_name") or "").strip()
        affiliation = (request.POST.get("affiliation") or "").strip()
        affiliation_url = (request.POST.get("affiliation_url") or "").strip()
        inst_profile_url = (request.POST.get("institutional_profile_url") or "").strip()
        years_exp = (request.POST.get("years_of_experience") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        country = (request.POST.get("country") or "").strip()

        errors: list[str] = []
        if not full_name:
            errors.append("Please enter your full name.")
        if not phone:
            errors.append("Phone number is required.")
        if not country:
            errors.append("Country is required.")
        if not affiliation:
            errors.append("Affiliation is required.")
        if not affiliation_url or "." not in affiliation_url:
            errors.append("Please share the website URL of your affiliation.")
        if not inst_profile_url or "." not in inst_profile_url:
            errors.append("Please share the link to your institutional profile page.")
        if not years_exp or not years_exp.replace(".", "", 1).isdigit():
            errors.append("Years of experience must be a number.")
        if not picked:
            errors.append("Choose at least one journal.")
        if len(statement) < 40:
            errors.append("Please say a little about why — a few sentences is "
                          "enough, and it is the part only you can write.")

        # Photo: required if profile has none; otherwise optional (replace).
        import os as _os
        photo = request.FILES.get("photo")
        if photo:
            MAX_PHOTO = 4 * 1024 * 1024
            PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")
            if photo.size > MAX_PHOTO:
                errors.append("Profile photo must be under 4 MB.")
            elif _os.path.splitext(photo.name)[1].lower() not in PHOTO_EXTS:
                errors.append("Profile photo must be JPG, PNG or WEBP.")
        elif not known.get("picture"):
            errors.append("Please upload a profile photo (JPG, PNG or WEBP).")

        # CV: always required for a board application.
        cv = request.FILES.get("cv")
        if not cv:
            errors.append("Please attach your CV (PDF, DOC or DOCX, up to 5 MB).")
        else:
            MAX_CV = 5 * 1024 * 1024
            CV_EXTS = (".pdf", ".doc", ".docx")
            if cv.size > MAX_CV:
                errors.append("CV must be under 5 MB.")
            elif _os.path.splitext(cv.name)[1].lower() not in CV_EXTS:
                errors.append("CV must be a PDF, DOC or DOCX file.")

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            payload = dict(known,
                           journals=picked, statement=statement,
                           full_name=full_name, affiliation=affiliation,
                           affiliation_url=affiliation_url,
                           institutional_profile_url=inst_profile_url,
                           years_of_experience=years_exp, phone=phone,
                           country=country,
                           cv_file=cv, photo_file=photo)
            # The applicant may correct what the registry filled in; their word wins.
            for field in ("designation", "department"):
                typed = (request.POST.get(field) or "").strip()
                if typed:
                    payload[field] = typed
            sent, detail, not_accepted = apply_bridge.send(payload)
            if sent:
                messages.success(
                    request,
                    "Your application has reached the editorial office. "
                    "You'll hear from them by email.")
                if not_accepted:
                    messages.warning(
                        request,
                        "One thing: the editorial platform does not have "
                        + ", ".join(not_accepted)
                        + ". The rest of your application went; write to the office if "
                          "you meant that journal.")
                return redirect("dashboard")
            messages.error(request, detail or "Something went wrong — please try again.")

    return render(request, "editorial/apply.html", {
        "known": known,
        "journals": journals,
        "roles": apply_bridge.ROLES,
        "subjects": subjects,
        "countries": apply_bridge.COUNTRIES,
    })


@queue_or_manager
def suggestions(request):
    """Ranked APID members for a journal + role, from the recommender.

    Editor picks a journal + role, and the page shows the top candidates with
    per-candidate "Invite" buttons. Wraps the mng recommend endpoint.
    """
    from apps.editorial.mng_client import recommend
    from apps.editorial.access import manager_journals, is_office
    from apps.editorial.models import Journal

    code = (request.GET.get("journal") or "").strip()
    role = (request.GET.get("role") or "associate").strip()

    if is_office(request.user):
        journals_qs = Journal.objects.order_by("title")
    else:
        journals_qs = manager_journals(request.user).order_by("title")

    candidates = []
    picked = None
    if code:
        picked = journals_qs.filter(abbreviation__iexact=code).first()
        if picked is not None:
            try:
                candidates = recommend(journal_code=picked.abbreviation,
                                       role=role, limit=25)
            except Exception as exc:                             # noqa: BLE001
                messages.error(request, f"Recommender error: {exc}")
            # wisp 2026-10-04: drop candidates already serving on this journal
            # with any active role -- showing an "Invite as Reviewer" button for
            # someone who is already a Reviewer is the same bug as on the
            # /profiles/ page.
            if candidates:
                from apps.editorial.models import Appointment
                already = set(
                    Appointment.objects.filter(
                        journal_id=picked.id, ended_on__isnull=True
                    ).values_list("member__apid", flat=True)
                )
                candidates = [c for c in candidates if c.get("apid") not in already]

    return render(request, "editorial/suggestions.html", {
        "journals": journals_qs,
        "picked": picked,
        "role": role,
        "candidates": candidates,
    })


@queue_or_manager
def flow(request):
    """One-page architectural map of the board application workflow."""
    return render(request, "editorial/flow.html", {})

def dashboard(request):
    # wisp 2026-10-02: office dashboard
    """A landing dashboard for office + journal-manager users.

    Shows a quick count of pending applications, boards they manage, recent
    decisions, and quick-action tiles. Linked to by the "Office" link in the top nav.
    """
    from django.contrib.auth.decorators import login_required as _login
    from apps.editorial.access import is_office, manager_journals
    from apps.editorial.models import Application, Appointment, Journal
    from apps.identity.models import Member

    if not request.user.is_authenticated:
        return redirect("/accounts/login/?next=/office/")
    office = is_office(request.user)
    managed = manager_journals(request.user) if not office else Journal.objects.none()
    if not (office or managed.exists()):
        raise PermissionDenied

    managed_ids = (None if office
                   else list(managed.values_list("id", flat=True)) or [-1])

    # Local applications — all for office, scoped to managed journals otherwise.
    local_apps = Application.objects.all()
    if managed_ids is not None:
        local_apps = local_apps.filter(journals__journal_id__in=managed_ids).distinct()
    local_pending = local_apps.filter(decision="").count()
    local_decided = local_apps.exclude(decision="").count()

    # Live counts from mng
    try:
        from apps.editorial.mng_client import queue_summary
        mng_counts = queue_summary(journal_ids=managed_ids) or {}
    except Exception:
        mng_counts = {}
    mng_pending = mng_counts.get("new", 0) + mng_counts.get("under_review", 0)

    # Boards
    if office:
        board_count = Journal.objects.count()
        active_appointments = Appointment.objects.filter(ended_on__isnull=True).count()
        total_members = Member.objects.count()
        with_appointment = Appointment.objects.filter(ended_on__isnull=True).values("member").distinct().count()
    else:
        board_count = managed.count()
        active_appointments = Appointment.objects.filter(ended_on__isnull=True, journal__in=managed).count()
        total_members = None
        with_appointment = None

    # Recent decisions table — journal + date filters apply to the table and
    # the shown count below it, so a CE scanning for "what happened on JOEE
    # since Monday" sees the matching rows and nothing else.
    try:
        rec_journal = int(request.GET.get("journal") or 0) or None
    except ValueError:
        rec_journal = None
    rec_from = (request.GET.get("date_from") or "").strip() or None
    rec_to = (request.GET.get("date_to") or "").strip() or None

    decisions_qs = local_apps.exclude(decision__in=["", "pending"])
    if rec_journal:
        decisions_qs = decisions_qs.filter(journals__journal_id=rec_journal).distinct()
    if rec_from:
        decisions_qs = decisions_qs.filter(decided_at__date__gte=rec_from)
    if rec_to:
        decisions_qs = decisions_qs.filter(decided_at__date__lte=rec_to)
    decisions_total = decisions_qs.count()

    # CSV export: give the Boss every matching decision for reporting, not
    # just the dashboard's top-25 snapshot.
    if request.GET.get("export") == "csv":
        def _csv_rows():
            for a in decisions_qs.select_related("member").order_by("-decided_at").iterator(chunk_size=500):
                yield {
                    "APID": a.member.apid if a.member else "",
                    "Applicant": (a.member.display_name if a.member else "") or "",
                    "Email": (a.member.email if a.member else "") or "",
                    "Application": a.pk,
                    "Role": a.applying_for or "",
                    "Subject": a.subject or "",
                    "Decision": a.decision,
                    "Decided": a.decided_at.isoformat() if a.decided_at else "",
                }
        return _csv_response(
            f"apid-decisions-{timezone.now().strftime('%Y%m%d')}.csv", _csv_rows())

    from apps.editorial.models import Appointment
    recent = list(decisions_qs.select_related("member")
                  .order_by("-decided_at")[:25])
    if recent:
        app_ids = [a.pk for a in recent]
        appts_by_app = {}
        for appt in (Appointment.objects
                     .filter(application_id__in=app_ids)
                     .select_related("journal")):
            appts_by_app.setdefault(appt.application_id, []).append(appt)
        for a in recent:
            a.accepted_appts = appts_by_app.get(a.pk, [])

    # Journal dropdown counts: decisions-per-journal matching the current date
    # range. Scope matches the main table so the number next to each option
    # reads the same.
    from collections import Counter as _Counter
    from apps.editorial.models import ApplicationJournal
    base = Journal.objects.all() if office else managed
    aj_qs = ApplicationJournal.objects.exclude(
        application__decision__in=["", "pending"])
    if not office:
        aj_qs = aj_qs.filter(journal__in=base)
    if rec_from:
        aj_qs = aj_qs.filter(application__decided_at__date__gte=rec_from)
    if rec_to:
        aj_qs = aj_qs.filter(application__decided_at__date__lte=rec_to)
    counts_by_j = _Counter()
    for jid, aid in aj_qs.values_list("journal_id", "application_id").distinct():
        if jid is not None:
            counts_by_j[jid] += 1
    journal_choices = [
        {**j, "app_count": counts_by_j.get(j["id"], 0)}
        for j in base.order_by("title").values("id", "title", "abbreviation")
    ]

    return render(request, "editorial/dashboard.html", {
        "office": office,
        "managed": managed,
        "mng_counts": mng_counts,
        "mng_pending": mng_pending,
        "local_pending": local_pending,
        "local_decided": local_decided,
        "board_count": board_count,
        "active_appointments": active_appointments,
        "total_members": total_members,
        "with_appointment": with_appointment,
        "recent": recent,
        "decisions_total": decisions_total,
        "journal_choices": journal_choices,
        "selected_journal": rec_journal,
        "date_from": rec_from or "",
        "date_to": rec_to or "",
    })

@queue_or_manager
def journal_manage(request, pk: int):
    """Journal profile for internal team — office or the journal's own managers.

    Office may reassign the commissioning editor and append/remove Appointments.
    A journal's own CE / EIC sees the same page in read-mostly mode: they can
    see team, pending applications and recent decisions on their own journal
    but can't reassign the commissioning editor.
    """
    from django.utils import timezone
    from django.db.models import Count, Q
    from apps.editorial.models import Appointment, Application, Decision, Journal
    from apps.identity.models import Member
    journal = get_object_or_404(Journal.objects.select_related("commissioning_editor"), pk=pk)

    user_is_office = is_office(request.user)
    if not user_is_office:
        if not manager_journals(request.user).filter(pk=pk).exists():
            raise PermissionDenied("This journal is not one you manage.")

    if request.method == "POST":
        if not user_is_office:
            raise PermissionDenied("Only the editorial office can edit the commissioning editor here.")
        action = (request.POST.get("action") or "").strip()
        if action == "assign":
            email = (request.POST.get("email") or "").strip()
            if not email:
                messages.error(request, "Enter the member's email.")
            else:
                m = Member.objects.filter(email__iexact=email).first()
                if not m:
                    messages.error(request, f"No APID member with email {email}.")
                else:
                    exists = Appointment.objects.filter(
                        member=m, journal=journal,
                        role="Commissioning Editor",
                        ended_on__isnull=True).exists()
                    if exists:
                        messages.info(request, f"{m.display_name} is already a CE on this journal.")
                    else:
                        Appointment.objects.create(
                            member=m, journal=journal,
                            role="Commissioning Editor",
                            started_on=timezone.now().date())
                        messages.success(request, f"{m.display_name} appointed as Commissioning Editor.")
        elif action == "remove":
            appt_id = request.POST.get("appointment_id")
            try:
                a = Appointment.objects.get(pk=int(appt_id), journal=journal,
                                            role="Commissioning Editor",
                                            ended_on__isnull=True)
                a.ended_on = timezone.now().date()
                a.save()
                messages.success(request, f"Removed {a.member.display_name}.")
            except (ValueError, Appointment.DoesNotExist):
                messages.error(request, "Appointment not found.")
        return redirect("journal-manage", pk=pk)

    ces = (Appointment.objects.filter(
        journal=journal, role="Commissioning Editor", ended_on__isnull=True)
        .select_related("member", "member__profile"))
    managers = (Appointment.objects.filter(
        journal=journal,
        role__in=["Editor-in-Chief", "Associate Editor-in-chief", "Associate Editor-in-Chief"],
        ended_on__isnull=True)
        .select_related("member", "member__profile"))
    # The journal's commissioning editor sits on a role mailbox like
    # chemical@stmjournals.com. The WP import attributed every legacy editor who
    # ever used that mailbox — EIC, Section Editor, Reviewer, Guest Editor — to
    # the single Member record now holding that email. Those are not the CE's
    # personal roles; strip them from the manager and board sections so the CE
    # shows up only where they belong: as the signatory.
    ce_email = (journal.commissioning_editor.email or "").strip().lower() \
        if journal.commissioning_editor else ""
    board_raw = (Appointment.objects.filter(journal=journal, ended_on__isnull=True)
                 .exclude(role__in=["Commissioning Editor", "Editor-in-Chief",
                                    "Associate Editor-in-chief", "Associate Editor-in-Chief"])
                 .select_related("member", "member__profile")
                 .order_by("member__full_name"))
    if ce_email:
        board_raw = board_raw.exclude(member__email__iexact=ce_email)
        managers = managers.exclude(member__email__iexact=ce_email)
    # Dedupe by member — the WP import collapsed multiple real people onto a
    # single Member row when they shared a role-based mailbox, so a journal can
    # legitimately show the same Member attached to 16 identical rows. Collapse
    # them to one row per member with a role count beside each role.
    import collections as _col
    board_by_member: dict[int, dict] = {}
    for a in board_raw:
        row = board_by_member.setdefault(a.member_id, {
            "member": a.member,
            "profile": getattr(a.member, "profile", None),
            "roles": _col.Counter(),
        })
        row["roles"][a.role] += 1
    board = list(board_by_member.values())[:60]
    for row in board:
        row["role_summary"] = ", ".join(
            f"{r} × {n}" if n > 1 else r
            for r, n in sorted(row["roles"].items(), key=lambda x: (-x[1], x[0]))
        )
    board_total_people = len(board_by_member)
    board_total_rows = sum(sum(r["roles"].values()) for r in board)

    # Application counts for this journal.
    app_counts = Application.objects.filter(
        journals__journal=journal
    ).aggregate(
        pending=Count("pk", filter=Q(decision=Decision.PENDING), distinct=True),
        accepted=Count("pk", filter=Q(decision=Decision.ACCEPTED), distinct=True),
        declined=Count("pk", filter=Q(decision=Decision.DECLINED), distinct=True),
    )

    # Last 10 decided for this journal.
    recent = (Application.objects
              .filter(journals__journal=journal)
              .exclude(decision__in=["", Decision.PENDING])
              .select_related("member")
              .order_by("-decided_at")
              .distinct()[:10])

    # Managers panel: also dedupe by member (same WP import issue).
    mgr_by_member: dict[int, dict] = {}
    for a in managers:
        row = mgr_by_member.setdefault(a.member_id, {
            "member": a.member,
            "roles": _col.Counter(),
            "started_on": a.started_on,
        })
        row["roles"][a.role] += 1
        if a.started_on and (not row["started_on"] or a.started_on < row["started_on"]):
            row["started_on"] = a.started_on
    managers_dedup = list(mgr_by_member.values())
    for row in managers_dedup:
        row["role_summary"] = ", ".join(
            f"{r} × {n}" if n > 1 else r
            for r, n in sorted(row["roles"].items(), key=lambda x: (-x[1], x[0]))
        )

    return render(request, "editorial/journal_manage.html", {
        "journal": journal,
        "commissioning_editors": ces,
        "managers": managers_dedup,
        "board": board,
        "board_total_people": board_total_people,
        "board_total_rows": board_total_rows,
        "app_counts": app_counts,
        "recent": recent,
        "user_is_office": user_is_office,
    })


@queue_or_manager
def journals_index(request):
    """Journals list.

    Office sees every journal with filter + bulk-assign. A commissioning editor
    (non-office) sees only the journals they commission — the admin tooling
    (bulk assign, editor filter, needs_ce) is hidden from them.
    """
    from django.db.models import Count, Q
    from apps.editorial.models import EditorialStaff, Journal

    user_is_office = is_office(request.user)

    if request.method == "POST" and request.POST.get("action") == "assign_editor":
        if not user_is_office:
            raise PermissionDenied("Only the editorial office can reassign commissioning editors.")
        ids = [int(x) for x in request.POST.getlist("journal_ids") if x.isdigit()]
        editor_id = request.POST.get("editor_id") or ""
        editor = None
        if editor_id == "__clear__":
            editor = None
        elif editor_id.isdigit():
            editor = EditorialStaff.objects.filter(pk=int(editor_id)).first()
            if not editor:
                messages.error(request, "That editor no longer exists.")
                return redirect("journals-index")
        else:
            messages.error(request, "Pick an editor (or Clear).")
            return redirect("journals-index")
        n = Journal.objects.filter(pk__in=ids).update(commissioning_editor=editor)
        label = editor.name if editor else "— cleared —"
        messages.success(request, f"Set commissioning editor on {n} journal(s): {label}")
        return redirect("journals-index")

    q = (request.GET.get("q") or "").strip()
    needs_ce = request.GET.get("needs_ce") == "1"
    editor_filter = request.GET.get("editor") or ""
    journals = Journal.objects.select_related("commissioning_editor").annotate(
        mgr_count=Count("appointments",
            filter=Q(appointments__role__in=["Editor-in-Chief", "Associate Editor-in-chief", "Associate Editor-in-Chief"],
                     appointments__ended_on__isnull=True)),
    )
    if not user_is_office:
        managed_ids = list(manager_journals(request.user).values_list("id", flat=True)) or [-1]
        journals = journals.filter(pk__in=managed_ids)
        needs_ce = False
        editor_filter = ""
    if q:
        journals = journals.filter(title__icontains=q)
    if needs_ce:
        journals = journals.filter(commissioning_editor__isnull=True)
    if editor_filter == "none":
        journals = journals.filter(commissioning_editor__isnull=True)
    elif editor_filter.isdigit():
        journals = journals.filter(commissioning_editor_id=int(editor_filter))
    journals = journals.order_by("commissioning_editor__name", "title")
    editors = EditorialStaff.objects.filter(active=True).order_by("name") if user_is_office else EditorialStaff.objects.none()
    return render(request, "editorial/journals_index.html", {
        "journals": journals,
        "q": q,
        "needs_ce": needs_ce,
        "editor_filter": editor_filter,
        "editors": editors,
        "user_is_office": user_is_office,
        "total": journals.count(),
        "without_ce": journals.filter(commissioning_editor__isnull=True).count(),
    })


@office_only
def emails_log(request):
    """Office view of outgoing mail — every message the system sent, newest first."""
    from apps.identity.models import EmailLog
    q = (request.GET.get("q") or "").strip()
    kind = (request.GET.get("kind") or "").strip()
    status = (request.GET.get("status") or "").strip()
    logs = EmailLog.objects.select_related("related_member")
    if q:
        logs = logs.filter(Q(to_address__icontains=q) | Q(subject__icontains=q))
    if kind:
        logs = logs.filter(kind=kind)
    if status:
        logs = logs.filter(status=status)
    if request.GET.get("export") == "csv":
        def _rows():
            for e in logs.iterator(chunk_size=500):
                yield {
                    "When": e.sent_at.isoformat() if e.sent_at else "",
                    "To": e.to_address,
                    "Member APID": e.related_member.apid if e.related_member_id else "",
                    "Kind": e.kind,
                    "Subject": e.subject,
                    "Status": e.status,
                }
        return _csv_response(f"apid-emails-{timezone.now().strftime('%Y%m%d')}.csv", _rows())
    total = logs.count()
    import math as _math
    try:
        page_num = max(1, int(request.GET.get("page") or 1))
    except (TypeError, ValueError):
        page_num = 1
    page_size = 50
    total_pages = max(1, _math.ceil(total / page_size))
    page_num = min(page_num, total_pages)
    rows = list(logs[(page_num - 1) * page_size: page_num * page_size])
    kinds = (EmailLog.objects.values_list("kind", flat=True)
             .exclude(kind="").distinct().order_by("kind"))
    return render(request, "editorial/emails_log.html", {
        "logs": rows, "total": total, "shown": len(rows),
        "page_num": page_num, "total_pages": total_pages,
        "q": q, "kind": kind, "status": status,
        "kinds": list(kinds),
        "statuses": EmailLog.Status.choices,
    })


@office_only
def email_log_detail(request, pk: int):
    """One email's full body — the office pulls it when a user asks what they got."""
    from apps.identity.models import EmailLog
    log = get_object_or_404(EmailLog.objects.select_related("related_member"), pk=pk)
    return render(request, "editorial/email_log_detail.html", {"log": log})


@office_only
def email_templates_index(request):
    """List all editable email templates by category."""
    from apps.identity.models import EmailTemplate
    from apps.identity import email_templates as _et
    _et.ensure_seeded()
    templates = EmailTemplate.objects.order_by("name")
    return render(request, "editorial/email_templates.html", {
        "templates": templates,
    })


@office_only
def email_template_edit(request, pk: int):
    """Edit the subject + body for one email category."""
    from apps.identity.models import EmailTemplate
    from apps.identity import email_templates as _et
    tmpl = get_object_or_404(EmailTemplate, pk=pk)
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "reset":
            d = _et.default_for(tmpl.category)
            tmpl.subject = d["subject"]
            tmpl.body = d["body"]
            tmpl.variables_help = d["variables_help"]
            tmpl.enabled = True
            tmpl.save()
            messages.success(request, f"Reset {tmpl.name} to the shipping default.")
            return redirect("email-template-edit", pk=pk)
        tmpl.name = (request.POST.get("name") or tmpl.name).strip()[:120]
        tmpl.subject = (request.POST.get("subject") or "").strip()[:500]
        tmpl.body = request.POST.get("body") or ""
        tmpl.enabled = "enabled" in request.POST
        tmpl.save()
        messages.success(request, f"Saved {tmpl.name}.")
        return redirect("email-template-edit", pk=pk)
    return render(request, "editorial/email_template_edit.html", {
        "tmpl": tmpl,
        "default_subject": _et.default_for(tmpl.category)["subject"],
        "default_body": _et.default_for(tmpl.category)["body"],
    })


@office_only
def editors_index(request):
    """Editorial-staff roster. Click a row to edit; one place for all their journals."""
    from django.db.models import Count
    from apps.editorial.models import EditorialStaff
    q = (request.GET.get("q") or "").strip()
    editors = EditorialStaff.objects.annotate(journal_count=Count("journals"))
    if q:
        editors = editors.filter(Q(name__icontains=q) | Q(email__icontains=q))
    editors = editors.order_by("-active", "-journal_count", "name")
    return render(request, "editorial/editors_index.html", {
        "editors": editors,
        "q": q,
        "total": editors.count(),
    })


@office_only
def editor_edit(request, pk: int = None):
    """Create or edit one editorial-staff member. Changes apply to all their journals."""
    from apps.editorial.models import EditorialStaff
    editor = None
    if pk:
        editor = get_object_or_404(EditorialStaff, pk=pk)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        email = (request.POST.get("email") or "").strip().lower()
        if not name or not email:
            messages.error(request, "Name and email are required.")
            return redirect(request.path)
        clash = EditorialStaff.objects.filter(email=email)
        if editor:
            clash = clash.exclude(pk=editor.pk)
        if clash.exists():
            messages.error(request, f"Another editor already has email {email}.")
            return redirect(request.path)
        fields = {
            "name": name,
            "email": email,
            "phone": (request.POST.get("phone") or "").strip(),
            "designation": (request.POST.get("designation")
                            or "Commissioning Editor").strip(),
            "notes": (request.POST.get("notes") or "").strip(),
            "active": "active" in request.POST,
        }
        if editor:
            for k, v in fields.items():
                setattr(editor, k, v)
            editor.save()
            messages.success(request, f"Updated {editor.name}.")
        else:
            editor = EditorialStaff.objects.create(**fields)
            messages.success(request, f"Created {editor.name}.")
        return redirect("editor-edit", pk=editor.pk)

    journals = editor.journals.order_by("title") if editor else []
    return render(request, "editorial/editor_edit.html", {
        "editor": editor,
        "journals": journals,
    })


@office_only
def editor_delete(request, pk: int):
    """Deactivate an editor (soft-delete). Keeps history, hides from pickers."""
    from apps.editorial.models import EditorialStaff
    editor = get_object_or_404(EditorialStaff, pk=pk)
    if request.method == "POST":
        editor.active = False
        editor.save(update_fields=["active"])
        messages.success(request, f"Deactivated {editor.name}.")
    return redirect("editors-index")

