"""The unread count, on every page.

A bell that is only on the feed is a bell nobody hears: a member arrives on their own
profile from an email or a search result, sees nothing, and leaves. One indexed count
per request for a signed-in member, and nothing at all for anyone else.
"""

from apps.social.views import unread_count


def notifications(request):
    return {"unread_notifications": unread_count(request)}
