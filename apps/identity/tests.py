"""What sign-in must and must not do.

Written so that every refusal is a test with a name. A Google sign-in that works is easy
to check by hand once; a Google sign-in that *refuses the right things* is not, and
those are the branches that matter — each of them signs somebody into somebody else's
record if it is wrong.
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.identity import google_login, google_oauth
from apps.identity.models import Member

VERIFIED = {"email": "one@example.com", "email_verified": True, "name": "One Person"}


def allow_everything(_email: str) -> bool:
    return True


class GoogleAccountMatching(TestCase):

    def setUp(self):
        self.member = Member.objects.create(
            username="one", apid="900001", email="one@example.com",
            full_name="One Person")

    def test_a_verified_address_finds_the_one_account(self):
        member, reason = google_login.member_for(VERIFIED, allow_everything)
        self.assertEqual(member, self.member)
        self.assertEqual(reason, "")

    def test_case_does_not_matter(self):
        member, _ = google_login.member_for(
            {**VERIFIED, "email": "ONE@Example.COM"}, allow_everything)
        self.assertEqual(member, self.member)

    def test_an_unverified_address_signs_nobody_in(self):
        member, reason = google_login.member_for(
            {**VERIFIED, "email_verified": False}, allow_everything)
        self.assertIsNone(member)
        self.assertEqual(reason, google_login.UNVERIFIED)

    def test_an_address_we_do_not_have_signs_a_new_member_in(self):
        """Boss, 2026-10-03: anyone with a verified Google email is allowed."""
        member, reason = google_login.member_for(
            {**VERIFIED, "email": "stranger@example.com",
             "given_name": "Strange", "family_name": "Ranger"},
            allow_everything)
        self.assertEqual(reason, "")
        self.assertIsNotNone(member)
        self.assertEqual(member.email, "stranger@example.com")
        self.assertEqual(Member.objects.count(), 2)

    def test_two_accounts_on_one_address_sign_neither_in(self):
        """The nine accounts that share four addresses. Picking the first would open
        somebody else's editorial history and look like an ordinary sign-in."""
        Member.objects.create(username="one-again", apid="900002",
                              email="One@example.com", full_name="One Person")
        member, reason = google_login.member_for(VERIFIED, allow_everything)
        self.assertIsNone(member)
        self.assertEqual(reason, google_login.AMBIGUOUS)

    def test_a_deactivated_account_stays_deactivated(self):
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        member, reason = google_login.member_for(VERIFIED, allow_everything)
        self.assertIsNone(member)
        self.assertEqual(reason, google_login.INACTIVE)

    def test_a_domain_list_is_obeyed_when_one_is_set(self):
        member, reason = google_login.member_for(
            VERIFIED, lambda email: email.endswith("@celnet.in"))
        self.assertIsNone(member)
        self.assertEqual(reason, google_login.OFF_DOMAIN)

    def test_every_refusal_has_something_to_say(self):
        for reason in (google_login.UNVERIFIED, google_login.AMBIGUOUS,
                       google_login.INACTIVE, google_login.OFF_DOMAIN):
            self.assertIn(reason, google_login.MESSAGES)
            self.assertTrue(google_login.MESSAGES[reason].strip())


class State(TestCase):
    """The half of the flow that is not about accounts at all."""

    def test_a_state_reads_back_in_the_browser_that_made_it(self):
        nonce = google_oauth.new_nonce()
        state = google_oauth.make_state(nonce, "/me/profile/")
        self.assertEqual(google_oauth.read_state(state, nonce)["p"], "/me/profile/")

    def test_our_own_state_is_useless_in_another_browser(self):
        state = google_oauth.make_state(google_oauth.new_nonce(), "/me/")
        self.assertIsNone(google_oauth.read_state(state, "a-different-nonce"))
        self.assertIsNone(google_oauth.read_state(state, ""))

    def test_a_tampered_state_is_refused(self):
        nonce = google_oauth.new_nonce()
        state = google_oauth.make_state(nonce, "/me/")
        packed, signature = state.split(".", 1)
        self.assertIsNone(google_oauth.read_state(f"{packed}x.{signature}", nonce))
        self.assertIsNone(google_oauth.read_state(packed, nonce))

    def test_a_stale_state_is_refused(self):
        nonce = google_oauth.new_nonce()
        with mock.patch("time.time", return_value=1_000_000):
            state = google_oauth.make_state(nonce, "/me/")
        with mock.patch("time.time",
                        return_value=1_000_000 + google_oauth.STATE_TTL_SECONDS + 1):
            self.assertIsNone(google_oauth.read_state(state, nonce))


@override_settings(APID_PUBLIC=False)
class TheSignInPage(TestCase):

    def test_no_button_when_there_are_no_credentials(self):
        with mock.patch.object(google_oauth, "configured", return_value=False):
            page = self.client.get(reverse("login")).content.decode()
        self.assertNotIn("Continue with Google", page)

    def test_the_button_appears_once_credentials_exist(self):
        with mock.patch.object(google_oauth, "configured", return_value=True):
            page = self.client.get(reverse("login")).content.decode()
        self.assertIn("Continue with Google", page)

    def test_starting_without_credentials_says_so_rather_than_failing(self):
        with mock.patch.object(google_oauth, "configured", return_value=False):
            response = self.client.get(reverse("google-start"))
        self.assertRedirects(response, reverse("login"))

    def test_next_can_only_be_one_of_our_own_pages(self):
        """`next` comes from a query string. A redirect to wherever it says is an open
        redirect wearing a sign-in page's clothes."""
        with mock.patch.object(google_oauth, "configured", return_value=True), \
             mock.patch.object(google_oauth, "client_id", return_value="x"):
            response = self.client.get(
                reverse("google-start") + "?next=https://example.com/steal")
        nonce = response.cookies[google_oauth.NONCE_COOKIE].value
        import urllib.parse
        state = urllib.parse.parse_qs(
            urllib.parse.urlsplit(response["Location"]).query)["state"][0]
        self.assertEqual(google_oauth.read_state(state, nonce)["p"], "/me/")

    def test_a_callback_with_a_forged_state_signs_nobody_in(self):
        response = self.client.get(
            reverse("google-callback") + "?code=abc&state=not-a-real-state")
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)


class UsernameOrEmail(TestCase):
    """Half this registry logs in with an address and half with a name."""

    def setUp(self):
        self.member = Member.objects.create(
            username="Puneet-Like-Name", apid="900003", email="solo@example.com")
        self.member.set_password("a-real-password")
        self.member.save()

    def _signs_in(self, username: str, password: str = "a-real-password") -> bool:
        self.client.logout()
        self.client.post(reverse("login"),
                         {"username": username, "password": password})
        return "_auth_user_id" in self.client.session

    def test_by_username(self):
        self.assertTrue(self._signs_in("Puneet-Like-Name"))

    def test_by_email_whatever_the_case(self):
        self.assertTrue(self._signs_in("SOLO@example.com"))

    def test_not_with_the_wrong_password(self):
        self.assertFalse(self._signs_in("solo@example.com", "not-the-password"))

    def test_not_when_the_address_is_on_two_accounts(self):
        other = Member.objects.create(username="other", apid="900004",
                                      email="solo@example.com")
        other.set_password("a-real-password")
        other.save()
        self.assertFalse(self._signs_in("solo@example.com"))
