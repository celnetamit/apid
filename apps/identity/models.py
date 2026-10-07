"""Who a member is, and the number that stays theirs.

The WordPress site identifies a member by their WordPress user id and nothing else:
the registration form's `zah2u3` field holds it, every other form carries it as a
hidden `user_id`, and the whole system is joined on it. There is no APID number in the
data — the "APID" is the account.

So the number is minted here, once, and the WordPress id is kept beside it forever.
Two reasons, and the second is the one that matters:

1. A member who has been telling colleagues their profile URL for three years must
   find the same profile at the same address after the move.
2. Every import re-run has to land on the same person. Keyed on the WordPress id, a
   second run updates; keyed on an email that somebody edited last week, it makes a
   duplicate and nothing says so.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


class Member(AbstractUser):
    """An academic with an APID.

    `AbstractUser` rather than a profile hanging off `auth.User`: sign-in, identity
    and the public record are the same thing here, and splitting them would mean
    every query about a person starts with a join.
    """

    #: The public identifier. Minted from the WordPress user id at import so that
    #: existing profile links keep working, and continued from the same series after.
    apid = models.CharField(max_length=16, unique=True, db_index=True)

    #: Where this record came from. Null for anyone who registers on the new site.
    wp_user_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)

    #: The WordPress password hash, kept exactly as it was so that nobody is forced to
    #: reset a password because we changed systems. Verified by `WordPressHasher`.
    legacy_password = models.CharField(max_length=255, blank=True)

    title = models.CharField(max_length=32, blank=True)          # Prof., Dr., Mr.
    full_name = models.CharField(max_length=200, blank=True)
    contact_number = models.CharField(max_length=40, blank=True)
    country = models.CharField(max_length=80, blank=True)

    registered_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)

    # wisp 2026-10-02 pm: share / invite feature.
    # Who invited this member, if anyone. Captured from ?ref=<APID> on the
    # registration landing. SET_NULL so purging an inviter does not cascade.
    referred_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="referrals",
        help_text="The APID member who invited this one, if any.")

    class Meta:
        indexes = [models.Index(fields=["full_name"])]

    def __str__(self) -> str:
        return f"{self.apid} — {self.full_name or self.username}"

    @property
    def display_name(self) -> str:
        return self.full_name or self.get_full_name() or self.username


class Role(models.TextChoices):
    """What the portfolio has appointed a member to be.

    Taken from the live capability rows rather than invented: author 11,794,
    editor 710, subscriber 579, editor-in-chief 191, commissioning editor 24,
    tech support 7, administrator 6.
    """

    AUTHOR = "author", "Author"
    EDITOR = "editor", "Editor"
    EDITOR_IN_CHIEF = "editor_in_chief", "Editor-in-Chief"
    COMMISSIONING_EDITOR = "commissioning_editor", "Commissioning Editor"
    REVIEWER = "reviewer", "Reviewer"
    SUBSCRIBER = "subscriber", "Subscriber"
    TECH_SUPPORT = "tech_support", "Tech Support"
    ADMINISTRATOR = "administrator", "Administrator"


class MemberRole(models.Model):
    """One role a member holds. A person is routinely an author *and* an editor, so
    this is a table rather than a column — which is also how WordPress stored it."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="roles")
    role = models.CharField(max_length=32, choices=Role.choices)
    granted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("member", "role")]

    def __str__(self) -> str:
        return f"{self.member.apid}: {self.get_role_display()}"


class EmailTemplate(models.Model):
    """Office-editable subject + body for every outgoing email category.

    Store one row per `category` (slug). The system reads this row before
    sending; if `enabled` is False the hard-coded default takes over. Bodies
    are plain-text with Python `str.format` placeholders — `{name}`, `{email}`,
    `{password}`, `{site}` — resolved at send time against the message context.

    An office editor sees only the categories we created up front; adding a
    new category requires a code change (because the sender has to know when
    to pick it).
    """

    category = models.CharField(max_length=60, unique=True, db_index=True,
                                help_text="Slug: login_credentials, welcome, …")
    name = models.CharField(max_length=120,
                            help_text="What the office sees in the editor.")
    subject = models.CharField(max_length=500)
    body = models.TextField()
    enabled = models.BooleanField(default=True)
    variables_help = models.TextField(blank=True,
        help_text="Short note on which {placeholders} the sender provides.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.category})"

    def render(self, context: dict) -> tuple[str, str]:
        """Format the subject + body with `context`. Missing keys render as blank."""
        class _SafeDict(dict):
            def __missing__(self, key):
                return ""
        safe = _SafeDict(context)
        try:
            return self.subject.format_map(safe), self.body.format_map(safe)
        except (ValueError, IndexError):
            return self.subject, self.body


class EmailLog(models.Model):
    """An outgoing email, logged for office audit. Captures recipient, sender,
    subject and body for every message the system sent — SES-backed or outbox
    fallback. Status is 'sent' when the backend accepted the message, 'failed'
    when the backend raised, 'outbox' when APID_EMAIL_BACKEND was unset and the
    body went to the local outbox folder instead.
    """

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        OUTBOX = "outbox", "Outbox"

    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    to_address = models.CharField(max_length=320, db_index=True)
    from_address = models.CharField(max_length=320, blank=True)
    subject = models.CharField(max_length=500)
    body = models.TextField(blank=True)
    kind = models.CharField(max_length=60, blank=True, db_index=True,
                            help_text="Loose tag — login_credentials, password_reset, …")
    status = models.CharField(max_length=20, default=Status.SENT, choices=Status.choices,
                              db_index=True)
    error = models.TextField(blank=True)
    related_member = models.ForeignKey(
        Member, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="emails_received",
        help_text="The APID member the message was sent to, when there is one.")

    class Meta:
        ordering = ["-sent_at"]
        indexes = [models.Index(fields=["-sent_at"])]

    def __str__(self) -> str:
        return f"{self.sent_at:%Y-%m-%d %H:%M} → {self.to_address}: {self.subject[:40]}"


class SupportRequest(models.Model):
    """A message sent through Contact Us NEW (form 122, 291 entries) — the help desk's
    history, kept so a member who writes in again can be answered with the past in view.

    `member` is empty for the 40-odd people who wrote in without an account and could
    not be matched by email; `email` is what identifies them then.
    """

    member = models.ForeignKey("identity.Member", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="support_requests")
    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True, db_index=True)
    category = models.CharField(max_length=60, blank=True)
    issue = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=40, blank=True)
    reply = models.TextField(blank=True)
    help_id = models.IntegerField(null=True, blank=True)
    wp_entry_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name or self.email}: {self.issue}"[:80]
