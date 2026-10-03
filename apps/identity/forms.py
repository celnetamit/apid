"""The forms a member fills in about themselves.

On the live site each of these is a Formidable form, and submitting one **creates a new
entry** rather than changing anything. That is why a member who has updated their
profile four times exists four times, why nobody can say which one is current without
reading timestamps, and why 37 members' public links point at a version the site itself
stopped showing. Here, editing edits.
"""

from __future__ import annotations

import os

from django import forms
from django.conf import settings

from apps.profiles.models import Profile
from apps.works.models import Publication

#: What a profile picture may be. Checked by decoding the file rather than by trusting
#: its name — an upload called `me.jpg` is not a JPEG because it says so.
PICTURE_TYPES = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp"}
MAX_PICTURE_BYTES = 5 * 1024 * 1024


class ProfileForm(forms.ModelForm):
    """Everything a member may change about their own profile.

    `wants_editorial_board` is deliberately absent: wanting to join a board is an
    application with a decision behind it, not a checkbox somebody can tick on their
    own record. It is read-only history here.
    """

    class Meta:
        model = Profile
        fields = [
            "profession", "affiliation", "affiliation_url", "department",
            "designation", "experience_years", "institutional_profile_url",
            "city", "state", "country", "pincode",
            "academic_qualification", "academic_university", "qualification_year",
            "expertise", "areas_of_interest", "biography",
            "orcid", "google_scholar_id", "researchgate_id", "ssrn_id", "scopus_id",
        ]
        widgets = {
            "expertise": forms.Textarea(attrs={"rows": 3}),
            "areas_of_interest": forms.Textarea(attrs={"rows": 3}),
            "biography": forms.Textarea(attrs={"rows": 8}),
        }
        labels = {
            "orcid": "ORCID iD", "ssrn_id": "SSRN author id",
            "google_scholar_id": "Google Scholar id",
            "researchgate_id": "ResearchGate id", "scopus_id": "Scopus author id",
        }

    def clean_orcid(self):
        """An ORCID that is not an ORCID is worse than none.

        ce4 now prints this above the references of every paper the member publishes,
        and manuscript-ngine will match authors on it. A typo here becomes a wrong
        attribution somewhere else, so the shape is checked — and only the shape, not
        whether the iD exists, which is a network call and a different question.
        """
        raw = (self.cleaned_data.get("orcid") or "").strip()
        if not raw:
            return ""
        digits = raw.replace("https://orcid.org/", "").replace("-", "").strip()
        if len(digits) != 16 or not digits[:15].isdigit() or digits[15] not in "0123456789X":
            raise forms.ValidationError(
                "An ORCID iD looks like 0000-0003-2478-3399.")
        return "-".join(digits[i:i + 4] for i in range(0, 16, 4))


class PublicationForm(forms.ModelForm):
    class Meta:
        model = Publication
        fields = ["title", "journal", "year", "doi", "link"]
        widgets = {"title": forms.Textarea(attrs={"rows": 2})}

    def clean_year(self):
        year = (self.cleaned_data.get("year") or "").strip()
        if year and not (year.isdigit() and 1900 <= int(year) <= 2100):
            raise forms.ValidationError("A four-digit year, please.")
        return year


class PictureForm(forms.Form):
    picture = forms.FileField(label="A new photograph")

    def clean_picture(self):
        upload = self.cleaned_data["picture"]
        if upload.size > MAX_PICTURE_BYTES:
            raise forms.ValidationError("Please keep it under 5 MB.")
        try:
            from PIL import Image
            image = Image.open(upload)
            image.verify()
        except ImportError:                                   # pragma: no cover
            raise forms.ValidationError(
                "Pictures cannot be checked on this server yet.")
        except Exception:                                     # noqa: BLE001
            raise forms.ValidationError("That file is not an image.")
        if image.format not in PICTURE_TYPES:
            raise forms.ValidationError(
                "JPEG, PNG, GIF or WebP, please — not " + str(image.format))
        upload.seek(0)
        self.image_format = image.format
        return upload

    def save_for(self, member) -> str:
        """Write it and return the path to store. One picture per member, always the
        same name, so uploading a new one replaces the old rather than filling the disk
        with a member's every attempt."""
        extension = PICTURE_TYPES[self.image_format]
        folder = os.path.join(settings.MEDIA_ROOT, "members", str(member.apid))
        os.makedirs(folder, exist_ok=True)
        for existing in os.listdir(folder):
            if existing.startswith("picture"):
                os.remove(os.path.join(folder, existing))
        name = f"picture{extension}"
        target = os.path.join(folder, name)
        upload = self.cleaned_data["picture"]
        # Written whole, then moved: a half-written picture that looks like a file is
        # worse than one that is plainly absent.
        with open(target + ".part", "wb") as handle:
            for chunk in upload.chunks():
                handle.write(chunk)
        os.replace(target + ".part", target)
        return f"members/{member.apid}/{name}"
