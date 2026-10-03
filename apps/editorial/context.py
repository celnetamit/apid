"""One flag, on every page: is this person the office?

A context processor rather than a mixin on each view. The header is rendered by every
template, including the ones the office never sees, and a link that appears only where
somebody remembered to pass a variable is a link that goes missing on the page where it
was needed.
"""

from apps.editorial.access import is_office


def office(request):
    return {"office": is_office(getattr(request, "user", None))}
