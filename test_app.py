import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import app


class CatalogueTest(unittest.TestCase):
    def test_entries_are_sane(self):
        drinks = app.load_catalogue()
        self.assertGreater(len(drinks), 20)
        names = [d["name"] for d in drinks]
        self.assertEqual(len(names), len(set(names)))
        for d in drinks:
            self.assertTrue(0 < d["ml"] <= 1500, d)
            self.assertTrue(0 < d["abv"] <= 60, d)


class StateTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "nested" / "state.json"

    def test_missing_file_gives_empty_state(self):
        self.assertEqual(app.load_state(self.path), {"profile": {}, "log": [], "food": False})

    def test_round_trip(self):
        data = {"profile": {"sex": "female", "weight": 60}, "log": [{"time": "2026-10-25T21:00:00"}], "food": True}
        app.save_state(data, self.path)
        self.assertEqual(app.load_state(self.path), data)

    def test_recent_drops_entries_older_than_a_day(self):
        now = datetime(2026, 10, 26, 8, 0)
        log = [{"time": (now - timedelta(hours=h)).isoformat()} for h in (25, 23, 1)]
        self.assertEqual(len(app.recent(log, now)), 2)

    def test_profile_r_uses_watson_only_with_age_and_height(self):
        self.assertEqual(app.profile_r({"sex": "male", "weight": 80}), 0.68)
        self.assertAlmostEqual(app.profile_r({"sex": "male", "weight": 80, "age": 35, "height": 180}), 0.709, places=3)


class AppSmokeTest(unittest.TestCase):
    def setUp(self):
        try:
            self.window = app.App(Path(tempfile.mkdtemp()) / "state.json",
                                  catalogue=[{"name": "Test Lager", "ml": 330, "abv": 5.0}])
        except tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.window.withdraw()

    def tearDown(self):
        self.window.destroy()

    def test_profile_then_drink_gives_an_estimate(self):
        w = self.window
        self.assertEqual(w.now_label["text"], "--")
        w.weight.set("80")
        w.age.set("35")
        w.height.set("180")
        w.save_profile()
        self.assertIn("Watson", w.r_label["text"])
        self.assertEqual(w.now_label["text"], "0.000%")

        w.listbox.selection_set(0)
        w.qty.set("2")
        w.when.set("30 min ago")
        w.add_drink()
        self.assertEqual(len(w.data["log"]), 1)
        self.assertEqual(len(w.tree.get_children()), 1)
        self.assertRegex(w.now_label["text"], r"^0\.0[1-9]\d% now$")
        self.assertGreater(len(w.chart.find_all()), 3)

        saved = app.load_state(w.state_path)
        self.assertEqual(saved["profile"]["weight"], 80)
        self.assertEqual(saved["log"][0]["count"], 2)

    def test_custom_drink_and_remove(self):
        w = self.window
        w.weight.set("70")
        w.save_profile()
        w.search.set("500ml 6%")
        w.add_drink()
        self.assertEqual(w.data["log"][0]["name"], "Custom 500 ml 6%")
        w.tree.selection_set("0")
        w.remove_selected()
        self.assertEqual(w.data["log"], [])


if __name__ == "__main__":
    unittest.main()
