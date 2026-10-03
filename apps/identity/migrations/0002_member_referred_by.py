# wisp 2026-10-02: track who invited whom.
# Boss asked for a share/invite feature; each account can carry a referred_by
# link so the Admin can see which members brought in which. Nullable because
# the referrer is optional (most imports and direct sign-ups have none), and
# SET_NULL on delete so purging an inviter does not cascade their invitees.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="member",
            name="referred_by",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="referrals",
                to="identity.member",
                help_text="The APID member who invited this one, if any."),
        ),
    ]
