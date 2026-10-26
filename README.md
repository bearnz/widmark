# boozer-calc

Python CLI that approximates blood alcohol (BAC) from the drinks you had.

## Running

Desktop app (profile, NZ drink list, live estimate and chart):

```
uv run app.py
```

Your profile and the last 24 hours of drinks are saved to
`~/.boozer-calc/state.json`, outside the repo. Only one window can be open at a
time, so two can't overwrite each other's drinks. Times can be rough: `now`,
`9pm`, `~21:30`, `noon`, `1h30m ago`, `an hour ago`. With age and height in
the profile, body water comes from the Watson formula instead of the fixed
Widmark factor.

Pick a drink, then how it was served: one of its units (can/bottle, handle,
pint, glass, bottle...) or any other amount in ml. Spirits are logged by the ml
poured, with the mixer recorded alongside; only the spirit counts.
Pin drinks you have often as favourites: they stay at the top of the list.
A qty above 1 is spread evenly from When over the time you give (one every 30
minutes unless you change it). Tick "With a meal" for drinks had with food: they
absorb more slowly. The log's With/without a meal button changes it afterwards.

The drink list (`drinks.json`) is generated from `data/abv_sources.csv`: one row
per product with its serving units, the ABV that Liquorland, Woolworths, New
World and PAK'nSAVE each list (collected October 2026), a `manual` column for checked values,
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

## Testing

```
uv run python -m unittest
```
