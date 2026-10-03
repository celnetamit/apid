"""Bring every member across, and refuse to call it done without counting.

The users table is the authority, not the registration form: 13,412 accounts exist and
6,763 registration entries, because the earliest members were created by hand and 23
entries point at an account that has since been deleted. Importing from the form would
have lost half the registry and nothing would have said so.

Re-runnable by design. Keyed on the WordPress user id, a second run updates the same
row; keyed on the email somebody edited last week, it makes a duplicate and the count
still looks plausible.
"""

from __future__ import annotations

import collections
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.identity.models import Member, MemberRole
from apps.migration.reader import (WordPress, first, joined,
                                   roles_from_capabilities, unserialise)

#: The registration form and the fields worth taking from it. The users table has the
#: account; these are the things only the form asked for.
REGISTRATION_FORM = 83
TITLE = "oh60m"
FULL_NAME = "lkm05"          # 2,756 entries
NAME_PARTS = "yja02"         # 3,539 entries — the older field
EMAIL = "qulq13"
PHONE = "32iss"
COUNTRY = "snawb"
USER_ID = "zah2u3"


def name_from(entry, fallback: str) -> str:
    """A member's name, from whichever field their year of registration used.

    Three sources because the form changed twice: `lkm05` holds it for 2,756 entries,
    the older `yja02` for 3,539, and 468 have neither — for those the account's own
    display name is all there is. A single-source import would have left 468 members
    with no name and looked complete.
    """
    full = first(entry.get(FULL_NAME)).strip()
    if full:
        return full
    parts = joined(entry.get(NAME_PARTS)).strip()
    if parts:
        return parts
    return fallback.strip()


class Command(BaseCommand):
    help = "Import members from the WordPress snapshot (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Count and report; write nothing.")
        parser.add_argument("--limit", type=int, default=0,
                            help="Stop after N accounts, for a quick look.")

    def handle(self, *args, **options):
        dry = options["dry_run"]
        limit = options["limit"]
        wp = WordPress()
        try:
            self._run(wp, dry, limit)
        finally:
            wp.close()

    def _run(self, wp: WordPress, dry: bool, limit: int) -> None:
        users = wp.users()
        if limit:
            users = users[:limit]
        self.stdout.write(f"accounts in the snapshot: {len(users)}")

        # The registration entry for each account, newest first — somebody who
        # registered twice has two, and the later one is the one they kept.
        registrations: dict[int, Any] = {}
        for entry in wp.entries(REGISTRATION_FORM):
            uid = entry.user_id or int(first(entry.get(USER_ID)) or 0) or None
            if uid:
                registrations[uid] = entry
        self.stdout.write(f"registration entries: {len(registrations)} accounts covered")

        # Four logins are used by more than one account — nine accounts in all, each
        # pair registered on the same day in 2022–23, which is a plugin that bypassed
        # WordPress's own unique check rather than anything a person did. They are
        # separate accounts with separate data, so they import separately; only the
        # login handle is made unique, and every one of them is named in the census
        # for a human to merge or leave.
        login_counts: collections.Counter = collections.Counter(
            u["user_login"] for u in users)
        shared_logins = {login for login, n in login_counts.items() if n > 1}

        meta = wp.user_meta(("wpapid_capabilities",))
        counts: collections.Counter = collections.Counter()
        unknown_roles: collections.Counter = collections.Counter()
        orphan_entries = [uid for uid in registrations
                          if uid not in {u["ID"] for u in users}]

        for user in users:
            entry = registrations.get(user["ID"])
            display = user.get("display_name") or user.get("user_login") or ""
            full_name = name_from(entry, display) if entry else display

            login = user["user_login"] or f"apid{user['ID']}"
            if login in shared_logins:
                login = f"{login}.{user['ID']}"
                counts["login made unique"] += 1
            fields = {
                "username": login[:150],
                "email": (user.get("user_email") or "")[:254],
                "legacy_password": user.get("user_pass") or "",
                "full_name": full_name[:200],
                "registered_at": user.get("user_registered"),
                "imported_at": timezone.now(),
            }
            if entry:
                fields.update({
                    "title": first(entry.get(TITLE))[:32],
                    "contact_number": first(entry.get(PHONE))[:40],
                    "country": first(entry.get(COUNTRY))[:80],
                })

            if not full_name:
                counts["no name anywhere"] += 1

            if dry:
                counts["would import"] += 1
            else:
                with transaction.atomic():
                    member, created = Member.objects.update_or_create(
                        wp_user_id=user["ID"],
                        defaults={**fields, "apid": str(user["ID"])},
                    )
                    counts["created" if created else "updated"] += 1

                    known, unknown = roles_from_capabilities(
                        meta.get(user["ID"], {}).get("wpapid_capabilities", ""))
                    for name in unknown:
                        unknown_roles[name] += 1
                    for role in known:
                        MemberRole.objects.get_or_create(member=member, role=role)
                        counts[f"role:{role}"] += 1

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("census"))
        for key, value in sorted(counts.items()):
            self.stdout.write(f"  {key:28} {value}")

        if orphan_entries:
            # Said out loud rather than dropped: these are registrations whose account
            # was deleted, and somebody may want to know that before cutover.
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"  {len(orphan_entries)} registration entries point at an account that "
                f"no longer exists — skipped: {orphan_entries[:8]}"))

        if shared_logins:
            self.stdout.write(self.style.WARNING(
                f"  {len(shared_logins)} logins are shared by more than one account "
                f"and were suffixed with the account id — the accounts themselves are "
                f"kept separate: {sorted(shared_logins)}"))

        if unknown_roles:
            self.stdout.write(self.style.WARNING(
                f"  capability names nobody has mapped: {dict(unknown_roles)}"))

        if not dry:
            imported = Member.objects.count()
            self.stdout.write("")
            if imported == len(users):
                self.stdout.write(self.style.SUCCESS(
                    f"  {imported} members in, {len(users)} accounts out — they agree."))
            else:
                # Loud on purpose. A migration that reports success while short of
                # rows is the failure this whole command exists to make impossible.
                raise SystemExit(
                    f"CENSUS FAILED: {len(users)} accounts in the snapshot, "
                    f"{imported} members imported.")
