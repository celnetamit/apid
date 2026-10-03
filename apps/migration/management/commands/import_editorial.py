"""The editorial pipeline: journals, applications, decisions, appointments.

Four forms that only make sense together, and on the live site nothing joins them
except a number typed into a hidden field:

* **25 — Journals Master** (278 entries): the portfolio.
* **159 — Editorial form** (2,445): a member asking to join a board, naming up to
  eleven journals in eleven separate columns.
* **200 — Editorial Acceptance** (1,668): the office's answer, in a *different* form,
  pointing back by "Application ID".
* **219 — Assign Journal Manager** (3,903): who actually serves where.

Importing them separately would keep the problem. They import as one pipeline: the
application is a row, its journals are rows beside it, the decision is a field on it,
and the appointment is a row that can name the application it came from.

Everything is counted, including the joins that fail. An acceptance whose application
id matches nothing is the single most important number in this command — it is the
measure of how much of the editorial history is actually reconstructable.
"""

from __future__ import annotations

import collections
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.editorial.models import (Application, ApplicationJournal, Appointment,
                                    Decision, Journal)
from apps.identity.models import Member
from apps.migration.reader import WordPress, first

JOURNALS = 25
APPLICATION = 159
ACCEPTANCE = 200
ASSIGNMENT = 219

JOURNAL_FIELDS = {
    "title": "qrnpz", "abbreviation": "efuv9", "publisher": "x4icr",
    "subject": "fwwjs", "image_url": "vpbwd", "status": "98kfd",
}

#: The eleven journal columns on the application form, in the order they are asked.
APPLICATION_JOURNALS = ["oupuy", "wu7ox", "se16t", "tdwsn", "1njfk", "lr8oo",
                        "7498z", "vrjc2", "f911h", "s4mgo", "usnh"]

APP_ID = "9vsz3"          # "Application ID", a hidden field on form 159
APP_APPLYING_FOR = "d4266"
APP_SUBJECT = "52ecc"
APP_DESIGNATION = "71otw"
APP_DEPARTMENT = "22n8x"
APP_AFFILIATION = "eix6y"

ACC_APP_ID = "yadqw"      # the same number, typed into the acceptance form
ACC_APP_LOOKUP = "sadm8"
ACC_APPROVAL = "v9667"
ACC_MAIN_ROLE = "3qut8"
ACC_MAIN_JOURNAL = "7jz7o"
ACC_ROLE_2, ACC_JOURNAL_2 = "5d8u0", "wtwy7"
ACC_ROLE_3, ACC_JOURNAL_3 = "fuk54", "r5n5n"

ASG_APP_ID = "10iti"
ASG_JOURNAL = "o6vkk"
ASG_ROLE = "4s0rg"
ASG_ELIGIBILITY = "san2n"

USER_KEYS = ("lqz5u", "zah2u3", "2e122a")

#: What the Approval radio actually says. Read off the data rather than guessed — the
#: form offers more than yes and no, and a value nobody has mapped is reported.
APPROVALS = {
    "approve": Decision.ACCEPTED, "approved": Decision.ACCEPTED,
    "accept": Decision.ACCEPTED, "accepted": Decision.ACCEPTED, "yes": Decision.ACCEPTED,
    "reject": Decision.DECLINED, "rejected": Decision.DECLINED,
    "decline": Decision.DECLINED, "declined": Decision.DECLINED, "no": Decision.DECLINED,
    "pending": Decision.PENDING, "hold": Decision.PENDING,
    "withdraw": Decision.WITHDRAWN, "withdrawn": Decision.WITHDRAWN,
}


def member_of(entry, members):
    if entry.user_id in members:
        return members[entry.user_id]
    for key in USER_KEYS:
        raw = first(entry.get(key)).strip()
        if raw.isdigit() and int(raw) in members:
            return members[int(raw)]
    return None


def digits(value: str) -> str:
    """An application id as it was typed. Some carry spaces or a stray prefix."""
    found = re.findall(r"\d+", value or "")
    return found[0] if found else ""


