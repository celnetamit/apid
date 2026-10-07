"""The whole migration, in the one order that is correct.

Written after getting it wrong. `import_profiles` stores what the form stored — a
WordPress attachment id — and `resolve_files` turns those into paths. Running the
import again afterwards puts the ids back, and 3,685 profile pictures quietly went
back to being numbers. Nothing failed; the census was green; every picture on the site
was a 404.

So the order stops being something to remember:

    members → profiles → cv sections → editorial → fetch the offloaded media → resolve the files

Idempotent end to end, which is the point — this runs nightly against a fresh snapshot
until cutover, and again on the morning of it.
"""

from __future__ import annotations

import time

from django.core.management import call_command
from django.core.management.base import BaseCommand

#: Order matters in exactly one place, and it is the last two: the media has to be
#: on disk before anything checks whether a file exists, and the resolve has to come
#: after every import that writes an attachment id.
STEPS = [
    ("members", "import_members", {}),
    ("profiles, publications, biographies", "import_profiles", {}),
    ("awards, conferences, projects, career history", "import_cv_sections", {}),
    ("role claims, reviewed papers, help-desk history", "import_member_history", {}),
    ("journals, applications, appointments", "import_editorial", {}),
    ("media WordPress moved to Google Cloud", "fetch_offloaded", {}),
    ("attachment ids → file paths", "resolve_files", {}),
]


class Command(BaseCommand):
    help = "Run the whole migration in the correct order (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--skip-media", action="store_true",
                            help="Leave the 15,092 files alone; everything else runs.")

    def handle(self, *args, **options):
        started = time.time()
        for label, command, kwargs in STEPS:
            if options["skip_media"] and command in ("fetch_offloaded", "resolve_files"):
                self.stdout.write(self.style.WARNING(f"— skipped: {label}"))
                continue
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(f"→ {label}"))
            call_command(command, **kwargs)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"migration finished in {time.time() - started:.0f}s"))
