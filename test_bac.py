import unittest
from datetime import datetime

from bac import (Drink, parse_approx_time, parse_drink, parse_weight, place_drinks, simulate,
                 time_below, watson_r)


class ApproxTimeTest(unittest.TestCase):
    NOW = datetime(2026, 10, 26, 1, 20, 33)  # Monday 01:20

    def check(self, text, expected):
        self.assertEqual(parse_approx_time(text, self.NOW), expected, text)

    def test_now_and_ago(self):
        self.check("now", datetime(2026, 10, 26, 1, 20))
        self.check("1h ago", datetime(2026, 10, 26, 0, 20))
        self.check("45 min ago", datetime(2026, 10, 26, 0, 35))
        self.check("1.5 hours ago", datetime(2026, 10, 25, 23, 50))

    def test_clock_times_are_the_most_recent_past(self):
        self.check("9pm", datetime(2026, 10, 25, 21, 0))
        self.check("~21:30", datetime(2026, 10, 25, 21, 30))
        self.check("about 12:30am", datetime(2026, 10, 26, 0, 30))
        self.check("1am", datetime(2026, 10, 26, 1, 0))
        self.check("2am", datetime(2026, 10, 25, 2, 0))

    def test_bare_hour_picks_latest_of_am_and_pm(self):
        self.check("11", datetime(2026, 10, 25, 23, 0))
        self.check("1", datetime(2026, 10, 26, 1, 0))
        self.check("9.15", datetime(2026, 10, 25, 21, 15))

    def test_rejects_nonsense(self):
        for text in ("25:00", "13pm", "9:75", "yesterday", "0am"):
            with self.assertRaises(ValueError, msg=text):
                parse_approx_time(text, self.NOW)


class WatsonTest(unittest.TestCase):
    def test_typical_adults_land_near_widmark_defaults(self):
        self.assertAlmostEqual(watson_r("male", 35, 180, 80), 0.709, places=3)
        self.assertAlmostEqual(watson_r("female", 35, 165, 65), 0.607, places=3)

    def test_heavier_body_means_lower_r(self):
        self.assertLess(watson_r("male", 35, 180, 120), watson_r("male", 35, 180, 80))


class ParseTest(unittest.TestCase):
    def test_preset_with_count_and_abv_override(self):
        grams, clock = parse_drink("2x pint 4.5% @21:05")
        self.assertAlmostEqual(grams, 2 * 568 * 0.045 * 0.789)
        self.assertEqual(clock, (21, 5))

    def test_ml_and_oz(self):
        self.assertAlmostEqual(parse_drink("330ml 5% @20:00")[0], 330 * 0.05 * 0.789)
        self.assertAlmostEqual(parse_drink("12oz 5% @20:00")[0], 12 * 29.5735 * 0.05 * 0.789)

    def test_bad_specs(self):
        for spec in ("pint", "330ml @20:00", "keg 5% @20:00"):
            with self.assertRaises(ValueError):
                parse_drink(spec)

    def test_weight_units(self):
        self.assertEqual(parse_weight("80"), 80)
        self.assertAlmostEqual(parse_weight("176lb"), 79.83, places=2)


class PlaceDrinksTest(unittest.TestCase):
    def test_last_night_rolls_past_midnight(self):
        now = datetime(2026, 10, 26, 8, 0)
        drinks = place_drinks([(10, (22, 0)), (10, (23, 30)), (10, (0, 45))], now)
        self.assertEqual([d.time for d in drinks], [
            datetime(2026, 10, 25, 22, 0),
            datetime(2026, 10, 25, 23, 30),
            datetime(2026, 10, 26, 0, 45),
        ])


class SimulateTest(unittest.TestCase):
    def test_peak_matches_widmark(self):
        # 54.4 g into 80 kg at r=0.68 -> 54.4 / (0.68 * 80000) * 100 = 0.1%,
        # less one minute of elimination when absorbed over one minute.
        drinks = [Drink(datetime(2026, 1, 1, 20, 0), 54.4)]
        series = simulate(drinks, 80, 0.68, 0.015, 1)
        self.assertAlmostEqual(max(b for _, b in series), 0.1 - 0.015 / 60, places=9)

    def test_rejects_zero_elimination(self):
        with self.assertRaises(ValueError):
            simulate([Drink(datetime(2026, 1, 1, 20, 0), 10)], 80, 0.68, 0.0, 45)

    def test_clears_at_elimination_rate(self):
        drinks = [Drink(datetime(2026, 1, 1, 20, 0), 54.4)]
        series = simulate(drinks, 80, 0.68, 0.015, 1)
        hours = (series[-1][0] - series[0][0]).total_seconds() / 3600
        self.assertAlmostEqual(hours, 0.1 / 0.015, delta=0.05)
        self.assertIsNone(time_below(series, 0.2))


if __name__ == "__main__":
    unittest.main()
