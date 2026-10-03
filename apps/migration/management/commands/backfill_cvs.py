"""Backfill `Application.cv` from the WordPress Formidable attachment files.

Form 159 (board applications) carried an "Upload CV" field (id 1278). The
value is a WP attachment post ID whose file sits under
`<oldapid-docroot>/wp-content/uploads/formidable/159/…`. The import of
applications preserved the Formidable entry id as `Application.wp_entry_id`
but not the CV file. This command walks the mapping, copies each file into
MEDIA_ROOT/applications/ and writes `Application.cv` to point at it.

Only applications without a current CV are touched — anything a user has
uploaded post-import is kept.

Usage:
  python manage.py backfill_cvs \
      --tsv /tmp/cv_map.tsv \
      --source /home/a23879271/AcademicProfiles/wp-content/uploads \
      [--dry-run]
"""
from __future__ import annotations

import collections
import os
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.editorial.models import Application


class Command(BaseCommand):
    help = "Copy Formidable 159 CV attachments into MEDIA_ROOT and link them."

    def add_arguments(self, parser):
        parser.add_argument("--tsv", required=True, type=Path,
                            help="TSV: entry_id<TAB>relative_path<TAB>mime")
        parser.add_argument("--source", required=True, type=Path,
                            help="Root of the oldapid wp-content/uploads/ tree.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *, tsv: Path, source: Path, dry_run: bool, **_) -> None:
        if not tsv.exists():
            raise CommandError(f"TSV not found: {tsv}")
        if not source.is_dir():
            raise CommandError(f"Source directory not found: {source}")

        media_root = Path(settings.MEDIA_ROOT)
        apps_dir = media_root / "applications"
        if not dry_run:
            apps_dir.mkdir(parents=True, exist_ok=True)

        mapping: dict[int, tuple[str, str]] = {}
        with tsv.open() as fp:
            for line in fp:
                parts = line.rstrip("\n").split("\t", 2)
                if len(parts) != 3:
                    continue
                eid_s, rel, mime = parts
                try:
                    eid = int(eid_s)
                except ValueError:
                    continue
                mapping[eid] = (rel, mime)
        self.stdout.write(f"Mapping rows: {len(mapping)}")

        qs = Application.objects.exclude(wp_entry_id__isnull=True)
        linked = kept = missing_src = no_match = failed = 0
        counts = collections.Counter()
        for app in qs.iterator():
            info = mapping.get(app.wp_entry_id)
            if not info:
                no_match += 1
                continue
            if app.cv:
                kept += 1
                continue
            rel, mime = info
            src = source / rel
            if not src.exists():
                missing_src += 1
                continue
            # Deterministic destination name to be idempotent across reruns.
            ext = src.suffix.lower() or ".jpg"
            dest_rel = f"applications/wp-{app.wp_entry_id}{ext}"
            dest_abs = media_root / dest_rel
            if dry_run:
                linked += 1
                counts[ext] += 1
                continue
            try:
                shutil.copy2(src, dest_abs)
                os.chmod(dest_abs, 0o644)
            except OSError as exc:
                failed += 1
                self.stdout.write(f"  COPY FAIL {src}: {exc}")
                continue
            app.cv = dest_rel
            app.save(update_fields=["cv"])
            linked += 1
            counts[ext] += 1

        prefix = "WOULD link" if dry_run else "Linked"
        self.stdout.write(
            f"\n{prefix}: {linked}\n"
            f"  already had a CV (kept):  {kept}\n"
            f"  Application not in map:   {no_match}\n"
            f"  source file missing:      {missing_src}\n"
            f"  copy failed:              {failed}")
        if counts:
            self.stdout.write("  by extension:")
            for ext, n in counts.most_common():
                self.stdout.write(f"    {ext:8} {n}")
