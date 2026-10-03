"""What happens after Google says who somebody is.

The protocol is in `google_oauth.py`. This is the part specific to the registry:

* **An unverified Google address signs nobody in.** An unverified address is a claim,
  not an identity, and here the address is what the account is matched on.
* **Exactly one existing account, or a new one.** If the email already matches a single
  member, that member is signed in. If it matches none, a new member is created from
  the Google claims (verified email, name) and signed in — Boss, 2026-10-03: anyone
  with a verified Google email is allowed. If the email matches two or more existing
  accounts (four such addresses carried over from the import), none of them is picked:
  guessing would hand somebody another person's editorial history and look like an
  ordinary sign-in.
* **Nothing is merged and nothing is guessed.** Not on name, not on affiliation.
"""

from __future__ import annotations

import logging

from django.db import transaction

from apps.identity.models import Member
from apps.identity.registration import next_apid
from apps.profiles.models import Profile

logger = logging.getLogger(__name__)

#: Returned to the view rather than raised: each of these is a sentence shown to a
#: person, and the view decides how to show it.
UNVERIFIED = "unverified"
AMBIGUOUS = "ambiguous"
INACTIVE = "inactive"
OFF_DOMAIN = "off_domain"

MESSAGES = {
    UNVERIFIED: ("Google has not verified that address, so it cannot be used to sign "
                 "in. Verify it with Google and try again."),
    AMBIGUOUS: ("More than one APID account has that email address, so we cannot tell "
                "which one is yours. The editorial office can merge them — please ask "
                "them, mentioning the address."),
    INACTIVE: "That account is not active. Please contact the editorial office.",
    OFF_DOMAIN: "That Google account is not allowed to sign in here.",
}


def member_for(claims: dict, domain_allowed) -> tuple[Member | None, str]:
    """`(member, reason)`. Exactly one is meaningful — a member, or a reason there
    isn't one."""
    email = (claims.get("email") or "").strip()
    if not claims.get("email_verified") or not email:
        return None, UNVERIFIED
    if not domain_allowed(email):
        return None, OFF_DOMAIN

    matches = list(Member.objects.filter(email__iexact=email)[:2])
    if len(matches) > 1:
        logger.info("google sign-in: %s matches %d accounts", email, len(matches))
        return None, AMBIGUOUS
    if matches:
        member = matches[0]
        if not member.is_active:
            return None, INACTIVE
        return member, ""

    return _create_from_google(claims, email), ""


@transaction.atomic
def _create_from_google(claims: dict, email: str) -> Member:
    """Mint a new member from a verified Google sign-in. Password is unusable: the
    only way back into this account is Google again (or an editorial-office reset)."""
    full_name = (claims.get("name")
                 or " ".join(filter(None, [claims.get("given_name"),
                                           claims.get("family_name")]))
                 or email.split("@")[0]).strip()
    # Same lock dance as RegistrationForm.create_member — two simultaneous
    # sign-ins must not be minted the same APID.
    Member.objects.select_for_update().filter(pk__in=[]).exists()
    username = email
    if Member.objects.filter(username__iexact=username).exists():
        username = f"g-{next_apid()}"
    apid = next_apid()
    member = Member(
        username=username,
        email=email,
        apid=apid,
        full_name=full_name,
        first_name=(claims.get("given_name") or "").strip()[:150],
        last_name=(claims.get("family_name") or "").strip()[:150],
    )
    member.set_unusable_password()
    member.save()
    Profile.objects.get_or_create(member=member)
    logger.info("google sign-in: created member %s (apid=%s)", email, apid)
    return member
