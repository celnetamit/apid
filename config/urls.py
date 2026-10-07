"""The public URLs, and every address the old site ever handed out.

`/apid-profiles/apid/<id>/` is first-class rather than an afterthought: 5,114 of those
are in the wild, printed in papers and pasted into CVs, and they keyed on a Formidable
entry rather than on anything stable. Each one redirects permanently to the member's
APID.
"""

from django.contrib import admin
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import include, path, re_path

from django.contrib.auth.views import LoginView
from django.views.static import serve

from apps.editorial import invite_views
from apps.editorial import stats_api as editorial_stats_api
from apps.editorial import views as editorial
from apps.identity import lookup
from apps.identity import views as identity
from apps.social import views as social
from apps.profiles import seo as profile_seo
from apps.profiles import share_views
from apps.content import views as content_pages
from apps.profiles import views as profiles


def _serve_media(request, path, document_root=None):
    """Uploads, except the publisher signature blocks: a director's signature and the brand
    stamps are only ever embedded in the PDFs, so nobody but office staff needs the file."""
    from django.http import Http404
    if path.startswith("imprints/") and not (request.user.is_authenticated and request.user.is_staff):
        raise Http404
    return serve(request, path, document_root=document_root)


urlpatterns = [
    path("", content_pages.home_marketing, name="home"),
    path("pages/<slug:slug>/", profiles.page, name="page"),
    path("directory/", profiles.directory, name="directory"),
    path("profiles/<str:apid>/", profiles.profile, name="profile"),
    # wisp 2026-10-02 pm: share widget assets + referral capture.
    path("profiles/<str:apid>/qr.png", share_views.profile_qr, name="profile-qr"),
    path("profiles/<str:apid>/card.png", share_views.profile_card, name="profile-card"),
    path("r/<str:apid>/", share_views.capture_referral, name="referral-capture"),

    # The old site's addresses. Permanent redirects, because a link in a published
    # paper cannot be updated.
    path("apid-profiles/apid/<int:entry_id>/", profiles.legacy_profile,
         name="legacy-profile"),
    path("apid-profiles/apid/<int:entry_id>", profiles.legacy_profile),

    # The member's own pages.
    # Explicit, before the auth include, so the page knows whether to draw the Google
    # button. `configured()` is asked at render time rather than at import: the
    # credentials come from the environment and a restart is not a redeploy.
    path("accounts/login/", LoginView.as_view(
        extra_context={"google_ready": identity.google_is_ready}), name="login"),
    path("accounts/register/", identity.register, name="register"),
    path("accounts/google/start/", identity.google_start, name="google-start"),
    path("accounts/google/callback/", identity.google_callback,
         name="google-callback"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("me/", identity.dashboard, name="dashboard"),
    path("me/profile/", identity.edit_profile, name="edit-profile"),
    path("me/photograph/", identity.edit_picture, name="edit-picture"),
    path("me/publications/", identity.publications, name="publications"),
    path("me/publications/orcid/", identity.orcid_sync, name="orcid-sync"),
    path("me/publications/add/", identity.edit_publication, name="add-publication"),
    path("me/publications/<int:pk>/", identity.edit_publication,
         name="edit-publication"),
    path("me/publications/<int:pk>/remove/", identity.delete_publication,
         name="delete-publication"),

    # Open by design and says nothing about the data: it is what a monitor asks.
    # A read-only window for the other systems in the estate — signed, narrow, and
    # answering exactly one question. See `apps.identity.lookup`.
    path("api/identity/members/", lookup.members, name="identity-lookup"),

    # mng calls this when a board application is accepted (or an appointment
    # ends) so APID's local `Appointment` table stays in sync with the mng
    # decision it never made.
    path("api/reflect/appointment/",
         __import__("apps.editorial.reflect", fromlist=["appointment"]).appointment,
         name="reflect-appointment"),

    path("healthz", lambda r: HttpResponse("ok\n", content_type="text/plain")),

    # For crawlers. Both files must be reachable without sign-in — the middleware
    # gates the rest.
    path("robots.txt", profile_seo.robots),
    path("sitemap.xml", profile_seo.sitemap),
    path("llms.txt", profile_seo.llms_txt),

    # The feed, and the four things a member can do on it.
    path("feed/", social.feed, name="feed"),
    path("feed/post/", social.write, name="write"),
    path("notifications/", social.notifications, name="notifications"),
    path("for-you/", social.for_you, name="for-you"),
    path("feed/<int:pk>/comment/", social.comment, name="comment"),
    path("feed/<int:pk>/react/", social.react, name="react"),
    path("people/<str:apid>/follow/", social.follow, name="follow"),
    path("people/<str:apid>/followers/", social.followers, name="followers"),
    path("people/<str:apid>/following/", social.following, name="following"),

    # The editorial office.
    path("apply/", editorial.apply, name="apply"),
    # Shared-key aggregate stats for the ops cockpit. 404 without the header.
    path("_apid/stats.json", editorial_stats_api.stats_json, name="apid-stats-json"),
    # Invitations: office → member
    path("profiles/<str:apid>/invite/", invite_views.invite_from_profile,
         name="invite-from-profile"),
    path("invites/<str:token>/", invite_views.respond_to_invitation,
         name="invite-respond"),
    # wisp 2026-10-02: office dashboard
    path("office/", editorial.dashboard, name="office-dashboard"),
    path("office/queue/", editorial.queue, name="editorial-queue"),
    path("office/suggestions/", editorial.suggestions, name="editorial-suggestions"),
    path("office/flow/", editorial.flow, name="editorial-flow"),
    path("office/application/<str:pk>/", editorial.application, name="application"),
    path("office/application/<str:pk>/cv", editorial.application_cv, name="application-cv"),
    path("office/approved/", editorial.approved_profiles, name="approved-profiles"),
    path("office/impersonate/stop/", editorial.stop_impersonating, name="stop-impersonating"),
    path("office/impersonate/<str:apid>/", editorial.impersonate, name="impersonate"),
    path("office/application/<str:pk>/decide/", editorial.decide, name="decide"),
    path("office/application/<int:pk>/resume.pdf", editorial.resume_pdf,
         name="application-resume"),
    # wisp 2026-10-02: journal CE routes
    path("office/journals/", editorial.journals_index, name="journals-index"),
    path("office/journals/<int:pk>/manage/", editorial.journal_manage, name="journal-manage"),
    path("office/editors/", editorial.editors_index, name="editors-index"),
    path("office/editors/new/", editorial.editor_edit, name="editor-new"),
    path("office/editors/<int:pk>/edit/", editorial.editor_edit, name="editor-edit"),
    path("office/editors/<int:pk>/delete/", editorial.editor_delete, name="editor-delete"),
    path("office/emails/", editorial.emails_log, name="emails-log"),
    path("office/emails/templates/", editorial.email_templates_index, name="email-templates"),
    path("office/emails/templates/<int:pk>/", editorial.email_template_edit, name="email-template-edit"),
    path("office/emails/<int:pk>/", editorial.email_log_detail, name="email-log-detail"),
    path("office/boards/", editorial.boards, name="boards"),
    path("office/boards/<int:pk>/", editorial.board, name="board"),
    path("documents/appointments/<int:pk>/letter.pdf", editorial.empanelment_letter,
         name="empanelment-letter"),
    path("documents/appointments/<int:pk>/certificate.pdf",
         editorial.editorial_certificate, name="editorial-certificate"),
    path("verify/appointment/<int:pk>/",
         editorial.verify_appointment, name="verify-appointment"),

    # wisp 2026-10-02: WP-compatible content pages
    path("about/", content_pages.page, {"slug": "about"}, name="page-about"),
    path("apid-profiles/", content_pages.apid_profiles, name="apid-profiles"),  # wisp 2026-10-02 live grid
    path("coming-soon/", content_pages.page, {"slug": "coming-soon"}, name="page-coming-soon"),
    path("contact/", content_pages.page, {"slug": "contact"}, name="page-contact"),
    path("cookie-policy-au/", content_pages.page, {"slug": "cookie-policy-au"}, name="page-cookie-au"),
    path("cookie-policy-br/", content_pages.page, {"slug": "cookie-policy-br"}, name="page-cookie-br"),
    path("cookie-policy-ca/", content_pages.page, {"slug": "cookie-policy-ca"}, name="page-cookie-ca"),
    path("cookie-policy-eu/", content_pages.page, {"slug": "cookie-policy-eu"}, name="page-cookie-eu"),
    path("cookie-policy-uk/", content_pages.page, {"slug": "cookie-policy-uk"}, name="page-cookie-uk"),
    path("cookie-policy-za/", content_pages.page, {"slug": "cookie-policy-za"}, name="page-cookie-za"),
    path("disclaimer/", content_pages.page, {"slug": "disclaimer"}, name="page-disclaimer"),
    path("elevate-your-research-publish-with/", content_pages.page, {"slug": "elevate-your-research-publish-with"}, name="page-elevate"),
    path("eligibility-benefits-and-responsibilities/", content_pages.page, {"slug": "eligibility-benefits-and-responsibilities"}, name="page-eligibility"),
    path("faqs/", content_pages.page, {"slug": "faqs"}, name="page-faqs"),
    path("help-center/", content_pages.page, {"slug": "help-center"}, name="page-help"),
    path("how-to-join-us-role-upgradation/", content_pages.page, {"slug": "how-to-join-us-role-upgradation"}, name="page-howjoin"),
    path("how-to-register-on-apid/", content_pages.page, {"slug": "how-to-register-on-apid"}, name="page-howreg"),
    path("imprint/", content_pages.page, {"slug": "imprint"}, name="page-imprint"),
    path("join-us/", content_pages.page, {"slug": "join-us"}, name="page-joinus"),
    path("maintenance/", content_pages.page, {"slug": "maintenance"}, name="page-maintenance"),
    path("opt-out-preferences/", content_pages.page, {"slug": "opt-out-preferences"}, name="page-optout"),
    path("privacy-policy/", content_pages.page, {"slug": "privacy-policy"}, name="page-privacy"),
    path("terms-and-conditions/", content_pages.page, {"slug": "terms-and-conditions"}, name="page-terms"),

    # Form pages: keep APID's working auth, 301 the WP URLs there.
    path("login/", lambda r: redirect("/accounts/login/", permanent=True)),
    path("registration/", lambda r: redirect("/accounts/register/", permanent=True)),
    path("reset-password/", lambda r: redirect("/accounts/password_reset/", permanent=True)),
    # wisp 2026-10-02: short WP slugs that would otherwise hit the auth gate.
    path("help/", lambda r: redirect("/help-center/", permanent=True)),
    path("membership/", lambda r: redirect("/eligibility-benefits-and-responsibilities/", permanent=True)),
    path("benefits/", lambda r: redirect("/eligibility-benefits-and-responsibilities/", permanent=True)),

    path("admin/", admin.site.urls),

    # Served by Django rather than by Caddy, on purpose: the uploads are behind the
    # same gate as the pages that show them, so while the site is closed a profile
    # picture is not readable by anyone who guesses its path. Caddy can take this over
    # the day the registry goes public.
    re_path(r"^media/(?P<path>.*)$", _serve_media,
            {"document_root": settings.MEDIA_ROOT}),
]

