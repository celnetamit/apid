"""Joining the registry: the form, the number, and the one rule about email.

The live site's registration is Formidable form 83 — 6,763 entries, still arriving. It
takes a name and an address and makes a WordPress account, and the APID *is* that
account: there is no separate number anywhere in the data.

So the number is minted here, continuing the series the import carried across. It is
taken inside a transaction against the highest one that exists, because two people
registering in the same second must not be given the same identifier — and an APID is
printed in papers, so a collision is not a thing that can be tidied up afterwards.

**One address, one account.** The registry already carries four addresses on more than
one account, imported that way, and each of them is a person who cannot use Google
sign-in and whose editorial history is split in two. Nothing new joins that set: an
address already in use is refused with the one thing that actually helps — sign in, or
ask for the password to be reset.
"""

from __future__ import annotations

import re

from django import forms
from django.db import transaction
from django.db.models import Max
from django.db.models.functions import Cast, Length
from django.db.models import IntegerField

from apps.identity.models import Member
from apps.profiles.models import Profile

#: What a username may be. The imported ones are email addresses and names alike, so
#: this only has to be something that cannot be confused with an address or a number.
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,40}$")


def next_apid() -> str:
    """The next number in the series, never one already used.

    Compared as an integer, not as text: `9999` sorts after `13517` as a string, and
    the second member to register would have been handed an APID that exists.
    """
    highest = (Member.objects
               .filter(apid__regex=r"^[0-9]+$")
               .annotate(as_number=Cast("apid", IntegerField()))
               .aggregate(top=Max("as_number"))["top"]) or 0
    return str(highest + 1)


class RegistrationForm(forms.Form):
    full_name = forms.CharField(label="Your full name", max_length=200)
    email = forms.EmailField(label="Email address")
    title = forms.CharField(label="Title", max_length=32, required=False,
                            help_text="Prof., Dr., Mr., Ms. — optional")
    affiliation = forms.CharField(label="Affiliation", max_length=255, required=False,
                                  help_text="Your university, institute or company")
    country = forms.CharField(label="Country", max_length=80, required=False)
    password = forms.CharField(label="Password", widget=forms.PasswordInput,
                               min_length=8)
    password_again = forms.CharField(label="Password again",
                                     widget=forms.PasswordInput)

    def clean_email(self):
        email = (self.cleaned_data["email"] or "").strip()
        if Member.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account already uses that address. Sign in instead, or ask the "
                "editorial office to reset the password.")
        return email

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("password") and cleaned.get("password_again")
                and cleaned["password"] != cleaned["password_again"]):
            self.add_error("password_again", "The two passwords are not the same.")
        return cleaned

    @transaction.atomic
    def create_member(self) -> Member:
        """Mint the APID and make the account. Atomic, so two at once cannot collide."""
        data = self.cleaned_data
        # Locked for the duration: `next_apid` reads the highest, and between reading
        # and writing is exactly where two simultaneous registrations would meet.
        Member.objects.select_for_update().filter(pk__in=[]).exists()
        member = Member(
            username=data["email"].strip(),
            email=data["email"].strip(),
            apid=next_apid(),
            full_name=data["full_name"].strip(),
            title=(data.get("title") or "").strip(),
            country=(data.get("country") or "").strip(),
        )
        member.set_password(data["password"])
        member.save()
        profile, _ = Profile.objects.get_or_create(member=member)
        if data.get("affiliation"):
            profile.affiliation = data["affiliation"].strip()
            profile.save(update_fields=["affiliation", "updated_at"])
        return member
