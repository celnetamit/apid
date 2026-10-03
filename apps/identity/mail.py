"""The letters this registry sends, and the reason none of them has gone out yet.

Two are needed at once: *welcome, here is your APID* and *here is a link to set a new
password*. Both are written, both are tested, and both are written to a folder on disk
rather than sent, because the only working sender on this estate is manuscript-ngine's
Amazon SES lane — its credentials, its verified domain, its bounce reputation — and
whether APID shares that lane is Amit's decision, not mine. The Google client was
borrowed the same way this morning and he was right to ask where it came from.

So: `APID_EMAIL_BACKEND` unset writes each message to `outbox/`, where it can be read,
counted and checked. Set it to the SES backend and the same code sends. Nothing about
the messages changes either way, which is the point of writing them now.

**Bounces are somebody else's reputation.** SES measures bounce rate per *account*, and
this registry carries 13,412 imported addresses of unknown freshness. When the lane is
opened it must be opened with the same suppression manuscript-ngine already applies —
that module refuses `.test`, `.invalid` and the reserved example domains before they
reach SES. Sending this registry's back catalogue at it without that would put every
other system on the account at risk.
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional

from django.conf import settings
from django.core.mail import EmailMessage
from django.urls import reverse

WELCOME_SUBJECT = "Your APID — {apid}"

WELCOME = """Dear {name},

Your account on the CELNET academic profile registry is ready.

    Your APID:  {apid}
    Sign in at: {site}{signin}

Your public profile lives at {site}{profile} — it is worth a few minutes to complete
it. Editors search this registry by affiliation and by area of expertise, and a profile
with neither is a profile nobody finds.

If you have an ORCID iD, adding it links your work here to your work everywhere else.

— The editorial office
CELNET · STM Journals
"""

RESET_SUBJECT = "Setting a new password for APID {apid}"

RESET = """Dear {name},

Somebody asked for a new password for your account ({email}) on the CELNET academic
profile registry. If that was not you, nothing has changed and you can ignore this.

To set a new password:

    {site}{link}

The link works once and expires in 24 hours.

— The editorial office
CELNET · STM Journals
"""


def _site(request=None) -> str:
    """The address to write into a letter.

    Built from the request where there is one, because a link is only useful if it
    points at the host the person actually used. manuscript-ngine's certificate QR
    spent months pointing at a retired host for exactly the want of this.
    """
    if request is not None:
        return f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"
    return getattr(settings, "APID_SITE_URL", "https://apid.celnet.in")


def _send(subject: str, body: str, to: str) -> bool:
    """Send, or write to the outbox. True when it left this machine."""
    if not to or "@" not in to:
        return False
    backend = os.environ.get("APID_EMAIL_BACKEND", "")
    if not backend:
        folder = os.path.join(settings.BASE_DIR.parent, "outbox")
        os.makedirs(folder, exist_ok=True)
        # Named from the address and the subject, by md5 rather than by `hash`:
        # Python's `hash` is salted per process, so the same letter written twice got
        # two different filenames and nothing could find the one it had just written.
        name = "".join(c if c.isalnum() or c in "-._@" else "_" for c in to)
        stamp = hashlib.md5(f"{to}|{subject}".encode()).hexdigest()[:8]
        path = os.path.join(folder, f"{name}-{stamp}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"To: {to}\nSubject: {subject}\n\n{body}")
        return False
    message = EmailMessage(subject=subject, body=body, to=[to])
    message.send(fail_silently=True)
    return True


def welcome(member, request=None) -> bool:
    site = _site(request)
    return _send(
        WELCOME_SUBJECT.format(apid=member.apid),
        WELCOME.format(
            name=member.full_name or member.username, apid=member.apid, site=site,
            signin=reverse("login"),
            profile=reverse("profile", kwargs={"apid": member.apid})),
        member.email)


def password_reset(member, link: str, request=None) -> bool:
    site = _site(request)
    return _send(
        RESET_SUBJECT.format(apid=member.apid),
        RESET.format(name=member.full_name or member.username, email=member.email,
                     site=site, link=link),
        member.email)
