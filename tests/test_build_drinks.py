import json
import shutil
import tempfile
import unittest
from pathlib import Path

import app
import build_drinks


def row(**values):
    base = {"drink": "Test", "type": "beer", "liquorland": "", "woolworths": "", "new_world": "", "paknsave": "", "brewery": "", "guess": "",
            "manual": ""}
    return {**base, **values}


class ResolveTest(unittest.TestCase):
    def test_statuses(self):
        cases = [
            (row(liquorland="5", woolworths="5"), ("agree", 5)),
            (row(liquorland="4.5", woolworths="2.5"), ("conflict", 4.5)),
            (row(woolworths="4.2", guess="4"), ("one source", 4.2)),
            (row(liquorland="4.5", woolworths="2.5", manual="4.8"), ("manual", 4.8)),
            (row(drink="Red wine (generic)", type="wine", guess="13"), ("generic", 13)),
            (row(liquorland="8.5", woolworths="8", brewery="8"), ("brewery", 8)),
            (row(brewery="4.2"), ("brewery", 4.2)),
            (row(liquorland="8.5", brewery="8", manual="8.2"), ("manual", 8.2)),
            (row(guess="5"), ("guess only", None)),
            (row(), ("missing", None)),
        ]
        for r, expected in cases:
            self.assertEqual(build_drinks.resolve(r), expected, r)

    def test_retailers_that_differ_from_the_brewery_are_reported(self):
        rows = [row(drink="Boss Level", liquorland="8.5", woolworths="8", brewery="8"),
                row(drink="Pilsner", woolworths="4.8", brewery="4.8"),
                row(drink="No brewery", liquorland="5", woolworths="4")]
        self.assertEqual([(r["drink"], b, off) for r, b, off in build_drinks.retailers_differ(rows)],
                         [("Boss Level", 8, {"Liquorland": 8.5})])

    def test_close_values_agree(self):
        self.assertEqual(build_drinks.resolve(row(liquorland="4.6", woolworths="4.5"))[0], "agree")


class UnitsTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(build_drinks.parse_units("can/bottle 330; large glass 250"),
                         [{"label": "can/bottle", "ml": 330}, {"label": "large glass", "ml": 250}])
        for bad in ("330", "pint", "pint 568;", "pint lots"):
            with self.assertRaises(ValueError, msg=bad):
                build_drinks.parse_units(bad)


def copy_sources(edit=None):
    """A temp copy of the real CSV, optionally edited line by line; returns (csv path, json path)."""
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "abv_sources.csv"
    lines = build_drinks.SOURCES.read_text(encoding="utf-8").splitlines()
    if edit:
        edit(lines)
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return src, tmp / "drinks.json"


def set_cell(line, column, value):
    """Edit one cell of the Heineken row (line 2)."""
    def edit(lines):
        header = lines[0].split(",")
        cells = lines[line - 1].split(",")
        cells[header.index(column)] = value
        lines[line - 1] = ",".join(cells)
    return edit


class ProblemsTest(unittest.TestCase):
    def assert_problem(self, edit, expected):
        src, out = copy_sources(edit)
        with self.assertRaises(build_drinks.BuildError) as ctx:
            build_drinks.build(src, out)
        self.assertIn(expected, str(ctx.exception))
        self.assertFalse(out.exists(), "nothing should be written")

    def test_bad_cells_are_named_by_line(self):
        self.assert_problem(set_cell(2, "manual", '"4,5"'), "line 2 (Heineken): manual '4,5' isn't a number")
        self.assert_problem(set_cell(2, "manual", "abc"), "manual 'abc' isn't a number")
        self.assert_problem(set_cell(2, "woolworths", "150"), "woolworths 150 isn't a possible ABV")
        self.assert_problem(set_cell(2, "type", "lager"), "type 'lager' isn't one of")
        self.assert_problem(set_cell(2, "units", "pint"), "can't read unit 'pint'")
        self.assert_problem(set_cell(2, "drink", "-Heineken"), "spreadsheets read as a formula")
        self.assert_problem(set_cell(2, "drink", "Tui"), "another row has the same name")

    def test_missing_column(self):
        def drop_note(lines):
            lines[:] = [line.rsplit(",", 1)[0] for line in lines]
        self.assert_problem(drop_note, "missing column(s) note")

    def test_reads_excel_utf8_with_byte_order_mark(self):
        src, out = copy_sources()
        src.write_text(src.read_text(encoding="utf-8"), encoding="utf-8-sig")
        build_drinks.build(src, out)
        self.assertTrue(out.exists())

    def test_unusual_abv_is_flagged_not_rejected(self):
        src, out = copy_sources(set_cell(2, "manual", "55"))
        rows = build_drinks.build(src, out)
        self.assertEqual([(r["drink"], abv) for r, abv in build_drinks.unusual(rows)], [("Heineken", 55)])


