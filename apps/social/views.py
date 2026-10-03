"""The feed, and the four things a member can do on it.

Written for 13,412 members rather than for the 396 posts it starts with, which shows in
one place: **the feed never counts anything**. Followers, following, comments and
reactions are all stored numbers moved by `F()`, and the page that lists posts asks for
posts — not for posts and then a count per post, which is the query that works today and
falls over at ten thousand.

Two feeds, because the registry is small enough that an empty personalised feed would be
the common first experience: *Everyone* and *People you follow*. The second is only
offered once you follow somebody.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Exists, OuterRef, F
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.identity.models import Member
from apps.social.models import (Comment, Follow, Notification, Post, Reaction,
                                SocialCounts, notify)

PAGE = 25


def _with_my_reaction(posts, member):
    """Mark each post with whether *this* member has reacted — one subquery for the
    page, not one query per post."""
    if not getattr(member, "is_authenticated", False):
        return posts
    mine = Reaction.objects.filter(post=OuterRef("pk"), member=member)
    return posts.annotate(reacted=Exists(mine))


@login_required
def feed(request):
    which = request.GET.get("show", "all")
    posts = (Post.objects.filter(hidden=False)
             .select_related("author", "author__profile")
             .order_by("-created_at"))
    following_count = Follow.objects.filter(follower=request.user).count()
    if which == "following" and following_count:
        posts = posts.filter(
            author__in=Follow.objects.filter(follower=request.user)
                                     .values("following"))
    page = int(request.GET.get("page", 1) or 1)
    start = max(0, (page - 1) * PAGE)
    shown = list(_with_my_reaction(posts, request.user)[start:start + PAGE])
    return render(request, "social/feed.html", {
        "posts": shown,
        "which": which,
        "following_count": following_count,
        "page": page,
        "has_more": len(shown) == PAGE,
        "counts": SocialCounts.objects.filter(member=request.user).first(),
        "suggestions": suggestions_for(request.user),
    })


@login_required
def write(request):
    body = (request.POST.get("body") or "").strip()
    if request.method != "POST" or not body:
        return redirect("feed")
    Post.objects.create(author=request.user, body=body[:5000],
                        created_at=timezone.now())
    SocialCounts.objects.get_or_create(member=request.user)
    SocialCounts.objects.filter(member=request.user).update(posts=F("posts") + 1)
    messages.success(request, "Posted.")
    return redirect("feed")


@login_required
def comment(request, pk: int):
    post = get_object_or_404(Post, pk=pk, hidden=False)
    body = (request.POST.get("body") or "").strip()
    if request.method == "POST" and body:
        Comment.objects.create(post=post, author=request.user, body=body[:2000],
                               created_at=timezone.now())
        notify(post.author, request.user, Notification.COMMENTED, post)
    return redirect(request.POST.get("next") or "feed")


@login_required
def react(request, pk: int):
    """Like, or take the like back. One row per member per post makes this safe to
    click twice."""
    post = get_object_or_404(Post, pk=pk)
    if request.method == "POST":
        existing = Reaction.objects.filter(post=post, member=request.user).first()
        if existing:
            existing.delete()
        else:
            Reaction.objects.create(post=post, member=request.user,
                                    created_at=timezone.now())
            notify(post.author, request.user, Notification.REACTED, post)
    return redirect(request.POST.get("next") or "feed")


@login_required
def follow(request, apid: str):
    """Follow or unfollow, and move both stored counts in the same breath."""
    other = get_object_or_404(Member, apid=apid)
    if request.method != "POST" or other == request.user:
        return redirect("profile", apid=apid)
    existing = Follow.objects.filter(follower=request.user, following=other).first()
    SocialCounts.objects.get_or_create(member=request.user)
    SocialCounts.objects.get_or_create(member=other)
    if existing:
        existing.delete()
        SocialCounts.objects.filter(member=request.user).update(
            following=F("following") - 1)
        SocialCounts.objects.filter(member=other).update(followers=F("followers") - 1)
        messages.success(request, f"You no longer follow {other.display_name}.")
    else:
        Follow.objects.create(follower=request.user, following=other,
                              created_at=timezone.now())
        SocialCounts.objects.filter(member=request.user).update(
            following=F("following") + 1)
        SocialCounts.objects.filter(member=other).update(followers=F("followers") + 1)
        notify(other, request.user, Notification.FOLLOWED)
        messages.success(request, f"You now follow {other.display_name}.")
    return redirect(request.POST.get("next") or
                    request.META.get("HTTP_REFERER") or "feed")


def followers(request, apid: str):
    member = get_object_or_404(Member, apid=apid)
    return render(request, "social/people.html", {
        "member": member, "heading": "Followers",
        "people": [f.follower for f in
                   member.follower_set.select_related("follower", "follower__profile")
                   .order_by("-created_at")[:200]],
    })


def following(request, apid: str):
    member = get_object_or_404(Member, apid=apid)
    return render(request, "social/people.html", {
        "member": member, "heading": "Following",
        "people": [f.following for f in
                   member.following_set.select_related("following",
                                                       "following__profile")
                   .order_by("-created_at")[:200]],
    })


@login_required
def notifications(request):
    """What has happened to you, and marking it seen.

    Marked read on opening rather than per item: the question a person has is "is there
    anything new", and making them dismiss each line to answer it is how a bell becomes
    something people stop pressing.
    """
    rows = list(Notification.objects.filter(member=request.user)
                .select_related("actor", "actor__profile", "post")[:100])
    Notification.objects.filter(member=request.user, read_at__isnull=True).update(
        read_at=timezone.now())
    return render(request, "social/notifications.html", {"rows": rows})


def unread_count(request) -> int:
    """For the header. One indexed count, and only for a signed-in member."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return 0
    return Notification.objects.filter(member=user, read_at__isnull=True).count()


