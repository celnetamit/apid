"""Content-based recommendation: the method, and why it is the right one here.

Collaborative filtering is not an option and the measurement is not close — 13,412
members, 176 follow edges, 26 members who follow two or more people, 3 pairs ever
followed together twice. What this registry has instead is 4,170 profiles with an
expertise and 6,409 publication titles, which is a thing Netflix does not have about
its films.
"""

from __future__ import annotations

from django.test import TestCase

from apps.editorial.models import Journal
from apps.identity.models import Member
from apps.profiles.models import Profile
from apps.recommend import similarity
from apps.recommend.models import SimilarMember, SuggestedJournal
from apps.works.models import Publication


class TheMethod(TestCase):

    def test_boilerplate_words_are_dropped(self):
        assert similarity.tokens("Research at the University of Things") == ["things"]

    def test_the_houses_own_name_is_not_a_field_of_study(self):
        """`celnet` was the reason two web developers were matched. It is where they
        work, not what they do."""
        assert "celnet" not in similarity.tokens("CELNET web developer")

    def test_a_rare_word_outweighs_a_common_one(self):
        docs = [["tribology", "wear"], ["wear", "steel"], ["wear", "iron"],
                ["wear", "copper"]]
        idf = similarity.build_idf(docs)
        assert idf["tribology"] > idf["wear"]

    def test_a_long_profile_does_not_beat_a_short_one_on_length(self):
        """Unnormalised, three verbose profiles sat at the top of everybody's list."""
        idf = similarity.build_idf([["tribology"], ["tribology", "wear"]])
        short = similarity.vector(["tribology"], idf)
        long = similarity.vector(["tribology"] * 40 + ["wear"] * 40, idf)
        assert abs(sum(v * v for v in short.values()) - 1.0) < 0.001
        assert abs(sum(v * v for v in long.values()) - 1.0) < 0.001

    def test_two_people_in_one_field_are_closer_than_two_in_different_ones(self):
        docs = {1: ["tribology", "wear", "coatings"],
                2: ["tribology", "wear", "friction"],
                3: ["constitutional", "jurisprudence"]}
        idf = similarity.build_idf(docs.values())
        vectors = {k: similarity.vector(v, idf) for k, v in docs.items()}
        assert (similarity.cosine(vectors[1], vectors[2])
                > similarity.cosine(vectors[1], vectors[3]))

    def test_nobody_shares_a_word_means_nobody_is_recommended(self):
        docs = {1: ["tribology"], 2: ["jurisprudence"]}
        idf = similarity.build_idf(docs.values())
        vectors = {k: similarity.vector(v, idf) for k, v in docs.items()}
        index = similarity.invert(vectors)
        assert similarity.nearest(vectors[1], vectors, index, skip=[1]) == []


class TheRebuild(TestCase):

    def setUp(self):
        def person(apid, expertise, title=""):
            m = Member.objects.create(username=f"u{apid}", apid=apid,
                                      full_name=f"Person {apid}",
                                      email=f"{apid}@example.org")
            Profile.objects.create(member=m, expertise=expertise)
            if title:
                Publication.objects.create(member=m, title=title)
            return m

        self.tribologist = person("920001", "tribology and wear of coatings")
        self.other_tribologist = person(
            "920002", "surface engineering",
            title="Friction and wear behaviour of tribology coatings")
        self.lawyer = person("920003", "constitutional law and jurisprudence")
        Journal.objects.create(title="Journal of Tribology and Surface Engineering",
                               subject="Tribology")
        Journal.objects.create(title="Journal of Constitutional Law",
                               subject="Law")

    def test_the_two_in_one_field_find_each_other_and_not_the_lawyer(self):
        from django.core.management import call_command
        call_command("rebuild_recommendations", verbosity=0)
        top = SimilarMember.objects.filter(member=self.tribologist,
                                           rank=0).first()
        assert top and top.other == self.other_tribologist
        assert not SimilarMember.objects.filter(member=self.tribologist,
                                                other=self.lawyer).exists()

    def test_a_publication_title_counts_as_the_members_own_words(self):
        """A title is what somebody actually did, in their field's vocabulary — the
        second tribologist's profile says only "surface engineering"."""
        from django.core.management import call_command
        call_command("rebuild_recommendations", verbosity=0)
        row = SimilarMember.objects.filter(member=self.other_tribologist,
                                           other=self.tribologist).first()
        assert row and "tribology" in row.because

    def test_a_journal_is_suggested_from_the_same_words(self):
        from django.core.management import call_command
        call_command("rebuild_recommendations", verbosity=0)
        top = SuggestedJournal.objects.filter(member=self.tribologist,
                                              rank=0).first()
        assert top and "Tribology" in top.journal.title

    def test_running_it_twice_does_not_double_anything(self):
        from django.core.management import call_command
        call_command("rebuild_recommendations", verbosity=0)
        first = SimilarMember.objects.count()
        call_command("rebuild_recommendations", verbosity=0)
        assert SimilarMember.objects.count() == first

    def test_the_reason_is_in_the_members_own_vocabulary(self):
        from django.core.management import call_command
        call_command("rebuild_recommendations", verbosity=0)
        row = SimilarMember.objects.filter(member=self.tribologist, rank=0).first()
        assert row.because and all(w.strip() for w in row.because.split(","))
