"""Publishing against a local bare repo standing in for GitHub, with gh faked."""
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import build_drinks
import publish

DAY = date(2026, 11, 5)
BRANCH = "chore/data-2026-11-05"
LOVE_DOCTOR = "Lakeman Love Doctor Hazy IPA"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


class PublishTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        seed = tmp / "seed"
        (seed / "data").mkdir(parents=True)
        for f in ("bac.py", "build_drinks.py", "drinks.json", publish.CSV_PATH):
            shutil.copy(publish.REPO / f, seed / f)
        git("init", "--quiet", "--initial-branch", "main", cwd=seed)
        git("add", ".", cwd=seed)
        git("commit", "--quiet", "-m", "seed", cwd=seed)
        # A bare clone stands in for GitHub; main is never pushed to it, only cloned.
        self.origin = tmp / "origin.git"
        git("clone", "--quiet", "--bare", str(seed), str(self.origin), cwd=tmp)
        self.repo, self.worktree = tmp / "checkout", tmp / "publish"
        git("clone", "--quiet", str(self.origin), str(self.repo), cwd=tmp)
        self.prs, self.bodies, self.created = [], {}, []
        patcher = mock.patch.object(publish, "gh", self.fake_gh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_gh(self, *args, cwd):
        def arg(flag):
            return args[args.index(flag) + 1]
        if args[:2] == ("pr", "list"):
            return json.dumps(self.prs)
        if args[:2] == ("pr", "create"):
            number = 20 + len(self.prs)
            self.created.append((arg("--base"), arg("--head")))
            self.prs.append({"number": number, "url": f"https://example.test/pull/{number}", "headRefName": arg("--head")})
            self.bodies[number] = Path(arg("--body-file")).read_text(encoding="utf-8")
            return f"https://example.test/pull/{number}\n"
        if args[:2] == ("pr", "edit"):
            self.bodies[int(args[2])] = Path(arg("--body-file")).read_text(encoding="utf-8")
            return ""
        raise AssertionError(f"unexpected gh call {args}")

    def set_abv(self, name, abv):
        build_drinks.set_manual(name, abv, self.repo / publish.CSV_PATH, self.repo / "drinks.json")

    def publish(self):
        return publish.publish(today=DAY, repo=self.repo, worktree=self.worktree)

    def on_origin(self, ref, path):
        return git("--git-dir", str(self.origin), "show", f"{ref}:{path}", cwd=self.repo)

    def origin_drink(self, ref, name):
        return next(d for d in json.loads(self.on_origin(ref, "drinks.json"))["drinks"] if d["name"] == name)

    def change_main(self, edit):
        """Land a commit on origin's main, as merging a PR would, without pushing to main."""
        other = self.repo.parent / "other"
        if not other.exists():
            git("clone", "--quiet", str(self.origin), str(other), cwd=self.repo.parent)
        git("pull", "--quiet", cwd=other)
        edit(other)
        git("commit", "--quiet", "-am", "main moves on", cwd=other)
        git("--git-dir", str(self.origin), "fetch", "--quiet", str(other), "main:main", cwd=other)

    def test_first_publish_opens_a_pr_for_the_day_and_leaves_the_checkout_alone(self):
        self.set_abv(LOVE_DOCTOR, 8.7)
        self.assertEqual(publish.local_changes(self.repo), {LOVE_DOCTOR: ({"manual": "8.7"}, True)})
        head, csv = git("rev-parse", "HEAD", cwd=self.repo), (self.repo / publish.CSV_PATH).read_text(encoding="utf-8")

        url, pushed = self.publish()
        self.assertEqual((url, pushed), ("https://example.test/pull/20", True))
        self.assertEqual(self.created, [("main", BRANCH)])
        drink = self.origin_drink(BRANCH, LOVE_DOCTOR)
        self.assertEqual((drink["abv"], drink["source"]), (8.7, "manual"))
        self.assertIn(f"{LOVE_DOCTOR}: ABV set to 8.7%", self.bodies[20])
        self.assertIn(f"- {LOVE_DOCTOR}: ABV set to 8.7%", git("--git-dir", str(self.origin), "log", "-1",
                                                               "--format=%B", BRANCH, cwd=self.repo))
        # The checkout keeps its branch, commit and uncommitted edit.
        self.assertEqual(git("branch", "--show-current", cwd=self.repo).strip(), "main")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)
        self.assertEqual((self.repo / publish.CSV_PATH).read_text(encoding="utf-8"), csv)

    def test_later_changes_the_same_day_go_on_the_same_branch_and_pr(self):
        self.set_abv(LOVE_DOCTOR, 8.7)
        self.publish()
        build_drinks.add_drink("Backyard Lager", "beer", 4.6, "can 440; pint 568",
                               self.repo / publish.CSV_PATH, self.repo / "drinks.json")
        url, pushed = self.publish()
        self.assertEqual((url, pushed), ("https://example.test/pull/20", True))
        self.assertEqual(len(self.created), 1)
        commits = git("--git-dir", str(self.origin), "rev-list", f"main..{BRANCH}", cwd=self.repo).split()
        self.assertEqual(len(commits), 2)
        self.assertEqual(self.origin_drink(BRANCH, "Backyard Lager")["abv"], 4.6)
        self.assertIn(f"{LOVE_DOCTOR}: ABV set to 8.7%", self.bodies[20])  # the PR lists the whole day
        self.assertIn("Added Backyard Lager (beer, 4.6%; serves can 440; pint 568)", self.bodies[20])

    def test_edited_drinks_are_the_rows_changed_since_the_last_commit(self):
        self.assertEqual(publish.edited_drinks(self.repo), set())
        self.set_abv(LOVE_DOCTOR, 8.7)
        build_drinks.add_drink("Backyard Lager", "beer", 4.6, "can 440", self.repo / publish.CSV_PATH,
                               self.repo / "drinks.json")
        self.assertEqual(publish.edited_drinks(self.repo), {LOVE_DOCTOR, "Backyard Lager"})
        self.assertEqual(publish.edited_drinks(self.repo.parent), set())  # not a checkout: none

    def test_publishing_again_with_nothing_new_pushes_nothing(self):
        self.set_abv(LOVE_DOCTOR, 8.7)
        self.publish()
        self.assertEqual(self.publish(), ("https://example.test/pull/20", False))
        commits = git("--git-dir", str(self.origin), "rev-list", f"main..{BRANCH}", cwd=self.repo).split()
        self.assertEqual(len(commits), 1)

    def test_only_the_changed_fields_are_applied_so_main_keeps_its_own_edits(self):
        self.set_abv(LOVE_DOCTOR, 8.7)

        def edit(other):  # main changes another column of the same row, and another row
            path = other / publish.CSV_PATH
            text = path.read_text(encoding="utf-8")
            row = next(line for line in text.splitlines() if line.startswith(LOVE_DOCTOR + ","))
            path.write_text(text.replace(row, row.rstrip(",") + ",Checked on the can"), encoding="utf-8")
            build_drinks.set_manual("Heineken", 5.1, path, other / "drinks.json")
        self.change_main(edit)
        self.publish()
        _, rows = publish.read_rows(self.on_origin(BRANCH, publish.CSV_PATH))
        self.assertEqual((rows[LOVE_DOCTOR]["manual"], rows[LOVE_DOCTOR]["status"], rows[LOVE_DOCTOR]["note"]),
                         ("8.7", "manual", "Checked on the can"))
        self.assertEqual(rows["Heineken"]["manual"], "5.1")

    def test_nothing_new_once_main_has_every_change(self):
        with self.assertRaisesRegex(publish.PublishError, "hasn't changed"):
            self.publish()
        self.set_abv(LOVE_DOCTOR, 8.7)
        self.change_main(lambda other: build_drinks.set_manual(
            LOVE_DOCTOR, 8.7, other / publish.CSV_PATH, other / "drinks.json"))
        with self.assertRaisesRegex(publish.PublishError, "can be discarded"):
            self.publish()
        self.assertEqual(self.created, [])


