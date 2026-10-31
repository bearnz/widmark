import unittest

from copstop import breath_ug, cop_stop


class CopStopTest(unittest.TestCase):
    def test_breath_and_blood_pair_as_in_nz_law(self):
        for bac, breath in ((0.05, 250), (0.08, 400), (0.03, 150), (0.16, 800)):
            self.assertAlmostEqual(breath_ug(bac), breath)

    def test_adult_tiers(self):
        cases = [(0, "clear"), (0.05, "clear"), (0.051, "infringement"), (0.08, "infringement"),
                 (0.081, "court"), (0.2, "court")]
        for bac, level in cases:
            self.assertEqual(cop_stop(bac, age=35).level, level, bac)

    def test_under_20_has_a_zero_limit(self):
        cases = [(0, "clear"), (0.001, "infringement"), (0.03, "infringement"), (0.031, "court"),
                 (0.08, "court"), (0.09, "court")]
        for bac, level in cases:
            self.assertEqual(cop_stop(bac, age=19).level, level, bac)
        self.assertIn("$2,250", cop_stop(0.05, age=19).headline)
        self.assertIn("$4,500", cop_stop(0.09, age=19).headline)  # over 80 mg: adult penalties

    def test_court_details_scale_with_the_reading(self):
        just_over = " ".join(cop_stop(0.1, age=35).details)
        self.assertIn("$500", just_over)  # breath 500 -> about $500
        self.assertNotIn("28 days", just_over)
        high = " ".join(cop_stop(0.17, age=35).details)
        self.assertIn("28 days", high)
        self.assertIn("interlock", high)

    def test_unknown_age_is_treated_as_adult_and_says_so(self):
        stop = cop_stop(0.03)
        self.assertEqual(stop.level, "clear")
        self.assertIn("add your age", " ".join(stop.details))


if __name__ == "__main__":
    unittest.main()
