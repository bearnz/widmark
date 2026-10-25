import json
import shutil
import tempfile
import unittest
from pathlib import Path

import app
import build_drinks


def row(**values):
    base = {"drink": "Test", "type": "beer", "liquorland": "", "woolworths": "", "new_world": "", "guess": "",
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
            (row(guess="5"), ("guess only", None)),
            (row(), ("missing", None)),
        ]
        for r, expected in cases:
            self.assertEqual(build_drinks.resolve(r), expected, r)

    def test_close_values_agree(self):
        self.assertEqual(build_drinks.resolve(row(liquorland="4.6", woolworths="4.5"))[0], "agree")


class UnitsTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(build_drinks.parse_units("can/bottle 330; large glass 250"),
                         [{"label": "can/bottle", "ml": 330}, {"label": "large glass", "ml": 250}])
        for bad in ("330", "pint", "pint 568;", "pint lots"):
            with self.assertRaises(ValueError, msg=bad):
                build_drinks.parse_units(bad)


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
