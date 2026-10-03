"""Reading a member's works off ORCID, and offering rather than importing."""

from __future__ import annotations

import io
import json

from django.test import TestCase

from apps.identity.models import Member
from apps.works import orcid
from apps.works.models import Publication

RECORD = {"group": [
    {"external-ids": {"external-id": [
        {"external-id-type": "doi", "external-id-value": "10.1000/abc",
         "external-id-url": {"value": "https://doi.org/10.1000/abc"}}]},
     "work-summary": [
         {"title": {"title": {"value": "A paper about things"}},
          "journal-title": {"value": "J Things"},
          "publication-date": {"year": {"value": "2024"}}, "type": "journal-article"},
         {"title": {"title": {"value": "A paper about things (duplicate deposit)"}}}]},
    {"external-ids": {"external-id": []},
     "work-summary": [{"title": {"title": {"value": "Another paper"}},
                       "publication-date": {"year": {"value": "2022"}}}]},
]}


def _opener(payload):
    def open_it(_request, timeout=None):
        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R(json.dumps(payload).encode())
    return open_it


class Reading(TestCase):

    def test_an_id_that_is_not_an_orcid_is_not_fetched(self):
        assert orcid.fetch("1234") == []
        assert orcid.looks_like_orcid("0000-0003-2478-3399")
        assert orcid.looks_like_orcid("https://orcid.org/0000-0002-1825-0097")

    def test_one_work_per_group_not_one_per_deposit(self):
        """ORCID groups the same paper's deposits together; listing all of them would
        offer the member their own paper three times."""
        works = orcid.fetch("0000-0003-2478-3399", opener=_opener(RECORD))
        assert [w["title"] for w in works] == ["A paper about things", "Another paper"]
        assert works[0]["doi"] == "10.1000/abc" and works[0]["year"] == "2024"

    def test_an_unreachable_record_is_an_empty_list_not_a_crash(self):
        def boom(_request, timeout=None):
            raise OSError("no network")
        assert orcid.fetch("0000-0003-2478-3399", opener=boom) == []


class Offering(TestCase):

    def setUp(self):
        self.member = Member.objects.create(username="w", apid="910001",
                                            email="w@example.org")

    def test_a_work_already_listed_by_doi_is_not_offered_again(self):
        Publication.objects.create(member=self.member, title="Something else",
                                   doi="10.1000/ABC")
        works = orcid.fetch("0000-0003-2478-3399", opener=_opener(RECORD))
        fresh = orcid.new_for(self.member, works)
        assert [w["title"] for w in fresh] == ["Another paper"]

    def test_a_work_already_listed_by_title_is_not_offered_again(self):
        """Punctuation and case differ between a manual entry and ORCID's copy."""
        Publication.objects.create(member=self.member, title="A Paper About Things!")
        works = orcid.fetch("0000-0003-2478-3399", opener=_opener(RECORD))
        assert [w["title"] for w in orcid.new_for(self.member, works)] == ["Another paper"]

    def test_nothing_is_added_until_it_is_ticked(self):
        works = orcid.fetch("0000-0003-2478-3399", opener=_opener(RECORD))
        orcid.new_for(self.member, works)
        assert Publication.objects.filter(member=self.member).count() == 0
