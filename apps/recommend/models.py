"""Stored recommendations: computed in a batch, read in one query.

Similarity over 13,412 members is 90 million pairs. Computing it per page view is the
thing that works in testing and falls over the first day somebody links to the site, so
the top twenty per member are written here by `rebuild_recommendations` and a page read
is one indexed lookup.

Each row keeps its score and the words that earned it. The words are not decoration:
"suggested because you both write about tribology and wear" is a recommendation
somebody can judge, and "suggested" is one they can only accept or ignore.
"""

from __future__ import annotations

from django.db import models

from apps.editorial.models import Journal
from apps.identity.models import Member


class SimilarMember(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE,
                               related_name="similar_to")
    other = models.ForeignKey(Member, on_delete=models.CASCADE,
                              related_name="similar_from")
    score = models.FloatField()
    because = models.CharField(max_length=200, blank=True)
    rank = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = [("member", "other")]
        indexes = [models.Index(fields=["member", "rank"])]
        ordering = ["rank"]

    def __str__(self) -> str:
        return f"{self.member.apid} ~ {self.other.apid} ({self.score})"


class SuggestedJournal(models.Model):
    """Where this member's own work says they should submit."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE,
                               related_name="suggested_journals")
    journal = models.ForeignKey(Journal, on_delete=models.CASCADE,
                                related_name="suggested_to")
    score = models.FloatField()
    because = models.CharField(max_length=200, blank=True)
    rank = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = [("member", "journal")]
        indexes = [models.Index(fields=["member", "rank"])]
        ordering = ["rank"]
