"""The office queue: who may open it, and what a decision actually does."""

from __future__ import annotations

import datetime as dt

from django.test import TestCase
from django.urls import reverse

from apps.editorial.access import is_office
from apps.editorial.models import (Application, ApplicationJournal, Appointment,
                                   Decision, Journal)
from apps.identity.models import Member, MemberRole

BACKEND = "apps.identity.backends.UsernameOrEmailBackend"


class WhoMayWorkTheQueue(TestCase):

    def setUp(self):
        self.journal = Journal.objects.create(title="Journal of Probes")
        self.applicant = Member.objects.create(username="applicant", apid="800001",
                                               full_name="An Applicant")
        self.application = Application.objects.create(
            member=self.applicant, applying_for="editorial board")

    def _as(self, member):
        self.client.force_login(member, backend=BACKEND)

    def test_a_signed_out_visitor_is_sent_to_sign_in(self):
        response = self.client.get(reverse("editorial-queue"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_an_ordinary_member_is_refused_rather_than_asked_to_sign_in_again(self):
        self._as(self.applicant)
        self.assertEqual(self.client.get(reverse("editorial-queue")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("application", args=[self.application.pk])
                            ).status_code, 403)

    def test_an_editor_is_not_the_office(self):
        """Editors serve on boards; they do not decide who joins one."""
        MemberRole.objects.create(member=self.applicant, role="editor")
        self.assertFalse(is_office(self.applicant))
        self._as(self.applicant)
        self.assertEqual(self.client.get(reverse("editorial-queue")).status_code, 403)

    def test_a_commissioning_editor_is(self):
        MemberRole.objects.create(member=self.applicant, role="commissioning_editor")
        self.assertTrue(is_office(self.applicant))
        self._as(self.applicant)
        self.assertEqual(self.client.get(reverse("editorial-queue")).status_code, 200)

    def test_staff_are(self):
        staff = Member.objects.create(username="office", apid="800002", is_staff=True)
        self.assertTrue(is_office(staff))
        self._as(staff)
        self.assertEqual(self.client.get(reverse("editorial-queue")).status_code, 200)

    def test_a_refused_member_cannot_decide_by_posting_straight_at_it(self):
        """The gate has to hold on the *action*, not only on the page that shows it."""
        self._as(self.applicant)
        response = self.client.post(reverse("decide", args=[self.application.pk]),
                                    {"action": "accept"})
        self.assertEqual(response.status_code, 403)
        self.application.refresh_from_db()
        self.assertEqual(self.application.decision, Decision.PENDING)
        self.assertEqual(Appointment.objects.count(), 0)


class Deciding(TestCase):

    def setUp(self):
        self.office = Member.objects.create(username="office", apid="800003",
                                            is_staff=True, full_name="The Office")
        self.journal = Journal.objects.create(title="Journal of Probes")
        self.other = Journal.objects.create(title="Journal of Other Things")
        self.applicant = Member.objects.create(username="applicant", apid="800004",
                                               full_name="An Applicant")
        self.application = Application.objects.create(member=self.applicant)
        ApplicationJournal.objects.create(application=self.application,
                                          journal=self.journal,
                                          stated_title="Journal of Probes")
        self.client.force_login(self.office, backend=BACKEND)

    def _decide(self, **extra):
        return self.client.post(reverse("decide", args=[self.application.pk]),
                                {"action": "accept", "role": "editor",
                                 "journal": [self.journal.pk], **extra})

    def test_accepting_records_the_decision_and_the_appointment_together(self):
        """Two forms on the live site, and the gap between them is where somebody sits
        accepted but appointed to nothing."""
        self._decide(started_on="2026-09-01")
        self.application.refresh_from_db()
        self.assertEqual(self.application.decision, Decision.ACCEPTED)
        self.assertEqual(self.application.decided_by, self.office)
        self.assertIsNotNone(self.application.decided_at)
        appointment = Appointment.objects.get()
        self.assertEqual(appointment.member, self.applicant)
        self.assertEqual(appointment.journal, self.journal)
        self.assertEqual(appointment.started_on, dt.date(2026, 9, 1))
        self.assertEqual(appointment.application, self.application)

    def test_clicking_twice_does_not_appoint_twice(self):
        self._decide(started_on="2026-09-01")
        self._decide(started_on="2026-09-01")
        self.assertEqual(Appointment.objects.count(), 1)

    def test_declining_appoints_nobody(self):
        self.client.post(reverse("decide", args=[self.application.pk]),
                         {"action": "decline", "note": "Not this year.",
                          "journal": [self.journal.pk], "role": "editor"})
        self.application.refresh_from_db()
        self.assertEqual(self.application.decision, Decision.DECLINED)
        self.assertEqual(self.application.note, "Not this year.")
        self.assertEqual(Appointment.objects.count(), 0)

    def test_accepting_with_no_journal_ticked_says_so(self):
        response = self.client.post(reverse("decide", args=[self.application.pk]),
                                    {"action": "accept", "role": "editor"},
                                    follow=True)
        self.assertEqual(Appointment.objects.count(), 0)
        self.assertContains(response, "No journal was ticked")

    def test_an_invented_action_changes_nothing(self):
        self.client.post(reverse("decide", args=[self.application.pk]),
                         {"action": "appoint-me-instead"})
        self.application.refresh_from_db()
        self.assertEqual(self.application.decision, Decision.PENDING)

    def test_a_get_never_decides(self):
        self.client.get(reverse("decide", args=[self.application.pk]))
        self.application.refresh_from_db()
        self.assertEqual(self.application.decision, Decision.PENDING)


class Boards(TestCase):

    def test_the_empty_boards_are_the_point(self):
        office = Member.objects.create(username="office", apid="800005", is_staff=True)
        busy = Journal.objects.create(title="A journal with an editor")
        Journal.objects.create(title="A journal with nobody")
        member = Member.objects.create(username="editor", apid="800006")
        Appointment.objects.create(member=member, journal=busy, role="editor")
        self.client.force_login(office, backend=BACKEND)
        response = self.client.get(reverse("boards"))
        self.assertEqual(response.context["empty"], 1)
        self.assertEqual(response.context["total"], 2)


class ApplyingFromHere(TestCase):
    """The bridge to the editorial platform: what it offers and what it admits to.

    None of these touch the network. The platform is stubbed, because the thing worth
    testing is what this side does with the answer — including the answer nobody looks
    at, the list of journals that were not accepted.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.delete("mng-journal-titles")
        self.here = Journal.objects.create(title="Omni Science: A Multi-disciplinary Journal")
        self.not_there = Journal.objects.create(title="International Journal of Mineral")

    def test_a_journal_the_platform_lacks_is_not_offered(self):
        from apps.editorial import apply as bridge
        bridge.accepted_titles = lambda: {bridge._loose("OmniScience: A Multi-disciplinary Journal")}
        offered = [j.title for j in bridge.journals_to_offer()]
        self.assertIn(self.here.title, offered)      # matched through the punctuation
        self.assertNotIn(self.not_there.title, offered)

    def test_an_unreachable_platform_offers_everything_rather_than_nothing(self):
        from apps.editorial import apply as bridge
        bridge.accepted_titles = lambda: set()
        self.assertEqual(len(bridge.journals_to_offer()), 2)

    def test_the_applicant_is_told_which_journals_were_not_accepted(self):
        """The failing half of a half-success. Written against a stub that reports one
        unknown journal, and checked to fail when `send` drops that third value."""
        from apps.editorial import apply as bridge
        member = Member.objects.create(username="applicant", apid="800007",
                                       email="a@example.com", full_name="A Person")
        bridge_send = bridge.send
        try:
            bridge.send = lambda payload, url="": (True, "an-id", ["International Journal of Mineral"])
            self.client.force_login(member, backend=BACKEND)
            response = self.client.post(reverse("apply"), {
                "journal": ["Omni Science: A Multi-disciplinary Journal|associate"],
                "statement": "x" * 60, "phone": "1", "affiliation": "Somewhere"},
                follow=True)
        finally:
            bridge.send = bridge_send
        said = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("does not have International Journal of Mineral", said)
