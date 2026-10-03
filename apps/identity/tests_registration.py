"""Joining the registry: the number, the address rule, and the letter."""

from __future__ import annotations

import os

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.identity import mail
from apps.identity.models import Member
from apps.identity.registration import RegistrationForm, next_apid
from apps.profiles.models import Profile

GOOD = {"full_name": "A New Member", "email": "new@university.edu",
        "affiliation": "A University", "country": "India",
        "password": "a-real-password", "password_again": "a-real-password"}


class TheNumber(TestCase):

    def test_the_series_continues_from_the_highest(self):
        Member.objects.create(username="a", apid="13517")
        assert next_apid() == "13518"

    def test_the_highest_is_found_as_a_number_not_as_text(self):
        """`9999` sorts after `13517` as text, and the next member would have been
        handed an APID that already exists."""
        Member.objects.create(username="a", apid="13517")
        Member.objects.create(username="b", apid="9999")
        assert next_apid() == "13518"

    def test_an_empty_registry_starts_at_one(self):
        assert next_apid() == "1"


class Registering(TestCase):

    def test_an_account_and_a_profile_are_made(self):
        form = RegistrationForm(GOOD)
        assert form.is_valid(), form.errors
        member = form.create_member()
        assert member.apid == "1" and member.full_name == "A New Member"
        assert member.check_password("a-real-password")
        assert Profile.objects.get(member=member).affiliation == "A University"

    def test_one_address_means_one_account(self):
        """Four addresses in the imported registry are on more than one account, and
        each is a person whose history is split in two. Nothing new joins that set."""
        Member.objects.create(username="held", apid="5", email="new@university.edu")
        form = RegistrationForm(GOOD)
        assert not form.is_valid()
        assert "already uses that address" in str(form.errors["email"])

    def test_the_address_is_matched_whatever_the_case(self):
        Member.objects.create(username="held", apid="5", email="NEW@University.EDU")
        assert not RegistrationForm(GOOD).is_valid()

    def test_two_different_passwords_are_refused(self):
        form = RegistrationForm({**GOOD, "password_again": "something-else"})
        assert not form.is_valid() and "password_again" in form.errors

    @override_settings(APID_PUBLIC=False)
    def test_the_page_is_reachable_signed_out_and_signs_you_in(self):
        response = self.client.post(reverse("register"), GOOD, follow=True)
        assert response.status_code == 200
        assert "_auth_user_id" in self.client.session
        assert Member.objects.filter(email="new@university.edu").exists()


class TheLetter(TestCase):

    def test_the_welcome_names_the_apid_and_the_profile(self):
        member = Member.objects.create(username="x", apid="13518",
                                       email="new@university.edu",
                                       full_name="A New Member")
        sent = mail.welcome(member)
        # Nothing leaves this machine until the sending lane is agreed.
        assert sent is False
        body = _outbox_text("new@university.edu", "13518")
        assert "13518" in body and "/profiles/13518/" in body
        assert "A New Member" in body

    def test_an_address_that_cannot_exist_is_not_written_to(self):
        member = Member.objects.create(username="y", apid="13519", email="")
        assert mail.welcome(member) is False


def _outbox_text(address: str, must_contain: str) -> str:
    """The letter written to this address that says `must_contain`.

    Looked up by content rather than by "the newest file": the outbox accumulates
    across a test run, and picking the last name alphabetically found another test's
    letter — which passed alone and failed in the suite.
    """
    from django.conf import settings
    folder = os.path.join(settings.BASE_DIR.parent, "outbox")
    for name in sorted(os.listdir(folder)):
        if not name.startswith(address.split("@")[0]):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as handle:
            body = handle.read()
        if must_contain in body:
            return body
    raise AssertionError(f"no letter to {address} mentioning {must_contain!r}")


class TheLookup(TestCase):
    """The read-only window the other systems in the estate use."""

    def setUp(self):
        import os
        os.environ["APID_LOOKUP_SECRET"] = "a-shared-secret-for-the-test"
        self.member = Member.objects.create(
            username="looked", apid="930001", email="Looked@Example.org",
            full_name="Looked Up")
        Profile.objects.create(member=self.member,
                               orcid="https://orcid.org/0000-0002-1825-0097",
                               affiliation="A University")

    def _ask(self, emails, sign_it=True, stamp=None):
        import json as _json
        import time as _time

        from apps.identity.lookup import sign
        body = _json.dumps({"emails": emails}).encode()
        stamp = stamp or str(int(_time.time()))
        headers = {"content_type": "application/json"}
        if sign_it:
            headers["HTTP_X_APID_TIMESTAMP"] = stamp
            headers["HTTP_X_APID_SIGNATURE"] = sign(body, stamp)
        return self.client.post(reverse("identity-lookup"), data=body, **headers)

    def test_a_signed_request_gets_the_registry_facts(self):
        response = self._ask(["looked@example.org"])
        assert response.status_code == 200
        found = response.json()["members"]["looked@example.org"]
        assert found["apid"] == "930001" and found["name"] == "Looked Up"
        # Normalised on the way out: the stored value is a full URL, and the other
        # system's column is exactly an ORCID wide. It refused the first backfill.
        assert found["orcid"] == "0000-0002-1825-0097"

    def test_an_unsigned_request_is_refused(self):
        assert self._ask(["looked@example.org"], sign_it=False).status_code == 401

    def test_a_stale_signature_is_refused(self):
        import time as _time
        old = str(int(_time.time()) - 3600)
        assert self._ask(["looked@example.org"], stamp=old).status_code == 401

    def test_an_address_on_two_accounts_is_not_answered(self):
        """Handing over the wrong person's ORCID is worse than handing over none."""
        Member.objects.create(username="twin", apid="930002",
                              email="looked@example.org")
        body = self._ask(["looked@example.org"]).json()
        assert body["members"] == {}
        assert body["ambiguous"] == ["looked@example.org"]

    def test_an_address_nobody_has_is_simply_absent(self):
        assert self._ask(["nobody@example.org"]).json()["members"] == {}


class TidyingOrcids(TestCase):

    def test_every_shape_the_import_carries(self):
        from apps.works.orcid import normalise_id
        assert normalise_id("https://orcid.org/0000-0003-2545-4474") == "0000-0003-2545-4474"
        # A misspelled domain, which a prefix-stripping rule would have carried through.
        assert normalise_id("https://ocrid.org/0000-0001-7876-2273") == "0000-0001-7876-2273"
        assert normalise_id("0000000218250097") == "0000-0002-1825-0097"
        assert normalise_id("0009-0004-9672-360x") == "0009-0004-9672-360X"

    def test_what_is_not_an_orcid_is_left_alone(self):
        from apps.works.orcid import normalise_id
        assert normalise_id("arfan74@orcid") == ""
        assert normalise_id("https://orcid.org/my-orcid?orcid=0000-00") == ""
        assert normalise_id("") == ""
