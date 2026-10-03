"""APID marketing / content pages.

Content for each slug is a pre-cleaned HTML fragment stored under
`templates/content/pages/{slug}.html`. The view loads that fragment and renders
it inside `content/page.html` with a dark library-themed banner, sidebar nav
and (for most pages) a mini-CTA strip.
"""
from __future__ import annotations

from django.http import Http404
from django.shortcuts import render
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string

from apps.identity.models import Member

TITLES = {
    "about": ("About APID",
              "A permanent, verified registry for researchers, scholars and academicians — built to outlive URL rot and keep academic identities resolvable, forever."),
    "apid-profiles": ("APID Profiles Showcase",
                      "Member profiles across the APID network — discover peers by discipline, affiliation and research interest."),
    "coming-soon": ("Coming Soon", None),
    "contact": ("Contact APID",
                "Reach out — the APID team responds within one working day."),
    "cookie-policy-au": ("Cookie Policy (Australia)", None),
    "cookie-policy-br": ("Cookie Policy (Brazil)", None),
    "cookie-policy-ca": ("Cookie Policy (Canada)", None),
    "cookie-policy-eu": ("Cookie Policy (EU)", None),
    "cookie-policy-uk": ("Cookie Policy (United Kingdom)", None),
    "cookie-policy-za": ("Cookie Policy (South Africa)", None),
    "disclaimer": ("Disclaimer", None),
    "elevate-your-research-publish-with": (
        "Elevate Your Research",
        "Publish with confidence — APID is backed by CELNET's editorial infrastructure."),
    "eligibility-benefits-and-responsibilities": (
        "Eligibility, Benefits & Responsibilities",
        "Who can register, what APID offers, and what members are expected to do."),
    "faqs": ("Frequently Asked Questions",
             "Help articles, troubleshooting guides, and tutorials."),
    "help-center": ("APID Help Center",
                    "We'd love to talk about what matters to you."),
    "how-to-join-us-role-upgradation": (
        "How to Join Us / Role Upgradation",
        "Steps to upgrade your APID role — reviewer, editor, board member."),
    "how-to-register-on-apid": (
        "How to Register on APID",
        "A step-by-step walkthrough of creating your APID profile."),
    "imprint": ("Imprint", None),
    "join-us": ("Join Us",
                "Create your APID profile and join 13,400+ scholars worldwide."),
    "maintenance": ("Maintenance", None),
    "opt-out-preferences": ("Cookie Preferences",
                            "Manage your cookie choices for apid.journalslibrary.com."),
    "privacy-policy": ("Privacy Policy", None),
    "terms-and-conditions": ("Terms & Conditions", None),
}

NO_CTA = {
    "privacy-policy", "terms-and-conditions", "disclaimer", "imprint",
    "opt-out-preferences",
    "cookie-policy-au", "cookie-policy-br", "cookie-policy-ca",
    "cookie-policy-eu", "cookie-policy-uk", "cookie-policy-za",
    "contact", "help-center", "maintenance", "coming-soon",
}


def page(request, slug):
    if slug not in TITLES:
        raise Http404
    try:
        body = render_to_string(f"content/pages/{slug}.html")
    except TemplateDoesNotExist:
        raise Http404
    title, lede = TITLES[slug]
    return render(request, "content/page.html", {
        "page_title": title,
        "lede": lede,
        "content": body,
        "show_cta": slug not in NO_CTA,
    })


def home_marketing(request):
    try:
        member_count = f"{Member.objects.count():,}"
    except Exception:
        member_count = "13,400+"
    return render(request, "content/home.html", {
        "member_count": member_count,
    })

from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, Value, When


def apid_profiles(request):
    """Live profile grid, 24 per page.

    Completed profiles — picture, role, biography, ORCID, expertise — surface
    first so a visitor landing cold sees finished work, not empty cards. Within
    the same completeness bracket, newest joiners first.
    """
    from apps.profiles.models import Profile
    one, zero = Value(1), Value(0)
    def flag(**lookup):
        return Case(When(then=zero, **lookup), default=one,
                    output_field=IntegerField())
    qs = (Profile.objects
          .select_related("member")
          .exclude(affiliation="")
          .annotate(
              has_picture=flag(picture=""),
              has_role=Case(
                  When(Q(designation="") & Q(department=""), then=zero),
                  default=one, output_field=IntegerField()),
              has_bio=flag(biography=""),
              has_orcid=flag(orcid=""),
              has_expertise=flag(expertise=""),
          )
          .order_by("-has_picture", "-has_role", "-has_bio",
                   "-has_orcid", "-has_expertise", "-member__date_joined"))
    paginator = Paginator(qs, 24)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "content/apid_profiles.html", {
        "profiles": page_obj.object_list,
        "page_obj": page_obj,
        "total_count": f"{Member.objects.count():,}",
        "with_picture_count": f"{Profile.objects.exclude(picture='').count():,}",
    })
