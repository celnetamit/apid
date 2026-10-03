"""Bring a member's published work across from ORCID.

Amit, 17 Sep 2026: *"agar user ORCID add karta hai toh usse bhi uska platform sync kara
do."*

**No key, no account, no OAuth.** ORCID's public API answers `pub.orcid.org/v3.0/<iD>/
works` for any public record, which is what almost every academic's record is. Sign-in
with ORCID is a separate thing and needs a client from ORCID; it is not needed to read
somebody's works, and waiting for it would have held this up for nothing.

**Offered, never imported.** This is the rule that matters and it comes from what an
ORCID record actually contains: everything any publisher ever deposited against that
iD, including the same paper three times from three sources, and occasionally somebody
else's paper from a mis-keyed deposit. A registry profile is a list its owner stands
behind. So the works arrive as a list with tick boxes, the member chooses, and nothing
appears on a profile because a machine fetched it.

**Already-listed works are matched and not offered again.** By DOI where there is one —
the only identifier that is actually an identifier — and otherwise by a normalised
title, so a second run does not offer somebody the twenty papers they imported an hour
ago.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

PUBLIC_API = "https://pub.orcid.org/v3.0/{orcid}/works"
TIMEOUT = 20

#: Anything not a letter or a digit. Two records of one paper differ by punctuation,
#: capitalisation and the odd non-breaking space, and by nothing else.
_LOOSE = re.compile(r"[^a-z0-9]+")


def normalise(title: str) -> str:
    return _LOOSE.sub("", (title or "").lower())[:120]


#: The sixteen characters of an ORCID, however they were typed around it. The imported
#: records hold `https://orcid.org/0000-…`, bare `0000-…`, and one `https://ocrid.org/…`
#: — a typo that a prefix-stripping rule would have carried straight through.
_ANYWHERE = re.compile(r"(\d{4})\D?(\d{4})\D?(\d{4})\D?(\d{3}[\dXx])")


def normalise_id(value: str) -> str:
    """`0000-0000-0000-000X`, or `""` if there is no ORCID in there at all.

    Written after manuscript-ngine refused the first backfill with "value too long for
    character varying(19)": its column is exactly an ORCID wide, and this registry's
    imported values are mostly full URLs. Pulling the digits out of whatever was typed
    is the only rule that survives a misspelled domain.
    """
    m = _ANYWHERE.search((value or "").strip())
    if not m:
        return ""
    return "-".join([m.group(1), m.group(2), m.group(3), m.group(4).upper()])


def looks_like_orcid(value: str) -> bool:
    return bool(normalise_id(value))


def fetch(orcid: str, opener=urllib.request.urlopen) -> List[Dict[str, Any]]:
    """Every work on the public record, flattened. Raises nothing — an empty list is
    "we could not read it", and the caller says so rather than showing a stack trace."""
    clean = normalise_id(orcid)
    if not clean:
        return []
    request = urllib.request.Request(
        PUBLIC_API.format(orcid=clean),
        headers={"Accept": "application/json",
                 "User-Agent": "celnet-apid/1.0 (+https://apid.celnet.in)"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return []

    works = []
    for group in payload.get("group") or []:
        # ORCID groups the duplicate deposits of one work together. The summary this
        # takes is the first of the group, which is what ORCID itself displays — the
        # rest are the same paper from another publisher's feed.
        summaries = group.get("work-summary") or []
        if not summaries:
            continue
        works.append(_one(summaries[0], group))
    return [w for w in works if w["title"]]


def _one(summary: Dict[str, Any], group: Dict[str, Any]) -> Dict[str, Any]:
    title = (((summary.get("title") or {}).get("title") or {}).get("value") or "").strip()
    year = (((summary.get("publication-date") or {}).get("year") or {})
            .get("value") or "")
    journal = ((summary.get("journal-title") or {}).get("value") or "").strip()
    doi = ""
    link = ""
    for external in ((group.get("external-ids") or {}).get("external-id") or []):
        kind = (external.get("external-id-type") or "").lower()
        value = (external.get("external-id-value") or "").strip()
        if kind == "doi" and not doi:
            doi = value
        if not link:
            link = ((external.get("external-id-url") or {}).get("value") or "")
    return {"title": title, "year": str(year), "journal": journal,
            "doi": doi, "link": link, "type": summary.get("type") or ""}


def new_for(member, works: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The works this member does not already have listed."""
    have_doi = {p.doi.lower().strip() for p in member.publications.all() if p.doi}
    have_title = {normalise(p.title) for p in member.publications.all()}
    fresh = []
    for work in works:
        if work["doi"] and work["doi"].lower() in have_doi:
            continue
        if normalise(work["title"]) in have_title:
            continue
        fresh.append(work)
    return fresh
