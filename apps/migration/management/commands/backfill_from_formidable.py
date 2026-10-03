"""Backfill missing Member/Profile fields from WordPress Formidable form 69.

The original `import_profiles` command captured most fields correctly, but the
audit at 2026-10-03 found three systemic gaps on the migrated registry:

  Profession        3,155 members WP has data, APID is blank
  Title             3,051 (lives on Member.title, not Profile)
  Contact number      997 (lives on Member.contact_number)
  ORCID / RG etc.       1-2 each

This command re-reads the live Formidable rows for form 69, picks the most
recent entry per user (which is also how the live site renders them), and
writes any value that is still blank on the APID side. It never overwrites
a non-blank APID field — a profile the user has corrected after migration
remains the source of truth.

Usage:
  python manage.py backfill_from_formidable  --tsv /tmp/form69.tsv
  python manage.py backfill_from_formidable  --tsv /tmp/form69.tsv --dry-run
"""
from __future__ import annotations

import collections
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.identity.models import Member
from apps.profiles.models import Profile


#: Field-name (as it appears in `wpapid_frm_fields.name`) → (model, django_field).
FIELD_MAP: dict[str, tuple[str, str]] = {
    "Title":                                 ("member",  "title"),
    "Full Name":                             ("member",  "full_name"),
    "Contact number":                        ("member",  "contact_number"),
    "Country":                               ("profile", "country"),
    "Gender":                                ("profile", "gender"),
    "Profession":                            ("profile", "profession"),
    "Affiliation":                           ("profile", "affiliation"),
    "Affiliation Website/URL":               ("profile", "affiliation_url"),
    "Department":                            ("profile", "department"),
    "Designation":                           ("profile", "designation"),
    "Experience (Number of Years)":          ("profile", "experience_years"),
    "Institutional Profile URL":             ("profile", "institutional_profile_url"),
    "State":                                 ("profile", "state"),
    "City":                                  ("profile", "city"),
    "Pincode":                               ("profile", "pincode"),
    "Academic Qualification":                ("profile", "academic_qualification"),
    "Academic University / Organization":    ("profile", "academic_university"),
    "Higher Academic Qualification Passing Year": ("profile", "qualification_year"),
    "ORCID":                                 ("profile", "orcid"),
    "Google Scholar ID":                     ("profile", "google_scholar_id"),
    "Research Gate ID":                      ("profile", "researchgate_id"),
    "Scopus ID":                             ("profile", "scopus_id"),
    "SSRN ID":                               ("profile", "ssrn_id"),
    "Expertise":                             ("profile", "expertise"),
    "Area of interest":                      ("profile", "areas_of_interest"),
}

#: Caps that mirror the Django max_length on each column so we don't blow a
#: VARCHAR when writing a value longer than the schema allows.
LIMITS: dict[str, int] = {
    "title": 32, "full_name": 200, "contact_number": 40,
    "country": 120, "gender": 32, "profession": 120, "affiliation": 255,
    "affiliation_url": 500, "department": 255, "designation": 160,
    "experience_years": 40, "institutional_profile_url": 500,
    "state": 120, "city": 120, "pincode": 20, "academic_qualification": 160,
    "academic_university": 255, "qualification_year": 10, "orcid": 40,
    "google_scholar_id": 80, "researchgate_id": 120, "scopus_id": 80,
    "ssrn_id": 80,
}


class Command(BaseCommand):
    help = "Backfill blank APID Member/Profile fields from Formidable form 69."

    def add_arguments(self, parser):
        parser.add_argument("--tsv", required=True, type=Path,
                            help=("Path to a mysql -N -B dump with columns "
                                  "entry_id, user_id, unix_ts, field_name, value "
                                  "— the shape the audit script already uses."))
        parser.add_argument("--dry-run", action="store_true",
                            help="Print the plan without writing anything.")

    def handle(self, *, tsv: Path, dry_run: bool, **_) -> None:
        if not tsv.exists():
            raise CommandError(f"TSV not found: {tsv}")

        # Group by entry id.
        entries: dict[int, dict] = {}
        with tsv.open() as fp:
            for line in fp:
                parts = line.rstrip("\n").split("\t", 4)
                if len(parts) != 5:
                    continue
                eid, uid, ts, fname, val = parts
                try:
                    eid, uid, ts = int(eid), int(uid or 0), int(ts or 0)
                except ValueError:
                    continue
                row = entries.setdefault(eid, {"user_id": uid, "ts": ts, "fields": {}})
                row["fields"][fname] = val or ""

        # Pick the most recent entry per user id.
        by_user: dict[int, dict] = {}
        for row in entries.values():
            uid = row["user_id"]
            if not uid:
                continue
            cur = by_user.get(uid)
            if cur is None or row["ts"] > cur["ts"]:
                by_user[uid] = row

        self.stdout.write(f"Entries read: {len(entries)}")
        self.stdout.write(f"Distinct users with an entry: {len(by_user)}")

        filled = collections.Counter()
        touched_members = 0
        touched_profiles = 0
        missing_members = 0

        for uid, row in by_user.items():
            m = Member.objects.filter(wp_user_id=uid).first()
            if not m:
                missing_members += 1
                continue
            profile, _ = Profile.objects.get_or_create(member=m)

            member_dirty = False
            profile_dirty = False
            for fname, (dst_model, dst_field) in FIELD_MAP.items():
                wp_val = (row["fields"].get(fname) or "").strip()
                if not wp_val or wp_val.startswith("a:"):
                    # Blank, or a serialized PHP array (attachments etc).
                    continue
                target = m if dst_model == "member" else profile
                cur = (getattr(target, dst_field, "") or "").strip()
                if cur:
                    # The APID side already has something — respect it.
                    continue
                cap = LIMITS.get(dst_field)
                new_val = wp_val[:cap] if cap else wp_val
                filled[fname] += 1
                if dry_run:
                    continue
                setattr(target, dst_field, new_val)
                if dst_model == "member":
                    member_dirty = True
                else:
                    profile_dirty = True
            if member_dirty:
                m.save()
                touched_members += 1
            if profile_dirty:
                profile.save()
                touched_profiles += 1

        prefix = "WOULD backfill" if dry_run else "Done"
        self.stdout.write(
            f"\n{prefix}. members_updated={touched_members} "
            f"profiles_updated={touched_profiles} "
            f"members_missing_in_apid={missing_members}")
        self.stdout.write("\n=== Fields backfilled ===")
        for fname, n in filled.most_common():
            self.stdout.write(f"  {fname:50} {n:>6}")
