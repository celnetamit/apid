"""Create or refresh a login for every active EditorialStaff and email the
temporary password to them.

For each active row in `EditorialStaff`:

1. Find the `Member` by email, or create a new one if none exists.
2. Rename `full_name` to match the editor's current name — the record may
   belong to a previous occupant of a role-based mailbox (so the Member row
   for `nursing.editor@celnet.in` says "Theja BC" today but the current
   commissioning editor is Farha Khan, and after this command runs she sees
   her own name when she signs in).
3. Set a fresh random password and mark the account active.
4. Send a plain-text English email with the login URL, the temporary
   password, and instructions to reset it immediately.

Idempotent only in the sense that running it twice invalidates the first
password; use `--only=<email>` to re-send to a single editor.
"""
from __future__ import annotations

import secrets
import string

from django.core.management.base import BaseCommand

from apps.editorial.models import EditorialStaff
from apps.identity import mail as mail_module
from apps.identity.models import Member

# Omit visually ambiguous characters so phone-screen reads are unambiguous.
_ALPHABET = "".join(
    c for c in string.ascii_letters + string.digits if c not in "Il0O1"
)


def _generate_password(length: int = 14) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


LOGIN_SUBJECT = "Your APID commissioning editor login"

LOGIN_BODY = """\
Hello {name},

An APID (Academic Publishing and Information Database) account has been
set up for you as a Commissioning Editor at Consortium e-Learning Network
Pvt Ltd. You can sign in to review and decide applications on the
journals you commission.

  Sign in:    {site}/accounts/login/
  Email:      {email}
  Password:   {password}

For your security, please change your password immediately after signing
in for the first time:

  {site}/accounts/password_reset/

Once signed in, open "My Profile" to update your name, phone, title and
other personal details — the record may still hold details from an
earlier user of this mailbox.

If you were not expecting this message, please ignore it; the account
remains locked unless you log in.

Regards,
The APID Office
Consortium e-Learning Network Pvt Ltd
"""


def _next_apid() -> str:
    """Pick the next unused APID number as a string.

    Members minted outside the WordPress import keep rising above the imported
    IDs, which stop at wp_user_id's maximum.
    """
    highest = 0
    for apid in Member.objects.values_list("apid", flat=True):
        try:
            highest = max(highest, int(apid))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


class Command(BaseCommand):
    help = ("Create / refresh a login for every active EditorialStaff and email "
            "the temporary password.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Print the plan without changing passwords or sending mail.")
        parser.add_argument("--only", type=str, default="",
                            help="Comma-separated list of staff emails to limit the send to.")
        parser.add_argument("--site", default="https://apid.journalslibrary.com",
                            help="Base URL used in the email body.")

    def handle(self, *, dry_run: bool, only: str, site: str, **_) -> None:
        only_set = {e.strip().lower() for e in only.split(",") if e.strip()}
        qs = EditorialStaff.objects.filter(active=True)
        if only_set:
            qs = qs.filter(email__in=only_set)
        qs = qs.order_by("name")

        self.stdout.write(
            f"Will process {qs.count()} editor(s). dry-run={dry_run}")

        sent = failed = created = updated = 0
        for staff in qs:
            email = staff.email.lower()
            member = Member.objects.filter(email__iexact=email).first()

            if dry_run:
                where = f"Member #{member.apid} ({member.full_name or member.username})" if member else "NEW Member"
                self.stdout.write(f"  [DRY] {email} → {where}; would rename to {staff.name!r}")
                continue

            password = _generate_password()
            if member is None:
                username_base = email.split("@", 1)[0] or f"staff{secrets.randbelow(99999)}"
                username = username_base[:150]
                # Guarantee uniqueness in case the base already exists.
                if Member.objects.filter(username=username).exists():
                    username = f"{username_base[:140]}.{secrets.randbelow(9999)}"
                member = Member(
                    username=username,
                    email=email,
                    full_name=staff.name,
                    apid=_next_apid(),
                    is_active=True,
                )
                member.set_password(password)
                member.save()
                created += 1
            else:
                if member.full_name != staff.name:
                    member.full_name = staff.name
                member.is_active = True
                member.set_password(password)
                member.save()
                updated += 1

            ok = mail_module._send(
                LOGIN_SUBJECT,
                LOGIN_BODY.format(name=staff.name, email=email,
                                  password=password, site=site.rstrip("/")),
                email,
            )
            if ok:
                sent += 1
                self.stdout.write(f"  SENT  {email} → {staff.name}")
            else:
                failed += 1
                self.stdout.write(
                    f"  FAIL  {email} → {staff.name} (not sent, check outbox / SES)")

        self.stdout.write(
            f"\nDone. created={created} updated={updated} sent={sent} failed={failed}")
