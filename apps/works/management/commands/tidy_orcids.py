"""Store every ORCID in the one shape an ORCID has.

The imported records hold `https://orcid.org/0000-…`, bare `0000-…`, a stray
`https://ocrid.org/…`, and values with spaces around them. That was invisible while
nothing read them; the first time another system did, it refused the lot — its column is
exactly nineteen characters, which is exactly an ORCID.

So they are tidied where they live rather than patched at every place that reads them.
Anything that is not an ORCID at all is left untouched and counted: it is somebody's
typing, and this command does not get to decide it is rubbish.
"""

from __future__ import annotations

import collections

from django.core.management.base import BaseCommand

from apps.profiles.models import Profile
from apps.works.orcid import normalise_id


class Command(BaseCommand):
    help = "Normalise every stored ORCID to 0000-0000-0000-000X."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        counts: collections.Counter = collections.Counter()
        for profile in Profile.objects.exclude(orcid="").iterator():
            raw = profile.orcid
            clean = normalise_id(raw)
            if not clean:
                counts["not an ORCID at all - left alone"] += 1
                self.stdout.write(f"  left: {raw[:60]!r}")
                continue
            if clean == raw:
                counts["already tidy"] += 1
                continue
            counts["tidied"] += 1
            if not options["dry_run"]:
                profile.orcid = clean
                profile.save(update_fields=["orcid", "updated_at"])

        self.stdout.write("")
        for key, value in sorted(counts.items()):
            self.stdout.write(f"  {key:38} {value}")
        if options["dry_run"]:
            self.stdout.write("\nNothing was changed - this was a dry run.")
