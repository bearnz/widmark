"""Rough blood alcohol (BAC) estimate from last night's drinks.

Widmark formula with linear absorption and constant (zero-order) elimination.
It's an estimate: real BAC varies a lot between people and between nights.
Never use it to decide whether you're OK to drive.

Example:
    python bac.py --weight 80kg --sex male --at 08:00 \
        --drink "2x pint 5% @20:00" --drink "glass 13% @22:30" --drink "shot @23:15"
"""
import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

ETHANOL_G_PER_ML = 0.789
ML_PER_OZ = 29.5735
STANDARD_DRINK_G = 10  # NZ/AU standard drink; the US uses 14 g
WIDMARK_R = {"male": 0.68, "female": 0.55}  # body water distribution ratio
ELIM_RATES = {"fast": 0.020, "typical": 0.015, "slow": 0.010}  # BAC % per hour
ABSORB_MINUTES = 45
ABSORB_MINUTES_FOOD = 90
BLOOD_WATER_FRACTION = 0.8  # links Watson body water to Widmark r
PLAUSIBLE_R = (0.4, 0.95)  # a Watson r outside this means the profile numbers don't fit together
WEIGHT_KG = (30, 300)
MAX_ABV = 100
THRESHOLDS = (0.08, 0.05, 0.02)

# name: (volume mL, default ABV %)
PRESETS = {
    "pint": (568, 5),
    "schooner": (425, 5),
    "can": (330, 5),
    "bottle": (330, 5),
    "glass": (150, 13),
    "shot": (30, 40),
}

DRINK_RE = re.compile(
    r"^\s*(?:(?P<count>\d+)\s*x\s*)?"
    r"(?P<size>\d+(?:\.\d+)?\s*(?:ml|oz)|[a-z]+)\s*"
    r"(?:(?P<abv>\d+(?:\.\d+)?)\s*%)?\s*"
    r"@\s*(?P<time>\d{1,2}:\d{2})\s*$",
    re.IGNORECASE,
)
APPROX_PREFIX_RE = re.compile(r"^(?:~|about|around|approx\.?)\s*")
AGO_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(h|hrs?|hours?|m|mins?|minutes?)\s+ago$")
CLOCK_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?$")
MINUTE = timedelta(minutes=1)


@dataclass
class Drink:
    time: datetime
    grams: float


def parse_clock(text):
    t = datetime.strptime(text.strip(), "%H:%M")
    return t.hour, t.minute


# Times are aware local datetimes (datetime.now().astimezone()), so the gap between
# two times is real elapsed time even across a daylight-saving change. Naive times
# are read as local.

def now_local():
    return datetime.now().astimezone()


def at_clock(day, hour, minute):
    """hour:minute local wall-clock time on the given date, with that moment's UTC offset."""
    return datetime(day.year, day.month, day.day, hour, minute).astimezone()


def local_date(t):
    return t.astimezone().date()


def most_recent(now, hour, minute):
    t = at_clock(local_date(now), hour, minute)
    return at_clock(local_date(now) - timedelta(days=1), hour, minute) if t > now else t


def parse_approx_time(text, now):
    """'now', '9pm', '~21:30', 'about 9', '1h ago', '45 min ago' -> an aware local datetime.

    Clock times are the most recent time that clock time came round; a bare hour
    with no am/pm picks whichever of the two came round most recently.
    """
    now = now.astimezone()
    s = APPROX_PREFIX_RE.sub("", text.strip().lower())
    if s in ("", "now"):
        return now.replace(second=0, microsecond=0)
    m = AGO_RE.match(s)
    if m:
        amount = float(m[1])
        delta = timedelta(hours=amount) if m[2].startswith("h") else timedelta(minutes=amount)
        return (now - delta).replace(second=0, microsecond=0).astimezone()
    m = CLOCK_RE.match(s)
    hour, minute = (int(m[1]), int(m[2] or 0)) if m else (99, 99)
    if m and m[3] and 1 <= hour <= 12:
        hours = [hour % 12 + (12 if m[3] == "pm" else 0)]
    elif m and not m[3] and hour <= 23:
        hours = [hour, (hour + 12) % 24] if hour <= 12 else [hour]
    else:
        hours = []
    if not hours or minute > 59:
        raise ValueError(f"can't read time {text!r}; try now, 9pm, ~21:30 or 45 min ago")
    return max(most_recent(now, h, minute) for h in hours)


