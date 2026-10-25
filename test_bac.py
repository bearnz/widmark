import unittest
from datetime import datetime

from bac import Drink, parse_drink, parse_weight, place_drinks, simulate, time_below


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
