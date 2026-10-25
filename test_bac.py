import argparse
import contextlib
import io
import unittest
from datetime import datetime, timedelta

import bac
from bac import (Drink, parse_approx_time, parse_drink, parse_weight, place_drinks, simulate,
                 time_below, watson_r)


class ApproxTimeTest(unittest.TestCase):
    NOW = datetime(2026, 10, 26, 1, 20, 33)  # Monday 01:20

    def check(self, text, expected):
        self.assertEqual(parse_approx_time(text, self.NOW), expected.astimezone(), text)

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
        for spec in ("pint", "330ml @20:00", "keg 5% @20:00", "330ml 500% @20:00", "330ml 0% @20:00",
                     "0x pint @20:00", "0ml 5% @20:00"):
            with self.assertRaises(ValueError):
                parse_drink(spec)

    def test_weight_units(self):
        self.assertEqual(parse_weight("80"), 80)
        self.assertAlmostEqual(parse_weight("176lb"), 79.83, places=2)
        for text in ("0kg", "10", "500kg"):
            with self.assertRaises(argparse.ArgumentTypeError, msg=text):
                parse_weight(text)


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                bac.main(["-w", "80", "--sex", "male", "-d", "pint @20:00", *args])
        return ctx.exception.code, err.getvalue()

    def test_rejects_implausible_r_instead_of_ignoring_it(self):
        for r in ("0", "-1", "2"):
            code, err = self.run_cli("--r", r)
            self.assertEqual(code, 2, r)
            self.assertIn("--r must be between", err)


def dst_change_day():
    """A day this year when the local UTC offset changes, or None where there's no daylight saving."""
    day = datetime(datetime.now().year, 1, 1)
    for _ in range(366):
        if day.astimezone().utcoffset() != (day + timedelta(hours=23)).astimezone().utcoffset():
            return day
        day += timedelta(days=1)
    return None


class DaylightSavingTest(unittest.TestCase):
    def test_gaps_are_real_elapsed_time_across_a_change(self):
        day = dst_change_day()
        if day is None:
            self.skipTest("local time zone has no daylight saving")
        evening_before = day - timedelta(hours=2)  # 22:00 the night before the change
        morning_after = day + timedelta(hours=8)
        drinks = place_drinks([(20, (22, 0))], morning_after)
        real = morning_after.timestamp() - evening_before.timestamp()
        self.assertEqual((morning_after.astimezone() - drinks[0].time).total_seconds(), real)
        self.assertNotEqual(real, (morning_after - evening_before).total_seconds())  # naive wall-clock gap

        ago = parse_approx_time("10 hours ago", morning_after)
        self.assertEqual(morning_after.timestamp() - ago.timestamp(), 10 * 3600)
        self.assertEqual(bac.fmt(drinks[0].time), evening_before.strftime("%a %H:%M"))


class PlaceDrinksTest(unittest.TestCase):
    def test_last_night_rolls_past_midnight(self):
        now = datetime(2026, 10, 26, 8, 0)
        drinks = place_drinks([(10, (22, 0)), (10, (23, 30)), (10, (0, 45))], now)
        self.assertEqual([d.time for d in drinks], [
            datetime(2026, 10, 25, 22, 0).astimezone(),
            datetime(2026, 10, 25, 23, 30).astimezone(),
            datetime(2026, 10, 26, 0, 45).astimezone(),
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
