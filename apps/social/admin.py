from django.contrib import admin

from apps.social.models import Comment, Follow, Post, Reaction, SocialCounts


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("author", "short", "comment_count", "reaction_count", "hidden",
                    "created_at")
    list_filter = ("hidden",)
    search_fields = ("body", "author__apid", "author__full_name", "author__email")
    autocomplete_fields = ["author"]
    readonly_fields = ("wp_entry_id", "comment_count", "reaction_count")
    date_hierarchy = "created_at"

    @admin.display(description="post")
    def short(self, obj):
        return obj.body[:80]


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("author", "post", "created_at", "hidden")
    search_fields = ("body", "author__apid", "author__full_name")
    autocomplete_fields = ["author", "post"]


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("follower", "following", "created_at")
    search_fields = ("follower__apid", "follower__full_name",
                     "following__apid", "following__full_name")
    autocomplete_fields = ["follower", "following"]


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = ("member", "post", "created_at")
    autocomplete_fields = ["member", "post"]


@admin.register(SocialCounts)
class SocialCountsAdmin(admin.ModelAdmin):
    list_display = ("member", "followers", "following", "posts")
    search_fields = ("member__apid", "member__full_name")
