"""The editorial pipeline, as state rather than as hidden fields.

On the live site an application is form 159 (2,445 entries) with `Journal 1` …
`Journal 11` and `EIC Name 1..3` columns; the decision is a *separate* form, 200
(1,668 entries, "Editorial Acceptance"); the appointment is a third, 219 (3,903
entries, "Assign Journal Manager"). Whether a given person is on a given board today
is answered by reading three forms and hoping.

Here the application is one row, the journals it names are rows beside it, and the
decision is a field with a date and a person attached. "Who is on this board" becomes
a query.
"""

from __future__ import annotations

from django.db import models

from apps.identity.models import Member


class EditorialStaff(models.Model):
    """Internal editorial-office staff — commissioning editors, publication managers,
    production editors. These are our own employees, not APID members; one person
    typically handles many journals, so edits are made once here and reflected on
    every journal they commission.

    Signs certificates and empanelment letters on behalf of Consortium e-Learning
    Network Pvt Ltd.
    """

    name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=40, blank=True)
    designation = models.CharField(max_length=120, default="Commissioning Editor")
    signature_image = models.ImageField(upload_to="signatures/", blank=True)
    active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Editorial staff member"
        verbose_name_plural = "Editorial staff"

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


class Journal(models.Model):
    """From the live Journals Master (form 25, 278 entries). Reconciling these with
    manuscript-ngine's own journal list — the same portfolio, two databases — is a job
    of its own and is deliberately not attempted at import time."""

    title = models.CharField(max_length=300)
    abbreviation = models.CharField(max_length=80, blank=True)
    publisher = models.CharField(max_length=200, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    image_url = models.URLField(max_length=800, blank=True)
    status = models.CharField(max_length=60, blank=True)
    wp_entry_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)

    commissioning_editor = models.ForeignKey(
        EditorialStaff, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="journals",
    )

    class Meta:
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


class Decision(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    WITHDRAWN = "withdrawn", "Withdrawn"


class Application(models.Model):
    """A member asking to join an editorial board."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE,
                               related_name="applications")
    applying_for = models.CharField(max_length=120, blank=True)
    subject = models.CharField(max_length=200, blank=True)

    decision = models.CharField(max_length=20, choices=Decision.choices,
                                default=Decision.PENDING, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="decisions_made")

    #: What the applicant said about themselves at the time. The live form copies the
    #: profile into the application, and those copies differ from the profile today —
    #: so they are kept as the application's own record rather than overwriting it.
    stated_designation = models.CharField(max_length=200, blank=True)
    stated_department = models.CharField(max_length=200, blank=True)
    stated_affiliation = models.CharField(max_length=255, blank=True)

    #: Why the office decided what it decided. The live system has nowhere to put this,
    #: so a declined application today is a fact with no reason attached — and the next
    #: person to look at it has to ask somebody who may not be here.
    note = models.TextField(blank=True)

    wp_entry_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    # wisp 2026-10-03: optional CV/resume. Stored under MEDIA_ROOT/applications/
    # with Django's default duplicate-name handling. Managers see a download link
    # on the application detail view.
    cv = models.FileField(upload_to="applications/", blank=True, max_length=500)

    class Meta:
        ordering = ["-applied_at"]

    def __str__(self) -> str:
        return f"{self.member.apid} → {self.applying_for or 'editorial board'}"


class ApplicationJournal(models.Model):
    """One journal named on an application. `Journal 1` … `Journal 11` become rows.

    Each choice is now decided independently by that journal's own manager or
    commissioning editor — accepted on one journal does not accept the others.
    """

    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                    related_name="journals")
    journal = models.ForeignKey(Journal, on_delete=models.SET_NULL, null=True,
                                blank=True, related_name="applications")
    stated_title = models.CharField(max_length=300, blank=True)
    preference = models.PositiveSmallIntegerField(default=1)

    # wisp 2026-10-02 pm: per-choice decision — each journal decides for itself.
    decision = models.CharField(max_length=20, choices=Decision.choices,
                                default=Decision.PENDING, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="choice_decisions_made")
    note = models.TextField(blank=True)
    role_appointed = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["preference"]
        unique_together = [("application", "preference")]


class Appointment(models.Model):
    """A member actually serving on a journal — the answer to "who is on this board"."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE,
                               related_name="appointments")
    journal = models.ForeignKey(Journal, on_delete=models.CASCADE,
                                related_name="appointments")
    role = models.CharField(max_length=60)
    started_on = models.DateField(null=True, blank=True)
    ended_on = models.DateField(null=True, blank=True)
    application = models.ForeignKey(Application, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name="appointments")
    wp_entry_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)

    class Meta:
        # Deliberately *not* unique on (member, journal, role, date). The live data has
        # the same person assigned to the same journal in the same role on the same day
        # more than once — member 773, journal 166, Guest Editor, 23 Nov 2023 — and a
        # constraint would have quietly merged two office actions into one. Each source
        # entry stays a row; the duplicates are counted and reported instead.
        indexes = [models.Index(fields=["journal", "role"]),
                   models.Index(fields=["member", "ended_on"])]

    def __str__(self) -> str:
        return f"{self.member.apid} — {self.role} of {self.journal}"

    @property
    def is_current(self) -> bool:
        return self.ended_on is None