def alcohol_grams(ml, abv, count=1):
    return count * ml * abv / 100 * ETHANOL_G_PER_ML


def check_abv(abv):
    if not 0 < abv <= MAX_ABV:
        raise ValueError(f"ABV must be more than 0% and at most {MAX_ABV}%, not {abv:g}%")
    return abv


def watson_r(sex, age, height_cm, weight_kg):
    """Widmark r from Watson's total body water estimate, for people who give age and height."""
    if sex == "male":
        water_l = 2.447 - 0.09516 * age + 0.1074 * height_cm + 0.3362 * weight_kg
    else:
        water_l = -2.097 + 0.1069 * height_cm + 0.2466 * weight_kg
    return water_l / (BLOOD_WATER_FRACTION * weight_kg)


def parse_drink(spec):
    """'2x pint 4.5% @21:00' -> (grams of alcohol, (hour, minute))."""
    m = DRINK_RE.match(spec)
    if not m:
        raise ValueError(f"can't read drink {spec!r}; expected e.g. '2x pint 5% @21:00'")
    size = m["size"].lower().replace(" ", "")
    if size.endswith("ml"):
        ml, abv = float(size[:-2]), None
    elif size.endswith("oz"):
        ml, abv = float(size[:-2]) * ML_PER_OZ, None
    elif size in PRESETS:
        ml, abv = PRESETS[size]
    else:
        raise ValueError(f"unknown size {m['size']!r}; use mL, oz or one of {', '.join(PRESETS)}")
    if m["abv"]:
        abv = float(m["abv"])
    if abv is None:
        raise ValueError(f"{spec!r} needs an ABV, e.g. '330ml 5% @20:00'")
    count = int(m["count"] or 1)
    if count < 1 or ml <= 0:
        raise ValueError(f"{spec!r} has no drink in it")
    return alcohol_grams(ml, check_abv(abv), count), parse_clock(m["time"])


def parse_weight(text):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(kg|lbs?)?\s*", text, re.IGNORECASE)
    if not m:
        raise argparse.ArgumentTypeError(f"can't read weight {text!r}; use e.g. 80kg or 176lb")
    value = float(m[1])
    kg = value * 0.45359237 if (m[2] or "").lower().startswith("lb") else value
    if not WEIGHT_KG[0] <= kg <= WEIGHT_KG[1]:
        raise argparse.ArgumentTypeError(f"weight must be {WEIGHT_KG[0]}-{WEIGHT_KG[1]} kg, not {kg:g} kg")
    return kg


def place_drinks(parsed, now):
    """Turn clock times into aware local datetimes.

    The first drink is the most recent time that clock time came round (so it's
    in the last 24 hours), and each later drink rolls past midnight as needed.
    """
    t = most_recent(now.astimezone(), *parsed[0][1])
    drinks = []
    for grams, (h, mi) in parsed:
        nxt = at_clock(local_date(t), h, mi)
        if nxt < t:
            nxt = at_clock(local_date(t) + timedelta(days=1), h, mi)
        t = nxt
        drinks.append(Drink(t, grams))
    return drinks


def roll_forward(clock, after):
    t = at_clock(local_date(after), *clock)
    return at_clock(local_date(after) + timedelta(days=1), *clock) if t < after else t


def simulate(drinks, weight_kg, r, elim_per_hour, absorb_minutes):
    """Minute-by-minute BAC (%) as a list of (datetime, bac), until back to zero.

    Each drink is absorbed at a steady rate over absorb_minutes; the body clears
    a fixed elim_per_hour whenever BAC is above zero.
    """
    if elim_per_hour <= 0 or absorb_minutes < 1:
        raise ValueError("elimination rate must be positive and absorption at least 1 minute")
    bac_per_gram = 100 / (r * weight_kg * 1000)
    absorb = timedelta(minutes=absorb_minutes)
    absorb_end = max(d.time for d in drinks) + absorb
    t = min(d.time for d in drinks)
    bac = 0.0
    series = [(t, bac)]
    while t < absorb_end or bac > 0:
        grams = sum(d.grams for d in drinks if d.time <= t < d.time + absorb) / absorb_minutes
        bac = max(0.0, bac + grams * bac_per_gram - elim_per_hour / 60)
        t += MINUTE
        series.append((t, bac))
    return series


def bac_at(series, when):
    start = series[0][0]
    i = int((when - start) / MINUTE)
    return series[i][1] if 0 <= i < len(series) else 0.0


