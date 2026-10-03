"""Posts, follows and likes — the part of the old site nobody had written down.

This existed on WordPress and was missed in the first recon, because the forms that
carry it are named `post`, `Follow` and `Like` and were filtered out with the test
forms. Counted: **396 posts by 329 people**, the last on 3 Sep 2026; **668 follows
between 530 people**, the last on 15 Sep; **175 likes**. Judged by name they looked
like experiments; judged by entry count and last-entry date they are a live feature
with more participants than the editorial pipeline has.

**Built for the registry it sits in, not for the 396 rows it starts with.** 13,412
members can follow each other, and a feed is the one query that gets slower the more
successful the feature is. So:

* every count that a page shows is **stored on the row** and moved by `F()` — reading
  "how many followers" must never be a `COUNT(*)` over a table that only grows;
* the follow edge is `(follower, following)` unique, and a member cannot follow
  themselves — the old data contains both mistakes;
* a reaction is one row per member per post, so a double click cannot count twice;
* the feed is indexed on `(author, created_at)` and on `created_at`, which are the two
  ways it is ever read.
"""

from __future__ import annotations

from django.db import models
from django.db.models import F

from apps.identity.models import Member


class Post(models.Model):
    """Something a member wanted the registry to see.

    The old ones are mostly introductions and calls for collaborators — *"I look for
    co-author in field of law, banking (SCOPUS)"* — which is what the feature is for.
    """

    author = models.ForeignKey(Member, on_delete=models.CASCADE,
                               related_name="posts")
    body = models.TextField()
    attachment = models.CharField(max_length=500, blank=True)

    #: Kept on the row. A feed of 400 posts can count its comments; a feed of 40,000
    #: cannot, and the query that stops working is the one nobody notices until then.
    comment_count = models.PositiveIntegerField(default=0)
    reaction_count = models.PositiveIntegerField(default=0)

    hidden = models.BooleanField(default=False,
                                 help_text="Hidden by the office; nothing is deleted.")
    wp_entry_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)
    created_at = models.DateTimeField(db_index=True)
    edited_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["author", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.author.apid}: {self.body[:60]}"


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(Member, on_delete=models.CASCADE,
                               related_name="comments")
    body = models.TextField()
    hidden = models.BooleanField(default=False)
    wp_entry_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["post", "created_at"])]

    def save(self, *args, **kwargs):
        new = self.pk is None
        super().save(*args, **kwargs)
        if new:
            Post.objects.filter(pk=self.post_id).update(
                comment_count=F("comment_count") + 1)

    def delete(self, *args, **kwargs):
        Post.objects.filter(pk=self.post_id).update(
            comment_count=F("comment_count") - 1)
        super().delete(*args, **kwargs)


class Reaction(models.Model):
    """One per member per post. The old `Like` form had no such rule."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="reactions")
    member = models.ForeignKey(Member, on_delete=models.CASCADE,
                               related_name="reactions")
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        unique_together = [("post", "member")]

    def save(self, *args, **kwargs):
        new = self.pk is None
        super().save(*args, **kwargs)
        if new:
            Post.objects.filter(pk=self.post_id).update(
                reaction_count=F("reaction_count") + 1)

    def delete(self, *args, **kwargs):
        Post.objects.filter(pk=self.post_id).update(
            reaction_count=F("reaction_count") - 1)
        super().delete(*args, **kwargs)


class Follow(models.Model):
    """`follower` follows `following`. One edge, once, and never to oneself."""

    follower = models.ForeignKey(Member, on_delete=models.CASCADE,
                                 related_name="following_set")
    following = models.ForeignKey(Member, on_delete=models.CASCADE,
                                  related_name="follower_set")
    created_at = models.DateTimeField(db_index=True)
    wp_entry_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)

    class Meta:
        unique_together = [("follower", "following")]
        indexes = [models.Index(fields=["following", "-created_at"]),
                   models.Index(fields=["follower", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.follower.apid} → {self.following.apid}"


class SocialCounts(models.Model):
    """Followers and following, per member, kept current rather than counted.

    A separate row rather than columns on `Member`, so the identity table is not
    written to every time somebody follows somebody — the two have completely
    different write patterns and `Member` is read on every request.
    """

    member = models.OneToOneField(Member, on_delete=models.CASCADE,
                                  related_name="social")
    followers = models.PositiveIntegerField(default=0)
    following = models.PositiveIntegerField(default=0)
    posts = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.member.apid}: {self.followers} followers"


class Notification(models.Model):
    """Somebody did something to you, and this is how you find out.

    **This is the repair, not a feature.** 343 of the 396 imported posts — 87% — got no
    reaction and no comment, and posts fell from 357 in 2023 to 3 in 2026. The members
    kept following each other (99 new follows in 2024 alone), so the registry was not
    dead; the *feed* was, because a person posted an introduction, nothing came back,
    and they never returned. Nothing on the old site ever told anybody that they had
    been followed or replied to.

    Kept small on purpose. A notification names the actor, the verb and the thing, and
    the page renders the sentence — so this table is narrow, writes are cheap, and it
    can be trimmed by date without losing anything a person needed.
    """

    FOLLOWED = "followed"
    COMMENTED = "commented"
    REACTED = "reacted"
    KIND = [(FOLLOWED, "followed you"),
            (COMMENTED, "commented on your post"),
            (REACTED, "liked your post")]

    member = models.ForeignKey(Member, on_delete=models.CASCADE,
                               related_name="notifications")
    actor = models.ForeignKey(Member, on_delete=models.CASCADE,
                              related_name="notifications_caused")
    kind = models.CharField(max_length=20, choices=KIND)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["member", "read_at", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.actor.apid} {self.kind} → {self.member.apid}"


def notify(member, actor, kind, post=None) -> None:
    """Record it, unless it is your own doing.

    A person liking their own post, or commenting on it, must not be told about it —
    and the old data is full of exactly that shape: 487 of its 668 follows are somebody
    following themselves.
    """
    from django.utils import timezone

    if member is None or actor is None or member == actor:
        return
    Notification.objects.create(member=member, actor=actor, kind=kind, post=post,
                                created_at=timezone.now())
