"""Profiles, biographies, pictures and publications.

Four forms, one member. Each is imported on its own terms and each is counted, because
they disagree with each other: 5,119 profiles, 1,270 biographies, 4,996 pictures and
6,421 publications across two forms, spread over 13,412 members. A member with no
profile is not a failed import — most members have never filled one in — and the only
way to tell that from a broken mapping is to count both sides.

Where a member has filled the same form twice, the newest entry wins and the earlier
ones are counted as superseded. That is a decision, not an accident: the live site
shows the newest too.
"""

from __future__ import annotations

import collections

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.identity.models import Member
from apps.migration.reader import WordPress, first
from apps.profiles.models import LegacyProfileLink, Profile
from apps.works.models import Publication

BASIC_PROFILE = 69
BIOGRAPHY = 82
PICTURE = 62
PUBLICATIONS = 227
MY_PUBLICATIONS = 66

#: Basic Profile (form 69) → Profile. Read off the live field list; a key that is not
#: here is a field we chose not to carry, and a key here that the form no longer has
#: is reported by `--check` rather than silently mapping to nothing.
PROFILE_FIELDS = {
    "gender": "bnibb",
    "profession": "xwsey",
    "affiliation": "chvtl",
    "affiliation_url": "f54rm",
    "affiliation_logo": "42ipm",
    "department": "ace0l",
    "designation": "kaaz6",
    "experience_years": "ym3b3",
    "institutional_profile_url": "snc78",
    "city": "j7dob",
    "state": "agw3a",
    "country": "rq3wo",
    "pincode": "bjkf3",
    "academic_qualification": "l6y4d",
    "academic_university": "28na2",
    "qualification_year": "m00oh",
    "expertise": "9sisd",
    "areas_of_interest": "bwar3",
    "orcid": "c8k13",
    "google_scholar_id": "oevj1",
    "researchgate_id": "3euep",
    "ssrn_id": "fv1si",
    "scopus_id": "wys86",
}

#: A designation or country that the newer select left blank is often still in the
#: older hidden field beside it. Falling back is why 5,119 profiles do not come out
#: with 900 empty designations.
FALLBACKS = {"designation": "b1wri", "country": "59sy3"}

USER_KEYS = ("lqz5u", "zah2u3", "3uv1p2", "xwisc")   # the user_id field, per form

TEXT_LIMITS = {
    "gender": 32, "profession": 120, "affiliation": 255, "affiliation_url": 500,
    "affiliation_logo": 500, "department": 255, "designation": 160,
    "experience_years": 40, "institutional_profile_url": 500, "city": 120,
    "state": 120, "country": 120, "pincode": 20, "academic_qualification": 160,
    "academic_university": 255, "qualification_year": 10, "orcid": 40,
    "google_scholar_id": 80, "researchgate_id": 120, "ssrn_id": 80, "scopus_id": 80,
}


def member_id_of(entry) -> int | None:
    """Which member an entry belongs to.

    `frm_items.user_id` is set when the form was submitted while signed in, and the
    forms also carry the id in a `user_id` field — filled in by the site itself. Both
    are checked because neither is always there.
    """
    if entry.user_id:
        return entry.user_id
    for key in USER_KEYS:
        raw = first(entry.get(key)).strip()
        if raw.isdigit():
            return int(raw)
    return None


