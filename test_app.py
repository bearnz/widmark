import json
import shutil
import tempfile
import time
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import app


class CatalogueTest(unittest.TestCase):
    def test_entries_are_sane(self):
        drinks = app.load_catalogue()
        self.assertGreater(len(drinks), 20)
        names = [d["name"] for d in drinks]
        self.assertEqual(len(names), len(set(names)))
        for d in drinks:
            self.assertTrue(d["abv"] is None or 0 < d["abv"] <= 60, d)
            self.assertTrue(d["units"], d)
            for u in d["units"]:
                self.assertTrue(u["label"] and 0 < u["ml"] <= 1500, d)
            self.assertEqual(d["pour"], d["type"] == "spirit", d)


class SetAbvTest(unittest.TestCase):
    """Set ABV through the window, writing to a copy of the real ABV table."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.sources, self.catalogue = tmp / "abv_sources.csv", tmp / "drinks.json"
        shutil.copy(app.build_drinks.SOURCES, self.sources)
        app.build_drinks.build(self.sources, self.catalogue)
        try:
            self.window = app.App(tmp / "state.json", catalogue_path=self.catalogue, sources=self.sources)
        except tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.window.withdraw()
        self.messages = []
        self.answers = []  # what the Set ABV box will return, in order
        for name, fake in (("showerror", lambda title, text, **_: self.messages.append(text)),
                           ("showinfo", lambda title, text, **_: self.messages.append(text)),
                           ("askyesno", lambda title, text, **_: True)):
            patcher = mock.patch.object(app.messagebox, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(app.simpledialog, "askstring", lambda *a, **k: self.answers.pop(0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.window.destroy()

    def pick(self, name):
        w = self.window
        w.search.set(name)
        w.listbox.selection_set(0)
        w._show_serve()

    def manual(self, drink):
        return {r["drink"]: r["manual"] for r in app.build_drinks.read(self.sources)[1]}[drink]

    def test_no_abv_drink_is_listed_set_then_cleared(self):
        w = self.window
        w.weight.set("80")
        w.save_profile()
        self.pick("big hoppa")
        self.assertEqual(w.listbox.get(0), "Mac's Big Hoppa IPA  (no ABV yet)")
        w.add_drink()
        self.assertEqual(w.data["log"], [])
        self.assertIn("has no ABV yet", self.messages[0])

        self.answers.append("5.7%")
        w.set_abv()
        self.assertEqual(self.manual("Mac's Big Hoppa IPA"), "5.7")
        saved = {d["name"]: d for d in app.load_catalogue(self.catalogue)}["Mac's Big Hoppa IPA"]
        self.assertEqual((saved["abv"], saved["source"]), (5.7, "manual"))
        self.assertEqual(w.listbox.get(w.listbox.curselection()[0]), "Mac's Big Hoppa IPA  (5.7%)")
        w.add_drink()
        self.assertEqual(w.data["log"][0]["abv"], 5.7)

        self.answers.append("")  # blank clears the manual value
        w.set_abv()
        self.assertEqual(self.manual("Mac's Big Hoppa IPA"), "")
        self.assertEqual(w.listbox.get(w.listbox.curselection()[0]), "Mac's Big Hoppa IPA  (no ABV yet)")

    def test_correcting_an_abv_offers_to_update_logged_drinks(self):
        w = self.window
        w.weight.set("80")
        w.save_profile()
        self.pick("heineken silver")
        w.add_drink()
        self.answers.append("4.5")
        w.set_abv()
        self.assertEqual(w.data["log"][0]["abv"], 4.5)
        self.assertEqual(app.load_state(w.state_path)["log"][0]["abv"], 4.5)

    def test_impossible_abv_is_refused_and_nothing_changes(self):
        self.pick("big hoppa")
        before = self.sources.read_text(encoding="utf-8")
        for answer in ("150", "lots"):
            self.answers.append(answer)
            self.window.set_abv()
        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.sources.read_text(encoding="utf-8"), before)


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
        self.assertEqual(app.load_state(self.path), {"profile": {}, "log": [], "favourites": []})

    def test_round_trip(self):
        data = {"profile": {"sex": "female", "weight": 60}, "log": [{"time": "2026-10-25T21:00:00", "food": True}],
                "favourites": ["Lion Red"]}
        app.save_state(data, self.path)
        self.assertEqual(app.load_state(self.path), data)

    def test_old_whole_log_meal_setting_moves_onto_each_drink(self):
        app.save_state({"log": [{"time": "2026-10-25T21:00:00"}, {"time": "2026-10-25T22:00:00", "food": False}],
                        "food": True}, self.path)
        self.assertEqual([e["food"] for e in app.load_state(self.path)["log"]], [True, False])

    def test_qty_spreads_evenly_and_meal_slows_absorption(self):
        entry = {"time": "2026-10-25T20:00:00", "count": 3, "ml": 330, "abv": 5, "spread_minutes": 60, "food": True}
        drinks = app.entry_drinks(entry)
        self.assertEqual([d.time.strftime("%H:%M") for d in drinks], ["20:00", "20:30", "21:00"])
        self.assertTrue(all(d.absorb_minutes == app.bac.ABSORB_MINUTES_FOOD for d in drinks))
        self.assertAlmostEqual(sum(d.grams for d in drinks), app.entry_grams(entry))
        old = {"time": "2026-10-25T20:00:00", "count": 3, "ml": 330, "abv": 5}  # logged before spreading existed
        self.assertEqual({d.time for d in app.entry_drinks(old)}, {drinks[0].time})

    def test_format_duration_reads_back(self):
        for minutes, text in ((150, "2h30m"), (60, "1h"), (45, "45 min"), (0, "0 min")):
            self.assertEqual(app.format_duration(minutes), text)
            self.assertEqual(app.bac.parse_duration(text), timedelta(minutes=minutes))

    def test_recent_drops_entries_older_than_a_day(self):
        now = datetime(2026, 10, 26, 8, 0)
        log = [{"time": (now - timedelta(hours=h)).isoformat()} for h in (25, 23, 1)]
        self.assertEqual(len(app.recent(log, now)), 2)

    def test_unreadable_file_is_moved_aside_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"profile": {"sex": "male", "weight": 80}, "log": [', encoding="utf-8")
        with self.assertRaises(app.StateError):
            app.load_state(self.path)
        self.assertFalse(self.path.exists())
        backups = list(self.path.parent.glob("state.unreadable-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertIn('"weight": 80', backups[0].read_text(encoding="utf-8"))
        self.assertEqual(app.load_state(self.path)["log"], [])

    def test_save_leaves_no_temp_file(self):
        app.save_state({"log": []}, self.path)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["state.json"])

    def test_old_naive_times_mix_with_new_aware_ones(self):
        now = datetime(2026, 10, 26, 8, 0).astimezone()
        log = [{"time": "2026-10-26T07:00:00"}, {"time": (now - timedelta(hours=2)).isoformat()}]
        self.assertEqual(len(app.recent(log, now)), 2)
        self.assertEqual(sorted(log, key=app.entry_time)[0], log[1])

    def test_implausible_watson_falls_back_to_the_default(self):
        r, how = app.body_water({"sex": "female", "age": 30, "height": 250, "weight": 30})
        self.assertEqual(r, 0.55)
        self.assertIn("implausible", how)

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
                 "units": [{"label": "shot", "ml": 15}, {"label": "double", "ml": 30},
                           {"label": "family", "ml": 37.5}]},
            ])
        except tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.window.withdraw()
        self.messages = []
        for kind in ("showerror", "showinfo", "showwarning"):
            patcher = mock.patch.object(app.messagebox, kind, lambda title, text, **_: self.messages.append(text))
            patcher.start()
            self.addCleanup(patcher.stop)

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
        self.assertRegex(w.now_label["text"], r"^0\.0\d\d% now$")
        self.assertNotEqual(w.now_label["text"], "0.000% now")
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

    def add_custom(self, text, qty="1", when="now"):
        w = self.window
        before = len(w.data["log"])
        self.messages.clear()
        w.search.set(text)
        w.qty.set(qty)
        w.when.set(when)
        w.add_drink()
        return len(w.data["log"]) - before

    def test_rejects_impossible_drinks_with_a_message(self):
        w = self.window
        w.weight.set("80")
        w.save_profile()
        for text, qty, when in (("500ml 150%", "1", "now"), ("0ml 5%", "1", "now"), ("330ml 5%", "25", "now"),
                                ("330ml 5%", "1", "25 hours ago")):
            self.assertEqual(self.add_custom(text, qty, when), 0, (text, qty, when))
            self.assertEqual(len(self.messages), 1, (text, qty, when))
        self.assertIn("more than 24 hours ago", self.messages[0])
        self.assertEqual(self.add_custom("330ml 5%", "24", "23 hours ago"), 1)

    def test_rejects_ages_outside_adult_range(self):
        w = self.window
        w.weight.set("80")
        w.height.set("180")
        for age in ("-5", "0", "400"):
            self.messages.clear()
            w.age.set(age)
            w.save_profile()
            self.assertEqual(len(self.messages), 1, age)
            self.assertNotIn("age", w.data["profile"])
        w.age.set("35")
        w.save_profile()
        self.assertEqual(w.data["profile"]["age"], 35)

    def test_log_selection_survives_the_minute_refresh(self):
        w = self.window
        w.weight.set("80")
        w.save_profile()
        self.add_custom("330ml 5%", when="2h ago")
        self.add_custom("330ml 5%", when="1h ago")
        w.tree.selection_set("1")
        w.refresh()
        self.assertEqual(w.tree.selection(), ("1",))

    def test_second_window_on_the_same_data_is_refused(self):
        with self.assertRaises(app.AlreadyOpen):
            app.App(self.window.state_path, catalogue=self.window.catalogue)
        self.window.destroy()  # closing the first window frees the data for the next one
        again = app.App(self.window.state_path, catalogue=self.window.catalogue)
        again.withdraw()
        self.window = again  # so tearDown closes it

    def test_main_explains_instead_of_opening_a_second_window(self):
        with mock.patch.object(app, "App", side_effect=app.AlreadyOpen("already open")):
            self.assertEqual(app.main(), 1)
        self.assertEqual(self.messages, ["already open"])

    def test_qty_suggests_a_spread_and_logs_it(self):
        w = self.window
        w.weight.set("80")
        w.save_profile()
        self.assertEqual(str(w.over_entry["state"]), "disabled")
        w.qty.set("4")
        self.assertEqual((str(w.over_entry["state"]), w.over.get()), ("normal", "1h30m"))
        self.assertEqual(self.add_custom("330ml 5%", qty="4", when="2h ago"), 1)
        e = w.data["log"][-1]
        self.assertEqual((e["count"], e["spread_minutes"]), (4, 90))
        self.assertIn("over 1h30m", w.tree.item("0")["values"][1])
        self.assertEqual(w.over.get(), "")  # back to qty 1

    def test_spread_running_past_now_is_refused(self):
        w = self.window
        w.weight.set("80")
        w.save_profile()
        w._over_typed = True
        w.over.set("3h")
        self.assertEqual(self.add_custom("330ml 5%", qty="4", when="1h ago"), 0)
        self.assertIn("after now", self.messages[0])
        w.over.set("soon")
        self.assertEqual(self.add_custom("330ml 5%", qty="4", when="1h ago"), 0)
        self.assertIn("how long", self.messages[0])

    def test_meal_is_per_drink_and_can_be_changed_later(self):
        w = self.window
        w.weight.set("80")
        w.save_profile()
        w.food.set(True)
        self.add_custom("330ml 5%", when="2h ago")
        w.food.set(False)
        self.add_custom("330ml 5%", when="1h ago")
        self.assertEqual([e["food"] for e in w.data["log"]], [True, False])
        self.assertTrue(w.tree.item("0")["values"][1].endswith(", with a meal"))
        w.tree.selection_set("1")
        w.toggle_meal()
        self.assertEqual([e["food"] for e in app.load_state(w.state_path)["log"]], [True, True])

    def test_warns_when_saved_data_was_unreadable(self):
        path = Path(tempfile.mkdtemp()) / "state.json"
        path.write_text("not json", encoding="utf-8")
        other = app.App(path, catalogue=self.window.catalogue)
        other.withdraw()
        deadline = time.monotonic() + 2
        while not self.messages and time.monotonic() < deadline:
            other.update()
        other.destroy()
        self.assertTrue(self.messages and "couldn't be read" in self.messages[0], self.messages)

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

        self.assertEqual(app.load_state(w.state_path)["favourites"], ["Test Gin"])

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
        self.assertEqual(w.spirit_ml.get(), "15")
        self.assertEqual([b["text"] for b in w.quick.winfo_children()], ["shot 15", "double 30", "family 37.5"])
        w.quick.winfo_children()[2].invoke()
        self.assertEqual(w.spirit_ml.get(), "37.5")
        w.spirit_ml.set("45")
        w.mixer_ml.set("150")
        w.add_drink()
        e = w.data["log"][-1]
        self.assertEqual((e["unit"], e["ml"], e["mixer"]), ("poured", 45, 150))
        self.assertAlmostEqual(app.entry_grams(e), 45 * 0.40 * 0.789)
        self.assertEqual(w.tree.item("0")["values"][1], "Test Gin, 45 ml + 150 ml mixer")


if __name__ == "__main__":
    unittest.main()
