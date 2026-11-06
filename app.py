"""Desktop app: save your profile, log drinks as you go, watch your estimated BAC, and plan
a session on its own tab.

Your profile, the last 24 hours of drinks and your plan are kept in ~/.boozer-calc/state.json.
Estimate only: never use it to decide whether you're fit to drive.
"""
import json
import math
import os
import re
import tkinter as tk
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import bac
import build_drinks
import copstop
import publish

CATALOGUE = build_drinks.CATALOGUE
STATE = Path.home() / ".boozer-calc" / "state.json"
LOG_HOURS = 24  # drinks older than this drop off the log
PLAN_DAYS = 7  # how far ahead a session can be planned
MAX_QTY = 24
PACE_MINUTES = 30  # default gap between drinks when a qty of them is logged at once
AGE = (18, 120)
HEIGHT_CM = (100, 250)
CUSTOM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*ml\s+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
HINT = "#666"
EDITED = "#b8860b"  # dark yellow: a drink added or edited here and not yet committed
GREEN, ORANGE, RED = "#2e7d32", "#d35400", "#c0392b"
OTHER = "other amount"
SEARCH_HINT = "Search, or type a custom drink like 500ml 6%"
NOT_LISTED_HINT = "Not listed? Add to list… saves it for next time"
DEFAULT_SERVE_TYPES = ("beer", "cider")  # types whose serve can be set as the one picked first
TYPES = {"All": "", "Beer": "beer", "Cider": "cider", "Wine": "wine", "RTDs": "rtd", "Spirits": "spirit"}


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
    return {"profile": data.get("profile", {}), "log": log, "plan": data.get("plan", []),
            "favourites": data.get("favourites", []), "cop_stop": data.get("cop_stop", True),
            "default_serves": data.get("default_serves", {}), "default_pour": data.get("default_pour")}


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


def legal_to_drive(series, slow, age, now=None):
    """(time, text, colour): when BAC is back under the NZ limit for your age at the typical
    elimination rate, and how to say it, giving the slow rate's later time too.

    Each time comes with the wait until it: from now, or for a plan (now is None, as it
    hasn't happened yet) from the first drink. time is None if never over the limit.
    """
    limit = copstop.legal_limit(age)
    what = "zero, the limit under 20" if not limit else (
        f"the {limit:.2f}% adult limit" + (" (assuming you're 20 or over)" if age is None else ""))
    t, t_slow = bac.time_below(series, limit), bac.time_below(slow, limit)
    when = "now" if now is not None else "the whole time"

    def at(time):
        # Rounded up, so the wait never ends before the clock time shown.
        wait = format_duration(math.ceil((time - (now or series[0][0])) / bac.MINUTE))
        return f"{bac.fmt(time)} ({'in ' + wait if now else wait + ' after your first drink'})"

    if t_slow is None or (now is not None and t_slow <= now):
        return t, f"Legal to drive {when}: under {what}", GREEN
    if t is None or (now is not None and t <= now):
        return t, (f"Probably legal to drive {when}, under {what}, but not until "
                   f"{at(t_slow)} if you clear alcohol slowly"), ORANGE
    return t, f"Legal to drive again from about {at(t)}, or {at(t_slow)} if you clear alcohol slowly", RED


