"""Desktop app: save your profile, log drinks as you go, watch your estimated BAC.

Your profile and the last 24 hours of drinks are kept in ~/.boozer-calc/state.json.
Estimate only: never use it to decide whether you're fit to drive.
"""
import json
import os
import re
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

import bac

CATALOGUE = Path(__file__).with_name("drinks.json")
STATE = Path.home() / ".boozer-calc" / "state.json"
LOG_HOURS = 24  # drinks older than this drop off the log
MAX_QTY = 24
PACE_MINUTES = 30  # default gap between drinks when a qty of them is logged at once
AGE = (18, 120)
HEIGHT_CM = (100, 250)
CUSTOM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*ml\s+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
HINT = "#666"
OTHER = "other amount"


class StateError(Exception):
    """state.json couldn't be read; it has been moved aside so nothing overwrites it."""


class AlreadyOpen(Exception):
    """Another window is already using this state.json."""


class StateLock:
    """An OS lock on state.json.lock, held while a window is open.

    Two windows on the same state.json would each save their own copy, the last one
    overwriting drinks added in the other, so a second window refuses to open. The OS
    releases the lock when the process ends, even after a crash, so it can't go stale.
    """

    def __init__(self, state_path):
        path = state_path.with_name(state_path.name + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(path, "a+")  # noqa: SIM115 - held open for the life of the window
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise AlreadyOpen("boozer-calc is already open. Use that window, or close it first, "
                              "so the two don't overwrite each other's drinks.") from None

    def release(self):
        self.file.close()


def load_catalogue(path=CATALOGUE):
    return json.loads(path.read_text(encoding="utf-8"))["drinks"]


def load_state(path=STATE):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        data = {}
    except ValueError as e:  # includes JSONDecodeError and UnicodeDecodeError
        backup = path.with_name(f"{path.stem}.unreadable-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        path.replace(backup)
        raise StateError(f"{path} couldn't be read ({e}), so it was moved to {backup.name} "
                         f"and the app started with an empty profile and log.") from e
    log = data.get("log", [])
    for entry in log:  # older versions had one meal setting for the whole log
        entry.setdefault("food", data.get("food", False))
    return {"profile": data.get("profile", {}), "log": log, "favourites": data.get("favourites", [])}


def save_state(data, path=STATE):
    """Write via a temporary file, so a crash mid-write can't leave a half-written state.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def entry_time(entry):
    """A logged drink's time as an aware local datetime (entries from older versions are naive)."""
    return datetime.fromisoformat(entry["time"]).astimezone()


def recent(log, now):
    cutoff = now.astimezone() - timedelta(hours=LOG_HOURS)
    return [e for e in log if entry_time(e) >= cutoff]


def entry_grams(entry):
    return bac.alcohol_grams(entry["ml"], entry["abv"], entry["count"])


def entry_drinks(entry):
    """One log entry as bac.Drinks: qty drinks spread evenly from its time over spread_minutes."""
    count = entry["count"]
    gap = timedelta(minutes=entry.get("spread_minutes", 0)) / (count - 1) if count > 1 else timedelta(0)
    absorb = bac.ABSORB_MINUTES_FOOD if entry.get("food") else bac.ABSORB_MINUTES
    grams = bac.alcohol_grams(entry["ml"], entry["abv"])
    return [bac.Drink(entry_time(entry) + i * gap, grams, absorb) for i in range(count)]


def body_water(profile):
    """(Widmark r, how it was worked out) for a saved profile."""
    default = bac.WIDMARK_R[profile["sex"]]
    if not (profile.get("age") and profile.get("height")):
        return default, "default for sex; add age and height for a closer fit"
    r = bac.watson_r(profile["sex"], profile["age"], profile["height"], profile["weight"])
    if not bac.PLAUSIBLE_R[0] <= r <= bac.PLAUSIBLE_R[1]:
        return default, f"default for sex: Watson gave an implausible {r:.2f}, check age, height and weight"
    return r, "Watson, from age and height"


def profile_r(profile):
    return body_water(profile)[0]


def estimate(profile, log):
    """BAC series at typical, slow and fast elimination for the logged drinks."""
    drinks = [d for e in log for d in entry_drinks(e)]
    r = profile_r(profile)
    return [bac.simulate(drinks, profile["weight"], r, bac.ELIM_RATES[k]) for k in ("typical", "slow", "fast")]


class App(tk.Tk):
    def __init__(self, state_path=STATE, catalogue=None):
        lock = StateLock(state_path)  # raises AlreadyOpen before any window appears
        super().__init__()
        self._lock = lock
        self.title("boozer-calc")
        self.state_path = state_path
        try:
            self.data = load_state(state_path)
        except StateError as e:
            self.data = load_state(state_path)
            warning = str(e)
            self.after(200, lambda: messagebox.showwarning("Saved data", warning, parent=self))
        self.catalogue = load_catalogue() if catalogue is None else catalogue
        left = ttk.Frame(self, padding=8)
        left.grid(row=0, column=0, sticky="ns")
        right = ttk.Frame(self, padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_profile(left)
        self._build_add(left)
        self._build_estimate(right)
        self._build_log(right)
        self._tick()

    # --- layout ---

    def _build_profile(self, parent):
        box = ttk.LabelFrame(parent, text="Profile", padding=8)
        box.pack(fill="x")
        p = self.data["profile"]
        self.sex = tk.StringVar(value=p.get("sex", "male"))
        self.age = tk.StringVar(value=fmt_num(p.get("age")))
        self.weight = tk.StringVar(value=fmt_num(p.get("weight")))
        self.height = tk.StringVar(value=fmt_num(p.get("height")))
        rows = [
            ("Sex", ttk.Combobox(box, textvariable=self.sex, values=list(bac.WIDMARK_R), state="readonly", width=12)),
            ("Age", ttk.Entry(box, textvariable=self.age, width=14)),
            ("Weight (kg)", ttk.Entry(box, textvariable=self.weight, width=14)),
            ("Height (cm)", ttk.Entry(box, textvariable=self.height, width=14)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=i, column=0, sticky="w", pady=2, padx=(0, 8))
            widget.grid(row=i, column=1, sticky="ew", pady=2)
        ttk.Button(box, text="Save profile", command=self.save_profile).grid(
            row=len(rows), column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.r_label = ttk.Label(box, foreground=HINT, wraplength=260)
        self.r_label.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_add(self, parent):
        box = ttk.LabelFrame(parent, text="Add a drink", padding=8)
        box.pack(fill="both", expand=True, pady=(8, 0))
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._filter())
        ttk.Entry(box, textvariable=self.search).pack(fill="x")
        ttk.Label(box, text="Search, or type a custom drink like 500ml 6%", foreground=HINT).pack(anchor="w")
        self.listbox = tk.Listbox(box, height=12, width=44, exportselection=False)
        self.listbox.pack(fill="both", expand=True, pady=4)
        self.listbox.bind("<<ListboxSelect>>", lambda _: self._show_serve())
        self.fav_button = ttk.Button(box, command=self.toggle_favourite)
        self.fav_button.pack(anchor="w", pady=(0, 4))

        # How it was served: one of the drink's units, or for spirits the mL poured plus mixer.
        serve = ttk.Frame(box)
        serve.pack(fill="x", pady=(0, 4))
        self.unit_row = ttk.Frame(serve)
        self.unit = tk.StringVar()
        self.other_ml = tk.StringVar()
        ttk.Label(self.unit_row, text="Serve").pack(side="left")
        self.unit_pick = ttk.Combobox(self.unit_row, textvariable=self.unit, state="readonly", width=24)
        self.unit_pick.pack(side="left", padx=(4, 8))
        self.unit_pick.bind("<<ComboboxSelected>>", lambda _: self._unit_changed())
        self.other_entry = ttk.Entry(self.unit_row, textvariable=self.other_ml, width=6)
        self.other_entry.pack(side="left")
        ttk.Label(self.unit_row, text="ml").pack(side="left", padx=(2, 0))
        self.pour_row = ttk.Frame(serve)
        self.spirit_ml = tk.StringVar()
        self.mixer_ml = tk.StringVar()
        for label, var in (("Spirit", self.spirit_ml), ("Mixer", self.mixer_ml)):
            line = ttk.Frame(self.pour_row)
            line.pack(fill="x", pady=1)
            ttk.Label(line, text=label, width=6).pack(side="left")
            ttk.Entry(line, textvariable=var, width=6).pack(side="left")
            ttk.Label(line, text="ml").pack(side="left", padx=(2, 0))
            if label == "Spirit":  # quick picks for the spirit, e.g. shot 15 / double 30
                self.quick = ttk.Frame(self.pour_row)
                self.quick.pack(anchor="w", pady=(0, 2))

        # Qty over 1 spreads the drinks evenly from When across "over".
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.qty = tk.StringVar(value="1")
        self.over = tk.StringVar()
        self.when = tk.StringVar(value="now")
        ttk.Label(row, text="Qty").pack(side="left")
        ttk.Spinbox(row, from_=1, to=MAX_QTY, textvariable=self.qty, width=4).pack(side="left", padx=(4, 8))
        ttk.Label(row, text="over").pack(side="left")
        self.over_entry = ttk.Entry(row, textvariable=self.over, width=9)
        self.over_entry.pack(side="left", padx=4)
        self._over_typed = False  # once the user types a span, stop suggesting one
        self.over_entry.bind("<Key>", lambda _: setattr(self, "_over_typed", True))
        self.qty.trace_add("write", lambda *_: self._qty_changed())
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(2, 0))
        ttk.Label(row, text="When").pack(side="left")
        ttk.Entry(row, textvariable=self.when, width=16).pack(side="left", padx=4)
        ttk.Label(box, text="(the first one) e.g. now, 9pm, ~21:30, 1h30m ago", foreground=HINT).pack(anchor="w")
        self.food = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="With a meal (slower absorption)", variable=self.food).pack(anchor="w", pady=(4, 0))
        ttk.Button(box, text="Add drink", command=self.add_drink).pack(fill="x", pady=(6, 0))
        self._qty_changed()
        self._filter()

    def destroy(self):
        super().destroy()
        self._lock.release()

    def _build_estimate(self, parent):
        box = ttk.LabelFrame(parent, text="Estimate", padding=8)
        box.pack(fill="x")
        self.now_label = ttk.Label(box, font=("Segoe UI", 22, "bold"))
        self.now_label.pack(anchor="w")
        self.detail_label = ttk.Label(box, justify="left")
        self.detail_label.pack(anchor="w")
        self.chart = tk.Canvas(box, width=600, height=230, background="white", highlightthickness=0)
        self.chart.pack(anchor="w", pady=(8, 0))
        ttk.Label(box, text="Estimate only. Individual BAC varies widely; don't drive on these numbers.",
                  foreground="#a33").pack(anchor="w", pady=(6, 0))

    def _build_log(self, parent):
        box = ttk.LabelFrame(parent, text="Last 24 hours", padding=8)
        box.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("time", "drink", "qty", "grams")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=7)
        for col, text, width in zip(cols, ("When", "Drink", "Qty", "Alcohol"), (90, 360, 40, 70)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Remove selected", command=self.remove_selected).pack(side="left")
        ttk.Button(row, text="With/without a meal", command=self.toggle_meal).pack(side="left", padx=6)
        ttk.Button(row, text="Clear all", command=self.clear_log).pack(side="left")

    # --- actions ---

    def save_profile(self):
        try:
            weight = float(self.weight.get())
            age = float(self.age.get()) if self.age.get().strip() else None
            height = float(self.height.get()) if self.height.get().strip() else None
        except ValueError:
            messagebox.showerror("Profile", "Age, weight and height must be numbers.", parent=self)
            return
        problems = []
        if not bac.WEIGHT_KG[0] <= weight <= bac.WEIGHT_KG[1]:
            problems.append(f"Weight is in kg, between {bac.WEIGHT_KG[0]} and {bac.WEIGHT_KG[1]}.")
        if age is not None and not AGE[0] <= age <= AGE[1]:
            problems.append(f"Age must be between {AGE[0]} and {AGE[1]}.")
        if height is not None and not HEIGHT_CM[0] <= height <= HEIGHT_CM[1]:
            problems.append(f"Height is in cm, between {HEIGHT_CM[0]} and {HEIGHT_CM[1]}.")
        if problems:
            messagebox.showerror("Profile", "\n".join(problems), parent=self)
            return
        self.data["profile"] = {"sex": self.sex.get(), "age": age, "weight": weight, "height": height}
        self._save()

    def add_drink(self):
        d = self._selected()
        custom = CUSTOM_RE.fullmatch(self.search.get().strip())
        try:
            if d and d["pour"]:
                entry = {"name": d["name"], "abv": d["abv"], "unit": "poured",
                         "ml": amount(self.spirit_ml.get(), "Spirit", 500),
                         "mixer": amount(self.mixer_ml.get() or "0", "Mixer", 2000, least=0)}
            elif d and self.unit.get() == OTHER:
                entry = {"name": d["name"], "abv": d["abv"], "unit": "other",
                         "ml": amount(self.other_ml.get(), "Amount", 3000)}
            elif d:
                unit = d["units"][self.unit_pick.current()]
                entry = {"name": d["name"], "abv": d["abv"], "unit": unit["label"], "ml": unit["ml"]}
            elif custom:
                abv = bac.check_abv(float(custom[2]))
                entry = {"name": f"Custom {abv:g}%", "abv": abv, "ml": amount(custom[1], "Drink size", 3000)}
            else:
                messagebox.showinfo("Add a drink", "Pick a drink from the list, or type one like 500ml 6%.",
                                    parent=self)
                return
            entry["count"] = whole(self.qty.get())
            now = bac.now_local()
            when = bac.parse_approx_time(self.when.get(), now)
            if when < now - timedelta(hours=LOG_HOURS):
                raise ValueError(f"That's more than {LOG_HOURS} hours ago, so it would drop straight off the log. "
                                 "Drinks that long ago no longer change the estimate much.")
            if entry["count"] > 1:
                spread = bac.parse_duration(self.over.get())
                if spread is None or spread > timedelta(hours=LOG_HOURS):
                    raise ValueError(f"Over must be how long the {entry['count']} drinks took, "
                                     "e.g. 2h, 90 min or 1h30m (0 min if all at once).")
                if when + spread > now + timedelta(minutes=1):
                    raise ValueError(f"The last of those would be at {bac.fmt(when + spread)}, after now. "
                                     "Set When to when the first one was, or shorten Over.")
                entry["spread_minutes"] = spread / timedelta(minutes=1)
            entry["food"] = self.food.get()
            entry["time"] = when.isoformat()
        except ValueError as e:
            messagebox.showerror("Add a drink", str(e), parent=self)
            return
        self.data["log"].append(entry)
        self.data["log"].sort(key=entry_time)
        self._over_typed = False
        self.qty.set("1")
        self.when.set("now")
        self._save()

    def _qty_changed(self):
        """Over only applies to a qty above 1; suggest one drink per PACE_MINUTES until the user types."""
        try:
            count = int(self.qty.get())
        except ValueError:
            count = 1
        self.over_entry.configure(state="normal" if count > 1 else "disabled")
        if not self._over_typed:
            self.over.set(format_duration((count - 1) * PACE_MINUTES) if count > 1 else "")

    def toggle_meal(self):
        for iid in self.tree.selection():
            entry = self.data["log"][int(iid)]
            entry["food"] = not entry.get("food")
        self._save()

    def remove_selected(self):
        for iid in sorted(self.tree.selection(), key=int, reverse=True):
            del self.data["log"][int(iid)]
        self._save()

    def clear_log(self):
        if self.data["log"] and messagebox.askyesno("Clear all", "Remove every logged drink?", parent=self):
            self.data["log"] = []
            self._save()

    def toggle_favourite(self):
        d = self._selected()
        if not d:
            return
        favourites = self.data["favourites"]
        if d["name"] in favourites:
            favourites.remove(d["name"])
        else:
            favourites.append(d["name"])
        save_state(self.data, self.state_path)
        self._filter(keep=d["name"])

    def _filter(self, keep=None):
        """List the drinks matching the search, favourites pinned first; reselect `keep` if listed."""
        words = self.search.get().lower().split()
        favourites = set(self.data["favourites"])
        matches = [d for d in self.catalogue
                   if all(w in f"{d['name']} {d.get('type', '')}".lower() for w in words)]
        self.matches = sorted(matches, key=lambda d: d["name"] not in favourites)
        self.listbox.delete(0, "end")
        for i, d in enumerate(self.matches):
            star = "★ " if d["name"] in favourites else ""
            self.listbox.insert("end", f"{star}{d['name']}  ({d['abv']:g}%)")
            if d["name"] == keep:
                self.listbox.selection_set(i)
                self.listbox.see(i)
        self._show_serve()

    def _selected(self):
        selected = self.listbox.curselection()
        return self.matches[selected[0]] if selected else None

    def _show_serve(self):
        """Show the serve choices for the selected drink: its units, or spirit and mixer mL."""
        d = self._selected()
        favourite = d is not None and d["name"] in self.data["favourites"]
        self.fav_button.configure(text="★ Unpin favourite" if favourite else "☆ Pin as favourite",
                                  state="normal" if d else "disabled")
        self.unit_row.pack_forget()
        self.pour_row.pack_forget()
        if d and d["pour"]:
            for button in self.quick.winfo_children():
                button.destroy()
            for u in d["units"]:
                ttk.Button(self.quick, text=f"{u['label']} {u['ml']:g}", width=10,
                           command=lambda ml=u["ml"]: self.spirit_ml.set(f"{ml:g}")).pack(side="left", padx=1)
            self.spirit_ml.set(f"{d['units'][0]['ml']:g}")
            self.pour_row.pack(fill="x")
        else:
            units = d["units"] if d else []
            self.unit_pick["values"] = [unit_text(u) for u in units] + [OTHER]
            self.unit.set(unit_text(units[0]) if units else OTHER)
            self.unit_row.pack(fill="x")
            self._unit_changed()

    def _unit_changed(self):
        self.other_entry.configure(state="normal" if self.unit.get() == OTHER else "disabled")

    def _save(self):
        save_state(self.data, self.state_path)
        self.refresh()

    def _tick(self):
        self.refresh()
        self.after(60_000, self._tick)

    # --- display ---

    def refresh(self):
        now = bac.now_local()
        # The log is rebuilt every minute; keep whatever rows were selected for Remove.
        log = self.data["log"]
        selected = [log[int(iid)] for iid in self.tree.selection() if int(iid) < len(log)]
        self.data["log"] = recent(log, now)
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(self.data["log"]):
            self.tree.insert("", "end", iid=str(i), values=(
                bac.fmt(entry_time(e)), describe(e), e["count"], f"{entry_grams(e):.0f} g"))
            if any(e is s for s in selected):
                self.tree.selection_add(str(i))
        self.chart.delete("all")
        p = self.data["profile"]
        if not p.get("weight"):
            self.r_label["text"] = "Fill in at least sex and weight, then save."
            self.now_label.configure(text="--", foreground="")
            self.detail_label["text"] = "Set up your profile to get an estimate."
            return
        r, how = body_water(p)
        self.r_label["text"] = f"Body water factor r = {r:.2f} ({how})"
        if not self.data["log"]:
            self.now_label.configure(text="0.000%", foreground="")
            self.detail_label["text"] = "No drinks in the last 24 hours."
            return

        series, slow, fast = estimate(p, self.data["log"])
        current = bac.bac_at(series, now)
        colour = "#c0392b" if current >= 0.08 else "#d35400" if current >= 0.05 else ""
        self.now_label.configure(text=f"{current:.3f}% now", foreground=colour)
        grams = sum(entry_grams(e) for e in self.data["log"])
        peak_t, peak = max(series, key=lambda s: s[1])
        lines = [
            f"{grams:.0f} g alcohol ({grams / bac.STANDARD_DRINK_G:.1f} standard drinks)",
            f"Now, slow to fast elimination: {bac.bac_at(fast, now):.3f}-{bac.bac_at(slow, now):.3f}%",
            f"Peak {peak:.3f}% around {bac.fmt(peak_t)}",
        ]
        for th in (0.08, 0.05):
            t = bac.time_below(series, th)
            if t:
                lines.append(f"Below {th:.2f}%: {bac.fmt(t)}")
        lines.append(f"Zero: {bac.fmt(series[-1][0])}  (fast {bac.fmt(fast[-1][0])}, slow {bac.fmt(slow[-1][0])})")
        self.detail_label["text"] = "\n".join(lines)
        self.draw_chart(series, now)

    def draw_chart(self, series, now):
        c = self.chart
        w, h = int(c["width"]), int(c["height"])
        left, right, top, bottom = 44, w - 12, 12, h - 22
        t0, t1 = series[0][0], max(series[-1][0], now)
        span = (t1 - t0).total_seconds() or 1
        ymax = max(0.1, max(b for _, b in series) * 1.15)

        def x(t):
            return left + (t - t0).total_seconds() / span * (right - left)

        def y(b):
            return bottom - b / ymax * (bottom - top)

        small = ("Segoe UI", 8)
        c.create_line(left, bottom, right, bottom, fill="#999")
        c.create_line(left, top, left, bottom, fill="#999")
        c.create_text(left - 4, bottom, text="0", anchor="e", font=small, fill=HINT)
        for th, colour in ((0.08, "#c0392b"), (0.05, "#d35400")):
            if th < ymax:
                c.create_line(left, y(th), right, y(th), fill=colour, dash=(4, 3))
                c.create_text(left - 4, y(th), text=f"{th:.2f}", anchor="e", font=small, fill=colour)
        step = timedelta(hours=1 if span <= 12 * 3600 else 2)
        tick = t0.replace(minute=0) + timedelta(hours=1)
        while tick < t1:
            c.create_line(x(tick), bottom, x(tick), bottom + 4, fill="#999")
            c.create_text(x(tick), bottom + 6, text=tick.astimezone().strftime("%H:%M"), anchor="n", font=small, fill=HINT)
            tick += step
        points = series[::5] + [series[-1]]
        c.create_line(*[v for t, b in points for v in (x(t), y(b))], fill="#2c6fbb", width=2)
        if t0 <= now <= t1:
            c.create_line(x(now), top, x(now), bottom, fill="#333", dash=(2, 2))
            c.create_text(x(now) + 3, top, text="now", anchor="nw", font=small)


def fmt_num(value):
    return "" if value is None else f"{value:g}"


def unit_text(unit):
    return f"{unit['label']} ({unit['ml']:g} ml)"


def describe(entry):
    """How a logged drink reads in the log, e.g. 'Heineken, pint (568 ml) over 2h, with a meal'."""
    if entry.get("unit") == "poured":
        mixer = f" + {entry['mixer']:g} ml mixer" if entry.get("mixer") else ""
        text = f"{entry['name']}, {entry['ml']:g} ml{mixer}"
    elif entry.get("unit"):
        text = f"{entry['name']}, {entry['unit']} ({entry['ml']:g} ml)"
    else:
        text = f"{entry['name']} ({entry['ml']:g} ml)"
    if entry.get("count", 1) > 1 and entry.get("spread_minutes"):
        text += f" over {format_duration(entry['spread_minutes'])}"
    return text + (", with a meal" if entry.get("food") else "")


def format_duration(minutes):
    """150 -> '2h30m', 60 -> '1h', 45 -> '45 min' (parse_duration reads all of them back)."""
    hours, mins = divmod(round(minutes), 60)
    return f"{hours}h{mins}m" if hours and mins else f"{hours}h" if hours else f"{mins} min"


def amount(text, what, most, least=0.1):
    """Parse a mL amount from a form field, or raise ValueError with a message for the user."""
    try:
        value = float(text)
    except ValueError:
        value = None
    if value is None or not least <= value <= most:
        raise ValueError(f"{what} must be an amount in ml, up to {most}.")
    return value


def whole(text):
    try:
        count = int(text)
    except ValueError:
        count = 0
    if not 1 <= count <= MAX_QTY:
        raise ValueError(f"Qty must be a whole number from 1 to {MAX_QTY}.")
    return count


def main():
    try:
        window = App()
    except AlreadyOpen as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("boozer-calc", str(e), parent=root)
        root.destroy()
        return 1
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
