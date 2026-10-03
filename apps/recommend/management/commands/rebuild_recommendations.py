"""Rebuild every member's neighbours and journal suggestions.

Runs over the whole registry and writes the top twenty people and top five journals per
member. Intended nightly; it is idempotent and safe to run at any time, and it reports
what it could not do rather than finishing quietly.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.editorial.models import Journal
from apps.identity.models import Member
from apps.profiles.models import Profile
from apps.recommend import similarity
from apps.recommend.models import SimilarMember, SuggestedJournal
from apps.works.models import Publication

PEOPLE = 20
JOURNALS = 5


class Command(BaseCommand):
    help = "Recompute similar members and suggested journals for everybody."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0,
                            help="Only the first N members, for a quick check.")

    def handle(self, *args, **options):
        started = time.time()

        # One document per member: their own words, and the titles of their own work.
        # Publication titles matter more than anything on the form — a title is what
        # somebody actually did, and it is written in the vocabulary of their field.
        words = {}
        for profile in Profile.objects.select_related("member").iterator():
            words[profile.member_id] = " ".join([
                profile.expertise or "", profile.areas_of_interest or "",
                profile.department or "", profile.designation or "",
                profile.academic_qualification or "", profile.biography or "",
                profile.affiliation or ""])
        for member_id, title in Publication.objects.values_list("member_id", "title"):
            if member_id in words:
                words[member_id] += " " + (title or "")

        docs = {k: similarity.tokens(v) for k, v in words.items()}
        docs = {k: v for k, v in docs.items() if len(v) >= 3}
        self.stdout.write(f"members with enough words: {len(docs)}")

        journals = {j.pk: similarity.tokens(
            " ".join([j.title or "", j.subject or "", j.abbreviation or ""]))
            for j in Journal.objects.all()}
        journals = {k: v for k, v in journals.items() if v}

        idf = similarity.build_idf(list(docs.values()) + list(journals.values()))
        vectors = {k: similarity.vector(v, idf) for k, v in docs.items()}
        journal_vectors = {k: similarity.vector(v, idf) for k, v in journals.items()}
        index = similarity.invert(vectors)
        journal_index = similarity.invert(journal_vectors)

        wanted = list(vectors)
        if options["limit"]:
            wanted = wanted[:options["limit"]]

        made_people = made_journals = 0
        for n, member_id in enumerate(wanted, start=1):
            target = vectors[member_id]
            people = similarity.nearest(target, vectors, index, PEOPLE, skip=[member_id])
            picks = similarity.nearest(target, journal_vectors, journal_index, JOURNALS)
            with transaction.atomic():
                SimilarMember.objects.filter(member_id=member_id).delete()
                SimilarMember.objects.bulk_create([
                    SimilarMember(member_id=member_id, other_id=other, score=score,
                                  rank=rank,
                                  because=self._because(target, vectors[other]))
                    for rank, (other, score) in enumerate(people)])
                SuggestedJournal.objects.filter(member_id=member_id).delete()
                SuggestedJournal.objects.bulk_create([
                    SuggestedJournal(member_id=member_id, journal_id=journal,
                                     score=score, rank=rank,
                                     because=self._because(target,
                                                           journal_vectors[journal]))
                    for rank, (journal, score) in enumerate(picks)])
            made_people += len(people)
            made_journals += len(picks)
            if n % 1000 == 0:
                self.stdout.write(f"  {n}/{len(wanted)}…")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("census"))
        self.stdout.write(f"  members processed            {len(wanted)}")
        self.stdout.write(f"  people recommendations       {made_people}")
        self.stdout.write(f"  journal suggestions          {made_journals}")
        # Counted over the members this run actually processed. Comparing against every
        # member with a stored recommendation gave "-4362" on a --limit run — a census
        # that can go negative is not measuring what it says.
        with_someone = (SimilarMember.objects.filter(member_id__in=wanted)
                        .values("member").distinct().count())
        self.stdout.write(f"  members with nobody to show  "
                          f"{len(wanted) - with_someone}")
        self.stdout.write(self.style.SUCCESS(
            f"finished in {time.time() - started:.0f}s"))

    @staticmethod
    def _because(a, b, most: int = 3) -> str:
        """The words the two have most strongly in common — the reason, in their own
        vocabulary."""
        shared = sorted(((a[w] * b.get(w, 0.0), w) for w in a if w in b), reverse=True)
        return ", ".join(word for _, word in shared[:most])[:200]
