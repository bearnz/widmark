# boozer-calc

Python CLI that approximates blood alcohol (BAC) from the drinks you had.

## Running

Desktop app (profile, NZ drink list, live estimate and chart):

```
uv run app.py
```

Your profile, the last 24 hours of drinks and your plan are saved to
`~/.boozer-calc/state.json`, outside the repo. Only one window can be open at a
time, so two can't overwrite each other's drinks. Times can be rough: `now`,
`9pm`, `~21:30`, `noon`, `1h30m ago`, `an hour ago`. With age and height in
the profile, body water comes from the Watson formula instead of the fixed
Widmark factor.

Pick a drink, then how it was served: one of its units (can/bottle, handle,
pint, glass, bottle...) or any other amount in ml. Every beer and cider also
offers the common 440 ml and 500 ml cans/bottles. For a beer or cider, the
button under Serve makes the chosen size your default for that type, so it is
picked first from then on (click it again to unset). Spirits are logged by the ml
poured, with the mixer recorded alongside; only the spirit counts.
Pin drinks you have often as favourites: they stay at the top of the list.
The buttons above the list (All, Beer, Cider, Wine, RTDs, Spirits) show one type of drink.
Drinks with no known ABV are listed greyed out; select one and use Set ABV to
enter the ABV from its label (any drink's ABV can be corrected the same way).
That writes the manual column in data/abv_sources.csv and rebuilds drinks.json,
so the change shows up in git as an edit to commit.
A drink that isn't listed can be added for good: type its name in the search,
then Add to list asks for its type, ABV and serves. It becomes a new row in
data/abv_sources.csv (noted "Added in the app"), the same as a Set ABV edit.
Publish list changes pushes those edits to GitHub, after showing them: they go
on one `chore/data-YYYY-MM-DD` branch per day with a PR into main, so a day's
edits are one PR. It works in its own worktree (`~/.boozer-calc/publish`), so
the checkout's branch and uncommitted files stay as they are (publish.py).
Once the PR is merged and Publish says there's nothing new, the checkout's
copies of the CSV and drinks.json can be discarded and main pulled.
Until then, drinks added or edited since the last commit are listed in dark
yellow, so uncommitted edits stand out.
Cop stop (on by default) shows what NZ law would do if you were pulled over
driving at your current BAC: the infringement or court penalty for your age,
from NZTA and Community Law (copstop.py lists the pages). Not legal advice.
A qty above 1 is spread evenly from When over the time you give (one every 30
minutes unless you change it). Tick "With a meal" for drinks had with food: they
absorb more slowly. The log's With/without a meal button changes it afterwards.

The window has two tabs, each its own session. Current session is the drinks
you've had; Plan a session is drinks you mean to have, at times like `9pm`,
`sat 8pm`, `tomorrow 6pm` or `in 2h` (up to a week ahead). Their drinks never
mix: a plan doesn't add to your BAC now, and tonight's drinks don't join the
plan. Edit selected loads a drink back into the form to change it; edited drinks
keep the ABV they were logged with. Both tabs say when you'd be legal to drive
again: back under the 0.05% adult limit (zero under 20) at the typical
elimination rate, with the later time for a slow one, and how long until each:
from now on the tonight tab, from the first drink on a plan.

The drink list (`drinks.json`) is generated from `data/abv_sources.csv`: one row
per product with its serving units, the ABV that Liquorland, Woolworths, New
World and PAK'nSAVE each list, the ABV the brewery itself lists (Garage Project,
Sawmill, and DB Breweries' brands; it wins over the retailers), all collected
October 2026, a `manual` column for checked values,
and a `status` column flagging conflicts and drinks with no retailer ABV. Edit
the CSV, then:

```
uv run build_drinks.py
```

One-off estimate from the command line:

```
uv run bac.py -w 80kg --sex male --at 08:00 -d "2x pint 5% @20:00" -d "glass 13% @22:30" -d "shot @00:30"
```

Drinks are `[N x] SIZE [ABV%] @HH:MM`; SIZE is mL, oz, or a preset (pint,
schooner, can, bottle, glass, shot). List them in the order you had them and
times roll past midnight. `--at` defaults to now; `--food` slows absorption;
`--chart` prints an hourly timeline. `uv run bac.py -h` lists everything.

It uses the Widmark formula with linear absorption and constant elimination.
The constants (r factors, elimination rates, absorption times, preset sizes)
are typical values that haven't been checked against a source. It's an
estimate only: never use it to decide whether you're fit to drive.

## Drink list PRs

Changes to the drink list (ABVs, new drinks, any edit to data/abv_sources.csv)
reach main only through the app's Publish button: one `chore/data-YYYY-MM-DD`
branch and PR per day. Don't fold them into feature PRs, and don't commit them
on a branch by hand; leave them uncommitted in the checkout for Publish.

Before changing anything in the app, check for open data PRs:

```
gh pr list --state open --json number,headRefName,url
```

If a `chore/data-` PR from a day before today is open, remind Josh to review
and merge it, with its link, and wait for him before making any changes.
Today's own data PR doesn't block anything.

## Testing

Tests live in `tests/`. From the repo root:

```
uv run python -m unittest
```

## Notes

Conventions are in ~/.claude/CLAUDE.md (feature branches -> PR -> main,
push only on approval, provenance labels).
