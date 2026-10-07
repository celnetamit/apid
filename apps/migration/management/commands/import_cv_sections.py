"""Awards, conferences, projects and career history.

Four sections of the live profile that had no home here: Honors & Awards (234
members), Add new conference (152 entries), Add New Project (136) and Career Timeline
(95 members).

Two of them are not what they look like. Honors & Awards and Career Timeline are
*container* forms — one entry per member holding nothing but a user id — and the rows
a member actually sees live in a child form, linked by `parent_item_id`. Importing the
parent forms gives 329 empty entries and none of the content, which is what the form
list suggests and not what is stored. The member comes from the child's own `user_id`,
or failing that its parent's.

Career Timeline's *Role* is a lookup into the Job Type Master form, so it holds an
entry id, not a role. It is resolved to the text; an id that no longer resolves is
reported rather than stored as a number.
"""

from __future__ import annotations

import collections
import datetime as dt

from django.core.management.base import BaseCommand

from apps.identity.models import Member
from apps.migration.reader import WordPress, first, joined
from apps.profiles.models import Award, CareerPosition, Conference, Project

AWARDS_PARENT, AWARDS = 72, 79
CAREER_PARENT, CAREER = 70, 71
CONFERENCES = 84
PROJECTS = 85
JOB_TYPE_MASTER_FIELD = 662   # the "Job Type" text on Job Type Master (form 81)


def day(raw: str):
    """A Formidable date (`2020-01-16`), or None. Never a guess at anything else."""
    try:
        return dt.date.fromisoformat(first(raw).strip()[:10])
    except ValueError:
        return None


def text(entry, key: str, limit: int | None = None) -> str:
    value = first(entry.get(key)).strip()
    return value[:limit] if limit else value


class Command(BaseCommand):
    help = "Import awards, conferences, projects and career history (idempotent)."

    def handle(self, *args, **options):
        wp = WordPress()
        try:
            members = Member.objects.in_bulk()
            counts: collections.Counter = collections.Counter()
            self._awards(wp, members, counts)
            self._career(wp, members, counts)
            self._conferences(wp, members, counts)
            self._projects(wp, members, counts)
        finally:
            wp.close()
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("census"))
        for key, value in sorted(counts.items()):
            self.stdout.write(f"  {key:46} {value}")
        for model in (Award, CareerPosition, Conference, Project):
            self.stdout.write(f"  {model.__name__ + 's in the database':46} "
                              f"{model.objects.count()}")

    # --- who an entry belongs to ---------------------------------------------

    def _owner(self, wp, entry, parents, members, counts, label):
        uid = entry.user_id or parents.get(entry.id)
        if uid is None:
            counts[f"{label}: entry with no member"] += 1
            return None
        if uid not in members:
            counts[f"{label}: entry for a deleted account"] += 1
            return None
        return members[uid]

    def _parent_owners(self, wp, child_form: int, parent_form: int) -> dict[int, int]:
        """`{child entry id: member id}` through the container entry, for children that
        were saved without a user id of their own."""
        owners = {r["id"]: r["user_id"] for r in wp._rows(
            f"SELECT id, user_id FROM {wp.prefix}frm_items WHERE form_id=%s",
            (parent_form,)) if r["user_id"]}
        return {r["id"]: owners[r["parent_item_id"]] for r in wp._rows(
            f"SELECT id, parent_item_id FROM {wp.prefix}frm_items WHERE form_id=%s",
            (child_form,)) if r["parent_item_id"] in owners}

    # --- sections ------------------------------------------------------------

    def _awards(self, wp, members, counts) -> None:
        parents = self._parent_owners(wp, AWARDS, AWARDS_PARENT)
        for entry in wp.entries(AWARDS):
            counts["awards: entries"] += 1
            member = self._owner(wp, entry, parents, members, counts, "awards")
            if member is None:
                continue
            name = text(entry, "lqi0x", 255)
            if not name:
                counts["awards: entry with no name"] += 1
                continue
            Award.objects.update_or_create(wp_entry_id=entry.id, defaults={
                "member": member, "name": name,
                "institution": text(entry, "bixew", 255),
                "description": text(entry, "scxa1"),
                "awarded_on": day(entry.get("248f9"))})
            counts["awards: imported"] += 1

    def _career(self, wp, members, counts) -> None:
        parents = self._parent_owners(wp, CAREER, CAREER_PARENT)
        roles = {str(r["item_id"]): r["meta_value"] for r in wp._rows(
            f"SELECT item_id, meta_value FROM {wp.prefix}frm_item_metas WHERE field_id=%s",
            (JOB_TYPE_MASTER_FIELD,))}
        for entry in wp.entries(CAREER):
            counts["career: entries"] += 1
            member = self._owner(wp, entry, parents, members, counts, "career")
            if member is None:
                continue
            organisation = text(entry, "jlrms", 255)
            if not organisation:
                counts["career: entry with no organisation"] += 1
                continue
            raw_role = text(entry, "3hxjx")
            role = roles.get(raw_role, "" if raw_role.isdigit() else raw_role)
            if raw_role and not role:
                counts["career: role id that no longer resolves"] += 1
            current = text(entry, "jpc0p").lower() == "yes"
            CareerPosition.objects.update_or_create(wp_entry_id=entry.id, defaults={
                "member": member, "organisation": organisation, "role": role[:255],
                "location": text(entry, "rqniw", 255),
                "started_on": day(entry.get("p7b4d")),
                # "Present" lives in the free-text twin of the end date; only a real
                # date is an end date.
                "ended_on": None if current else (day(entry.get("80hwj"))
                                                  or day(entry.get("8yt68"))),
                "is_current": current})
            counts["career: imported"] += 1

    def _conferences(self, wp, members, counts) -> None:
        for entry in wp.entries(CONFERENCES):
            counts["conferences: entries"] += 1
            member = self._owner(wp, entry, {}, members, counts, "conferences")
            if member is None:
                continue
            name = text(entry, "u9qtn", 255)
            if not name:
                counts["conferences: entry with no name"] += 1
                continue
            Conference.objects.update_or_create(wp_entry_id=entry.id, defaults={
                "member": member, "name": name,
                "url": text(entry, "hpjw7", 500),
                "organizer": text(entry, "h7ueb", 255),
                "starts_on": day(entry.get("c4yj5")), "ends_on": day(entry.get("54xgl")),
                "location": text(entry, "amq66", 255),
                "roles": joined(entry.get("knua3"), ", ")[:255]})
            counts["conferences: imported"] += 1

    def _projects(self, wp, members, counts) -> None:
        for entry in wp.entries(PROJECTS):
            counts["projects: entries"] += 1
            member = self._owner(wp, entry, {}, members, counts, "projects")
            if member is None:
                continue
            title = text(entry, "9q6vz", 255)
            if not title:
                counts["projects: entry with no title"] += 1
                continue
            Project.objects.update_or_create(wp_entry_id=entry.id, defaults={
                "member": member, "title": title, "goal": text(entry, "dn8om"),
                "stage": text(entry, "fel6h", 120),
                "started_on": day(entry.get("cmr1z")),
                "sponsor": text(entry, "alf0r", 255)})
            counts["projects: imported"] += 1
