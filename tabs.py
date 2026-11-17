"""Extra tabs of the editor window: the job search plan and the company list with process status.

Both read from docs/ (git-ignored, personal):
  docs/job-search-plan.md   shown formatted, editable as Markdown; reloaded when the file changes
  docs/employers.csv        one row per company; Status, Last contact and Notes are edited here and
                            saved straight back to the file
"""

from __future__ import annotations

import csv
import datetime
import os
import re
import shutil
import webbrowser
from collections import Counter
from pathlib import Path
from tkinter import messagebox, ttk
import tkinter as tk

DOCS = Path(__file__).resolve().parent / "docs"
PLAN = DOCS / "job-search-plan.md"
EMPLOYERS = DOCS / "employers.csv"

# Steps of the process, in order. Values in the CSV that are not listed here are kept as they are.
STATUSES = ["Not contacted", "Contacted", "Applied", "Interview", "Offer", "Rejected", "Not an option"]
# A change to one of these sets "Last contact" to today.
CONTACT_STATUSES = {"Contacted", "Applied", "Interview", "Offer", "Rejected"}
STATUS_COLORS = {
    "Contacted": "#1F5FA8",
    "Applied": "#6A3FA0",
    "Interview": "#B26A00",
    "Offer": "#2E7D32",
    "Rejected": "#8A94A3",
    "Not an option": "#8A94A3",
}
REQUIRED_COLUMNS = ["Company", "Status", "Last contact", "Notes"]

# Tk builds without Xft only have X11 core fonts, which cover Latin-1 (æøå are fine) but show
# characters like • – “ as garbage. Text that is only displayed goes through `shown()`.
_LATIN1 = str.maketrans({"•": "·", "–": "-", "—": "-", "‒": "-", "“": '"', "”": '"', "„": '"',
                         "‘": "'", "’": "'", "…": "...", "→": "->", "←": "<-", "≈": "~", "✓": "v"})


def shown(text: str) -> str:
    return text.translate(_LATIN1)


# --------------------------------------------------------------------------- employers.csv

def load_companies(path: Path = EMPLOYERS) -> tuple[list[str], list[dict[str, str]]]:
    """(column names, rows). Missing tracking columns are added (empty)."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    fields += [c for c in REQUIRED_COLUMNS if c not in fields]
    return fields, [{c: (r.get(c) or "") for c in fields} for r in rows]


def save_companies(fields: list[str], rows: list[dict[str, str]], path: Path = EMPLOYERS) -> None:
    """Write the CSV via a temporary file, so a crash never leaves half a file."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- markdown -> Text

_INLINE = re.compile(r"\*\*(?P<bold>.+?)\*\*|(?<!\\)\[(?P<label>[^\]]+?)\]\((?P<url>[^)\s]+)\)")


def _unescape(text: str) -> str:
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>])", r"\1", text)


