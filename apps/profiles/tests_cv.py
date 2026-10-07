import datetime as dt

from django.test import TestCase

from apps.identity.models import Member
from apps.migration.management.commands.import_cv_sections import day
from apps.profiles.models import Award, CareerPosition, Conference, Project


class CvSectionsTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(username="cvtester", apid="800777", full_name="Cv Tester")

    def test_day_parses_only_real_dates(self):
        self.assertEqual(day("2020-01-16"), dt.date(2020, 1, 16))
        self.assertIsNone(day("Present"))
        self.assertIsNone(day(""))

    def test_sections_hang_off_the_member(self):
        Award.objects.create(member=self.member, name="MRA", wp_entry_id=1)
        Conference.objects.create(member=self.member, name="ICC", wp_entry_id=2)
        Project.objects.create(member=self.member, title="P", wp_entry_id=3)
        CareerPosition.objects.create(member=self.member, organisation="X",
                                      is_current=True, wp_entry_id=4)
        self.assertEqual(self.member.awards.count(), 1)
        self.assertEqual(self.member.conferences.count(), 1)
        self.assertEqual(self.member.projects.count(), 1)
        self.assertEqual(self.member.careerpositions.count(), 1)
