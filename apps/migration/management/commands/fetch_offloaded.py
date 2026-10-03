"""Fetch the media WordPress moved off the server.

A third of the profile pictures are not on the web host at all: the site runs **WP
Stateless in `cdn` mode**, which uploads each file to Google Cloud Storage and deletes
the local copy. `rsync` of `wp-content/uploads` therefore returns a tree that looks
complete — 34,942 files — and is missing 1,327 profile pictures and 634 affiliation
logos, with nothing to say so. The resolver found them because it checked that each
file it named actually existed, which is the only reason this was noticed before
cutover rather than after.

The object name is in each attachment's `sm_cloud` metadata, and the bucket answers
unauthenticated GETs, so no credentials are needed — measured, not assumed:
`storage.googleapis.com/…/2023/07/logo_stm-1.png` returns 200 and 10,984 bytes, which
is the size the attachment's own metadata records.

Files are written into the same snapshot tree under their `_wp_attached_file` path, so
everything downstream sees one complete set of uploads and neither knows nor cares
which half came from where.
"""

from __future__ import annotations

import collections
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from django.core.management.base import BaseCommand

from apps.migration.reader import WordPress

USER_AGENT = "celnet-apid-migration/1.0 (+https://apid.celnet.in)"


def ascii_url(url: str) -> str:
    """A URL `urllib` can actually send.

    Thirteen of these files are named in Arabic, Chinese, Russian and Bengali —
    `جامعة-الملك-عبدالعزيز.png`, `微信图片_20230818152120.png` — and one carries a narrow
    no-break space. `urllib` raises `UnicodeEncodeError` on all of them, which is not
    an HTTP error and so escapes any `HTTPError` handler around it.

    This is the same bug that killed manuscript-ngine's WordPress import after ten of
    eighty-two records, and it is fixed the same way. `safe="/%"` matters: without the
    `%` an already-encoded path is encoded twice (`%20` becomes `%2520`), which does
    not raise — it just 404s, which is far harder to notice.
    """
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc,
        urllib.parse.quote(parts.path, safe="/%"),
        urllib.parse.quote(parts.query, safe="=&%"),
        parts.fragment))

#: `sm_cloud` is PHP-serialised. Only two values are wanted and both are plain
#: strings, so they are read directly rather than by unserialising the whole thing.
_NAME = re.compile(r's:4:"name";s:\d+:"(.*?)";')
_BUCKET = re.compile(r's:6:"bucket";s:\d+:"(.*?)";')


class Command(BaseCommand):
    help = "Download attachments that WP Stateless moved to Google Cloud Storage."

    def add_arguments(self, parser):
        parser.add_argument("--uploads", default=None,
                            help="The uploads tree to fill in.")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        uploads = options["uploads"] or os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "..", "..", "..", "snapshot", "uploads"))
        if not os.path.isdir(uploads):
            raise SystemExit(f"No uploads tree at {uploads}")

        wp = WordPress()
        try:
            paths = {r["post_id"]: r["meta_value"] for r in wp._rows(
                "SELECT post_id, meta_value FROM wpapid_postmeta "
                "WHERE meta_key='_wp_attached_file'")}
            clouds = {r["post_id"]: r["meta_value"] for r in wp._rows(
                "SELECT post_id, meta_value FROM wpapid_postmeta "
                "WHERE meta_key='sm_cloud'")}
        finally:
            wp.close()

        counts: collections.Counter = collections.Counter()
        wanted = []
        for post_id, path in paths.items():
            if os.path.exists(os.path.join(uploads, path)):
                counts["already local"] += 1
                continue
            cloud = clouds.get(post_id, "")
            name = _NAME.search(cloud)
            bucket = _BUCKET.search(cloud)
            if not (name and bucket):
                # No cloud record and no local file: the attachment row outlived its
                # file. Counted, never invented.
                counts["gone: no local file and no cloud record"] += 1
                continue
            wanted.append((post_id, path,
                           f"https://storage.googleapis.com/{bucket.group(1)}/"
                           f"{name.group(1)}"))

        self.stdout.write(f"attachments: {len(paths)}  |  to fetch: {len(wanted)}")
        if options["limit"]:
            wanted = wanted[:options["limit"]]
        if options["dry_run"]:
            for _, path, url in wanted[:5]:
                self.stdout.write(f"  would fetch {url} -> {path}")
            self._report(counts, len(wanted))
            return

        for post_id, path, url in wanted:
            target = os.path.join(uploads, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                request = urllib.request.Request(ascii_url(url),
                                                 headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read()
            except urllib.error.HTTPError as exc:
                counts[f"HTTP {exc.code}"] += 1
                continue
            except UnicodeEncodeError:
                # Kept separate from a network error on purpose: one is the site being
                # slow, the other is our own encoding and needs a code change.
                counts["name could not be encoded"] += 1
                continue
            except Exception:                                   # noqa: BLE001
                counts["network error"] += 1
                time.sleep(1)
                continue
            if not body:
                counts["empty response"] += 1
                continue
            # Written whole, then moved into place: a half-written picture that looks
            # like a file is worse than one that is plainly absent.
            tmp = target + ".part"
            with open(tmp, "wb") as handle:
                handle.write(body)
            os.replace(tmp, target)
            counts["fetched"] += 1

        self._report(counts, len(wanted))

    def _report(self, counts, wanted: int) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("census"))
        for key, value in sorted(counts.items()):
            self.stdout.write(f"  {key:44} {value}")
        got = counts.get("fetched", 0)
        if wanted and got < wanted:
            self.stdout.write(self.style.WARNING(
                f"  {wanted - got} of {wanted} did not come down — they are named in "
                f"the counts above, not hidden in a success message."))
