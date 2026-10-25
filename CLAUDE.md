# boozer-calc

Python CLI that approximates blood alcohol (BAC) from the drinks you had.

## Running

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

## Notes

Conventions are in ~/.claude/CLAUDE.md (feature branches -> PR -> main,
push only on approval, provenance labels).