class App(tk.Tk):
    """The window: your profile, then a tab for the drinks you're having and a tab for a plan.

    The two tabs are separate sessions: each has its own drinks (the "log" and "plan" lists
    in the state) and its own estimate, so a plan never adds to tonight's BAC or vice versa.
    """

    def __init__(self, state_path=STATE, catalogue=None, catalogue_path=CATALOGUE, sources=build_drinks.SOURCES):
        """catalogue overrides the drinks read from catalogue_path (tests use it); Set ABV writes
        to sources (the ABV table) and rebuilds catalogue_path."""
        lock = StateLock(state_path)  # raises AlreadyOpen before any window appears
        super().__init__()
        self._lock = lock
        self.catalogue_path = catalogue_path
        self.sources = sources
        self.title("boozer-calc")
        self.state_path = state_path
        try:
            self.data = load_state(state_path)
        except StateError as e:
            self.data = load_state(state_path)
            warning = str(e)
            self.after(200, lambda: messagebox.showwarning("Saved data", warning, parent=self))
        if catalogue is None:
            self.reload_catalogue()
        else:
            self.catalogue, self.edited = catalogue, set()
        self.cop_on = tk.BooleanVar(value=self.data["cop_stop"])
        self._build_profile()
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.current = Session(self.tabs, self, "log")
        self.plan = Session(self.tabs, self, "plan")
        self.tabs.add(self.current, text="Current session")
        self.tabs.add(self.plan, text="Plan a session")
        self.sessions = (self.current, self.plan)
        self._tick()

    def _build_profile(self):
        box = ttk.LabelFrame(self, text="Profile", padding=8)
        box.pack(fill="x", padx=8, pady=8)
        p = self.data["profile"]
        self.sex = tk.StringVar(value=p.get("sex", "male"))
        self.age = tk.StringVar(value=fmt_num(p.get("age")))
        self.weight = tk.StringVar(value=fmt_num(p.get("weight")))
        self.height = tk.StringVar(value=fmt_num(p.get("height")))
        rows = [
            ("Sex", ttk.Combobox(box, textvariable=self.sex, values=list(bac.WIDMARK_R), state="readonly", width=8)),
            ("Age", ttk.Entry(box, textvariable=self.age, width=6)),
            ("Weight (kg)", ttk.Entry(box, textvariable=self.weight, width=6)),
            ("Height (cm)", ttk.Entry(box, textvariable=self.height, width=6)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=0, column=2 * i, sticky="w", padx=(12 if i else 0, 4))
            widget.grid(row=0, column=2 * i + 1, sticky="w")
        ttk.Button(box, text="Save profile", command=self.save_profile).grid(row=0, column=2 * len(rows), padx=(12, 0))
        self.r_label = ttk.Label(box, foreground=HINT)
        self.r_label.grid(row=1, column=0, columnspan=2 * len(rows) + 1, sticky="w", pady=(4, 0))

    def destroy(self):
        super().destroy()
        self._lock.release()

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
        self.save()

    def reload_catalogue(self):
        """Read the drink list again, and which drinks were added or edited since the last commit."""
        self.catalogue = load_catalogue(self.catalogue_path)
        self.edited = publish.edited_drinks(sources=self.sources)

    def refilter(self):
        """Re-list the drinks on every tab (after a favourite or ABV change), keeping each selection."""
        for s in self.sessions:
            selected = s._selected()
            s._filter(keep=selected and selected["name"])

    def publish_list(self):
        """Push the drink list changes made here (Set ABV, Add to list, CSV edits) to today's
        chore/data- branch and open or update its PR, after showing what will go."""
        title = "Publish list changes"
        try:
            changes = publish.local_changes()
            if not changes:
                messagebox.showinfo(title, "Nothing to publish: the drink list hasn't changed since the "
                                    "last commit.", parent=self)
                return
            stale = publish.stale_prs()
        except publish.PublishError as e:
            messagebox.showerror(title, str(e), parent=self)
            return
        lines = "\n".join(f"• {line}" for line in publish.describe_all(changes))
        text = (f"Push these to {publish.branch_for(date.today())} on GitHub and open or update "
                f"its PR into main?\n\n{lines}")
        if stale:
            days = ", ".join(f"#{p['number']} ({p['headRefName'][len(publish.BRANCH_PREFIX):]})" for p in stale)
            text += (f"\n\nStill open from an earlier day: {days}. It didn't merge itself, so its "
                     "secret scan may have failed: check it, or its changes go into today's PR too.")
        if not messagebox.askyesno(title, text, parent=self):
            return
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            url, pushed = publish.publish(changes)
        except publish.PublishError as e:
            messagebox.showerror(title, str(e), parent=self)
            return
        finally:
            self.configure(cursor="")
        done = "Pushed. The PR is" if pushed else "Nothing new since the last publish. The PR is"
        if messagebox.askyesno(title, f"{done} {url}\n\nIt merges into main by itself once its secret scan passes. Open it?", parent=self):
            webbrowser.open(url)

    def _cop_changed(self):
        self.data["cop_stop"] = self.cop_on.get()
        self.save()

    def save(self):
        save_state(self.data, self.state_path)
        self.refresh()

    def _tick(self):
        self.refresh()
        self.after(60_000, self._tick)

    def refresh(self):
        p = self.data["profile"]
        if p.get("weight"):
            r, how = body_water(p)
            self.r_label["text"] = f"Body water factor r = {r:.2f} ({how})"
        else:
            self.r_label["text"] = "Fill in at least sex and weight, then save."
        for s in self.sessions:
            s.refresh()


class Session(ttk.Frame):
    """One tab: a form to add drinks, the estimate and chart, and the session's drinks.

    key is the state list it keeps its drinks in: "log" for drinks you've had (times in the
    past), "plan" for drinks you mean to have (times usually in the future).
    """

    def __init__(self, parent, app, key):
        super().__init__(parent, padding=8)
        self.app = app
        self.key = key
        self.planning = key == "plan"
        self.editing = None  # the entry the form is editing, or None when it adds a new one
        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="ns")
        right = ttk.Frame(self, padding=(8, 0, 0, 0))
        right.grid(row=0, column=1, sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_add(left)
        self._build_estimate(right)
        self._build_log(right)

    @property
    def entries(self):
        return self.app.data[self.key]

    # --- layout ---

    def _build_add(self, parent):
        self.add_box = ttk.LabelFrame(parent, text=self._add_title(), padding=8)
        box = self.add_box
        box.pack(fill="both", expand=True)
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._filter())
        ttk.Entry(box, textvariable=self.search).pack(fill="x")
        self.hint = ttk.Label(box, foreground=HINT)
        self.hint.pack(anchor="w")
        self.kind = tk.StringVar(value="")  # drink type to list, "" for all of them
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(4, 0))
        for label, kind in TYPES.items():
            ttk.Radiobutton(row, text=label, value=kind, variable=self.kind, style="Toolbutton",
                            command=self._filter).pack(side="left", expand=True, fill="x")
        self.listbox = tk.Listbox(box, height=12, width=44, exportselection=False)
        self.listbox.pack(fill="both", expand=True, pady=4)
        self.listbox.bind("<<ListboxSelect>>", lambda _: self._show_serve())
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(0, 4))
        self.fav_button = ttk.Button(row, command=self.toggle_favourite)
        self.fav_button.pack(side="left")
        self.abv_button = ttk.Button(row, text="Set ABV…", command=self.set_abv)
        self.abv_button.pack(side="left", padx=6)
        ttk.Button(row, text="Add to list…", command=self.new_drink).pack(side="left")
        ttk.Button(box, text="Publish list changes…", command=self.app.publish_list).pack(anchor="w", pady=(0, 4))

        # How it was served: one of the drink's units, or for spirits the mL poured plus mixer.
        # A beer or cider starts on the serve saved as the default for its type.
        serve = ttk.Frame(box)
        serve.pack(fill="x", pady=(0, 4))
        self.unit_row = ttk.Frame(serve)
        line = ttk.Frame(self.unit_row)
        line.pack(fill="x")
        self.unit = tk.StringVar()
        self.other_ml = tk.StringVar()
        ttk.Label(line, text="Serve").pack(side="left")
        self.unit_pick = ttk.Combobox(line, textvariable=self.unit, state="readonly", width=24)
        self.unit_pick.pack(side="left", padx=(4, 8))
        self.unit_pick.bind("<<ComboboxSelected>>", lambda _: self._unit_changed())
        self.other_entry = ttk.Entry(line, textvariable=self.other_ml, width=6)
        self.other_entry.pack(side="left")
        ttk.Label(line, text="ml").pack(side="left", padx=(2, 0))
        self.default_button = ttk.Button(self.unit_row, command=self.toggle_default)
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
            var.trace_add("write", lambda *_: self._pour_changed())
        # Any spirit starts on the pour (spirit and mixer mL) saved as the default.
        self.pour_button = ttk.Button(self.pour_row, command=self.toggle_default_pour)
        self.pour_button.pack(anchor="w", pady=(2, 0))

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
        examples = "9pm, sat 8pm, tomorrow 6pm, in 2h" if self.planning else "now, 9pm, ~21:30, 1h30m ago"
        ttk.Label(box, text=f"(the first one) e.g. {examples}", foreground=HINT).pack(anchor="w")
        self.food = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="With a meal (slower absorption)", variable=self.food).pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        self.add_button = ttk.Button(row, text="Add drink", command=self.add_drink)
        self.add_button.pack(side="left", fill="x", expand=True)
        self.cancel_button = ttk.Button(row, text="Cancel edit", command=self.cancel_edit)
        self._qty_changed()
        self._filter()

    def _add_title(self):
        return "Edit a drink" if self.editing else "Add a planned drink" if self.planning else "Add a drink"

    def _build_estimate(self, parent):
        box = ttk.LabelFrame(parent, text="Plan estimate" if self.planning else "Estimate", padding=8)
        box.pack(fill="x")
        self.now_label = ttk.Label(box, font=("Segoe UI", 22, "bold"))
        self.now_label.pack(anchor="w")
        self.legal_label = ttk.Label(box, font=("Segoe UI", 11, "bold"), wraplength=590, justify="left")
        self.legal_label.pack(anchor="w", pady=(0, 4))
        self.detail_label = ttk.Label(box, justify="left")
        self.detail_label.pack(anchor="w")

        # Cop stop: what NZ law would do if you were pulled over driving at the current
        # BAC, or for a plan at its peak.
        cop = ttk.Frame(box)
        cop.pack(fill="x", pady=(8, 0))
        moment = "at your peak" if self.planning else "now"
        ttk.Checkbutton(cop, text=f"Cop stop: what if you were pulled over driving {moment}?",
                        variable=self.app.cop_on, command=self.app._cop_changed).pack(anchor="w")
        self.cop_headline = ttk.Label(cop, font=("Segoe UI", 10, "bold"), wraplength=590, justify="left")
        self.cop_details = ttk.Label(cop, wraplength=590, justify="left")
        self.chart = tk.Canvas(box, width=600, height=230, background="white", highlightthickness=0)
        self.chart.pack(anchor="w", pady=(8, 0))
        ttk.Label(box, text="Estimate only. Individual BAC varies widely; don't drive on these numbers.",
                  foreground="#a33").pack(anchor="w", pady=(6, 0))

    def _build_log(self, parent):
        box = ttk.LabelFrame(parent, text="Planned drinks" if self.planning else "Last 24 hours", padding=8)
        box.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("time", "drink", "qty", "grams")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=7)
        for col, text, width in zip(cols, ("When", "Drink", "Qty", "Alcohol"), (90, 360, 40, 70)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Edit selected", command=self.edit_selected).pack(side="left")
        ttk.Button(row, text="Remove selected", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(row, text="With/without a meal", command=self.toggle_meal).pack(side="left")
        ttk.Button(row, text="Clear all", command=self.clear_log).pack(side="left", padx=6)

    # --- actions ---

    def add_drink(self):
        """Add the drink in the form, or save it over the one being edited."""
        d = self._selected()
        custom = CUSTOM_RE.fullmatch(self.search.get().strip())
        abv = d["abv"] if d else None
        if d and self.editing and d["name"] == self.editing["name"]:
            abv = self.editing["abv"]  # an edited drink keeps the ABV it was logged with
        if d and abv is None:
            messagebox.showinfo("Add a drink", f"{d['name']} has no ABV yet. Use Set ABV… to enter the "
                                "one on its label, then add it.", parent=self.app)
            return
        try:
            if d and d["pour"]:
                entry = {"name": d["name"], "abv": abv, "unit": "poured",
                         "ml": amount(self.spirit_ml.get(), "Spirit", 500),
                         "mixer": amount(self.mixer_ml.get() or "0", "Mixer", 2000, least=0)}
            elif d and self.unit.get() == OTHER:
                entry = {"name": d["name"], "abv": abv, "unit": "other",
                         "ml": amount(self.other_ml.get(), "Amount", 3000)}
            elif d:
                unit = d["units"][self.unit_pick.current()]
                entry = {"name": d["name"], "abv": abv, "unit": unit["label"], "ml": unit["ml"]}
            elif custom:
                abv = bac.check_abv(float(custom[2]))
                entry = {"name": f"Custom {abv:g}%", "abv": abv, "ml": amount(custom[1], "Drink size", 3000)}
            else:
                messagebox.showinfo("Add a drink", "Pick a drink from the list, or type one like 500ml 6%.",
                                    parent=self.app)
                return
            entry["count"] = whole(self.qty.get())
            now = bac.now_local()
            if self.planning:
                when = bac.parse_future_time(self.when.get(), now)
                if when > now + timedelta(days=PLAN_DAYS):
                    raise ValueError(f"That's more than {PLAN_DAYS} days away. Plan up to a week ahead.")
            else:
                when = bac.parse_approx_time(self.when.get(), now)
            if when < now - timedelta(hours=LOG_HOURS):
                raise ValueError(f"That's more than {LOG_HOURS} hours ago, so it would drop straight off the log. "
                                 "Drinks that long ago no longer change the estimate much.")
            if entry["count"] > 1:
                spread = bac.parse_duration(self.over.get())
                if spread is None or spread > timedelta(hours=LOG_HOURS):
                    raise ValueError(f"Over must be how long the {entry['count']} drinks took, "
                                     "e.g. 2h, 90 min or 1h30m (0 min if all at once).")
                if not self.planning and when + spread > now + timedelta(minutes=1):
                    raise ValueError(f"The last of those would be at {bac.fmt(when + spread)}, after now. "
                                     "Set When to when the first one was, or shorten Over.")
                entry["spread_minutes"] = spread / timedelta(minutes=1)
            entry["food"] = self.food.get()
            entry["time"] = when.isoformat()
        except ValueError as e:
            messagebox.showerror("Edit a drink" if self.editing else "Add a drink", str(e), parent=self.app)
            return
        at = next((i for i, e in enumerate(self.entries) if e is self.editing), None)
        if at is None:
            self.entries.append(entry)
        else:
            self.entries[at] = entry
        self.entries.sort(key=entry_time)
        self._reset_form()
        self.app.save()

    def edit_selected(self):
        """Load the selected drink into the form, where Save changes puts it back changed."""
        selected = self.tree.selection()
        if len(selected) != 1:
            messagebox.showinfo("Edit a drink", "Select one drink to edit.", parent=self.app)
            return
        e = self.editing = self.entries[int(selected[0])]
        known = any(d["name"] == e["name"] for d in self.app.catalogue)
        self.search.set(e["name"] if known else f"{e['ml']:g}ml {e['abv']:g}%")
        self._filter(keep=e["name"])
        d = self._selected()
        if d and d["pour"]:
            self.spirit_ml.set(f"{e['ml']:g}")
            self.mixer_ml.set(f"{e['mixer']:g}" if e.get("mixer") else "")
        elif d:
            labels = [(u["label"], u["ml"]) for u in d["units"]]
            if (e.get("unit"), e["ml"]) in labels:
                self.unit_pick.current(labels.index((e["unit"], e["ml"])))
            else:
                self.unit.set(OTHER)
                self.other_ml.set(f"{e['ml']:g}")
            self._unit_changed()
        self._over_typed = True
        self.qty.set(str(e["count"]))
        self.over.set(format_duration(e.get("spread_minutes", 0)) if e["count"] > 1 else "")
        t = entry_time(e)
        # Written so this tab's When reads it back: a plan's day, or the most recent HH:MM.
        self.when.set(f"{bac.DAYS[t.weekday()].title()} {t:%H:%M}" if self.planning else f"{t:%H:%M}")
        self.food.set(bool(e.get("food")))
        self.add_box["text"] = self._add_title()
        self.add_button["text"] = "Save changes"
        self.cancel_button.pack(side="left", padx=(6, 0))

    def cancel_edit(self):
        self._reset_form()

    def _reset_form(self):
        """Back to adding a new drink. A plan keeps its When, as the next drink is often near it."""
        self.editing = None
        self.add_box["text"] = self._add_title()
        self.add_button["text"] = "Add drink"
        self.cancel_button.pack_forget()
        self._over_typed = False
        self.qty.set("1")
        if not self.planning:
            self.when.set("now")

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
            entry = self.entries[int(iid)]
            entry["food"] = not entry.get("food")
        self.app.save()

    def remove_selected(self):
        for iid in sorted(self.tree.selection(), key=int, reverse=True):
            if self.entries[int(iid)] is self.editing:
                self._reset_form()
            del self.entries[int(iid)]
        self.app.save()

    def clear_log(self):
        what = "planned" if self.planning else "logged"
        if self.entries and messagebox.askyesno("Clear all", f"Remove every {what} drink?", parent=self.app):
            self.entries.clear()
            if self.editing:
                self._reset_form()
            self.app.save()

    def set_abv(self):
        """Ask for the selected drink's ABV and save it in the ABV table's manual column."""
        d = self._selected()
        if not d:
            return
        if d["abv"] is None:
            now = "no ABV yet" + (f" (old guess {d['guess']:g}%)" if d.get("guess") else "")
        else:
            now = f"{d['abv']:g}% ({d['source']})"
        prompt = f"{d['name']}: {now}.\n\nABV % from the label:"
        if d["source"] == "manual":
            prompt += "\n(Leave blank to go back to the retailers' and brewery's values.)"
        text = simpledialog.askstring("Set ABV", prompt, parent=self.app)
        if text is None:
            return
        text = text.strip().rstrip("%").strip()
        if not text and d["source"] != "manual":
            return
        try:
            abv = bac.check_abv(float(text)) if text else None
            build_drinks.set_manual(d["name"], abv, self.app.sources, self.app.catalogue_path)
        except (ValueError, build_drinks.BuildError) as e:
            messagebox.showerror("Set ABV", str(e) if text else f"Couldn't clear it: {e}", parent=self.app)
            return
        self.app.reload_catalogue()
        self.app.refilter()
        new = self._selected()
        # Logged and planned drinks keep the ABV they were added with unless the user says
        # otherwise, and never lose it (clearing a manual value can leave the drink with no ABV).
        stale = [e for e in self.app.data["log"] + self.app.data["plan"]
                 if new and new["abv"] is not None and e["name"] == d["name"] and e["abv"] != new["abv"]]
        if stale and messagebox.askyesno(
                "Set ABV", f"Update the {len(stale)} {d['name']} already logged or planned to {new['abv']:g}% too?",
                parent=self.app):
            for e in stale:
                e["abv"] = new["abv"]
            self.app.save()

    def new_drink(self):
        """Add a drink that isn't listed to the drink list for good, starting from the search.

        It goes into the ABV table as a new row, like Set ABV, so it shows up in git to commit.
        """
        search = " ".join(self.search.get().split())
        kind = self.kind.get() or "beer"
        values = {"name": "" if CUSTOM_RE.fullmatch(search) else search, "type": kind,
                  "abv": "", "units": build_drinks.DEFAULT_UNITS[kind]}
        while True:  # on a mistake, ask again with what was typed
            values = ask_new_drink(self.app, values)
            if values is None:
                return
            try:
                name = build_drinks.add_drink(values["name"], values["type"], optional_abv(values["abv"]),
                                              values["units"], self.app.sources, self.app.catalogue_path)
                break
            except (ValueError, build_drinks.BuildError) as e:
                messagebox.showerror("Add to list", str(e), parent=self.app)
        self.app.reload_catalogue()
        self.app.refilter()
        if self.kind.get() not in ("", values["type"]):
            self.kind.set("")
        self.search.set(name)
        self._filter(keep=name)

    def toggle_favourite(self):
        d = self._selected()
        if not d:
            return
        favourites = self.app.data["favourites"]
        if d["name"] in favourites:
            favourites.remove(d["name"])
        else:
            favourites.append(d["name"])
        save_state(self.app.data, self.app.state_path)
        self.app.refilter()

    def toggle_default(self):
        """Make the chosen serve the default for this drink's type, or unset it if it already is."""
        d, unit = self._selected(), self._picked_unit()
        if not (d and unit):
            return
        defaults = self.app.data["default_serves"]
        if defaults.get(d["type"]) == unit["ml"]:
            del defaults[d["type"]]
        else:
            defaults[d["type"]] = unit["ml"]
        save_state(self.app.data, self.app.state_path)
        for s in self.app.sessions:
            s._unit_changed()

    def toggle_default_pour(self):
        """Make the spirit and mixer mL in the form the default pour, or unset it if they already are."""
        pour = self._typed_pour()
        if pour is None:
            return
        self.app.data["default_pour"] = None if pour == self.app.data["default_pour"] else pour
        save_state(self.app.data, self.app.state_path)
        for s in self.app.sessions:
            s._pour_changed()

    def _typed_pour(self):
        """The spirit and mixer mL typed in the form as {"ml", "mixer"}, or None if not valid amounts."""
        try:
            return {"ml": amount(self.spirit_ml.get(), "Spirit", 500),
                    "mixer": amount(self.mixer_ml.get() or "0", "Mixer", 2000, least=0)}
        except ValueError:
            return None

    def _pour_changed(self):
        pour = self._typed_pour()
        is_default = pour is not None and pour == self.app.data["default_pour"]
        self.pour_button.configure(
            text="★ Default pour (click to unset)" if is_default else "☆ Make this my default pour",
            state="normal" if pour else "disabled")

    def _filter(self, keep=None):
        """List the drinks of the chosen type matching the search, favourites pinned first;
        reselect `keep` if listed.

        The serve choices reset only if that changes which drink is selected, so re-listing
        (after a favourite or ABV change) doesn't undo a half-filled form.
        """
        before = self._selected()
        words = self.search.get().lower().split()
        favourites = set(self.app.data["favourites"])
        kind = self.kind.get()
        matches = [d for d in self.app.catalogue
                   if kind in ("", d.get("type"))
                   and all(w in f"{d['name']} {d.get('type', '')}".lower() for w in words)]
        self.matches = sorted(matches, key=lambda d: d["name"] not in favourites)
        search = self.search.get().strip()
        not_listed = search and not matches and not CUSTOM_RE.fullmatch(search)
        self.hint["text"] = NOT_LISTED_HINT if not_listed else SEARCH_HINT
        self.listbox.delete(0, "end")
        for i, d in enumerate(self.matches):
            star = "★ " if d["name"] in favourites else ""
            abv = "no ABV yet" if d["abv"] is None else f"{d['abv']:g}%"
            self.listbox.insert("end", f"{star}{d['name']}  ({abv})")
            if d["name"] in self.app.edited:
                self.listbox.itemconfigure(i, foreground=EDITED)
            elif d["abv"] is None:
                self.listbox.itemconfigure(i, foreground=HINT)
            if d["name"] == keep:
                self.listbox.selection_set(i)
                self.listbox.see(i)
        after = self._selected()
        if before and after and before["name"] == after["name"]:
            self._show_buttons()
        else:
            self._show_serve()

    def _selected(self):
        selected = self.listbox.curselection()
        return self.matches[selected[0]] if selected else None

    def _show_buttons(self):
        d = self._selected()
        favourite = d is not None and d["name"] in self.app.data["favourites"]
        self.fav_button.configure(text="★ Unpin favourite" if favourite else "☆ Pin as favourite",
                                  state="normal" if d else "disabled")
        self.abv_button.configure(state="normal" if d else "disabled")

    def _show_serve(self):
        """Show the serve choices for the selected drink: its units, or spirit and mixer mL."""
        self._show_buttons()
        d = self._selected()
        self.unit_row.pack_forget()
        self.pour_row.pack_forget()
        if d and d["pour"]:
            for button in self.quick.winfo_children():
                button.destroy()
            for u in d["units"]:
                ttk.Button(self.quick, text=f"{u['label']} {u['ml']:g}", width=10,
                           command=lambda ml=u["ml"]: self.spirit_ml.set(f"{ml:g}")).pack(side="left", padx=1)
            pour = self.app.data["default_pour"]
            if pour:
                self.spirit_ml.set(f"{pour['ml']:g}")
                self.mixer_ml.set(f"{pour['mixer']:g}" if pour["mixer"] else "")
            else:
                self.spirit_ml.set(f"{d['units'][0]['ml']:g}")
            self.pour_row.pack(fill="x")
        else:
            units = d["units"] if d else []
            default = self.app.data["default_serves"].get(d["type"]) if d else None
            first = next((u for u in units if u["ml"] == default), units[0] if units else None)
            self.unit_pick["values"] = [unit_text(u) for u in units] + [OTHER]
            self.unit.set(unit_text(first) if first else OTHER)
            self.unit_row.pack(fill="x")
            self._unit_changed()

    def _picked_unit(self):
        """The selected drink's unit chosen in Serve, or None for an other amount or no drink."""
        d = self._selected()
        i = self.unit_pick.current()
        return d["units"][i] if d and not d["pour"] and 0 <= i < len(d["units"]) else None

    def _unit_changed(self):
        self.other_entry.configure(state="normal" if self.unit.get() == OTHER else "disabled")
        d, unit = self._selected(), self._picked_unit()
        if not (d and d["type"] in DEFAULT_SERVE_TYPES):
            self.default_button.pack_forget()
            return
        is_default = unit is not None and self.app.data["default_serves"].get(d["type"]) == unit["ml"]
        self.default_button.configure(
            text=f"★ Default {d['type']} serve (click to unset)" if is_default else f"☆ Make this my default {d['type']} serve",
            state="normal" if unit else "disabled")
        self.default_button.pack(anchor="w", pady=(2, 0))

    # --- display ---

    def refresh(self):
        now = bac.now_local()
        # The list is rebuilt every minute; keep whatever rows were selected for Remove.
        log = self.entries
        selected = [log[int(iid)] for iid in self.tree.selection() if int(iid) < len(log)]
        self.app.data[self.key] = log = recent(log, now)
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(log):
            self.tree.insert("", "end", iid=str(i), values=(
                bac.fmt(entry_time(e)), describe(e), e["count"], f"{entry_grams(e):.0f} g"))
            if any(e is s for s in selected):
                self.tree.selection_add(str(i))
        self.chart.delete("all")
        self.legal_label["text"] = ""
        p = self.app.data["profile"]
        if not p.get("weight"):
            self.now_label.configure(text="--", foreground="")
            self.detail_label["text"] = "Set up your profile to get an estimate."
            self._show_cop(None)
            return
        if not log and self.planning:
            self.now_label.configure(text="--", foreground="")
            self.detail_label["text"] = "Add the drinks you plan to have, e.g. 2 pints from 9pm."
            self._show_cop(None)
            return
        if not log:
            self.now_label.configure(text="0.000%", foreground="")
            self.detail_label["text"] = "No drinks in the last 24 hours."
            self._show_cop(0.0)
            return

        series, slow, fast = estimate(p, log)
        current = bac.bac_at(series, now)
        grams = sum(entry_grams(e) for e in log)
        peak_t, peak = max(series, key=lambda s: s[1])
        shown = peak if self.planning else current  # what the headline and cop stop are about
        colour = RED if shown >= 0.08 else ORANGE if shown >= 0.05 else ""
        lines = [f"{grams:.0f} g alcohol ({grams / bac.STANDARD_DRINK_G:.1f} standard drinks)"]
        if self.planning:
            self.now_label.configure(text=f"Peak {peak:.3f}% around {bac.fmt(peak_t)}", foreground=colour)
            lines.append(f"Peak, fast to slow elimination: {max(b for _, b in fast):.3f}"
                         f"-{max(b for _, b in slow):.3f}%")
        else:
            self.now_label.configure(text=f"{current:.3f}% now", foreground=colour)
            lines += [f"Now, slow to fast elimination: {bac.bac_at(fast, now):.3f}-{bac.bac_at(slow, now):.3f}%",
                      f"Peak {peak:.3f}% around {bac.fmt(peak_t)}"]
        legal, text, legal_colour = legal_to_drive(series, slow, p.get("age"), None if self.planning else now)
        self.legal_label.configure(text=text, foreground=legal_colour)
        limit = copstop.legal_limit(p.get("age"))
        for th in (0.08, 0.05):
            t = bac.time_below(series, th)
            if t and th != limit:  # the legal limit is in the line above
                lines.append(f"Below {th:.2f}%: {bac.fmt(t)}")
        lines.append(f"Zero: {bac.fmt(series[-1][0])}  (fast {bac.fmt(fast[-1][0])}, slow {bac.fmt(slow[-1][0])})")
        self.detail_label["text"] = "\n".join(lines)
        self._show_cop(shown)
        self.draw_chart(series, now, legal)

    def _show_cop(self, bac_percent):
        """Show what a stop would mean at bac_percent, or hide it (toggle off, or no profile yet)."""
        self.cop_headline.pack_forget()
        self.cop_details.pack_forget()
        if bac_percent is None or not self.app.cop_on.get():
            return
        stop = copstop.cop_stop(bac_percent, self.app.data["profile"].get("age"))
        colour = {"clear": GREEN, "infringement": ORANGE, "court": RED}[stop.level]
        self.cop_headline.configure(text=stop.headline, foreground=colour)
        self.cop_details["text"] = "\n".join(
            stop.details + ["NZ law as of October 2026, for a first offence. Not legal advice."])
        self.cop_headline.pack(anchor="w", pady=(2, 0))
        self.cop_details.pack(anchor="w")

    def draw_chart(self, series, now, legal=None):
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
        for th, colour in ((0.08, RED), (0.05, ORANGE)):
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
        if legal and t0 <= legal <= t1:
            c.create_line(x(legal), top, x(legal), bottom, fill=GREEN, dash=(2, 2))
            c.create_text(x(legal) + 3, top + 12, text="legal to drive", anchor="nw", font=small, fill=GREEN)
        if t0 <= now <= t1:
            c.create_line(x(now), top, x(now), bottom, fill="#333", dash=(2, 2))
            c.create_text(x(now) + 3, top, text="now", anchor="nw", font=small)


class NewDrinkDialog(simpledialog.Dialog):
    """Asks for a drink to add to the list: its name, type, ABV and how it's served."""

    def __init__(self, parent, values):
        self.values = values
        super().__init__(parent, "Add to list")

    def body(self, master):
        self.vars = {k: tk.StringVar(value=self.values.get(k, "")) for k in ("name", "type", "abv", "units")}
        name = ttk.Entry(master, textvariable=self.vars["name"], width=40)
        kind = ttk.Combobox(master, textvariable=self.vars["type"], values=build_drinks.TYPE_ORDER,
                            state="readonly", width=10)
        kind.bind("<<ComboboxSelected>>", lambda _: self._type_changed())
        self.shown_type = self.vars["type"].get()
        rows = [("Name", name, None), ("Type", kind, None),
                ("ABV %", ttk.Entry(master, textvariable=self.vars["abv"], width=8),
                 "from the label; leave blank if you don't know it yet"),
                ("Serves", ttk.Entry(master, textvariable=self.vars["units"], width=40),
                 "each a name and ml, e.g. can 440; pint 568")]
        for i, (label, widget, hint) in enumerate(rows):
            ttk.Label(master, text=label).grid(row=2 * i, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
            widget.grid(row=2 * i, column=1, sticky="w", pady=(4, 0))
            if hint:
                ttk.Label(master, text=hint, foreground=HINT).grid(row=2 * i + 1, column=1, sticky="w")
        return name

    def _type_changed(self):
        """Swap in the new type's usual serves, unless they've been changed from the old type's."""
        if self.vars["units"].get().strip() == build_drinks.DEFAULT_UNITS.get(self.shown_type):
            self.vars["units"].set(build_drinks.DEFAULT_UNITS[self.vars["type"].get()])
        self.shown_type = self.vars["type"].get()

    def apply(self):
        self.result = {k: v.get() for k, v in self.vars.items()}


def ask_new_drink(parent, values):
    """The values entered in the Add to list box, starting from `values`; None if cancelled."""
    return NewDrinkDialog(parent, values).result


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


def optional_abv(text):
    """An ABV typed as '4.5' or '4.5%', or None if left blank."""
    text = text.strip().rstrip("%").strip()
    try:
        return float(text) if text else None
    except ValueError:
        raise ValueError(f"ABV {text!r} isn't a number, e.g. 4.5 (or leave it blank).") from None


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
