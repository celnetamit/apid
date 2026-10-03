"""Import commissioning editors from the publications-office CSV.

The CSV format (one row per journal):

    ,Abbreviation,Journal,Subject,Email,Name

One editor typically owns many journals, so we deduplicate by (lower-cased)
email and keep the longest observed name — "Susmita Jahan" wins over "Susmita"
on the same email.  Journals are matched by `abbreviation` (case-insensitive).

Usage:

    python manage.py import_commissioning_editors path/to/editors.csv
    python manage.py import_commissioning_editors path/to/editors.csv --dry-run
"""
from __future__ import annotations

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.editorial.models import EditorialStaff, Journal


class Command(BaseCommand):
    help = "Import commissioning editors and map them to journals by abbreviation."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=Path)
        parser.add_argument("--dry-run", action="store_true",
                            help="Roll back after printing the summary.")

    def handle(self, *, csv_path: Path, dry_run: bool, **_) -> None:
        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")

        with csv_path.open(newline="") as fp:
            rows = list(csv.DictReader(fp))
        if not rows:
            raise CommandError("CSV is empty.")

        self.stdout.write(f"Rows read: {len(rows)}")

        try:
            with transaction.atomic():
                self._run(rows)
                if dry_run:
                    self.stdout.write(self.style.WARNING(
                        "--dry-run: rolling back"))
                    raise _Rollback()
        except _Rollback:
            pass

    def _run(self, rows: list[dict]) -> None:
        # 1. Deduplicate editors by lower-cased email, keeping the longest name.
        editors_by_email: dict[str, str] = {}
        for r in rows:
            email = (r.get("Email") or "").strip().lower()
            name = (r.get("Name") or "").strip()
            if not email or not name:
                continue
            prev = editors_by_email.get(email, "")
            if len(name) > len(prev):
                editors_by_email[email] = name
        self.stdout.write(f"Unique editors by email: {len(editors_by_email)}")

        # 2. Upsert EditorialStaff for each editor.
        staff_by_email: dict[str, EditorialStaff] = {}
        created = updated = 0
        for email, name in editors_by_email.items():
            obj, is_new = EditorialStaff.objects.update_or_create(
                email=email,
                defaults={"name": name, "active": True,
                          "designation": "Commissioning Editor"},
            )
            staff_by_email[email] = obj
            if is_new:
                created += 1
            else:
                updated += 1
        self.stdout.write(
            f"EditorialStaff: {created} created, {updated} updated")

        # 3. Map journals to editors by abbreviation.
        matched = 0
        unmatched: list[str] = []
        for r in rows:
            abbr = (r.get("Abbreviation") or "").strip()
            email = (r.get("Email") or "").strip().lower()
            if not abbr or not email:
                continue
            editor = staff_by_email.get(email)
            if not editor:
                continue
            j = Journal.objects.filter(abbreviation__iexact=abbr).first()
            if not j:
                unmatched.append(abbr)
                continue
            if j.commissioning_editor_id != editor.pk:
                j.commissioning_editor = editor
                j.save(update_fields=["commissioning_editor"])
            matched += 1

        self.stdout.write(
            f"Journals mapped: {matched}, unmatched: {len(unmatched)}")
        for u in unmatched[:30]:
            self.stdout.write(f"  unmatched abbreviation: {u}")
        if len(unmatched) > 30:
            self.stdout.write(f"  ... and {len(unmatched) - 30} more")


class _Rollback(Exception):
    pass
