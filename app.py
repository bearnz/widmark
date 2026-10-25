"""Desktop app: save your profile, log drinks as you go, watch your estimated BAC.

Your profile and the last 24 hours of drinks are kept in ~/.boozer-calc/state.json.
Estimate only: never use it to decide whether you're fit to drive.
"""
import json
import re
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

import bac

CATALOGUE = Path(__file__).with_name("drinks.json")
STATE = Path.home() / ".boozer-calc" / "state.json"
LOG_HOURS = 24  # drinks older than this drop off the log
CUSTOM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*ml\s+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
HINT = "#666"


def load_catalogue(path=CATALOGUE):
    return json.loads(path.read_text(encoding="utf-8"))["drinks"]


def load_state(path=STATE):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {"profile": data.get("profile", {}), "log": data.get("log", []), "food": data.get("food", False)}


def save_state(data, path=STATE):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def recent(log, now):
    cutoff = now - timedelta(hours=LOG_HOURS)
    return [e for e in log if datetime.fromisoformat(e["time"]) >= cutoff]


def entry_grams(entry):
    return bac.alcohol_grams(entry["ml"], entry["abv"], entry["count"])


def profile_r(profile):
    if profile.get("age") and profile.get("height"):
        return bac.watson_r(profile["sex"], profile["age"], profile["height"], profile["weight"])
    return bac.WIDMARK_R[profile["sex"]]


def estimate(profile, log, food):
    """BAC series at typical, slow and fast elimination for the logged drinks."""
    drinks = [bac.Drink(datetime.fromisoformat(e["time"]), entry_grams(e)) for e in log]
    absorb = bac.ABSORB_MINUTES_FOOD if food else bac.ABSORB_MINUTES
    r = profile_r(profile)
    return [bac.simulate(drinks, profile["weight"], r, bac.ELIM_RATES[k], absorb)
            for k in ("typical", "slow", "fast")]


