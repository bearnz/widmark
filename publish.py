"""Publish drink list changes made in the app (Set ABV, Add to list) to GitHub as a PR.

Those edits land in data/abv_sources.csv in this checkout, uncommitted. Publishing puts them,
with drinks.json rebuilt, on one chore/data-YYYY-MM-DD branch per day and opens a PR for it
into main, or updates the day's PR if one is open: a day's changes are one PR, not one each.

It works in a worktree of its own (WORKTREE), so this checkout's branch and uncommitted work
are never touched, and it commits and pushes with plain git, so the git hooks run as usual.
Only the rows changed here since the last commit are applied, field by field, onto the day's
branch (or main), so rows changed on main since this checkout was last pulled are kept.
"""
import csv
import io
import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).parent
WORKTREE = Path.home() / ".widmark" / "publish"
CSV_PATH = "data/abv_sources.csv"
CATALOGUE_PATH = "drinks.json"
BRANCH_PREFIX = "chore/data-"
BASE = "main"
DERIVED = ("status",)  # worked out by build_drinks.py, so not a change of its own


class PublishError(Exception):
    """Publishing stopped; the message says why. Nothing was pushed unless it says so."""


def run(args, cwd, stdin=None):
    try:
        done = subprocess.run(args, cwd=cwd, input=stdin, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        raise PublishError(f"{args[0]} isn't installed, or isn't on PATH.") from None
    if done.returncode:
        raise PublishError(f"{' '.join(str(a) for a in args[:3])} failed:\n{(done.stderr or done.stdout).strip()}")
    return done.stdout


def git(*args, cwd=REPO, stdin=None):
    return run(["git", *args], cwd, stdin)


def gh(*args, cwd=REPO):
    return run(["gh", *args], cwd)


def branch_for(day):
    return BRANCH_PREFIX + day.isoformat()


def read_rows(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    return reader.fieldnames, {row["drink"]: row for row in reader}


def changed_rows(before, after):
    """{drink: (fields that changed with their new values, whether the drink was there before)}
    between two versions of the CSV. Removed rows aren't changes the app makes, so are ignored."""
    _, old = read_rows(before)
    _, new = read_rows(after)
    changes = {}
    for name, row in new.items():
        diff = {k: v for k, v in row.items() if k not in DERIVED and old.get(name, {}).get(k) != v}
        if diff:
            changes[name] = (diff, name in old)
    return changes


def local_changes(repo=REPO, sources=None):
    """The drink list changes in this checkout since its last commit (sources, the CSV the
    app edits, defaults to the checkout's own)."""
    sources = repo / CSV_PATH if sources is None else sources
    return changed_rows(git("show", f"HEAD:{CSV_PATH}", cwd=repo), sources.read_text(encoding="utf-8-sig"))


def edited_drinks(repo=REPO, sources=None):
    """Names of the drinks added or edited since the last commit, or none if git can't say."""
    try:
        return set(local_changes(repo, sources))
    except PublishError:
        return set()


def apply(changes, text):
    """text (a CSV) with each change's fields set, and new drinks added at the end."""
    fields, rows = read_rows(text)
    for name, (diff, _) in changes.items():
        rows.setdefault(name, dict.fromkeys(fields, "")).update(diff)
    out = io.StringIO()
    writer = csv.DictWriter(out, fields, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows.values())
    return out.getvalue()


def describe(name, diff, existed):
    """One line for a change, as the confirmation, commit and PR list it."""
    if not existed:
        abv = f"{diff['manual']}%" if diff.get("manual") else "no ABV yet"
        return f"Added {name} ({diff.get('type', '?')}, {abv}; serves {diff.get('units', '?')})"
    parts = []
    if "manual" in diff:
        parts.append(f"ABV set to {diff['manual']}%" if diff["manual"] else "manual ABV cleared")
    others = [k for k in diff if k != "manual"]
    if others:
        parts.append(f"{', '.join(others)} edited")
    return f"{name}: {'; '.join(parts)}"


def describe_all(changes):
    return [describe(name, diff, existed) for name, (diff, existed) in changes.items()]


def open_data_prs(repo=REPO):
    """Open PRs from chore/data- branches, as [{"number", "url", "headRefName"}], oldest day first."""
    prs = json.loads(gh("pr", "list", "--state", "open", "--json", "number,url,headRefName", cwd=repo) or "[]")
    return sorted((p for p in prs if p["headRefName"].startswith(BRANCH_PREFIX)), key=lambda p: p["headRefName"])


def stale_prs(today=None, repo=REPO):
    """Open data PRs from a day before today: merge those before going on."""
    today_branch = branch_for(today or date.today())
    return [p for p in open_data_prs(repo) if p["headRefName"] < today_branch]


def _checkout(base, repo, worktree):
    """The publishing worktree, at base (detached), made the first time it's needed."""
    if (worktree / ".git").exists():
        git("checkout", "--quiet", "--force", "--detach", base, cwd=worktree)
    else:
        git("worktree", "prune", cwd=repo)  # forget one whose folder was deleted
        worktree.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "--quiet", "--detach", str(worktree), base, cwd=repo)


def publish(changes=None, today=None, repo=REPO, worktree=WORKTREE):
    """Push this checkout's drink list changes to the day's branch and open or update its PR.

    Returns (PR url, whether anything new was pushed). Raises PublishError if there's nothing
    to publish or a git or gh step fails (the message says whether anything was pushed).
    """
    changes = local_changes(repo) if changes is None else changes
    if not changes:
        raise PublishError("Nothing to publish: the drink list hasn't changed since the last commit.")
    day = today or date.today()
    branch = branch_for(day)
    git("fetch", "--quiet", "--prune", "origin", cwd=repo)
    pr = next((p for p in open_data_prs(repo) if p["headRefName"] == branch), None)
    has_branch = bool(git("ls-remote", "--heads", "origin", branch, cwd=repo).strip())
    base = f"origin/{branch}" if pr and has_branch else f"origin/{BASE}"
    _checkout(base, repo, worktree)

    sources = worktree / CSV_PATH
    sources.write_text(apply(changes, sources.read_text(encoding="utf-8-sig")), encoding="utf-8")
    run([sys.executable, "build_drinks.py"], worktree)  # the branch's own build, so its drinks.json matches
    git("add", CSV_PATH, CATALOGUE_PATH, cwd=worktree)
    pushed = bool(git("diff", "--cached", "--name-only", cwd=worktree).strip())
    if pushed:
        lines = describe_all(changed_rows(git("show", f"{base}:{CSV_PATH}", cwd=worktree),
                                          sources.read_text(encoding="utf-8")))
        message = f"chore(data): drink list updates for {day.isoformat()}\n\n" + "\n".join(f"- {line}" for line in lines)
        git("commit", "--quiet", "--file", "-", cwd=worktree, stdin=message + "\n")
        git("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}", cwd=worktree)
    elif not pr:
        raise PublishError(f"Nothing new to publish: {BASE} already has every change in this checkout.\n\n"
                           "This checkout's copies of the CSV and drinks.json can be discarded and main pulled.")

    lines = describe_all(changed_rows(git("show", f"origin/{BASE}:{CSV_PATH}", cwd=worktree),
                                      sources.read_text(encoding="utf-8")))
    body = (f"Drink list changes made in the app on {day.isoformat()}, published with its Publish button.\n\n"
            "## Changes\n\n" + "\n".join(f"- {line}" for line in lines) + "\n\n"
            "## Review notes\n\nThe ABVs were typed in by hand from labels: check each against its label "
            "or the brewery.\n\n## Testing\n\ndrinks.json was rebuilt from the CSV by this branch's "
            "build_drinks.py, which refuses a table with problems.\n")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
    try:
        if pr:
            gh("pr", "edit", str(pr["number"]), "--body-file", f.name, cwd=repo)
            return pr["url"], pushed
        out = gh("pr", "create", "--base", BASE, "--head", branch,
                 "--title", f"chore(data): drink list updates for {day.isoformat()}", "--body-file", f.name, cwd=repo)
        return out.strip().splitlines()[-1], pushed
    except PublishError as e:
        where = "were pushed to" if pushed else "are on"
        raise PublishError(f"The changes {where} {branch}, but the PR step failed:\n{e}") from None
    finally:
        Path(f.name).unlink()