class MarkdownView(tk.Text):
    """Read-only Text that shows the subset of Markdown used in the docs: headings, paragraphs,
    **bold**, [links](url), bullets, task boxes, block quotes and tables."""

    def __init__(self, parent, colors: dict[str, str], fonts: dict[str, tuple], **kw) -> None:
        family = fonts["body"][0]
        super().__init__(parent, wrap="word", relief="flat", borderwidth=0, highlightthickness=0,
                         padx=28, pady=20, background=colors["card"], foreground=colors["ink"],
                         font=(family, 11), cursor="arrow", spacing1=1, spacing3=1, **kw)
        self.colors = colors
        self.tag_configure("h1", font=(family, 20, "bold"), spacing1=4, spacing3=10)
        self.tag_configure("h2", font=(family, 15, "bold"), foreground=colors["accent"], spacing1=18, spacing3=6)
        self.tag_configure("h3", font=(family, 12, "bold"), spacing1=12, spacing3=4)
        self.tag_configure("bold", font=(family, 11, "bold"))
        self.tag_configure("muted", foreground=colors["muted"])
        self.tag_configure("bullet", lmargin1=14, lmargin2=34)
        self.tag_configure("quote", lmargin1=18, lmargin2=18, foreground="#3A4656",
                           background="#F4F6F9", rmargin=20)
        self.tag_configure("row", lmargin1=14, lmargin2=14, spacing1=8)
        self.tag_configure("cell", lmargin1=30, lmargin2=30)
        self.tag_configure("link", foreground=colors["accent"], underline=True)
        self.tag_bind("link", "<Enter>", lambda e: self.configure(cursor="hand2"))
        self.tag_bind("link", "<Leave>", lambda e: self.configure(cursor="arrow"))
        self._links = 0

    def show(self, markdown: str) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        lines = markdown.splitlines()
        gap = True                       # True right after a gap (blank line, heading or table)
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            i += 1
            if line.lstrip().startswith("|"):              # table: collect all its lines
                table = [line]
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    table.append(lines[i])
                    i += 1
                self._table(table)
                gap = True
                continue
            if not line.strip():
                if not gap:
                    self.insert("end", "\n")
                gap = True
                continue
            gap = False
            if m := re.match(r"(#{1,3}) (.*)", line):
                self._inline(m.group(2), (f"h{len(m.group(1))}",))
                gap = True                                  # headings have their own spacing
            elif line.startswith(">"):
                self._inline(line[1:].strip(), ("quote",))
                self.insert("end", "\n", ("quote",))
                continue
            elif m := re.match(r"\s*[-*] \[( |x|X)\] (.*)", line):
                self.insert("end", "[x]  " if m.group(1).strip() else "[  ]  ", ("bullet",))
                self._inline(m.group(2), ("bullet",))
            elif m := re.match(r"\s*[-*] (.*)", line):
                self.insert("end", "·  ", ("bullet",))
                self._inline(m.group(1), ("bullet",))
            else:
                self._inline(line, ())
            self.insert("end", "\n")
        self.configure(state="disabled")

    def insert(self, index, chars, *args):
        super().insert(index, shown(chars), *args)

    def _inline(self, text: str, tags: tuple[str, ...]) -> None:
        pos = 0
        for m in _INLINE.finditer(text):
            self.insert("end", _unescape(text[pos:m.start()]), tags)
            if m.group("bold") is not None:
                self._inline(m.group("bold"), tags + ("bold",))
            else:
                self._links += 1
                tag = f"link{self._links}"
                url = m.group("url")
                self.tag_bind(tag, "<Button-1>", lambda e, u=url: webbrowser.open(u))
                self.insert("end", _unescape(m.group("label")), tags + ("link", tag))
            pos = m.end()
        self.insert("end", _unescape(text[pos:]), tags)

    def _table(self, lines: list[str]) -> None:
        """Tables become one block per row: first cell in bold, the rest as 'Header: value' lines."""
        rows = [[c.strip() for c in re.split(r"(?<!\\)\|", ln.strip().strip("|"))] for ln in lines]
        rows = [r for r in rows if not all(re.fullmatch(r":?-+:?", c) for c in r)]
        if not rows:
            return
        header, body = rows[0], rows[1:]
        for r in body:
            self._inline(r[0], ("row", "bold"))
            self.insert("end", "\n")
            for h, c in zip(header[1:], r[1:]):
                if c:
                    self.insert("end", f"{_unescape(h)}:  ", ("cell", "muted"))
                    self._inline(c, ("cell",))
                    self.insert("end", "\n")
        self.insert("end", "\n")


# --------------------------------------------------------------------------- tabs

def _toolbar(parent, title: str, fonts) -> ttk.Frame:
    bar = ttk.Frame(parent, style="Card.TFrame", padding=(16, 12))
    bar.pack(fill="x")
    ttk.Label(bar, text=title, style="Title.Card.TLabel").pack(side="left")
    return bar


