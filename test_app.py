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
            self.assertTrue(0 < d["abv"] <= 60, d)
            self.assertTrue(d["units"], d)
            for u in d["units"]:
                self.assertTrue(u["label"] and 0 < u["ml"] <= 1500, d)
            self.assertEqual(d["pour"], d["type"] == "spirit", d)


class DescribeTest(unittest.TestCase):
    def test_units_pours_and_old_entries(self):
        self.assertEqual(app.describe({"name": "Heineken", "unit": "pint", "ml": 568}), "Heineken, pint (568 ml)")
        self.assertEqual(app.describe({"name": "Gin", "unit": "poured", "ml": 45, "mixer": 150}),
                         "Gin, 45 ml + 150 ml mixer")
        self.assertEqual(app.describe({"name": "Gin", "unit": "poured", "ml": 30, "mixer": 0}), "Gin, 30 ml")
        self.assertEqual(app.describe({"name": "Lager", "ml": 330}), "Lager (330 ml)")

    def test_amount_rejects_out_of_range(self):
        self.assertEqual(app.amount("45", "Spirit", 500), 45)
        self.assertEqual(app.amount("0", "Mixer", 2000, least=0), 0)
        for text in ("", "abc", "0", "600"):
            with self.assertRaises(ValueError, msg=text):
                app.amount(text, "Spirit", 500)


class StateTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "nested" / "state.json"

    def test_missing_file_gives_empty_state(self):
        self.assertEqual(app.load_state(self.path), {"profile": {}, "log": [], "food": False, "favourites": []})

    def test_round_trip(self):
        data = {"profile": {"sex": "female", "weight": 60}, "log": [{"time": "2026-10-25T21:00:00"}], "food": True,
                "favourites": ["Lion Red"]}
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
            self.window = app.App(Path(tempfile.mkdtemp()) / "state.json", catalogue=[
                {"name": "Test Lager", "type": "beer", "abv": 5.0, "pour": False,
                 "units": [{"label": "can/bottle", "ml": 330}, {"label": "pint", "ml": 568}]},
                {"name": "Test Gin", "type": "spirit", "abv": 40.0, "pour": True,
                 "units": [{"label": "nip", "ml": 30}, {"label": "double", "ml": 60}]},
            ])
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
        w._show_serve()
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
        self.assertEqual(w.data["log"][0]["name"], "Custom 6%")
        self.assertEqual(w.data["log"][0]["ml"], 500)
        w.tree.selection_set("0")
        w.remove_selected()
        self.assertEqual(w.data["log"], [])

    def pick(self, name):
        w = self.window
        w.search.set(name)
        w.listbox.selection_set(0)
        w._show_serve()

    def test_choosing_a_unit_or_other_amount(self):
        w = self.window
        self.pick("lager")
        self.assertEqual(list(w.unit_pick["values"]), ["can/bottle (330 ml)", "pint (568 ml)", app.OTHER])
        self.assertEqual(str(w.other_entry["state"]), "disabled")
        w.unit_pick.current(1)
        w._unit_changed()
        w.add_drink()
        self.assertEqual((w.data["log"][-1]["unit"], w.data["log"][-1]["ml"]), ("pint", 568))
        self.assertEqual(w.tree.item("0")["values"][1], "Test Lager, pint (568 ml)")

        w.unit_pick.current(2)
        w._unit_changed()
        self.assertEqual(str(w.other_entry["state"]), "normal")
        w.other_ml.set("400")
        w.add_drink()
        self.assertEqual((w.data["log"][-1]["unit"], w.data["log"][-1]["ml"]), ("other", 400))

    def test_favourites_are_pinned_and_remembered(self):
        w = self.window
        self.assertEqual(str(w.fav_button["state"]), "disabled")
        self.pick("gin")
        self.assertEqual(w.fav_button["text"], "☆ Pin as favourite")
        w.fav_button.invoke()
        self.assertEqual(w.data["favourites"], ["Test Gin"])
        self.assertEqual(w.fav_button["text"], "★ Unpin favourite")
        self.assertEqual(w._selected()["name"], "Test Gin")  # still selected after re-listing

        w.search.set("")
        self.assertEqual(w.listbox.get(0, "end"), ("★ Test Gin  (40%)", "Test Lager  (5%)"))

        reopened = app.App(w.state_path, catalogue=w.catalogue)
        reopened.withdraw()
        self.assertEqual(reopened.listbox.get(0), "★ Test Gin  (40%)")
        reopened.destroy()

        self.pick("gin")
        w.fav_button.invoke()
        w.search.set("")
        self.assertEqual(w.data["favourites"], [])
        self.assertEqual(w.listbox.get(0), "Test Lager  (5%)")

    def test_spirit_pour_counts_only_the_spirit(self):
        w = self.window
        self.pick("gin")
        self.assertTrue(w.pour_row.winfo_manager())
        self.assertFalse(w.unit_row.winfo_manager())
        self.assertEqual(w.spirit_ml.get(), "30")
        w.quick.winfo_children()[1].invoke()  # the "double 60" button
        self.assertEqual(w.spirit_ml.get(), "60")
        w.spirit_ml.set("45")
        w.mixer_ml.set("150")
        w.add_drink()
        e = w.data["log"][-1]
        self.assertEqual((e["unit"], e["ml"], e["mixer"]), ("poured", 45, 150))
        self.assertAlmostEqual(app.entry_grams(e), 45 * 0.40 * 0.789)
        self.assertEqual(w.tree.item("0")["values"][1], "Test Gin, 45 ml + 150 ml mixer")


if __name__ == "__main__":
    unittest.main()