def time_below(series, threshold):
    """First time BAC drops to or below threshold for good, or None if it never went above."""
    above = [i for i, (_, b) in enumerate(series) if b > threshold]
    return series[above[-1] + 1][0] if above else None


def fmt(t):
    return t.astimezone().strftime("%a %H:%M")


def timeline(series, mark=None, width=40):
    peak = max(b for _, b in series) or 1
    rows = []
    for t, b in series[::60]:
        bar = "#" * round(b / peak * width)
        flag = "  <- --at" if mark and t <= mark < t + timedelta(hours=1) else ""
        rows.append(f"  {fmt(t)}  {b:.3f}%  {bar}{flag}")
    return "\n".join(rows)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Approximate BAC from drinks consumed (Widmark formula). An estimate only: "
        "never use it to decide whether you're fit to drive.",
        epilog="Drink format: '[N x] SIZE [ABV%] @HH:MM'. SIZE is mL, oz, or a preset "
        + ", ".join(f"{k} ({v[0]}mL, {v[1]}%)" for k, v in PRESETS.items())
        + ". Give drinks in the order you had them; times roll past midnight.",
    )
    p.add_argument("--drink", "-d", action="append", required=True, metavar="SPEC",
                   help="a drink, e.g. '2x pint 4.5%% @21:00' (repeat for each)")
    p.add_argument("--weight", "-w", type=parse_weight, required=True, help="body weight, e.g. 80kg or 176lb")
    p.add_argument("--sex", choices=WIDMARK_R, required=True, help="sets the Widmark r factor")
    p.add_argument("--r", type=float, help="override the Widmark r factor")
    p.add_argument("--at", type=parse_clock, metavar="HH:MM", help="time to estimate BAC for (default: now)")
    p.add_argument("--food", action="store_true", help="drank with a meal: slower absorption (90 min, not 45)")
    p.add_argument("--elim", type=float, default=ELIM_RATES["typical"],
                   help="elimination rate in BAC %% per hour (default 0.015)")
    p.add_argument("--chart", action="store_true", help="print an hourly timeline")
    args = p.parse_args(argv)
    if args.elim <= 0:
        p.error("--elim must be positive")
    if args.r is not None and not PLAUSIBLE_R[0] <= args.r <= PLAUSIBLE_R[1]:
        p.error(f"--r must be between {PLAUSIBLE_R[0]} and {PLAUSIBLE_R[1]}")

    try:
        parsed = [parse_drink(s) for s in args.drink]
    except ValueError as e:
        p.error(str(e))

    now = now_local()
    drinks = place_drinks(parsed, now)
    at = roll_forward(args.at, drinks[0].time) if args.at else now
    r = WIDMARK_R[args.sex] if args.r is None else args.r
    absorb = ABSORB_MINUTES_FOOD if args.food else ABSORB_MINUTES
    series = simulate(drinks, args.weight, r, args.elim, absorb)
    # Same night at the slow and fast ends of normal elimination, for a range.
    slow = simulate(drinks, args.weight, r, ELIM_RATES["slow"], absorb)
    fast = simulate(drinks, args.weight, r, ELIM_RATES["fast"], absorb)

    grams = sum(d.grams for d in drinks)
    peak_t, peak = max(series, key=lambda s: s[1])
    print(f"Drinks: {fmt(drinks[0].time)} to {fmt(drinks[-1].time)}, "
          f"{grams:.0f} g alcohol ({grams / STANDARD_DRINK_G:.1f} standard drinks of {STANDARD_DRINK_G} g)")
    print(f"Peak:   {peak:.3f}% around {fmt(peak_t)}")
    print(f"At {fmt(at)}: {bac_at(series, at):.3f}%  "
          f"(range {bac_at(fast, at):.3f}-{bac_at(slow, at):.3f}% for fast-slow elimination)")
    for th in THRESHOLDS:
        t = time_below(series, th)
        print(f"Below {th:.2f}%: {fmt(t) if t else 'never above'}")
    print(f"Zero:   {fmt(series[-1][0])}  (fast {fmt(fast[-1][0])}, slow {fmt(slow[-1][0])})")
    if args.chart:
        print("\nHourly timeline:")
        print(timeline(series, at))
    print("\nEstimate only. Individual BAC varies widely; don't drive on these numbers.")


if __name__ == "__main__":
    sys.exit(main())