class PlanTab(ttk.Frame):
    """docs/job-search-plan.md. Shown formatted; "Edit" switches to the Markdown source, which "Save"
    writes back to the file (the previous version is kept as job-search-plan.md.bak).
    The view is reloaded whenever the tab is shown and the file has changed (not while editing)."""

    def __init__(self, parent, colors, fonts, path: Path = PLAN) -> None:
        super().__init__(parent, style="Card.TFrame")
        self.colors, self.path, self.mtime = colors, path, None
        self.editing = False
        self.edit_mtime = None                   # file's mtime when editing started
        bar = _toolbar(self, "Job search plan", fonts)
        self.hint = ttk.Label(bar, style="Help.Card.TLabel")
        self.hint.pack(side="left", padx=(12, 0))
        # view mode
        self.reload_btn = ttk.Button(bar, text="Reload", command=lambda: self.refresh(force=True))
        self.edit_btn = ttk.Button(bar, text="Edit", style="Accent.TButton", command=self.start_edit)
        # edit mode
        self.save_btn = ttk.Button(bar, text="Save", style="Accent.TButton", command=self.save)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.cancel_edit)
        self.saved = ttk.Label(bar, style="Status.Card.TLabel")
        self.saved.pack(side="right", padx=(0, 12))
        tk.Frame(self, height=1, background=colors["border"]).pack(fill="x")

        self.body = ttk.Frame(self, style="Card.TFrame")
        self.body.pack(fill="both", expand=True)
        self.view = MarkdownView(self.body, colors, fonts)
        self.editor = tk.Text(self.body, wrap="word", undo=True, autoseparators=True, font=(fonts["body"][0], 12),  # 11 drops "|"
                              relief="flat", borderwidth=0, highlightthickness=0, padx=28, pady=20,
                              background="#FFFFFF", foreground=colors["ink"], insertbackground=colors["ink"])
        self.editor.bind("<Control-s>", lambda e: (self.save(), "break")[1])
        self.editor.bind("<Escape>", lambda e: self.cancel_edit())
        self.scroll = ttk.Scrollbar(self.body, orient="vertical")
        self.scroll.pack(side="right", fill="y")
        self._show(self.view)
        self._buttons()
        self.refresh()

    def _show(self, widget: tk.Text) -> None:
        for w in (self.view, self.editor):
            w.pack_forget()
        widget.pack(side="left", fill="both", expand=True)
        widget.configure(yscrollcommand=self.scroll.set)
        self.scroll.configure(command=widget.yview)

    def _buttons(self) -> None:
        for b in (self.reload_btn, self.edit_btn, self.save_btn, self.cancel_btn):
            b.pack_forget()
        if self.editing:
            self.save_btn.pack(side="right")
            self.cancel_btn.pack(side="right", padx=(0, 6))
            self.hint.configure(text="Markdown: # heading, **bold**, - bullet, - [ ] task, > quote, | table |"
                                     "   ·   Ctrl+S saves")
        else:
            self.edit_btn.pack(side="right")
            self.reload_btn.pack(side="right", padx=(0, 6))
            self.hint.configure(text=f"docs/{self.path.name}")

    def refresh(self, force: bool = False) -> None:
        if self.editing:                         # never replace text that is being edited
            return
        if not self.path.exists():
            self.mtime = None
            self.view.show(f"# No plan yet\n\nClick Edit to write one; it is saved as `docs/{self.path.name}`.\n")
            return
        mtime = self.path.stat().st_mtime
        if force or mtime != self.mtime:
            self.mtime = mtime
            top = self.view.yview()[0]
            self.view.show(self.path.read_text(encoding="utf-8"))
            self.view.yview_moveto(top)

    # ---- editing
    def modified(self) -> bool:
        return self.editing and self.editor.edit_modified()

    def start_edit(self) -> None:
        text = self.path.read_text(encoding="utf-8") if self.path.exists() else "# Job search plan\n\n"
        self.edit_mtime = self.path.stat().st_mtime if self.path.exists() else None
        top = self.view.yview()[0]
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self.editing = True
        self.saved.configure(text="")
        self._show(self.editor)
        self._buttons()
        self.editor.yview_moveto(top)
        self.editor.mark_set("insert", self.editor.index("@0,0"))
        self.editor.focus_set()

    def _stop_edit(self) -> None:
        self.editing = False
        self._show(self.view)
        self._buttons()
        self.refresh(force=True)

    def cancel_edit(self) -> None:
        if self.modified() and not messagebox.askyesno(
                "Discard changes", "Discard the changes to the job search plan?", parent=self):
            return
        self._stop_edit()

    def save(self) -> bool:
        """Write the editor's text to the file and go back to the formatted view. False if not saved."""
        if not self.editing:
            return True
        now = self.path.stat().st_mtime if self.path.exists() else None
        if now != self.edit_mtime and not messagebox.askyesno(
                "File changed", f"docs/{self.path.name} was changed outside the editor after you started "
                                "editing.\n\nOverwrite it with your version?", parent=self):
            return False
        text = self.editor.get("1.0", "end-1c").rstrip() + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            self.saved.configure(text=f"Could not save: {e}", foreground=self.colors["error"])
            return False
        self.saved.configure(text=f"Saved {datetime.datetime.now():%H:%M}", foreground=self.colors["muted"])
        self._stop_edit()
        return True

    def confirm_close(self) -> bool:
        """Before the window closes: offer to save unsaved edits. False means don't close."""
        if not self.modified():
            return True
        answer = messagebox.askyesnocancel("Unsaved changes", "Save the changes to the job search plan?",
                                           parent=self)
        if answer is None:
            return False
        return self.save() if answer else True