class SetManualTest(unittest.TestCase):
    def test_sets_and_clears_and_rebuilds(self):
        src, out = copy_sources()
        build_drinks.set_manual("Tiger Lager", 5.1, src, out)
        drinks = {d["name"]: d for d in json.loads(out.read_text(encoding="utf-8"))["drinks"]}
        self.assertEqual((drinks["Tiger Lager"]["abv"], drinks["Tiger Lager"]["source"]), (5.1, "manual"))
        build_drinks.set_manual("Tiger Lager", None, src, out)
        drinks = {d["name"]: d for d in json.loads(out.read_text(encoding="utf-8"))["drinks"]}
        self.assertEqual((drinks["Tiger Lager"]["abv"], drinks["Tiger Lager"]["source"]), (5, "brewery"))

    def test_refuses_unknown_drinks_and_impossible_abvs(self):
        src, out = copy_sources()
        with self.assertRaises(build_drinks.BuildError):
            build_drinks.set_manual("Not A Beer", 5, src, out)
        with self.assertRaises(ValueError):
            build_drinks.set_manual("Tiger Lager", 150, src, out)
        self.assertFalse(out.exists())

    def test_drinks_with_no_abv_are_listed_with_their_guess(self):
        src, out = copy_sources()
        build_drinks.build(src, out)
        drinks = {d["name"]: d for d in json.loads(out.read_text(encoding="utf-8"))["drinks"]}
        self.assertEqual((drinks["Steinlager Pure"]["abv"], drinks["Steinlager Pure"]["guess"]), (None, 5))
        self.assertEqual(drinks["Mac's Big Hoppa IPA"]["source"], "missing")
        self.assertNotIn("guess", drinks["Mac's Big Hoppa IPA"])


class AddDrinkTest(unittest.TestCase):
    def drinks(self, out):
        return {d["name"]: d for d in json.loads(out.read_text(encoding="utf-8"))["drinks"]}

    def test_adds_a_row_and_rebuilds(self):
        src, out = copy_sources()
        name = build_drinks.add_drink("  Backyard   Lager ", "beer", 4.6, "can 440; pint 568;", src, out)
        self.assertEqual(name, "Backyard Lager")
        added = self.drinks(out)["Backyard Lager"]
        self.assertEqual((added["abv"], added["source"], added["pour"]), (4.6, "manual", False))
        self.assertEqual(added["units"], [{"label": "can", "ml": 440}, {"label": "pint", "ml": 568}])
        row = build_drinks.read(src)[1][-1]
        self.assertEqual((row["drink"], row["manual"], row["status"], row["note"]),
                         ("Backyard Lager", "4.6", "manual", build_drinks.ADDED_NOTE))

    def test_no_abv_yet_is_listed_as_missing(self):
        src, out = copy_sources()
        build_drinks.add_drink("House Gin", "spirit", None, build_drinks.DEFAULT_UNITS["spirit"], src, out)
        added = self.drinks(out)["House Gin"]
        self.assertEqual((added["abv"], added["source"], added["pour"]), (None, "missing", True))

    def test_refuses_listed_names_and_bad_rows_changing_nothing(self):
        src, out = copy_sources()
        before = src.read_text(encoding="utf-8")
        for name, kind, abv, units in (("heineken", "beer", 5, "pint 568"),  # listed, in another case
                                       ("", "beer", 5, "pint 568"),
                                       ("New One", "mead", 5, "pint 568"),
                                       ("New One", "beer", 5, "a pint"),
                                       ("New One", "beer", 5, "")):
            with self.assertRaises(build_drinks.BuildError, msg=name or "no name"):
                build_drinks.add_drink(name, kind, abv, units, src, out)
        with self.assertRaises(ValueError):
            build_drinks.add_drink("New One", "beer", 150, "pint 568", src, out)
        self.assertEqual(src.read_text(encoding="utf-8"), before)
        self.assertFalse(out.exists())

    def test_default_units_parse(self):
        for kind in build_drinks.TYPE_ORDER:
            self.assertTrue(build_drinks.parse_units(build_drinks.DEFAULT_UNITS[kind]))


class InSyncTest(unittest.TestCase):
    def test_drinks_json_matches_the_csv(self):
        tmp = Path(tempfile.mkdtemp())
        shutil.copy(build_drinks.SOURCES, tmp / "abv_sources.csv")
        build_drinks.build(tmp / "abv_sources.csv", tmp / "drinks.json")
        self.assertEqual(json.loads((tmp / "drinks.json").read_text(encoding="utf-8")),
                         json.loads(app.CATALOGUE.read_text(encoding="utf-8")),
                         "drinks.json is stale: run uv run build_drinks.py")
        self.assertEqual((tmp / "abv_sources.csv").read_text(encoding="utf-8").splitlines(),
                         build_drinks.SOURCES.read_text(encoding="utf-8").splitlines(),
                         "the CSV status column is stale: run uv run build_drinks.py")


if __name__ == "__main__":
    unittest.main()
