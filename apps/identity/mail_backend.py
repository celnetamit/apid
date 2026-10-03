"""Email backend that logs every outgoing message before delegating to SES.

Wraps `django_ses.SESBackend` so password reset, mailer commands and ad-hoc
`EmailMessage.send()` calls all land in `EmailLog` with their recipient, subject
and body. Office pages can then show "what was sent to this member" without
reading inbox folders.
"""
from __future__ import annotations

import logging

from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class LoggingSESBackend(BaseEmailBackend):
    """Save each message to `EmailLog`, then hand off to `django_ses.SESBackend`.

    The log row is written whether the SES delegate accepts or rejects, so the
    office can see failed sends too.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django_ses import SESBackend
        self._delegate = SESBackend(*args, **kwargs)

    def open(self):
        return self._delegate.open()

    def close(self):
        return self._delegate.close()

    def send_messages(self, email_messages):
        from apps.identity.models import EmailLog, Member
        sent_total = 0
        for message in email_messages:
            # Delegate first — a message that SES refuses must still be logged.
            try:
                n = self._delegate.send_messages([message])
                status = EmailLog.Status.SENT if n else EmailLog.Status.FAILED
                error = ""
            except Exception as exc:
                n = 0
                status = EmailLog.Status.FAILED
                error = f"{type(exc).__name__}: {exc}"[:2000]
            sent_total += n or 0

            for to in (message.to or [""]):
                try:
                    member = Member.objects.filter(email__iexact=to).first() if to else None
                    EmailLog.objects.create(
                        to_address=(to or "")[:320],
                        from_address=(message.from_email or "")[:320],
                        subject=(message.subject or "")[:500],
                        body=(message.body or "")[:50000],
                        kind=_infer_kind(message.subject or ""),
                        status=status,
                        error=error,
                        related_member=member,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("EmailLog write failed; dropping log row only")
        return sent_total


def _infer_kind(subject: str) -> str:
    """Best-effort tag the message with its purpose for filtering."""
    s = subject.lower()
    if "password" in s and ("reset" in s or "change" in s):
        return "password_reset"
    if "commissioning editor login" in s or ("login" in s and "credential" in s):
        return "login_credentials"
    if "welcome" in s or "set up" in s:
        return "welcome"
    if "verify" in s or "confirm" in s:
        return "verification"
    if "application" in s:
        return "application"
    return "other"
