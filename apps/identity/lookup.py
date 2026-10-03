"""A read-only window onto the registry, for the other systems in the estate.

Measured before it was written: manuscript-ngine holds 6,453 accounts and 3,474
authorships, and **not one of them carries an ORCID** — the field exists on four of its
models and every one is empty. APID holds 2,154. 3,116 people are in both systems by
email, which is 48% of manuscript-ngine's accounts.

That matters beyond tidiness: manuscript-ngine is what deposits to Crossref, and an
ORCID on a deposit is what ties a paper to a person everywhere else in the world. The
data to do it has been sitting in a different database the whole time.

**Read-only, and narrow on purpose.** This answers one question — *what does the
registry know about this address* — and returns four fields. It cannot create, change
or delete anything, and it does not expose a member who has no ORCID and no profile any
differently from one who does not exist.

**Signed, not open.** The two services share a secret and the caller signs the request;
an unsigned or stale request is refused. Both run on this box and talk over the
loopback, so this is not protecting against the internet — it is making sure that the
day one of them moves, the link fails loudly rather than quietly answering anybody.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

from django.db.models.functions import Lower
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.identity.models import Member
from apps.works.orcid import normalise_id

#: Ten minutes. Long enough for a clock to be a little out, short enough that a
#: signature copied from a log is worthless.
MAX_AGE_SECONDS = 600

#: One call answers up to this many addresses. manuscript-ngine's backfill asks about
#: 6,453 accounts; in batches of 500 that is thirteen requests rather than 6,453.
MAX_BATCH = 500


def _secret() -> str:
    return os.environ.get("APID_LOOKUP_SECRET", "")


def sign(body: bytes, timestamp: str, secret: str = "") -> str:
    """The signature over the exact bytes sent, not over the parsed request.

    Signing the parsed form would let a caller change what a field means without
    changing the signature, which is the whole class of bug this avoids.
    """
    key = (secret or _secret()).encode()
    return hmac.new(key, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


@csrf_exempt
@require_POST
def members(request):
    """`{"emails": [...]}` → what the registry knows about each.

    Only addresses that match exactly one member are answered. Four addresses here are
    on more than one account, and returning either one would hand manuscript-ngine an
    ORCID that may belong to the other person.
    """
    if not _secret():
        return JsonResponse({"error": "lookup is not configured"}, status=503)

    timestamp = request.headers.get("X-APID-Timestamp", "")
    signature = request.headers.get("X-APID-Signature", "")
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > MAX_AGE_SECONDS:
        return JsonResponse({"error": "stale or missing timestamp"}, status=401)
    if not hmac.compare_digest(sign(request.body, timestamp), signature):
        return JsonResponse({"error": "bad signature"}, status=401)

    try:
        asked = json.loads(request.body or b"{}").get("emails") or []
    except ValueError:
        return JsonResponse({"error": "body is not JSON"}, status=400)
    if not isinstance(asked, list):
        return JsonResponse({"error": "emails must be a list"}, status=400)
    if len(asked) > MAX_BATCH:
        return JsonResponse({"error": f"at most {MAX_BATCH} addresses"}, status=413)

    wanted = {str(a).strip().lower() for a in asked if str(a).strip()}
    found: dict = {}
    duplicated: set = set()
    matches = (Member.objects.annotate(lower_email=Lower("email"))
               .filter(lower_email__in=wanted)
               .select_related("profile") if wanted else [])
    for member in matches:
        key = member.email.strip().lower()
        if key in found:
            # Two accounts on one address. Neither is answered: handing over the wrong
            # person's ORCID is worse than handing over none.
            duplicated.add(key)
            continue
        profile = getattr(member, "profile", None)
        found[key] = {
            "apid": member.apid,
            "name": member.full_name or member.display_name,
            # Normalised on the way out. The imported values are mostly full URLs
            # and one carries a misspelled domain; manuscript-ngine's column is exactly
            # an ORCID wide and refused the first backfill outright.
            "orcid": normalise_id(profile.orcid if profile else ""),
            "affiliation": (profile.affiliation if profile else "") or "",
        }
    for key in duplicated:
        found.pop(key, None)

    return JsonResponse({"members": found, "asked": len(wanted),
                         "ambiguous": sorted(duplicated)})
