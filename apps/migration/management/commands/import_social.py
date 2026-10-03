"""The posts, follows and likes the first recon missed.

Three Formidable forms carry a live social layer that was filtered out with the test
forms because of what they are called: `post` (396 entries, 329 people, last on 3 Sep
2026), `Follow` (668 between 530 people, last on 15 Sep) and `Like` (175). More people
have used this than have ever applied for an editorial role.

**People are matched by email, not by the entry's user id.** Both are present and the
email is the one that is right: the follow rows record the follower and the person
followed *as addresses*, and the user id on the row is only ever the person who
submitted the form. Matching the other side any other way is impossible.

Everything unmatched is counted and named, never guessed at.
"""

from __future__ import annotations

import collections

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from apps.identity.models import Member
from apps.migration.reader import WordPress, first
from apps.social.models import Comment, Follow, Post, Reaction, SocialCounts

POSTS, FOLLOWS, LIKES, COMMENTS = 193, 236, 238, 194

POST_BODY, POST_EMAIL, POST_FILE, POST_OWN_ID = "knbg7", "btuqa", "qahg4", "gaxo2"
FOLLOW_FROM, FOLLOW_TO = "hqvy9", "vkjlo"
LIKE_FROM, LIKE_TO = "emeyz", "5mv4l"
COMMENT_POST, COMMENT_BODY = "ylc3o", "x4g2g"


