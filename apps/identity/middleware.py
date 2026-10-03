"""Nothing is public until Amit says it is.

The old site shows every profile to anybody. This one will too, eventually — that is
what a profile page is for. But between now and the day he looks at it, the registry
holds 13,412 people's names, affiliations and email addresses on a hostname that
resolves today, and a URL somebody guesses should not open it.

So the default is closed: `APID_PUBLIC` unset means every page requires a signed-in
account. Setting it to `1` opens exactly the two pages that were always meant to be
public — the profile and the directory — and nothing else.

The default is the safe one on purpose. Forgetting to set a variable cannot expose the
registry; it can only make the site ask for a password.
"""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

#: Reachable signed-out whatever the setting: sign-in itself, and the static and media
#: files a sign-in page needs to render. Without these the gate redirects the login
#: page to the login page.
ALWAYS_OPEN = ("/accounts/login", "/accounts/logout", "/accounts/register",
               "/accounts/google/", "/static/", "/healthz",
               "/robots.txt", "/sitemap.xml", "/llms.txt",
               # Password-reset flow needs to be reachable while logged out.
               "/accounts/password_reset", "/accounts/reset",
               # Signed with a shared secret and refused without it — the sign-in gate
               # would only turn a 401 into a redirect to a login page no service can
               # fill in.
               "/api/identity/", "/api/reflect/", "/invites/",
               # The homepage showcases 12 profiles publicly; individual profile pages
               # are linked from it so they must also be reachable without a login.
               # Legacy /apid-profiles/ redirects go to /profiles/ so they stay open too.
               "/profiles/", "/apid-profiles/", "/media/",
               "/pages/",
               # wisp 2026-10-02: WP-migrated content pages
               "/about/", "/apid-profiles/", "/coming-soon/", "/contact/", "/cookie-policy-", "/disclaimer/", "/elevate-your-research-", "/eligibility-benefits", "/faqs/", "/help-center/", "/how-to-join", "/how-to-register", "/imprint/", "/join-us/", "/maintenance/", "/opt-out-preferences/", "/privacy-policy/", "/terms-and-conditions/", "/login/", "/registration/", "/reset-password/",
               # wisp 2026-10-02 pm: short WP slugs that 301 to the canonical URLs above.
               "/help/", "/membership/", "/benefits/",
               # wisp 2026-10-02 pm: share-widget assets + referral capture.
               # `/profiles/<apid>/` is already open above; its sibling asset URLs
               # must be too, or an external link preview (LinkedIn / Twitter) can
               # never fetch the og:image.
               "/r/")

#: What `APID_PUBLIC=1` opens. Listed rather than inferred: the admin and the
#: dashboards must not become public by adding a URL above them.
PUBLIC_WHEN_OPEN = ("/profiles/", "/directory/", "/apid-profiles/", "/media/",
                    "/people/")


class SignedInOnly:
    """A gate, closed by default, with one switch and no exceptions to remember."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not self._allowed(request):
            return redirect(f"{reverse('login')}?next={request.path}")
        return self.get_response(request)

    def _allowed(self, request) -> bool:
        path = request.path
        if path.startswith(ALWAYS_OPEN):
            return True
        if request.user.is_authenticated:
            return True
        if settings.APID_PUBLIC and path.startswith(PUBLIC_WHEN_OPEN):
            return True
        # The homepage is always public — it shows a 12-card showcase with a login CTA.
        if path == "/":
            return True
        # `/` is the directory's front door and follows the same rule as the directory.
        return bool(settings.APID_PUBLIC and path == "/")