class ChangesTest(unittest.TestCase):
    HEADER = "drink,type,units,manual,status,note\n"

    def test_status_alone_is_not_a_change_and_removed_rows_are_ignored(self):
        before = self.HEADER + "A,beer,pint 568,,missing,\nB,beer,pint 568,,missing,\n"
        after = self.HEADER + "A,beer,pint 568,,guess only,\n"
        self.assertEqual(publish.changed_rows(before, after), {})

    def test_apply_sets_fields_and_appends_new_drinks(self):
        base = self.HEADER + "A,beer,pint 568,,missing,kept\n"
        changes = {"A": ({"manual": "5"}, True), "C": ({"drink": "C", "type": "cider", "units": "can 440"}, False)}
        self.assertEqual(publish.apply(changes, base),
                         self.HEADER + "A,beer,pint 568,5,missing,kept\nC,cider,can 440,,,\n")

    def test_describe(self):
        self.assertEqual(publish.describe("A", {"manual": ""}, True), "A: manual ABV cleared")
        self.assertEqual(publish.describe("A", {"manual": "5", "units": "pint 568"}, True),
                         "A: ABV set to 5%; units edited")
        self.assertEqual(publish.describe("C", {"type": "cider", "manual": "", "units": "can 440"}, False),
                         "Added C (cider, no ABV yet; serves can 440)")

    def test_stale_prs_are_data_prs_from_before_today(self):
        prs = [{"number": 3, "url": "u3", "headRefName": "chore/data-2026-11-05"},
               {"number": 2, "url": "u2", "headRefName": "chore/data-2026-11-04"},
               {"number": 4, "url": "u4", "headRefName": "feature/other"}]
        with mock.patch.object(publish, "gh", lambda *a, cwd: json.dumps(prs)):
            self.assertEqual([p["number"] for p in publish.stale_prs(DAY)], [2])
            self.assertEqual([p["number"] for p in publish.open_data_prs()], [2, 3])


if __name__ == "__main__":
    unittest.main()
