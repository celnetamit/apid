"""The feed, the edges, and the counts that must not be counted."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.identity.models import Member
from apps.social.models import (Comment, Follow, Notification, Post, Reaction,
                                SocialCounts)

BACKEND = "apps.identity.backends.UsernameOrEmailBackend"


def _member(apid, name="Someone"):
    return Member.objects.create(username=f"u{apid}", apid=apid, full_name=name,
                                 email=f"u{apid}@example.org")


class Counting(TestCase):

    def setUp(self):
        self.me = _member("900001", "Me")
        self.other = _member("900002", "Other")
        self.post = Post.objects.create(author=self.other, body="Hello",
                                        created_at=timezone.now())

    def test_a_comment_moves_the_stored_count(self):
        """The feed never counts. A count that is computed per post is the query that
        works today and falls over at ten thousand."""
        Comment.objects.create(post=self.post, author=self.me, body="Hi",
                               created_at=timezone.now())
        self.post.refresh_from_db()
        assert self.post.comment_count == 1

    def test_removing_a_comment_moves_it_back(self):
        c = Comment.objects.create(post=self.post, author=self.me, body="Hi",
                                   created_at=timezone.now())
        c.delete()
        self.post.refresh_from_db()
        assert self.post.comment_count == 0

    def test_one_member_can_react_once(self):
        Reaction.objects.create(post=self.post, member=self.me,
                                created_at=timezone.now())
        from django.db import IntegrityError, transaction
        try:
            with transaction.atomic():
                Reaction.objects.create(post=self.post, member=self.me,
                                        created_at=timezone.now())
            raise AssertionError("a second reaction was allowed")
        except IntegrityError:
            pass
        self.post.refresh_from_db()
        assert self.post.reaction_count == 1


class Following(TestCase):

    def setUp(self):
        self.me = _member("900003", "Me")
        self.other = _member("900004", "Other")
        self.client.force_login(self.me, backend=BACKEND)

    def test_follow_and_unfollow_move_both_counts(self):
        self.client.post(reverse("follow", args=[self.other.apid]))
        assert Follow.objects.filter(follower=self.me, following=self.other).exists()
        assert SocialCounts.objects.get(member=self.other).followers == 1
        assert SocialCounts.objects.get(member=self.me).following == 1

        self.client.post(reverse("follow", args=[self.other.apid]))
        assert not Follow.objects.filter(follower=self.me,
                                         following=self.other).exists()
        assert SocialCounts.objects.get(member=self.other).followers == 0
        assert SocialCounts.objects.get(member=self.me).following == 0

    def test_nobody_follows_themselves(self):
        """487 rows of the imported data are exactly this."""
        self.client.post(reverse("follow", args=[self.me.apid]))
        assert not Follow.objects.filter(follower=self.me, following=self.me).exists()

    def test_the_same_edge_cannot_exist_twice(self):
        Follow.objects.create(follower=self.me, following=self.other,
                              created_at=timezone.now())
        from django.db import IntegrityError, transaction
        try:
            with transaction.atomic():
                Follow.objects.create(follower=self.me, following=self.other,
                                      created_at=timezone.now())
            raise AssertionError("a duplicate edge was allowed")
        except IntegrityError:
            pass


class TheFeed(TestCase):

    def setUp(self):
        self.me = _member("900005", "Me")
        self.other = _member("900006", "Other")
        self.client.force_login(self.me, backend=BACKEND)

    def test_posting_and_reading_it_back(self):
        self.client.post(reverse("write"), {"body": "Looking for a co-author."})
        page = self.client.get(reverse("feed"))
        assert "Looking for a co-author." in page.content.decode()
        assert SocialCounts.objects.get(member=self.me).posts == 1

    def test_an_empty_post_is_not_made(self):
        self.client.post(reverse("write"), {"body": "   "})
        assert Post.objects.count() == 0

    def test_the_following_feed_shows_only_people_you_follow(self):
        Post.objects.create(author=self.other, body="From a stranger",
                            created_at=timezone.now())
        third = _member("900007", "Third")
        Post.objects.create(author=third, body="From someone I follow",
                            created_at=timezone.now())
        Follow.objects.create(follower=self.me, following=third,
                              created_at=timezone.now())
        body = self.client.get(reverse("feed"), {"show": "following"}).content.decode()
        assert "From someone I follow" in body
        assert "From a stranger" not in body

    def test_a_hidden_post_is_not_in_the_feed(self):
        Post.objects.create(author=self.other, body="Hidden by the office",
                            created_at=timezone.now(), hidden=True)
        assert "Hidden by the office" not in self.client.get(
            reverse("feed")).content.decode()

    def test_reacting_twice_takes_the_reaction_back(self):
        post = Post.objects.create(author=self.other, body="x",
                                   created_at=timezone.now())
        self.client.post(reverse("react", args=[post.pk]))
        post.refresh_from_db()
        assert post.reaction_count == 1
        self.client.post(reverse("react", args=[post.pk]))
        post.refresh_from_db()
        assert post.reaction_count == 0


class Notifications(TestCase):
    """The repair for the silence: 343 of 396 imported posts got no answer at all,
    and nothing ever told anybody they had been followed or replied to."""

    def setUp(self):
        self.me = _member("900010", "Me")
        self.other = _member("900011", "Other")
        self.post = Post.objects.create(author=self.me, body="Hello",
                                        created_at=timezone.now())
        self.client.force_login(self.other, backend=BACKEND)

    def test_a_follow_tells_the_person_followed(self):
        self.client.post(reverse("follow", args=[self.me.apid]))
        note = Notification.objects.get(member=self.me)
        assert note.actor == self.other and note.kind == Notification.FOLLOWED

    def test_a_comment_and_a_like_tell_the_author(self):
        self.client.post(reverse("comment", args=[self.post.pk]), {"body": "Nice"})
        self.client.post(reverse("react", args=[self.post.pk]))
        kinds = set(Notification.objects.filter(member=self.me)
                    .values_list("kind", flat=True))
        assert kinds == {Notification.COMMENTED, Notification.REACTED}

    def test_your_own_doing_is_never_a_notification(self):
        """The old data is 487 self-follows; nobody needs telling about themselves."""
        self.client.force_login(self.me, backend=BACKEND)
        self.client.post(reverse("react", args=[self.post.pk]))
        self.client.post(reverse("comment", args=[self.post.pk]), {"body": "mine"})
        assert Notification.objects.filter(member=self.me).count() == 0

    def test_taking_a_like_back_does_not_notify_again(self):
        self.client.post(reverse("react", args=[self.post.pk]))
        self.client.post(reverse("react", args=[self.post.pk]))
        assert Notification.objects.filter(member=self.me,
                                           kind=Notification.REACTED).count() == 1

    def test_the_count_is_unread_only_and_opening_clears_it(self):
        from apps.social.views import unread_count
        self.client.post(reverse("follow", args=[self.me.apid]))
        self.client.force_login(self.me, backend=BACKEND)

        class R:
            user = self.me
        assert unread_count(R()) == 1
        self.client.get(reverse("notifications"))
        assert unread_count(R()) == 0

    def test_the_bell_is_on_every_page_not_only_the_feed(self):
        self.client.post(reverse("follow", args=[self.me.apid]))
        self.client.force_login(self.me, backend=BACKEND)
        page = self.client.get(reverse("profile", args=[self.me.apid])).content.decode()
        assert 'class="badge"' in page


class Suggestions(TestCase):
    """57% of the imported posts are somebody introducing themselves into silence.
    The registry knows 5,286 profiles with an expertise; the least it can do is show
    a new member the people they came to find."""

    def setUp(self):
        from apps.profiles.models import Profile
        self.me = _member("900020", "Me")
        Profile.objects.create(member=self.me, expertise="polymer composites",
                               affiliation="A University")
        self.match = _member("900021", "A Polymer Person")
        Profile.objects.create(member=self.match,
                               expertise="polymer composites and ageing")
        self.stranger = _member("900022", "Someone Else")
        Profile.objects.create(member=self.stranger, expertise="constitutional law")

    def test_people_in_the_same_field_are_suggested(self):
        from apps.social.views import suggestions_for
        people = suggestions_for(self.me)
        assert self.match in people and self.stranger not in people

    def test_somebody_already_followed_is_not_suggested(self):
        from apps.social.views import suggestions_for
        Follow.objects.create(follower=self.me, following=self.match,
                              created_at=timezone.now())
        assert suggestions_for(self.me) == []

    def test_you_are_never_suggested_to_yourself(self):
        from apps.social.views import suggestions_for
        assert self.me not in suggestions_for(self.me)