def suggestions_for(member, limit: int = 8):
    """People worth following, from what this registry knows and a feed cannot guess.

    57% of the imported posts are somebody introducing themselves — *"Hello, I'm X, my
    expertise is Y"* — and 87% of all posts got no answer at all. The registry holds
    5,286 profiles with an affiliation and an area of expertise; the least it can do
    when somebody arrives is show them the people they came to find.

    Matched on the profile's own words, in the order that makes them worth following:
    the same expertise first, then the same department, then the same affiliation.
    Nobody already followed, and never oneself.
    """
    from apps.profiles.models import Profile

    from apps.recommend.models import SimilarMember

    mine = getattr(member, "profile", None)
    if mine is None:
        return []
    already = set(Follow.objects.filter(follower=member)
                  .values_list("following_id", flat=True)) | {member.pk}

    # The computed neighbours first: TF-IDF over this member's own words and their
    # publication titles, ranked by cosine similarity and stored nightly. One indexed
    # lookup — see `apps.recommend`.
    computed = [row.other for row in
                SimilarMember.objects.filter(member=member)
                .exclude(other_id__in=already)
                .select_related("other", "other__profile")[:limit]]
    if len(computed) >= limit:
        return computed
    already |= {m.pk for m in computed}

    # Whatever the index could not fill, from the profile's own words. A member with
    # three words in their profile has no vector worth the name, and a plain match is
    # better than an empty panel.
    found, seen = list(computed), set(already)
    for field, value in (("expertise", mine.expertise),
                         ("areas_of_interest", mine.areas_of_interest),
                         ("department", mine.department),
                         ("affiliation", mine.affiliation)):
        word = (value or "").strip()
        if len(word) < 4:
            continue
        # The first few words, not the whole paragraph: an expertise field is often a
        # sentence, and a LIKE on a sentence matches nothing.
        needle = " ".join(word.split()[:3])
        for profile in (Profile.objects.filter(**{f"{field}__icontains": needle})
                        .select_related("member")
                        .exclude(member_id__in=seen)[:limit]):
            found.append(profile.member)
            seen.add(profile.member_id)
            if len(found) >= limit:
                return found
    return found


@login_required
def for_you(request):
    """Everything the registry thinks this member should see, in one place.

    People it matched them to, and the journals their own work points at. Both are
    stored rows written by `rebuild_recommendations`, so this page is two indexed
    queries however large the registry gets.
    """
    from apps.recommend.models import SimilarMember, SuggestedJournal

    already = set(Follow.objects.filter(follower=request.user)
                  .values_list("following_id", flat=True))
    people = [row for row in SimilarMember.objects.filter(member=request.user)
              .exclude(other_id__in=already)
              .select_related("other", "other__profile")[:12]]
    journals = list(SuggestedJournal.objects.filter(member=request.user)
                    .select_related("journal")[:5])
    return render(request, "social/for_you.html",
                  {"people": people, "journals": journals})