class Command(BaseCommand):
    help = "Import journals, applications, decisions and appointments."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        wp = WordPress()
        try:
            members = {m.wp_user_id: m for m in Member.objects.all()}
            counts: collections.Counter = collections.Counter()
            journals = self._journals(wp, counts, options["dry_run"])
            apps = self._applications(wp, members, journals, counts,
                                      options["dry_run"])
            self._decisions(wp, apps, counts, options["dry_run"])
            self._appointments(wp, members, journals, apps, counts,
                               options["dry_run"])
            self._report(counts)
        finally:
            wp.close()

    # --- the portfolio -------------------------------------------------------

    def _journals(self, wp, counts, dry) -> dict[str, Journal]:
        by_title: dict[str, Journal] = {}
        for entry in wp.entries(JOURNALS):
            counts["journal entries"] += 1
            values = {name: first(entry.get(key)).strip()
                      for name, key in JOURNAL_FIELDS.items()}
            if not values["title"]:
                counts["journal with no title"] += 1
                continue
            if not dry:
                journal, _ = Journal.objects.update_or_create(
                    wp_entry_id=entry.id,
                    defaults={k: v[:300] for k, v in values.items()})
                by_title[values["title"].lower()] = journal
                if values["abbreviation"]:
                    by_title[values["abbreviation"].lower()] = journal
            counts["journals imported"] += 1
        return by_title

    # --- applications --------------------------------------------------------

    def _applications(self, wp, members, journals, counts, dry) -> dict[str, Application]:
        by_app_id: dict[str, Application] = {}
        for entry in wp.entries(APPLICATION):
            counts["application entries"] += 1
            member = member_of(entry, members)
            if member is None:
                # Kept as a count, not a guess. The form carries the applicant's email
                # but matching on it would attach somebody's editorial history to a
                # namesake, which is worse than a gap.
                counts["application with no member"] += 1
                continue
            if dry:
                continue
            with transaction.atomic():
                application, _ = Application.objects.update_or_create(
                    wp_entry_id=entry.id,
                    defaults={
                        "member": member,
                        "applying_for": first(entry.get(APP_APPLYING_FOR))[:120],
                        "subject": first(entry.get(APP_SUBJECT))[:200],
                        "stated_designation": first(entry.get(APP_DESIGNATION))[:200],
                        "stated_department": first(entry.get(APP_DEPARTMENT))[:200],
                        "stated_affiliation": first(entry.get(APP_AFFILIATION))[:255],
                        "applied_at": entry.created_at,
                    })
                counts["applications imported"] += 1

                application.journals.all().delete()
                for position, key in enumerate(APPLICATION_JOURNALS, start=1):
                    title = first(entry.get(key)).strip()
                    if not title:
                        continue
                    ApplicationJournal.objects.create(
                        application=application, preference=position,
                        stated_title=title[:300],
                        journal=journals.get(title.lower()))
                    counts["journal choices"] += 1
                    if title.lower() not in journals:
                        counts["journal choice not in the master list"] += 1

            app_id = digits(first(entry.get(APP_ID)))
            if app_id:
                by_app_id[app_id] = application
            else:
                counts["application with no application id"] += 1
        return by_app_id

    # --- decisions -----------------------------------------------------------

    def _decisions(self, wp, apps, counts, dry) -> None:
        unmapped: collections.Counter = collections.Counter()
        for entry in wp.entries(ACCEPTANCE):
            counts["acceptance entries"] += 1
            app_id = digits(first(entry.get(ACC_APP_ID))) or digits(
                first(entry.get(ACC_APP_LOOKUP)))
            application = apps.get(app_id)
            if application is None:
                # The number that matters. Every one of these is a decision the office
                # made that cannot be attached to the application it was about.
                counts["acceptance that matches no application"] += 1
                continue
            raw = first(entry.get(ACC_APPROVAL)).strip().lower()
            decision = APPROVALS.get(raw)
            if decision is None:
                unmapped[raw or "(blank)"] += 1
                decision = Decision.PENDING
            if dry:
                continue
            application.decision = decision
            application.decided_at = entry.created_at
            application.save(update_fields=["decision", "decided_at"])
            counts[f"decision:{decision}"] += 1
        if unmapped:
            counts["approval values nobody has mapped"] = sum(unmapped.values())
            self.stdout.write(self.style.WARNING(
                f"  approval values seen and not mapped: {dict(unmapped)}"))

    # --- appointments --------------------------------------------------------

    def _appointments(self, wp, members, journals, apps, counts, dry) -> None:
        for entry in wp.entries(ASSIGNMENT):
            counts["assignment entries"] += 1
            member = member_of(entry, members)
            title = first(entry.get(ASG_JOURNAL)).strip()
            role = first(entry.get(ASG_ROLE)).strip() or "editor"
            if member is None:
                counts["assignment with no member"] += 1
                continue
            journal = journals.get(title.lower())
            if journal is None:
                counts["assignment whose journal is not in the master list"] += 1
                continue
            if dry:
                continue
            application = apps.get(digits(first(entry.get(ASG_APP_ID))))
            if Appointment.objects.filter(
                    member=member, journal=journal, role=role[:60],
                    started_on=entry.created_at.date() if entry.created_at else None
            ).exclude(wp_entry_id=entry.id).exists():
                # Two office actions that say the same thing. Kept as two rows, because
                # merging them would erase the fact that it happened twice — and
                # counted, because somebody may want to tidy them.
                counts["assignment repeated for the same member, journal and day"] += 1
            Appointment.objects.update_or_create(
                wp_entry_id=entry.id,
                defaults={"member": member, "journal": journal, "role": role[:60],
                          "application": application,
                          "started_on": entry.created_at.date() if entry.created_at
                          else None})
            counts["appointments imported"] += 1

    def _report(self, counts) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("census"))
        for key, value in sorted(counts.items()):
            self.stdout.write(f"  {key:48} {value}")
        self.stdout.write("")
        self.stdout.write(f"  journals      {Journal.objects.count()}")
        self.stdout.write(f"  applications  {Application.objects.count()}")
        self.stdout.write(f"  appointments  {Appointment.objects.count()}")
