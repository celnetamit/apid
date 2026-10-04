"""The academic profile: who a member is professionally, and where they can be found.

Every field here exists on the live Basic Profile form (id 69, 5,119 entries). What is
different is the shape. Formidable keeps one row per field per entry; a profile that
somebody edited four times is four entries and nobody can say which is current without
looking at the timestamps. Here a member has one profile, and its history is the
revision trail rather than a pile of entries.
"""

from __future__ import annotations

import re

from django.db import models

from apps.identity.models import Member

_ORCID_ID_RE = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dXx]")
_SCHOLAR_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,16}$")


class Profile(models.Model):
    """One per member. Created empty at import when the member never filled it in —
    5,119 of 13,412 members have one, and a missing profile is a fact about them, not
    a gap in the import."""

    member = models.OneToOneField(Member, on_delete=models.CASCADE,
                                  related_name="profile")

    gender = models.CharField(max_length=32, blank=True)
    profession = models.CharField(max_length=120, blank=True)

    # --- where they work -----------------------------------------------------
    affiliation = models.CharField(max_length=255, blank=True)
    affiliation_url = models.URLField(max_length=500, blank=True)
    affiliation_logo = models.CharField(max_length=500, blank=True)
    department = models.CharField(max_length=255, blank=True)
    designation = models.CharField(max_length=160, blank=True)
    experience_years = models.CharField(max_length=40, blank=True)
    institutional_profile_url = models.URLField(max_length=500, blank=True)

    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, blank=True)
    pincode = models.CharField(max_length=20, blank=True)

    # --- what they know ------------------------------------------------------
    academic_qualification = models.CharField(max_length=160, blank=True)
    academic_university = models.CharField(max_length=255, blank=True)
    qualification_year = models.CharField(max_length=10, blank=True)
    expertise = models.TextField(blank=True)
    areas_of_interest = models.TextField(blank=True)
    biography = models.TextField(blank=True)

    picture = models.CharField(max_length=500, blank=True)

    # --- the identifiers other systems know them by --------------------------
    # Five of them on the live form, and ORCID is the one ce4 now prints above the
    # references of every paper — so these are a join to real published work, not
    # decoration.
    orcid = models.CharField(max_length=40, blank=True, db_index=True)
    google_scholar_id = models.CharField(max_length=80, blank=True)
    researchgate_id = models.CharField(max_length=120, blank=True)
    ssrn_id = models.CharField(max_length=80, blank=True)
    scopus_id = models.CharField(max_length=80, blank=True)

    wants_editorial_board = models.BooleanField(null=True)

    #: The Formidable entry this came from, so a re-import updates instead of
    #: duplicating, and so any row can be traced back to what it was made from.
    wp_entry_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"profile of {self.member.apid}"

    @property
    def is_complete(self) -> bool:
        """Enough to show publicly. Deliberately modest: a name and somewhere to
        place them."""
        return bool(self.affiliation and (self.department or self.designation))

    # ORCID + Scholar come in from the live form as a mix: bare IDs, full
    # URLs, parenthesised URLs ("(https://orcid.org/0000-0001-...)," and
    # outright junk like "#403microbiology"). The template shouldn't try to
    # build a URL from any of that — these two properties return a canonical
    # link only when the stored value parses, and an empty string otherwise so
    # the row is simply hidden.
    @property
    def orcid_url(self) -> str:
        v = (self.orcid or "").strip()
        if not v:
            return ""
        m = _ORCID_ID_RE.search(v)
        if m:
            return f"https://orcid.org/{m.group(0)}"
        return ""

    @property
    def orcid_display(self) -> str:
        v = (self.orcid or "").strip()
        m = _ORCID_ID_RE.search(v)
        return m.group(0) if m else ""

    @property
    def scholar_url(self) -> str:
        v = (self.google_scholar_id or "").strip()
        if not v:
            return ""
        if v.lower().startswith(("http://", "https://")):
            return v if "scholar.google." in v.lower() else ""
        if _SCHOLAR_ID_RE.match(v):
            return f"https://scholar.google.com/citations?user={v}&hl=en"
        return ""


class Qualification(models.Model):
    """A degree. The live site keeps these in a repeater form (id 4), so a member has
    several and they arrive as separate rows."""

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE,
                                related_name="qualifications")
    degree = models.CharField(max_length=160)
    institution = models.CharField(max_length=255, blank=True)
    year = models.CharField(max_length=10, blank=True)
    wp_entry_id = models.IntegerField(null=True, blank=True, db_index=True)

    def __str__(self) -> str:
        return f"{self.degree} ({self.year})" if self.year else self.degree


class LegacyProfileLink(models.Model):
    """Every address a member's profile has ever had on the old site.

    The public URL today is `/apid-profiles/apid/<id>/` where the id is the member's
    **Basic Profile entry** — not their account, and not a number anyone chose. Opening
    one proved it: `/apid-profiles/apid/85015/` is Pakiso Moses Makhoahle, whose account
    is 13517 and whose registration entry is 85009.

    That number is also not stable. A member who fills the profile form again gets a new
    entry and a new address, and 37 members have done exactly that — so some of the
    links in the wild point at entries the site itself no longer shows.

    The new site therefore keeps a stable APID of its own *and* remembers every old id,
    and every one of them redirects. A member who put their profile link in a paper
    three years ago must not find a 404 because we tidied the identity up.
    """

    member = models.ForeignKey(Member, on_delete=models.CASCADE,
                               related_name="legacy_links")
    wp_entry_id = models.IntegerField(unique=True, db_index=True)
    form_id = models.IntegerField()
    superseded = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"/apid-profiles/apid/{self.wp_entry_id}/ → {self.member.apid}"
