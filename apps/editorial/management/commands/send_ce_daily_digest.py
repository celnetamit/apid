"""Email every active commissioning editor a one-paragraph digest of their day.

For each row in `EditorialStaff(active=True)`:

* Pending applications on journals they commission
* Decisions made on those journals in the last 24 hours
* Journals they commission that have an empty editorial board

If every number is zero the digest is skipped — nobody wants a daily email
whose only line reads "nothing happened". Set `--force` to send anyway.

Routed through the usual `EmailTemplate(category="ce_daily_digest")` so the
office can edit the subject + body without a code change. The default copy
is the shipping template in `apps.identity.email_templates.DEFAULTS`.

Idempotent in the sense that running it twice sends the same digest twice;
run it from cron once per day, not per hour.
"""
from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.editorial.models import Appointment, Journal, Application, Decision, EditorialStaff
from apps.identity.email_templates import render as render_template
from apps.identity.models import Member


class Command(BaseCommand):
    help = "Email each commissioning editor a daily digest of their journals."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Print what would be sent without touching SES.")
        parser.add_argument("--only", default="",
                            help="Comma-separated staff emails; limit the run to these.")
        parser.add_argument("--force", action="store_true",
                            help="Send even when every count is zero.")
        parser.add_argument("--site", default="https://apid.journalslibrary.com")

    def handle(self, *, dry_run: bool, only: str, force: bool, site: str, **_):
        only_set = {e.strip().lower() for e in only.split(",") if e.strip()}
        qs = EditorialStaff.objects.filter(active=True)
        if only_set:
            qs = qs.filter(email__in=only_set)
        qs = qs.order_by("name")

        today = timezone.now().date()
        since = timezone.now() - dt.timedelta(hours=24)
        self.stdout.write(f"Daily digest for {today} ({qs.count()} editors).")

        sent, skipped, failed = 0, 0, 0
        for staff in qs:
            journals = list(Journal.objects.filter(commissioning_editor=staff))
            jids = [j.pk for j in journals]
            if not jids:
                skipped += 1
                continue

            pending = (Application.objects
                       .filter(journals__journal_id__in=jids,
                               decision=Decision.PENDING)
                       .distinct().count())
            decided = (Application.objects
                       .filter(journals__journal_id__in=jids,
                               decided_at__gte=since)
                       .exclude(decision__in=["", Decision.PENDING])
                       .distinct().count())
            # Empty board = no active appointments on that journal (the
            # commissioning editor themselves is signatory, not a board member).
            empty_boards = 0
            empty_titles = []
            for j in journals:
                has_board = (Appointment.objects
                             .filter(journal=j, ended_on__isnull=True)
                             .exists())
                if not has_board:
                    empty_boards += 1
                    empty_titles.append(j.abbreviation or j.title)

            if not (pending or decided or empty_boards) and not force:
                skipped += 1
                self.stdout.write(f"  skip  {staff.email}  (nothing to report)")
                continue

            # Detail: a short per-journal line only when a journal has one of
            # these three things worth looking at. Keep it to one screen.
            detail = []
            for j in journals:
                j_pending = (Application.objects
                             .filter(journals__journal_id=j.pk,
                                     decision=Decision.PENDING)
                             .distinct().count())
                j_decided = (Application.objects
                             .filter(journals__journal_id=j.pk,
                                     decided_at__gte=since)
                             .exclude(decision__in=["", Decision.PENDING])
                             .distinct().count())
                j_empty = (not Appointment.objects
                           .filter(journal=j, ended_on__isnull=True).exists())
                if j_pending or j_decided or j_empty:
                    bits = []
                    if j_pending: bits.append(f"{j_pending} pending")
                    if j_decided: bits.append(f"{j_decided} decided")
                    if j_empty:   bits.append("empty board")
                    detail.append(f"  • {j.abbreviation or j.title}: {', '.join(bits)}")

            context = {
                "name": staff.name,
                "email": staff.email,
                "date": today.strftime("%d %b %Y"),
                "journal_count": len(journals),
                "journal_count_s": "" if len(journals) == 1 else "s",
                "pending": pending,
                "decided": decided,
                "empty_boards": empty_boards,
                "detail_lines": "\n".join(detail) + ("\n" if detail else ""),
                "site": site.rstrip("/"),
            }
            subject, body = render_template("ce_daily_digest", context)

            if dry_run:
                self.stdout.write(f"  DRY   {staff.email}  — {subject}")
                sent += 1
                continue

            msg = EmailMessage(
                subject=subject, body=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL",
                                   "apid@celnet.in"),
                to=[staff.email],
                headers={"X-APID-Category": "ce_daily_digest"},
            )
            try:
                msg.send(fail_silently=False)
                sent += 1
                self.stdout.write(f"  sent  {staff.email}  "
                                  f"(p={pending} d={decided} empty={empty_boards})")
            except Exception as exc:                                  # noqa: BLE001
                failed += 1
                self.stdout.write(self.style.ERROR(
                    f"  FAIL  {staff.email}  {exc}"))

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"sent={sent}  skipped={skipped}  failed={failed}"))
