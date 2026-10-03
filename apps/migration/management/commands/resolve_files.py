"""Turn the attachment ids the forms store into files that exist.

A Formidable file field does not hold a path — it holds a WordPress attachment id, and
the path lives in `postmeta._wp_attached_file` on that attachment. The import stores
what the form stored, so a profile picture arrives as `14954`; this resolves those to
`formidable/50/….jpg` and, crucially, **checks that the file is actually in the
snapshot**.

That last part is the point. 15,092 attachments have a path; 34,942 files came across
in the rsync, because WordPress writes several sized copies of every image. A resolver
that only rewrote the id would look finished and leave broken pictures on the new site
for somebody to find later.
"""

from __future__ import annotations

import collections
import os

from django.core.management.base import BaseCommand

from apps.migration.reader import WordPress
from apps.profiles.models import Profile

UPLOADS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "..", "snapshot",
    "uploads")


class Command(BaseCommand):
    help = "Resolve profile picture and affiliation logo ids to real file paths."

    def add_arguments(self, parser):
        parser.add_argument("--uploads", default=os.path.normpath(UPLOADS),
                            help="Where the copied wp-content/uploads tree is.")

    def handle(self, *args, **options):
        uploads = options["uploads"]
        if not os.path.isdir(uploads):
            raise SystemExit(f"No uploads tree at {uploads}")

        wp = WordPress()
        try:
            rows = wp._rows(
                "SELECT post_id, meta_value FROM wpapid_postmeta "
                "WHERE meta_key='_wp_attached_file'")
        finally:
            wp.close()
        paths = {str(r["post_id"]): r["meta_value"] for r in rows}
        self.stdout.write(f"attachments with a path: {len(paths)}")

        counts: collections.Counter = collections.Counter()
        for profile in Profile.objects.exclude(picture="").iterator():
            counts["picture ids"] += 1
            self._resolve(profile, "picture", paths, uploads, counts)
        for profile in Profile.objects.exclude(affiliation_logo="").iterator():
            counts["logo ids"] += 1
            self._resolve(profile, "affiliation_logo", paths, uploads, counts)

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("census"))
        for key, value in sorted(counts.items()):
            self.stdout.write(f"  {key:34} {value}")

    def _resolve(self, profile, field: str, paths, uploads, counts) -> None:
        value = (getattr(profile, field) or "").strip()
        if "/" in value:
            if value.startswith(("wp/", "members/")):
                counts[f"{field}: already a path"] += 1
                return
            # A path from before the media split, when the snapshot's uploads tree was
            # itself the media root. Moved under `wp/` here rather than by a one-off
            # script, so a database restored from any older dump heals on the next run
            # instead of serving 3,541 broken pictures.
            setattr(profile, field, ("wp/" + value)[:500])
            profile.save(update_fields=[field, "updated_at"])
            counts[f"{field}: moved under wp/"] += 1
            return
        if not value.isdigit():
            counts[f"{field}: not an id"] += 1
            return
        path = paths.get(value)
        if not path:
            # The attachment row is gone — the id points at nothing. Left as it is
            # rather than blanked, so the number is still there for anyone tracing it.
            counts[f"{field}: attachment missing"] += 1
            return
        if not os.path.exists(os.path.join(uploads, path)):
            counts[f"{field}: file not in the snapshot"] += 1
            return
        # Stored under `wp/`, which is a symlink to the snapshot's uploads tree. The
        # prefix is not cosmetic: member uploads live beside it under `members/`, and
        # the snapshot is re-rsynced (with --delete) until cutover. Sharing one
        # directory would mean a routine re-sync quietly removing a picture a member
        # had just uploaded — the news.nolege.in lesson, applied before it happened.
        setattr(profile, field, ("wp/" + path)[:500])
        profile.save(update_fields=[field, "updated_at"])
        counts[f"{field}: resolved"] += 1
