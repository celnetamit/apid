"""What a member has published.

Two live forms hold this: "Publications" (id 227, 5,430 entries — a title and a link)
and "My Publications" (id 66, 991 entries). They are the same thing recorded twice,
years apart, so they import into one model and the form each row came from is kept.
"""

from __future__ import annotations

from django.db import models

from apps.identity.models import Member


class Publication(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE,
                              related_name="publications")
    title = models.TextField()
    link = models.URLField(max_length=1000, blank=True)
    journal = models.CharField(max_length=255, blank=True)
    year = models.CharField(max_length=10, blank=True)
    doi = models.CharField(max_length=120, blank=True, db_index=True)

    #: Which live form this came from — 227 or 66. Kept because the two mean slightly
    #: different things and a later tidy-up will need to tell them apart.
    wp_form_id = models.IntegerField(null=True, blank=True)
    wp_entry_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "title"]

    def __str__(self) -> str:
        return self.title[:80]
