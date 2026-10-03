"""Ensure every active EditorialStaff's Member has a public APID profile.

Creates a `Profile` row if missing, and fills in the two fields the public
page needs to render — `affiliation` and `designation` — only when blank, so
anybody who has already written a richer profile is left alone.

Defaults for the gap-fill:
  affiliation = "Consortium e-Learning Network Pvt Ltd"
  designation = staff.designation  (usually "Commissioning Editor")

Usage:
  python manage.py ensure_commissioning_editor_profiles [--dry-run]
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.editorial.models import EditorialStaff
from apps.identity.models import Member
from apps.profiles.models import Profile

DEFAULT_AFFILIATION = "Consortium e-Learning Network Pvt Ltd"


class Command(BaseCommand):
    help = ("Create or complete the APID Profile for every active "
            "EditorialStaff member.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *, dry_run: bool, **_) -> None:
        qs = EditorialStaff.objects.filter(active=True).order_by("name")
        created = aff_set = des_set = unchanged = no_member = 0
        for staff in qs:
            member = Member.objects.filter(email__iexact=staff.email).first()
            if not member:
                no_member += 1
                self.stdout.write(f"  SKIP (no member): {staff.email}")
                continue
            profile = getattr(member, "profile", None) or \
                Profile.objects.filter(member=member).first()
            will_create = profile is None
            touched = False
            if will_create:
                if dry_run:
                    self.stdout.write(f"  [DRY] CREATE Profile for {staff.name} ({member.apid})")
                else:
                    profile = Profile(member=member,
                                      affiliation=DEFAULT_AFFILIATION,
                                      designation=staff.designation)
                    profile.save()
                    created += 1
                    self.stdout.write(f"  CREATED  Profile for {staff.name} ({member.apid})")
                continue
            if not profile.affiliation:
                if dry_run:
                    self.stdout.write(f"  [DRY] FILL affiliation for {staff.name} ({member.apid})")
                else:
                    profile.affiliation = DEFAULT_AFFILIATION
                    touched = True
                    aff_set += 1
            if not profile.designation:
                if dry_run:
                    self.stdout.write(f"  [DRY] FILL designation for {staff.name} ({member.apid})")
                else:
                    profile.designation = staff.designation
                    touched = True
                    des_set += 1
            if touched:
                profile.save()
                self.stdout.write(f"  UPDATED  Profile for {staff.name} ({member.apid})")
            elif not will_create:
                unchanged += 1

        self.stdout.write(
            f"\nDone. created={created} affiliation_filled={aff_set} "
            f"designation_filled={des_set} unchanged={unchanged} "
            f"no_member={no_member}")
