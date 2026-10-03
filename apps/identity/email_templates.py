"""Resolve subject + body for a mail category from `EmailTemplate` with a
hard-coded fallback for every category.

Every outgoing email the system sends should go through `render(category, ctx)`
so the office can edit copy without a code change. If no row exists, or the
row is disabled, we fall back to the default template defined here.
"""
from __future__ import annotations

from typing import Dict, Tuple


#: Hard-coded defaults — the shipping-day copy for every category we know about.
#: `variables_help` is what we show the editor as a hint.
DEFAULTS: Dict[str, Dict[str, str]] = {
    "login_credentials": {
        "name": "Commissioning editor login",
        "subject": "Your APID commissioning editor login",
        "body": (
            "Hello {name},\n\n"
            "An APID (Academic Publishing and Information Database) account has been\n"
            "set up for you as a Commissioning Editor at Consortium e-Learning Network\n"
            "Pvt Ltd. You can sign in to review and decide applications on the\n"
            "journals you commission.\n\n"
            "  Sign in:    {site}/accounts/login/\n"
            "  Email:      {email}\n"
            "  Password:   {password}\n\n"
            "For your security, please change your password immediately after signing\n"
            "in for the first time:\n\n"
            "  {site}/accounts/password_reset/\n\n"
            "Once signed in, open \"My Profile\" to update your name, phone, title and\n"
            "other personal details — the record may still hold details from an\n"
            "earlier user of this mailbox.\n\n"
            "If you were not expecting this message, please ignore it; the account\n"
            "remains locked unless you log in.\n\n"
            "Regards,\n"
            "The APID Office\n"
            "Consortium e-Learning Network Pvt Ltd\n"
        ),
        "variables_help": "{name} {email} {password} {site}",
    },
    "welcome": {
        "name": "Welcome / account created",
        "subject": "Welcome to APID",
        "body": (
            "Hello {name},\n\n"
            "Welcome to APID — your Academic Publishing and Information Database\n"
            "account is live.\n\n"
            "  Sign in:  {site}/accounts/login/\n"
            "  Your APID: {apid}\n\n"
            "Complete your profile so the editorial office can find you for board,\n"
            "reviewer and section-editor roles:\n\n"
            "  {site}/me/\n\n"
            "— The APID Office\n"
        ),
        "variables_help": "{name} {email} {apid} {site}",
    },
    "application_received": {
        "name": "Application received",
        "subject": "Your APID application #{application_id}",
        "body": (
            "Hello {name},\n\n"
            "Your application #{application_id} to join an editorial board has reached\n"
            "the office. We will write to you once the decision is made.\n\n"
            "Journals: {journals}\n\n"
            "— The APID Office\n"
        ),
        "variables_help": "{name} {email} {application_id} {journals} {site}",
    },
    "application_accepted": {
        "name": "Application accepted (per journal)",
        "subject": "Welcome aboard — {journal}",
        "body": (
            "Dear {name},\n\n"
            "We are delighted to confirm your appointment as {role} on\n"
            "{journal}. Your empanelment letter and certificate are attached\n"
            "(or available in your APID profile).\n\n"
            "  Your profile: {site}/profiles/{apid}/\n\n"
            "— The APID Office\n"
        ),
        "variables_help": "{name} {email} {apid} {journal} {role} {site}",
    },
    "application_declined": {
        "name": "Application declined (per journal)",
        "subject": "Your APID application decision — {journal}",
        "body": (
            "Dear {name},\n\n"
            "Thank you for your interest in {journal}. On this occasion the\n"
            "editorial office is unable to add you to the board.\n\n"
            "You remain a member of APID and are welcome to apply to other\n"
            "journals or re-apply later.\n\n"
            "— The APID Office\n"
        ),
        "variables_help": "{name} {email} {journal} {site}",
    },
    "editorial_invite": {
        "name": "Reviewer / editor invitation",
        "subject": "APID — Invitation to join {journal} as {role}",
        "body": (
            "Hello {name},\n\n"
            "The editorial office of {journal} would like to invite you to serve\n"
            "as {role}. If you accept, please follow the link below to confirm:\n\n"
            "  {accept_url}\n\n"
            "If you prefer not to, this message needs no reply.\n\n"
            "— The APID Office\n"
        ),
        "variables_help": "{name} {email} {journal} {role} {accept_url}",
    },
}


def default_for(category: str) -> Dict[str, str]:
    """Return the hard-coded (subject, body, variables_help, name) for `category`."""
    return DEFAULTS.get(category, {
        "name": category,
        "subject": "",
        "body": "",
        "variables_help": "",
    })


def render(category: str, context: dict) -> Tuple[str, str]:
    """Return (subject, body) for `category`, applying the office-edited copy
    when `EmailTemplate.enabled` is True; otherwise the hard-coded default."""
    from apps.identity.models import EmailTemplate
    tmpl = EmailTemplate.objects.filter(category=category, enabled=True).first()
    if tmpl is None:
        d = default_for(category)
        return _fmt(d["subject"], context), _fmt(d["body"], context)
    return tmpl.render(context)


def _fmt(s: str, ctx: dict) -> str:
    class _SafeDict(dict):
        def __missing__(self, key):
            return ""
    try:
        return s.format_map(_SafeDict(ctx))
    except (ValueError, IndexError):
        return s


def ensure_seeded() -> int:
    """Create any `EmailTemplate` rows that don't yet exist, from the DEFAULTS
    table. Returns the number created. Safe to call on every start-up — it only
    inserts rows that are missing."""
    from apps.identity.models import EmailTemplate
    created = 0
    for category, d in DEFAULTS.items():
        _, is_new = EmailTemplate.objects.get_or_create(
            category=category,
            defaults={
                "name": d["name"],
                "subject": d["subject"],
                "body": d["body"],
                "variables_help": d["variables_help"],
                "enabled": True,
            },
        )
        if is_new:
            created += 1
    return created
