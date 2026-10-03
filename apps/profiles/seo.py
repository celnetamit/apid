"""Three files a crawler asks for before it looks at anything else.

The sitemap lists exactly what should be indexed: the homepage, every live
content page (policies, FAQs, how-tos, about…), and every member with a
useful profile (an affiliation, a biography, a publication, or just the
APID number — because 5,114 printed-in-papers `/apid-profiles/apid/<id>/`
redirects need to resolve to a 200).

robots.txt is the mirror of the middleware: allow what the middleware opens,
disallow every path that requires sign-in. We also give AI crawlers an
explicit answer rather than relying on `User-agent: *` default.

llms.txt is a markdown index aimed at LLMs that follow Jeremy Howard's
proposed convention (<https://llmstxt.org>). We hand them the structured
entry points they would otherwise reconstruct from the sitemap.
"""

from datetime import datetime, timezone

from django.db.models import Q
from django.http import HttpResponse
from django.utils.http import http_date

from apps.identity.models import Member
from apps.profiles.models import Profile


# Keep in sync with the content.views.TITLES registry — these are the
# evergreen informational pages a search engine should know about.
CONTENT_PAGES = [
    ("about/", "monthly", "0.6"),
    ("apid-profiles/", "weekly", "0.8"),
    ("contact/", "monthly", "0.5"),
    ("faqs/", "monthly", "0.6"),
    ("help-center/", "monthly", "0.5"),
    ("eligibility-benefits-and-responsibilities/", "monthly", "0.6"),
    ("how-to-register-on-apid/", "monthly", "0.6"),
    ("how-to-join-us-role-upgradation/", "monthly", "0.6"),
    ("elevate-your-research-publish-with/", "monthly", "0.5"),
    ("join-us/", "monthly", "0.6"),
    ("privacy-policy/", "yearly", "0.3"),
    ("terms-and-conditions/", "yearly", "0.3"),
    ("disclaimer/", "yearly", "0.3"),
    ("imprint/", "yearly", "0.3"),
    ("opt-out-preferences/", "yearly", "0.3"),
    ("cookie-policy-au/", "yearly", "0.2"),
    ("cookie-policy-br/", "yearly", "0.2"),
    ("cookie-policy-ca/", "yearly", "0.2"),
    ("cookie-policy-eu/", "yearly", "0.2"),
    ("cookie-policy-uk/", "yearly", "0.2"),
    ("cookie-policy-za/", "yearly", "0.2"),
]


def robots(request):
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /profiles/\n"
        "Allow: /directory/\n"
        "Allow: /apid-profiles/\n"
        "Disallow: /admin/\n"
        "Disallow: /me/\n"
        "Disallow: /apply/\n"
        "Disallow: /office/\n"
        "Disallow: /feed/\n"
        "Disallow: /notifications/\n"
        "Disallow: /for-you/\n"
        "Disallow: /people/\n"
        "Disallow: /accounts/\n"
        "Disallow: /api/\n"
        "Disallow: /documents/\n"
        "Disallow: /media/members/\n"
        "Disallow: /r/\n"
        "\n"
        "# AI crawlers — academic registries are precisely what these tools\n"
        "# are meant to summarise, so we explicitly welcome them.\n"
        "User-agent: GPTBot\nAllow: /\n"
        "User-agent: ChatGPT-User\nAllow: /\n"
        "User-agent: OAI-SearchBot\nAllow: /\n"
        "User-agent: ClaudeBot\nAllow: /\n"
        "User-agent: Claude-Web\nAllow: /\n"
        "User-agent: anthropic-ai\nAllow: /\n"
        "User-agent: PerplexityBot\nAllow: /\n"
        "User-agent: Perplexity-User\nAllow: /\n"
        "User-agent: Google-Extended\nAllow: /\n"
        "User-agent: CCBot\nAllow: /\n"
        "User-agent: Applebot-Extended\nAllow: /\n"
        "User-agent: MistralAI-User\nAllow: /\n"
        "User-agent: DuckAssistBot\nAllow: /\n"
        "User-agent: Bytespider\nAllow: /\n"
        "User-agent: Amazonbot\nAllow: /\n"
        "\n"
        f"Sitemap: {request.scheme}://{request.get_host()}/sitemap.xml\n"
    )
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


