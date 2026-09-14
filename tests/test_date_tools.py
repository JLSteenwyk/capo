import unittest
from capo.date_tools import shift


class DateToolTests(unittest.TestCase):
    def test_two_weeks_before_crosses_dst_in_local_time(self):
        value = shift('2026-11-08T18:00:00-08:00', '-14', 'America/Los_Angeles')
        self.assertEqual(value['value'], '2026-10-25T18:00:00-07:00')

    def test_all_day_year_boundary_and_leap_day(self):
        self.assertEqual(shift('2027-01-05', '-14', 'America/Los_Angeles')['value'], '2026-12-22')
        self.assertEqual(shift('2028-03-01', '-1', 'UTC')['value'], '2028-02-29')

    def test_gap_and_fold_require_a_choice(self):
        gap = shift('2026-03-07T02:30:00-08:00', '1', 'America/Los_Angeles')
        self.assertTrue(gap['needs_clarification']); self.assertEqual(gap['choices'], [])
        fold = shift('2026-10-31T01:30:00-07:00', '1', 'America/Los_Angeles')
        self.assertTrue(fold['needs_clarification']); self.assertEqual(len(fold['choices']), 2)

    def test_no_guessing_offsets_or_unbounded_ranges(self):
        for value, days in [('2026-09-14T09:00:00', '1'), ('2026-09-14T09:00:00-08:00', '1'),
                            ('2026-09-14', 'tomorrow'), ('2026-09-14', '99999')]:
            with self.assertRaises(ValueError): shift(value, days, 'America/Los_Angeles')