class StatusFilter:
    """'Status' button that opens a list of checkboxes, one per status, to show or hide companies.

    The list stays open while ticking boxes; a click outside it or Escape closes it.
    `hidden` is the set of statuses not shown. `counts` (status -> number) is set by the owner."""

    CLOSED = {"Rejected", "Not an option"}
    IN_PROGRESS = {"Contacted", "Applied", "Interview", "Offer"}

    def __init__(self, parent, colors, fonts, on_change) -> None:
        self.colors, self.fonts, self.on_change = colors, fonts, on_change
        self.hidden: set[str] = set()
        self.counts: Counter = Counter()
        self.popup: tk.Toplevel | None = None
        style = ttk.Style(parent)
        style.configure("Filter.TCheckbutton", background=colors["card"], padding=(4, 3))
        style.map("Filter.TCheckbutton", background=[("active", colors["hover"])])
        self.button = ttk.Button(parent, command=self.toggle, padding=(10, 4))
        self._update_label()

    def statuses(self) -> list[str]:
        """The standard steps plus any other value found in the file (e.g. empty)."""
        return STATUSES + sorted(s for s in self.counts if s not in STATUSES)

    def _update_label(self) -> None:
        hidden = [s for s in self.statuses() if s in self.hidden]
        if not hidden:
            text = "Status: all"
        elif len(hidden) <= 2:
            text = "Hiding: " + ", ".join(h or "(no status)" for h in hidden)
        else:
            text = f"Status: {len(self.statuses()) - len(hidden)} of {len(self.statuses())} shown"
        self.button.configure(text=text + "  v")      # plain 'v': this Tk can't draw an arrow glyph

    def _set_hidden(self, hidden: set[str]) -> None:
        self.hidden = set(hidden)
        if self.popup:
            for s, var in self.vars.items():
                var.set(s not in self.hidden)
        self._update_label()
        self.on_change()

    def toggle(self) -> None:
        if self.popup:
            self.close()
        else:
            self.open()

    def open(self) -> None:
        c = self.colors
        top = self.popup = tk.Toplevel(self.button)
        top.overrideredirect(True)
        top.configure(background=c["border"])
        frame = ttk.Frame(top, style="Card.TFrame", padding=(10, 8))
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        self.vars = {}
        for s in self.statuses():
            var = self.vars[s] = tk.BooleanVar(value=s not in self.hidden)
            label = f"{s or '(no status)'}  ({self.counts.get(s, 0)})"
            ttk.Checkbutton(frame, text=label, variable=var, style="Filter.TCheckbutton",
                            command=lambda s=s, v=var: self._checked(s, v.get())).pack(anchor="w", fill="x")

        tk.Frame(frame, height=1, background=c["border"]).pack(fill="x", pady=(8, 6))
        presets = ttk.Frame(frame, style="Card.TFrame")
        presets.pack(fill="x")
        for text, hidden in (("Show all", set()),
                             ("Hide closed", self.CLOSED),
                             ("Only in progress", {s for s in self.statuses() if s not in self.IN_PROGRESS})):
            ttk.Button(presets, text=text, padding=(8, 3),
                       command=lambda h=hidden: self._set_hidden(h)).pack(side="left", padx=(0, 4))

        top.update_idletasks()
        x, y = self.button.winfo_rootx(), self.button.winfo_rooty() + self.button.winfo_height() + 2
        top.geometry(f"+{x}+{y}")
        top.bind("<Escape>", lambda e: self.close())
        top.bind("<Button-1>", self._click)
        top.focus_set()
        top.grab_set()           # clicks elsewhere come here, so they can close the list

    def _click(self, e) -> None:
        top = self.popup
        inside = (top.winfo_rootx() <= e.x_root < top.winfo_rootx() + top.winfo_width() and
                  top.winfo_rooty() <= e.y_root < top.winfo_rooty() + top.winfo_height())
        if not inside:
            self.close()

    def _checked(self, status: str, visible: bool) -> None:
        (self.hidden.discard if visible else self.hidden.add)(status)
        self._update_label()
        self.on_change()

    def close(self) -> None:
        if self.popup:
            self.popup.grab_release()
            self.popup.destroy()
            self.popup = None