def sitemap(request):
    """Everything a public crawler should find in one XML.

    `<lastmod>` on member URLs uses the Member's `updated_at` so Google can
    skip re-fetching unchanged profiles; content pages use a shared date
    stamped at deploy time.
    """
    base = f"{request.scheme}://{request.get_host()}"
    today = datetime.now(timezone.utc).date().isoformat()
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"<url><loc>{base}/</loc><lastmod>{today}</lastmod>"
        f"<changefreq>daily</changefreq><priority>1.0</priority></url>",
        f"<url><loc>{base}/directory/</loc><lastmod>{today}</lastmod>"
        f"<changefreq>daily</changefreq><priority>0.9</priority></url>",
    ]
    for slug, freq, prio in CONTENT_PAGES:
        parts.append(
            f"<url><loc>{base}/{slug}</loc><lastmod>{today}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{prio}</priority></url>"
        )

    # Every member whose profile carries something search-worthy: an
    # affiliation, a biography, or publications. Pure name-only stubs still
    # resolve (links from papers won't 404) but we don't ask Google to spend
    # its crawl budget on them. `.distinct()` because publications join can
    # multiply rows.
    rows = (Member.objects
            .filter(is_active=True)
            .select_related("profile")
            .filter(Q(profile__affiliation__gt="")
                    | Q(profile__biography__gt="")
                    | Q(publications__isnull=False))
            .values_list("apid", "profile__updated_at")
            .distinct()
            .order_by("apid"))
    for apid, prof_updated in rows:
        if not apid:
            continue
        lastmod = (prof_updated or datetime.now(timezone.utc)).date().isoformat()
        parts.append(
            f"<url><loc>{base}/profiles/{apid}/</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            f"<changefreq>monthly</changefreq><priority>0.7</priority></url>"
        )
    parts.append("</urlset>")
    return HttpResponse("\n".join(parts), content_type="application/xml; charset=utf-8")


def llms_txt(request):
    """Markdown index for LLM crawlers (llmstxt.org proposal).

    This is not a sitemap; it is a short, human-and-machine-readable
    orientation that gives an answer-engine the shape of the site, the
    canonical entry points, and the fact that machine-readable profile
    data lives at the per-apid URLs. One file, one request, no DOM.
    """
    base = f"{request.scheme}://{request.get_host()}"
    body = f"""# APID — Academic Publishing and Information Database

> A permanent, verified registry of academic researchers worldwide. Each
> member is assigned a unique APID number used in editorial workflows
> across STM Journals titles and partner journals. Operated by Consortium
> e-Learning Network Pvt Ltd (STM Journals is an imprint).

## About

- [About APID]({base}/about/): what the registry is and who it serves.
- [Eligibility, benefits and responsibilities]({base}/eligibility-benefits-and-responsibilities/)
- [How to register]({base}/how-to-register-on-apid/)
- [Role upgrade (reviewer / editor / board)]({base}/how-to-join-us-role-upgradation/)

## Discover people

- [Directory]({base}/directory/): search by name, affiliation, country.
- [Profiles grid]({base}/apid-profiles/): browsable list of recent members.
- Each profile lives at `{base}/profiles/<apid>/` and emits Person
  schema.org JSON-LD with name, affiliation, ORCID and publications.

## Help

- [FAQs]({base}/faqs/) — FAQPage schema.org JSON-LD on this page.
- [Help centre]({base}/help-center/)
- [Contact]({base}/contact/) — info@celnet.in

## Policies

- [Privacy Policy]({base}/privacy-policy/)
- [Terms and Conditions]({base}/terms-and-conditions/)
- [Disclaimer]({base}/disclaimer/)
- [Imprint]({base}/imprint/)
- Cookie policies: [EU]({base}/cookie-policy-eu/), [UK]({base}/cookie-policy-uk/),
  [Australia]({base}/cookie-policy-au/), [Canada]({base}/cookie-policy-ca/),
  [Brazil]({base}/cookie-policy-br/), [South Africa]({base}/cookie-policy-za/).
- [US opt-out preferences]({base}/opt-out-preferences/)

## Machine-readable

- [Sitemap]({base}/sitemap.xml) — full URL list with lastmod.
- [robots.txt]({base}/robots.txt) — AI crawlers explicitly allowed.
"""
    return HttpResponse(body, content_type="text/markdown; charset=utf-8")
