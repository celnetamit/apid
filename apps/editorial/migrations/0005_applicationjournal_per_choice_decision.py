# wisp 2026-10-02: per-journal decisions on each ApplicationJournal.
# Boss asked for each journal choice to be decidable independently by its
# own manager/CE instead of one application-wide decision. Add decision,
# decided_at, decided_by, note, role_appointed, then backfill from the
# existing application-level fields so historical data stays coherent.
from django.db import migrations, models
import django.db.models.deletion


def backfill(apps, schema_editor):
    Application = apps.get_model("editorial", "Application")
    Appointment = apps.get_model("editorial", "Appointment")
    for a in Application.objects.exclude(decision="pending").iterator():
        if a.decision == "accepted":
            appt = Appointment.objects.filter(application=a).first()
            if appt and appt.journal_id:
                a.journals.filter(journal_id=appt.journal_id).update(
                    decision="accepted",
                    decided_at=a.decided_at,
                    decided_by_id=a.decided_by_id,
                    role_appointed=appt.role,
                )
            # Other journal choices for an accepted app stay pending — we
            # do not know whether the office actually ruled on them.
        else:  # declined / withdrawn: the whole app was closed off.
            a.journals.update(
                decision=a.decision,
                decided_at=a.decided_at,
                decided_by_id=a.decided_by_id,
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0004_application_note"),
        ("identity", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationjournal",
            name="decision",
            field=models.CharField(
                choices=[("pending", "Pending"), ("accepted", "Accepted"),
                         ("declined", "Declined"), ("withdrawn", "Withdrawn")],
                db_index=True, default="pending", max_length=20),
        ),
        migrations.AddField(
            model_name="applicationjournal",
            name="decided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="applicationjournal",
            name="decided_by",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="choice_decisions_made",
                to="identity.member"),
        ),
        migrations.AddField(
            model_name="applicationjournal",
            name="note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="applicationjournal",
            name="role_appointed",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.RunPython(backfill, noop_reverse),
    ]
