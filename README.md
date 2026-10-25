# boozer-calc

Python CLI that approximates blood alcohol (BAC) from the drinks you had.

## Running

Desktop app (profile, NZ drink list, live estimate and chart):

```
uv run app.py
```

Your profile and the last 24 hours of drinks are saved to
`~/.boozer-calc/state.json`, outside the repo. Times can be rough: `now`,
`9pm`, `~21:30`, `45 min ago`. The drink list is `drinks.json`; its sizes and
ABVs are from memory and worth checking against labels. With age and height in
the profile, body water comes from the Watson formula instead of the fixed
Widmark factor.

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