class Command(BaseCommand):
    help = "Import posts, follows and likes from the WordPress forms."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        wp = WordPress()
        counts: collections.Counter = collections.Counter()
        try:
            by_email = {}
            for member in Member.objects.exclude(email=""):
                by_email.setdefault(member.email.strip().lower(), member)
            by_wp = {m.wp_user_id: m for m in Member.objects.all() if m.wp_user_id}

            def whose(entry, key):
                address = first(entry.get(key)).strip().lower()
                if address and address in by_email:
                    return by_email[address]
                return None

            posts = self._posts(wp, by_email, by_wp, counts, options["dry_run"])
            self._comments(wp, posts, by_wp, counts, options["dry_run"])
            self._edges(wp, FOLLOWS, FOLLOW_FROM, FOLLOW_TO, whose, counts,
                        options["dry_run"], Follow)
            self._likes(wp, posts, whose, counts, options["dry_run"])
            if not options["dry_run"]:
                self._recount(counts)
        finally:
            wp.close()
        self._report(counts)

    # --- posts ---------------------------------------------------------------

    def _posts(self, wp, by_email, by_wp, counts, dry):
        posts = {}
        for entry in wp.entries(POSTS):
            counts["post entries"] += 1
            body = first(entry.get(POST_BODY)).strip()
            author = (by_email.get(first(entry.get(POST_EMAIL)).strip().lower())
                      or by_wp.get(entry.user_id))
            if author is None:
                counts["post with no member"] += 1
                continue
            if not body:
                # A post with no text is the file-only case; kept, because the file is
                # the post. Counted so the number is visible either way.
                counts["post with no text"] += 1
            if dry:
                continue
            post, _ = Post.objects.update_or_create(
                wp_entry_id=entry.id,
                defaults={"author": author, "body": body,
                          "attachment": first(entry.get(POST_FILE))[:500],
                          "created_at": entry.created_at})
            posts[f"entry:{entry.id}"] = post
            own = first(entry.get(POST_OWN_ID)).strip()
            if own:
                # The form keeps its own "Post ID" counter, empty on the first three
                # posts and 1, 2, 3… after — and that, not the entry id, is what the
                # comment form writes down. Both keys are kept so either finds it.
                posts[f"own:{own}"] = post
            counts["posts imported"] += 1
        return posts

    def _comments(self, wp, posts, by_wp, counts, dry):
        for entry in wp.entries(COMMENTS):
            counts["comment entries"] += 1
            body = first(entry.get(COMMENT_BODY)).strip()
            author = by_wp.get(entry.user_id)
            named = first(entry.get(COMMENT_POST)).strip()
            post = posts.get(f"own:{named}") or posts.get(f"entry:{named}")
            if not body or author is None or post is None:
                counts["comment that could not be placed"] += 1
                continue
            if dry:
                continue
            Comment.objects.update_or_create(
                wp_entry_id=entry.id,
                defaults={"post": post, "author": author, "body": body,
                          "created_at": entry.created_at})
            counts["comments imported"] += 1

    # --- follows -------------------------------------------------------------

    def _edges(self, wp, form, from_key, to_key, whose, counts, dry, model):
        for entry in wp.entries(form):
            counts["follow entries"] += 1
            follower = whose(entry, from_key)
            following = whose(entry, to_key)
            if follower is None or following is None:
                counts["follow whose people are not both here"] += 1
                continue
            if follower == following:
                # In the old data. A self-follow is a row nothing can render.
                counts["follow of oneself"] += 1
                continue
            if dry:
                continue
            _, made = model.objects.get_or_create(
                follower=follower, following=following,
                defaults={"created_at": entry.created_at, "wp_entry_id": entry.id})
            counts["follows imported" if made else "follow already there"] += 1

    def _likes(self, wp, posts, whose, counts, dry):
        """The like rows name two people, not a post.

        `Unique` reads `[2783]_[2781]` on some rows and `_someone@example.com` on
        others — two different schemes, neither of which survives the move. So a like
        is attached to the most recent post by the person who was liked at that moment,
        which is what the button meant on the page, and the ones that cannot be placed
        are counted rather than invented.
        """
        for entry in wp.entries(LIKES):
            counts["like entries"] += 1
            member = whose(entry, LIKE_FROM)
            liked = whose(entry, LIKE_TO)
            if member is None or liked is None:
                counts["like whose people are not both here"] += 1
                continue
            target = (Post.objects.filter(author=liked,
                                          created_at__lte=entry.created_at)
                      .order_by("-created_at").first())
            if target is None:
                # The like is older than any post we have for that person. Their first
                # post is the only honest target, and the case is counted so the number
                # is on the record rather than hidden inside "imported".
                target = Post.objects.filter(author=liked).order_by("created_at").first()
                if target is not None:
                    counts["like attached to the person's first post"] += 1
            if target is None:
                counts["like whose person has never posted"] += 1
                continue
            if dry:
                continue
            _, made = Reaction.objects.get_or_create(
                post=target, member=member,
                defaults={"created_at": entry.created_at})
            counts["likes imported" if made else "like already there"] += 1

    # --- counters ------------------------------------------------------------

    def _recount(self, counts):
        """Rebuild every stored count from the rows. Idempotent, and the only place
        the two can be made to agree after an import."""
        SocialCounts.objects.all().update(followers=0, following=0, posts=0)
        for member_id, n in (Follow.objects.values_list("following")
                             .annotate(n=Count("id"))):
            SocialCounts.objects.update_or_create(
                member_id=member_id, defaults={"followers": n})
        for member_id, n in (Follow.objects.values_list("follower")
                             .annotate(n=Count("id"))):
            row, _ = SocialCounts.objects.get_or_create(member_id=member_id)
            row.following = n
            row.save(update_fields=["following"])
        for member_id, n in Post.objects.values_list("author").annotate(n=Count("id")):
            row, _ = SocialCounts.objects.get_or_create(member_id=member_id)
            row.posts = n
            row.save(update_fields=["posts"])
        for post in Post.objects.all().only("id"):
            Post.objects.filter(pk=post.pk).update(
                comment_count=Comment.objects.filter(post_id=post.pk).count(),
                reaction_count=Reaction.objects.filter(post_id=post.pk).count())
        counts["members with a social count"] = SocialCounts.objects.count()

    def _report(self, counts):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("census"))
        for key, value in sorted(counts.items()):
            self.stdout.write(f"  {key:44} {value}")