class App(tk.Tk):
    def __init__(self, state_path=STATE, catalogue=None):
        super().__init__()
        self.title("boozer-calc")
        self.state_path = state_path
        self.data = load_state(state_path)
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
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.qty = tk.StringVar(value="1")
        self.when = tk.StringVar(value="now")
        ttk.Label(row, text="Qty").pack(side="left")
        ttk.Spinbox(row, from_=1, to=12, textvariable=self.qty, width=4).pack(side="left", padx=(4, 12))
        ttk.Label(row, text="When").pack(side="left")
        ttk.Entry(row, textvariable=self.when, width=16).pack(side="left", padx=4)
        ttk.Label(box, text="e.g. now, 9pm, ~21:30, 45 min ago", foreground=HINT).pack(anchor="w")
        ttk.Button(box, text="Add drink", command=self.add_drink).pack(fill="x", pady=(6, 0))
        self.food = tk.BooleanVar(value=self.data["food"])
        ttk.Checkbutton(box, text="Had a meal (slower absorption)", variable=self.food,
                        command=self._food_changed).pack(anchor="w", pady=(6, 0))
        self._filter()

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
        for col, text, width in zip(cols, ("When", "Drink", "Qty", "Alcohol"), (90, 300, 40, 70)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Remove selected", command=self.remove_selected).pack(side="left")
        ttk.Button(row, text="Clear all", command=self.clear_log).pack(side="left", padx=6)

    # --- actions ---

    def save_profile(self):
        try:
            weight = float(self.weight.get())
            age = float(self.age.get()) if self.age.get().strip() else None
            height = float(self.height.get()) if self.height.get().strip() else None
        except ValueError:
            messagebox.showerror("Profile", "Age, weight and height must be numbers.", parent=self)
            return
        if not 30 <= weight <= 300:
            messagebox.showerror("Profile", "Weight is in kg, between 30 and 300.", parent=self)
            return
        if height is not None and not 100 <= height <= 250:
            messagebox.showerror("Profile", "Height is in cm, between 100 and 250.", parent=self)
            return
        self.data["profile"] = {"sex": self.sex.get(), "age": age, "weight": weight, "height": height}
        self._save()

    def add_drink(self):
        selected = self.listbox.curselection()
        custom = CUSTOM_RE.fullmatch(self.search.get().strip())
        if selected:
            d = self.matches[selected[0]]
            name, ml, abv = d["name"], d["ml"], d["abv"]
        elif custom:
            ml, abv = float(custom[1]), float(custom[2])
            name = f"Custom {ml:g} ml {abv:g}%"
        else:
            messagebox.showinfo("Add a drink", "Pick a drink from the list, or type one like 500ml 6%.", parent=self)
            return
        try:
            count = int(self.qty.get())
            if count < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Add a drink", "Qty must be a whole number, 1 or more.", parent=self)
            return
        try:
            when = bac.parse_approx_time(self.when.get(), datetime.now())
        except ValueError as e:
            messagebox.showerror("Add a drink", str(e), parent=self)
            return
        self.data["log"].append({"time": when.isoformat(), "name": name, "count": count, "ml": ml, "abv": abv})
        self.data["log"].sort(key=lambda e: e["time"])
        self.qty.set("1")
        self.when.set("now")
        self._save()

    def remove_selected(self):
        for iid in sorted(self.tree.selection(), key=int, reverse=True):
            del self.data["log"][int(iid)]
        self._save()

    def clear_log(self):
        if self.data["log"] and messagebox.askyesno("Clear all", "Remove every logged drink?", parent=self):
            self.data["log"] = []
            self._save()

    def _food_changed(self):
        self.data["food"] = self.food.get()
        self._save()

    def _filter(self):
        words = self.search.get().lower().split()
        self.matches = [d for d in self.catalogue if all(w in d["name"].lower() for w in words)]
        self.listbox.delete(0, "end")
        for d in self.matches:
            self.listbox.insert("end", f"{d['name']}  ({d['ml']:g} ml, {d['abv']:g}%)")

    def _save(self):
        save_state(self.data, self.state_path)
        self.refresh()

    def _tick(self):
        self.refresh()
        self.after(60_000, self._tick)

    # --- display ---

    def refresh(self):
        now = datetime.now()
        self.data["log"] = recent(self.data["log"], now)
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(self.data["log"]):
            self.tree.insert("", "end", iid=str(i), values=(
                bac.fmt(datetime.fromisoformat(e["time"])), e["name"], e["count"], f"{entry_grams(e):.0f} g"))
        self.chart.delete("all")
        p = self.data["profile"]
        if not p.get("weight"):
            self.r_label["text"] = "Fill in at least sex and weight, then save."
            self.now_label.configure(text="--", foreground="")
            self.detail_label["text"] = "Set up your profile to get an estimate."
            return
        how = ("Watson, from age and height" if p.get("age") and p.get("height")
               else "default for sex; add age and height for a closer fit")
        self.r_label["text"] = f"Body water factor r = {profile_r(p):.2f} ({how})"
        if not self.data["log"]:
            self.now_label.configure(text="0.000%", foreground="")
            self.detail_label["text"] = "No drinks in the last 24 hours."
            return

        series, slow, fast = estimate(p, self.data["log"], self.data["food"])
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
            c.create_text(x(tick), bottom + 6, text=tick.strftime("%H:%M"), anchor="n", font=small, fill=HINT)
            tick += step
        points = series[::5] + [series[-1]]
        c.create_line(*[v for t, b in points for v in (x(t), y(b))], fill="#2c6fbb", width=2)
        if t0 <= now <= t1:
            c.create_line(x(now), top, x(now), bottom, fill="#333", dash=(2, 2))
            c.create_text(x(now) + 3, top, text="now", anchor="nw", font=small)


def fmt_num(value):
    return "" if value is None else f"{value:g}"


if __name__ == "__main__":
    App().mainloop()
