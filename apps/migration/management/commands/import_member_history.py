"""Role claims, reviewed papers and help-desk messages.

Three things the live site kept in forms that look like profile sections and are not:

* **Role claims** — Contributions (67), Role Update (80), Editorial Registration's
  Contribution rows (98). A member saying "I am an Editor of this journal"; the office
  approves or ignores it. 47 of Role Update's 264 entries were filed signed-out, so
  they carry a name and an email and no user id.
* **Reviewed papers** — Reviewer Contribution Form (205), whose rows are child form 206.
* **Contact Us NEW** (122) — the help desk's history, replies included.

A signed-out entry is matched to a member by email, and only when that email belongs to
exactly one member; two people sharing an address is a reason to leave it unmatched, not
to pick one. Unmatched role claims and papers are counted and skipped (a claim nobody can
be held to is not worth keeping); unmatched help-desk messages are kept by email.
"""

from __future__ import annotations

import collections
import datetime as dt

from django.core.management.base import BaseCommand

from apps.identity.models import Member, SupportRequest
from apps.migration.reader import WordPress, first, joined
from apps.profiles.models import ClaimedRole, ReviewedPaper

CONTRIBUTIONS, ROLE_UPDATE, EDITORIAL_CONTRIBUTION = 67, 80, 98
REVIEWER_PARENT, REVIEWER_PAPERS = 205, 206
CONTACT = 122
EDITORIAL_REGISTRATION = 97


def day(raw: str):
    try:
        return dt.date.fromisoformat(first(raw).strip()[:10])
    except ValueError:
        return None


def text(entry, key: str, limit: int | None = None) -> str:
    value = first(entry.get(key)).strip()
    return value[:limit] if limit else value


class Command(BaseCommand):
    help = "Import role claims, reviewed papers and help-desk messages (idempotent)."

    def handle(self, *args, **options):
        wp = WordPress()
        try:
            self.members = Member.objects.in_bulk()
            by_email = collections.defaultdict(list)
            for m in self.members.values():
                if m.email:
                    by_email[m.email.strip().lower()].append(m)
            self.by_email = {e: ms[0] for e, ms in by_email.items() if len(ms) == 1}
            counts: collections.Counter = collections.Counter()
            self._claims(wp, counts)
            self._papers(wp, counts)
            self._support(wp, counts)
        finally:
            wp.close()
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("census"))
        for key, value in sorted(counts.items()):
            self.stdout.write(f"  {key:50} {value}")
        for model in (ClaimedRole, ReviewedPaper, SupportRequest):
            self.stdout.write(f"  {model.__name__ + 's in the database':50} "
                              f"{model.objects.count()}")

    # --- who an entry belongs to ---------------------------------------------

    def _member(self, user_id, email: str = ""):
        if user_id and user_id in self.members:
            return self.members[user_id]
        return self.by_email.get((email or "").strip().lower())

    def _owners(self, wp, child_form: int, parent_form: int) -> dict[int, int]:
        parents = {r["id"]: r["user_id"] for r in wp._rows(
            f"SELECT id, user_id FROM {wp.prefix}frm_items WHERE form_id=%s",
            (parent_form,)) if r["user_id"]}
        return {r["id"]: parents[r["parent_item_id"]] for r in wp._rows(
            f"SELECT id, parent_item_id FROM {wp.prefix}frm_items WHERE form_id=%s",
            (child_form,)) if r["parent_item_id"] in parents}

    # --- role claims ---------------------------------------------------------

    def _claims(self, wp, counts) -> None:
        owners = self._owners(wp, EDITORIAL_CONTRIBUTION, EDITORIAL_REGISTRATION)
        for form in (CONTRIBUTIONS, ROLE_UPDATE, EDITORIAL_CONTRIBUTION):
            for entry in wp.entries(form):
                counts[f"role claims: form {form} entries"] += 1
                member = self._member(entry.user_id or owners.get(entry.id),
                                      text(entry, "c2kgy"))
                if member is None:
                    counts["role claims: no member found"] += 1
                    continue
                if form == CONTRIBUTIONS:
                    row = dict(kind=text(entry, "tpkeo", 40), subject=text(entry, "rqhcc", 160),
                               journal=text(entry, "hun3d", 255),
                               abbreviation=text(entry, "u8p7m", 60),
                               journal_url=text(entry, "pvq39", 500),
                               role=text(entry, "7zie2", 255), status=text(entry, "rjv14", 40),
                               remark=text(entry, "xs504"))
                elif form == ROLE_UPDATE:
                    roles = [r for k in ("mvv3f", "81skq", "z3gw5")
                             for r in joined(entry.get(k), ", ").split(", ") if r]
                    row = dict(kind=text(entry, "z08sg", 40), subject=text(entry, "dmel", 160),
                               journal=text(entry, "wovh2", 255),
                               role=", ".join(dict.fromkeys(roles))[:255])
                else:
                    row = dict(kind="Journal", subject=text(entry, "7zv9w", 160),
                               journal=text(entry, "tdaho", 255),
                               journal_url=text(entry, "mzm5f", 500))
                if not row.get("journal"):
                    counts["role claims: entry with no journal"] += 1
                    continue
                ClaimedRole.objects.update_or_create(wp_entry_id=entry.id, defaults={
                    "member": member, "source_form": form,
                    "created_at": entry.created_at, **row})
                counts["role claims: imported"] += 1

    # --- reviewed papers -----------------------------------------------------

    def _papers(self, wp, counts) -> None:
        owners = self._owners(wp, REVIEWER_PAPERS, REVIEWER_PARENT)
        for entry in wp.entries(REVIEWER_PAPERS):
            counts["papers: entries"] += 1
            member = self._member(entry.user_id or owners.get(entry.id))
            if member is None:
                counts["papers: no member found"] += 1
                continue
            title = text(entry, "zbjdt", 500)
            journal = text(entry, "compl", 255)
            if not (title or journal):
                counts["papers: empty entry"] += 1
                continue
            n = text(entry, "tvzd1")
            ReviewedPaper.objects.update_or_create(wp_entry_id=entry.id, defaults={
                "member": member, "subject": text(entry, "yvj1y", 160),
                "journal": journal, "abbreviation": text(entry, "7a9vo", 60),
                "paper_title": title, "published_on": day(entry.get("4mv5a")),
                "papers_count": int(n) if n.isdigit() else None})
            counts["papers: imported"] += 1

    # --- help desk -----------------------------------------------------------

    def _support(self, wp, counts) -> None:
        for entry in wp.entries(CONTACT):
            counts["support: entries"] += 1
            email = text(entry, "29yf4d22", 254)
            issue, body = text(entry, "wlj0z", 255), text(entry, "9jv0r122")
            if not (issue or body):
                counts["support: empty entry"] += 1
                continue
            category = text(entry, "nljyc", 60)
            help_id = text(entry, "tw948")
            member = self._member(entry.user_id, email)
            SupportRequest.objects.update_or_create(wp_entry_id=entry.id, defaults={
                "member": member,
                "name": " ".join(filter(None, (text(entry, "qh4icy22"),
                                               text(entry, "ocfup122")))) [:255],
                "email": email, "issue": issue, "description": body,
                "category": "" if category.lower().startswith("select") else category,
                "status": text(entry, "tyk9q", 40), "reply": text(entry, "jp0q4"),
                "help_id": int(help_id) if help_id.isdigit() else None,
                "created_at": entry.created_at})
            counts["support: imported"] += 1
            if member is None:
                counts["support: kept by email only"] += 1