class Command(BaseCommand):
    help = "Import profiles, biographies, pictures and publications."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true",
                            help="Report the field mapping against the live form and "
                                 "import nothing.")

    def handle(self, *args, **options):
        wp = WordPress()
        try:
            if options["check"]:
                self._check(wp)
                return
            members = {m.wp_user_id: m for m in Member.objects.all()}
            self.stdout.write(f"members in the database: {len(members)}")
            counts: collections.Counter = collections.Counter()
            self._profiles(wp, members, counts)
            self._simple(wp, members, counts, BIOGRAPHY, "biography", "g3ky3")
            self._simple(wp, members, counts, PICTURE, "picture", "hi9zl2")
            self._publications(wp, members, counts)
            self._report(counts)
        finally:
            wp.close()

    # --- the mapping, checked against the live form --------------------------

    def _check(self, wp: WordPress) -> None:
        """Every key we map must exist on the form. A form somebody edits is the whole
        reason this command exists — mng lost a month of submissions to exactly that."""
        fields = wp.fields(BASIC_PROFILE)
        missing = {name: key for name, key in PROFILE_FIELDS.items()
                   if key not in fields}
        for name, key in FALLBACKS.items():
            if key not in fields:
                missing[f"{name} (fallback)"] = key
        if missing:
            self.stdout.write(self.style.ERROR(
                f"  {len(missing)} mapped keys are not on form {BASIC_PROFILE}: {missing}"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"  all {len(PROFILE_FIELDS)} mapped keys exist on form {BASIC_PROFILE}"))
        unmapped = [f"{k} ({v['name'][:36]})" for k, v in fields.items()
                    if k not in set(PROFILE_FIELDS.values()) | set(FALLBACKS.values())
                    and v["type"] not in ("divider", "end_divider", "html", "submit",
                                          "break", "user_id", "form", "hidden")]
        self.stdout.write(f"  {len(unmapped)} fields on the form are not carried:")
        for item in unmapped:
            self.stdout.write(f"      {item}")

    # --- profiles ------------------------------------------------------------

    def _profiles(self, wp, members, counts) -> None:
        latest: dict[int, object] = {}
        all_entries: list[tuple[int, int]] = []
        for entry in wp.entries(BASIC_PROFILE):
            counts["profile entries"] += 1
            uid = member_id_of(entry)
            if uid is None:
                counts["profile entry with no member"] += 1
                continue
            if uid not in members:
                counts["profile entry for a deleted account"] += 1
                continue
            if uid in latest:
                counts["profile superseded by a later entry"] += 1
            latest[uid] = entry
            # Every entry id is a public address somebody may have written down —
            # including the superseded ones, which the old site itself no longer
            # shows. All of them are remembered so all of them can redirect.
            all_entries.append((uid, entry.id))

        for uid, entry in latest.items():
            values = {}
            for name, key in PROFILE_FIELDS.items():
                value = first(entry.get(key)).strip()
                if not value and name in FALLBACKS:
                    value = first(entry.get(FALLBACKS[name])).strip()
                limit = TEXT_LIMITS.get(name)
                values[name] = value[:limit] if limit else value
            wants = first(entry.get("pe06z")).strip().lower()
            values["wants_editorial_board"] = (
                True if wants.startswith("yes") else False if wants else None)

            with transaction.atomic():
                Profile.objects.update_or_create(
                    member=members[uid],
                    defaults={**values, "wp_entry_id": entry.id,
                              "source_updated_at": entry.updated_at or entry.created_at},
                )
            counts["profiles imported"] += 1

        for uid, entry_id in all_entries:
            LegacyProfileLink.objects.update_or_create(
                wp_entry_id=entry_id,
                defaults={"member": members[uid], "form_id": BASIC_PROFILE,
                          "superseded": latest[uid].id != entry_id})
            counts["legacy profile links"] += 1

    # --- one-field forms -----------------------------------------------------

    def _simple(self, wp, members, counts, form_id: int, field: str, key: str) -> None:
        latest: dict[int, object] = {}
        for entry in wp.entries(form_id):
            counts[f"{field} entries"] += 1
            uid = member_id_of(entry)
            if uid in members:
                latest[uid] = entry
            elif uid is None:
                counts[f"{field} entry with no member"] += 1
            else:
                counts[f"{field} entry for a deleted account"] += 1

        for uid, entry in latest.items():
            value = first(entry.get(key)).strip()
            if not value:
                counts[f"{field} entry that is empty"] += 1
                continue
            profile, _ = Profile.objects.get_or_create(member=members[uid])
            setattr(profile, field, value[:500] if field == "picture" else value)
            profile.save(update_fields=[field, "updated_at"])
            counts[f"{field} imported"] += 1

    # --- publications --------------------------------------------------------

    def _publications(self, wp, members, counts) -> None:
        for form_id, title_key, link_key in (
                (PUBLICATIONS, "x27xt", "v3s9w"), (MY_PUBLICATIONS, None, None)):
            fields = wp.fields(form_id)
            if title_key is None:
                # The older form's keys are not the newer one's; find them by name
                # rather than hard-coding a guess.
                title_key = next((k for k, f in fields.items()
                                  if "title" in f["name"].lower()), None)
                link_key = next((k for k, f in fields.items()
                                 if "link" in f["name"].lower() or "url" in f["name"].lower()),
                                None)
                if not title_key:
                    counts[f"form {form_id}: no title field found"] += 1
                    continue

            for entry in wp.entries(form_id):
                counts[f"publication entries (form {form_id})"] += 1
                uid = member_id_of(entry)
                if uid not in members:
                    counts["publication for a deleted or unknown account"] += 1
                    continue
                title = first(entry.get(title_key)).strip()
                if not title:
                    counts["publication with no title"] += 1
                    continue
                Publication.objects.update_or_create(
                    wp_entry_id=entry.id,
                    defaults={
                        "member": members[uid],
                        "title": title,
                        "link": first(entry.get(link_key)).strip()[:1000] if link_key else "",
                        "wp_form_id": form_id,
                        "created_at": entry.created_at,
                    })
                counts["publications imported"] += 1

    def _report(self, counts) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("census"))
        for key, value in sorted(counts.items()):
            self.stdout.write(f"  {key:42} {value}")
        self.stdout.write("")
        self.stdout.write(f"  profiles in the database     {Profile.objects.count()}")
        self.stdout.write(f"  publications in the database {Publication.objects.count()}")
