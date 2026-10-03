"""Template context processors — things every page may want to show.

The impersonation banner needs this: whether the current request is being made
*on behalf of* an admin (because the admin clicked "Login as" on a member).
"""


def impersonation(request):
    """Expose `impersonator_display` to every template.

    Empty when nobody is impersonating, non-empty while the office/admin is
    signed in as another member. The base template renders a red banner on it.
    """
    try:
        display = request.session.get("impersonator_display", "")
    except AttributeError:
        display = ""
    return {"impersonator_display": display}