class CompaniesTab(ttk.Frame):
    """Table of docs/employers.csv with a detail panel. Status, Last contact and Notes are saved to
    the file as soon as they change. `on_application(company)` starts an application to that company."""

    COLUMNS = [("Company", 220), ("Fit", 70), ("Track", 120), ("Town", 150), ("Status", 110),
               ("Last contact", 100)]
    SORT_KEYS = {
        "Fit": lambda v: {"High": 0, "Medium": 1, "Low": 2}.get(v, 3),
        "Status": lambda v: STATUSES.index(v) if v in STATUSES else len(STATUSES),
    }

    def __init__(self, parent, colors, fonts, on_application=None, path: Path = EMPLOYERS) -> None:
        super().__init__(parent)
        self.colors, self.fonts, self.path = colors, fonts, path
        self.on_application = on_application
        self.fields: list[str] = []
        self.rows: list[dict[str, str]] = []
        self.current: int | None = None          # index of the company shown in the detail panel
        self.sort_column, self.sort_reverse = None, False
        self.mtime = None

        self._style()
        top = ttk.Frame(self, padding=(0, 0, 0, 10))
        top.pack(fill="x")
        ttk.Label(top, text="Search").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._fill_tree())
        ttk.Entry(top, textvariable=self.search, width=28).pack(side="left", padx=(6, 16))
        self.status_filter = StatusFilter(top, colors, fonts, on_change=self._fill_tree)
        self.status_filter.button.pack(side="left", padx=(0, 16))
        self.summary = ttk.Label(top, foreground=colors["muted"])
        self.summary.pack(side="left")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        panes.add(self._build_table(panes), weight=3)
        panes.add(self._build_details(panes), weight=2)
        self.refresh(force=True)

    def _style(self) -> None:
        s = ttk.Style(self)
        c = self.colors
        s.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=c["ink"],
                    rowheight=26, borderwidth=0, font=self.fonts["body"])
        s.map("Treeview", background=[("selected", c["accent"])], foreground=[("selected", "#FFFFFF")])
        s.configure("Treeview.Heading", background=c["hover"], foreground=c["ink"], font=self.fonts["bold"],
                    borderwidth=0, relief="flat", padding=(6, 5))
        s.map("Treeview.Heading", background=[("active", c["border"])])

    # ---- left: table
    def _build_table(self, parent) -> tk.Frame:
        border = tk.Frame(parent, background=self.colors["border"])
        inner = ttk.Frame(border, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.tree = ttk.Treeview(inner, columns=[c for c, _ in self.COLUMNS], show="headings",
                                 selectmode="browse")
        for col, width in self.COLUMNS:
            self.tree.heading(col, text=col, anchor="w", command=lambda c=col: self._sort(c))
            self.tree.column(col, width=width, minwidth=50, anchor="w", stretch=col in ("Company", "Track"))
        for status, color in STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        return border

    # ---- right: details of the selected company
    def _build_details(self, parent) -> tk.Frame:
        border = tk.Frame(parent, background=self.colors["border"])
        d = ttk.Frame(border, style="Card.TFrame", padding=(16, 14))
        d.pack(fill="both", expand=True, padx=1, pady=1)
        d.columnconfigure(1, weight=1)
        self.d_title = ttk.Label(d, text="Choose a company", style="Title.Card.TLabel")
        self.d_title.grid(row=0, column=0, columnspan=3, sticky="w")
        self.d_info = ttk.Label(d, style="Help.Card.TLabel", justify="left")
        self.d_info.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self.d_what = ttk.Label(d, style="Card.TLabel", justify="left", wraplength=380)
        self.d_what.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.d_source = tk.Label(d, background=self.colors["card"], foreground=self.colors["accent"],
                                 cursor="hand2", font=self.fonts["small"] + ("underline",),
                                 anchor="w", justify="left", wraplength=380)
        self.d_source.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.d_source.bind("<Button-1>", lambda e: self._open_source())
        d.bind("<Configure>", lambda e: (self.d_what.configure(wraplength=e.width - 40),
                                         self.d_source.configure(wraplength=e.width - 40)))

        ttk.Label(d, text="Status", style="Field.Card.TLabel").grid(row=4, column=0, sticky="w", pady=(18, 0))
        self.d_status = tk.StringVar()
        self.status_box = ttk.Combobox(d, textvariable=self.d_status, state="readonly", values=STATUSES, width=18)
        self.status_box.grid(row=4, column=1, sticky="w", pady=(18, 0), padx=(12, 0))
        self.status_box.bind("<<ComboboxSelected>>", lambda e: self._status_changed())

        ttk.Label(d, text="Last contact", style="Field.Card.TLabel").grid(row=5, column=0, sticky="w", pady=(8, 0))
        row = ttk.Frame(d, style="Card.TFrame")
        row.grid(row=5, column=1, sticky="w", pady=(8, 0), padx=(12, 0))
        self.d_date = ttk.Entry(row, width=14)
        self.d_date.pack(side="left")
        self.d_date.bind("<FocusOut>", lambda e: self._commit())
        self.d_date.bind("<Return>", lambda e: self._commit())
        ttk.Button(row, text="Today", command=self._today).pack(side="left", padx=(6, 0))

        ttk.Label(d, text="Notes", style="Field.Card.TLabel").grid(row=6, column=0, sticky="nw", pady=(14, 0))
        box = tk.Frame(d, background="#FFFFFF", highlightthickness=1,
                       highlightbackground=self.colors["border"], highlightcolor=self.colors["border"])
        box.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        d.rowconfigure(7, weight=1)
        self.d_notes = tk.Text(box, wrap="word", undo=True, font=self.fonts["body"], height=8, padx=8, pady=6,
                               relief="flat", borderwidth=0, highlightthickness=0, background="#FFFFFF",
                               foreground=self.colors["ink"], insertbackground=self.colors["ink"])
        nscroll = ttk.Scrollbar(box, orient="vertical", command=self.d_notes.yview)
        self.d_notes.configure(yscrollcommand=nscroll.set)
        self.d_notes.pack(side="left", fill="both", expand=True)
        nscroll.pack(side="right", fill="y")
        self.d_notes.bind("<FocusIn>", lambda e: box.configure(highlightbackground=self.colors["accent"]))
        self.d_notes.bind("<FocusOut>", lambda e: (box.configure(highlightbackground=self.colors["border"]),
                                                   self._commit()))

        bottom = ttk.Frame(d, style="Card.TFrame")
        bottom.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.app_btn = ttk.Button(bottom, text="Write application", style="Accent.TButton",
                                  command=self._write_application)
        self.app_btn.pack(side="left")
        self.saved = ttk.Label(bottom, style="Status.Card.TLabel")
        self.saved.pack(side="right")
        self._show_details(None)
        return border

    # ---- data
    def refresh(self, force: bool = False) -> None:
        """(Re)load the CSV if it changed on disk, e.g. edited in another program."""
        if not self.path.exists():
            self.fields, self.rows, self.mtime = [], [], None
            self.summary.configure(text=f"docs/{self.path.name} not found")
            self._fill_tree()
            return
        mtime = self.path.stat().st_mtime
        if not force and mtime == self.mtime:
            return
        self._commit(save=False)
        name = self.rows[self.current]["Company"] if self.current is not None else None
        self.fields, self.rows = load_companies(self.path)
        self.mtime = mtime
        self.current = next((i for i, r in enumerate(self.rows) if r["Company"] == name), None)
        self._fill_tree()

    def _save(self) -> None:
        try:
            save_companies(self.fields, self.rows, self.path)
            self.mtime = self.path.stat().st_mtime
            self.saved.configure(text=f"Saved {datetime.datetime.now():%H:%M}",
                                 foreground=self.colors["muted"])
        except OSError as e:
            self.saved.configure(text=f"Could not save: {e}", foreground=self.colors["error"])

    def _visible(self, r: dict[str, str]) -> bool:
        if r["Status"] in self.status_filter.hidden:
            return False
        q = self.search.get().strip().lower()
        return not q or any(q in (r.get(c) or "").lower() for c in ("Company", "Town", "Track", "What they do", "Notes"))

    def _fill_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.status_filter.counts = Counter(r["Status"] for r in self.rows)
        self.status_filter._update_label()
        order = list(range(len(self.rows)))
        if self.sort_column:
            key = self.SORT_KEYS.get(self.sort_column, str.lower)
            order.sort(key=lambda i: key(self.rows[i][self.sort_column]), reverse=self.sort_reverse)
        for i in order:
            r = self.rows[i]
            if self._visible(r):
                self.tree.insert("", "end", iid=str(i), values=[shown(r.get(c, "")) for c, _ in self.COLUMNS],
                                 tags=(r["Status"],))
        if self.current is not None and self.tree.exists(str(self.current)):
            self.tree.selection_set(str(self.current))
            self.tree.see(str(self.current))
        self._update_summary()

    def _update_summary(self) -> None:
        counts = Counter(r["Status"] for r in self.rows)
        parts = [f"{counts[s]} {s.lower()}" for s in STATUSES if counts[s]]
        parts += [f"{n} {s.lower() or 'no status'}" for s, n in counts.items() if s not in STATUSES]
        shown = len(self.tree.get_children())
        self.summary.configure(text=f"{shown} of {len(self.rows)} shown   ·   " + "  ·  ".join(parts))

    def _sort(self, column: str) -> None:
        self.sort_reverse = not self.sort_reverse if self.sort_column == column else False
        self.sort_column = column
        for col, _ in self.COLUMNS:
            arrow = (" ▼" if self.sort_reverse else " ▲") if col == column else ""
            self.tree.heading(col, text=col + arrow)
        self._fill_tree()

    # ---- details
    def _on_select(self) -> None:
        sel = self.tree.selection()
        index = int(sel[0]) if sel else None
        if index == self.current:
            return
        self._commit()
        self.current = index
        self._show_details(index)

    def _show_details(self, index: int | None) -> None:
        r = self.rows[index] if index is not None else None
        state = "normal" if r else "disabled"
        self.d_title.configure(text=shown(r["Company"]) if r else "Choose a company")
        info = [r.get(c, "") for c in ("Fit", "Track", "Town")] if r else []
        self.d_info.configure(text="   ·   ".join(
            f"{label}: {shown(v)}" for label, v in zip(("Fit", "Track", "Town"), info) if v))
        self.d_what.configure(text=shown(r.get("What they do", "")) if r else "")
        self.d_source.configure(text=r.get("Source", "") if r else "")
        self.d_status.set(r["Status"] if r else "")
        values = STATUSES + ([r["Status"]] if r and r["Status"] and r["Status"] not in STATUSES else [])
        self.status_box.configure(values=values, state="readonly" if r else "disabled")
        self.d_date.configure(state="normal")
        self.d_date.delete(0, "end")
        self.d_date.insert(0, r["Last contact"] if r else "")
        self.d_date.configure(state=state)
        self.d_notes.configure(state="normal")
        self.d_notes.delete("1.0", "end")
        self.d_notes.insert("1.0", r["Notes"] if r else "")
        self.d_notes.edit_reset()
        self.d_notes.configure(state=state)
        self.app_btn.configure(state=state if self.on_application else "disabled")

    def _commit(self, save: bool = True) -> None:
        """Copy the detail panel into the current row; save if anything changed."""
        if self.current is None or self.current >= len(self.rows):
            return
        r = self.rows[self.current]
        new = {"Status": self.d_status.get(), "Last contact": self.d_date.get().strip(),
               "Notes": self.d_notes.get("1.0", "end-1c").strip()}
        if all(r[k] == v for k, v in new.items()):
            return
        r.update(new)
        iid = str(self.current)
        if self.tree.exists(iid):
            self.tree.item(iid, values=[shown(r.get(c, "")) for c, _ in self.COLUMNS], tags=(r["Status"],))
        self._update_summary()
        if save:
            self._save()

    def _status_changed(self) -> None:
        if self.current is not None and self.d_status.get() != self.rows[self.current]["Status"] \
                and self.d_status.get() in CONTACT_STATUSES:
            self._today(commit=False)
        self._commit()

    def _today(self, commit: bool = True) -> None:
        self.d_date.delete(0, "end")
        self.d_date.insert(0, datetime.date.today().isoformat())
        if commit:
            self._commit()

    def _open_source(self) -> None:
        url = self.d_source.cget("text").strip()
        if url.startswith(("http://", "https://")):
            webbrowser.open(url)

    def _write_application(self) -> None:
        if self.current is not None and self.on_application:
            self._commit()
            self.on_application(self.rows[self.current]["Company"])
