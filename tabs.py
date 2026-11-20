"""Extra tabs of the editor window: the job search plan and the company list with process status.

Both read from docs/ (git-ignored, personal):
  docs/job-search-plan.md   shown formatted, editable as Markdown; reloaded when the file changes
  docs/employers.csv        one row per company (companies.CompanyList); every field is edited in the
                            Companies tab and saved straight back to the file

Also the job posting helpers used by the Companies tab and the quicklist: `ask_posting` (title + link
dialog) and `PostingTextWindow` (the posting's text, pasted in and kept in output/<company>/<posting>/).
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
import webbrowser
from collections import Counter
from pathlib import Path
from tkinter import font as tkfont, messagebox, simpledialog, ttk
import tkinter as tk

import appearance
import companies
from companies import FITS, STATUSES

DOCS = companies.DOCS
PLAN = DOCS / "job-search-plan.md"

GOOD = appearance.COLORS["good"]           # "posting text saved" (same green as a ready document)
STATUS_COLORS = appearance.COLORS["status"]

# Tk builds without Xft only have X11 core fonts, which cover Latin-1 (æøå are fine) but show
# characters like • – “ as garbage. Text that is only displayed goes through `shown()`.
_LATIN1 = str.maketrans({"•": "·", "–": "-", "—": "-", "‒": "-", "“": '"', "”": '"', "„": '"',
                         "‘": "'", "’": "'", "…": "...", "→": "->", "←": "<-", "≈": "~", "✓": "v"})


def shown(text: str) -> str:
    return text.translate(_LATIN1)


# --------------------------------------------------------------------------- markdown -> Text

_INLINE = re.compile(r"\*\*(?P<bold>.+?)\*\*|(?<!\\)\[(?P<label>[^\]]+?)\]\((?P<url>[^)\s]+)\)")


def _unescape(text: str) -> str:
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>])", r"\1", text)


class LinkLabel(tk.Label):
    """A web link shown as its full URL (never just "open"); a click opens it. Empty when there is no URL."""

    def __init__(self, parent, colors, fonts, background: str | None = None) -> None:
        family, size = fonts["small"][:2]
        super().__init__(parent, anchor="w", justify="left", foreground=colors["accent"],
                         background=background or colors["card"], font=(family, size, "underline"),
                         borderwidth=0, padx=0, pady=0)
        self.url = ""
        self.bind("<Button-1>", lambda e: self.url and webbrowser.open(self.url))
        # Wrap long URLs at the label's width.
        self.bind("<Configure>", lambda e: self.configure(wraplength=max(e.width, 50)))

    def set(self, url: str) -> None:
        self.url = url.strip() if url.strip().startswith(("http://", "https://")) else ""
        self.configure(text=self.url, cursor="hand2" if self.url else "")


class MarkdownView(tk.Text):
    """Read-only Text that shows the subset of Markdown used in the docs: headings, paragraphs,
    **bold**, [links](url), bullets, task boxes, block quotes and tables."""

    def __init__(self, parent, colors: dict[str, str], fonts: dict[str, tuple], **kw) -> None:
        family = fonts["body"][0]
        super().__init__(parent, wrap="word", relief="flat", borderwidth=0, highlightthickness=0,
                         padx=28, pady=20, background=colors["card"], foreground=colors["ink"],
                         font=(family, appearance.pt(11)), cursor="arrow", spacing1=1, spacing3=1, **kw)
        self.colors = colors
        self.tag_configure("h1", font=(family, appearance.pt(20), "bold"), spacing1=4, spacing3=10)
        self.tag_configure("h2", font=(family, appearance.pt(15), "bold"), foreground=colors["accent"], spacing1=18, spacing3=6)
        self.tag_configure("h3", font=(family, appearance.pt(12), "bold"), spacing1=12, spacing3=4)
        self.tag_configure("bold", font=(family, appearance.pt(11), "bold"))
        self.tag_configure("muted", foreground=colors["muted"])
        self.tag_configure("bullet", lmargin1=14, lmargin2=34)
        self.tag_configure("quote", lmargin1=18, lmargin2=18, foreground=colors["quote_ink"],
                           background=colors["quote_bg"], rmargin=20)
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
        self.editor = tk.Text(self.body, wrap="word", undo=True, autoseparators=True, font=(fonts["body"][0], appearance.pt(12)),  # 11 drops "|"
                              relief="flat", borderwidth=0, highlightthickness=0, padx=28, pady=20,
                              background=appearance.COLORS["field"], foreground=colors["ink"], insertbackground=colors["ink"])
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

# --------------------------------------------------------------------------- job postings

def ask_posting(parent, model: companies.CompanyList, row: dict[str, str], title: str = "", url: str = "",
                heading: str = "Add job posting") -> tuple[str, str] | None:
    """Dialog for a posting's title and link. Returns (title, url), or None if cancelled.
    `title` is the posting being edited ("" for a new one)."""
    dlg = tk.Toplevel(parent)
    dlg.title(heading)
    dlg.transient(parent.winfo_toplevel())
    dlg.resizable(True, False)
    f = ttk.Frame(dlg, padding=16)
    f.pack(fill="both", expand=True)
    f.columnconfigure(1, weight=1)
    ttk.Label(f, text=f"Company: {shown(row['Company'])}").grid(row=0, column=0, columnspan=2, sticky="w")
    entries = []
    for i, (label, value) in enumerate((("Title", title), ("Link", url)), start=1):
        ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=(10, 0))
        e = ttk.Entry(f, width=56)
        e.insert(0, value)
        e.grid(row=i, column=1, sticky="ew", padx=(10, 0), pady=(10, 0))
        entries.append(e)
    ttk.Label(f, text="The title names the folder output/<company>/<title>/. The link is optional.",
              foreground=appearance.COLORS["muted"]).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
    result: dict = {}

    def ok(_event=None):
        t, u = entries[0].get().strip(), entries[1].get().strip()
        problem = model.posting_problem(row, t, ignore=title or None)
        if problem is None and u and not u.startswith(("http://", "https://")):
            problem = "The link must start with http:// or https://"
        if problem:
            messagebox.showwarning(heading, problem, parent=dlg)
            return
        result["value"] = (t, u)
        dlg.destroy()

    buttons = ttk.Frame(f)
    buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(buttons, text="OK", style="Accent.TButton", command=ok).pack(side="left")
    ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(side="left", padx=(6, 0))
    dlg.bind("<Return>", ok)
    dlg.bind("<Escape>", lambda e: dlg.destroy())
    dlg.wait_visibility()
    dlg.grab_set()
    entries[0].focus_set()
    parent.wait_window(dlg)
    return result.get("value")


class PostingTextWindow(tk.Toplevel):
    """The text of a job posting, pasted in so it can be read after the ad is taken down.
    Saved as output/<company>/<posting>/job_posting.txt. One window per posting; `show` reuses it."""

    open_windows: dict[tuple[str, str], "PostingTextWindow"] = {}

    @classmethod
    def show(cls, parent, model, colors, fonts, company: str, posting: str) -> "PostingTextWindow":
        w = cls.open_windows.get((company, posting))
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            w.text.focus_set()
            return w
        return cls(parent, model, colors, fonts, company, posting)

    @classmethod
    def renamed(cls, old_company: str, new_company: str, old_posting: str | None = None,
                new_posting: str | None = None) -> None:
        """Keep open windows pointing at the right folder after a company or posting is renamed."""
        for (c, t), w in list(cls.open_windows.items()):
            if c == old_company and (old_posting is None or t == old_posting):
                del cls.open_windows[(c, t)]
                w.company, w.posting = new_company, (new_posting if old_posting is not None else t)
                cls.open_windows[(w.company, w.posting)] = w
                w._update_head()

    @classmethod
    def confirm_close_all(cls) -> bool:
        """Before the editor closes: offer to save text that isn't saved. False = cancel closing."""
        return all(w.close() for w in list(cls.open_windows.values()))

    def __init__(self, parent, model: companies.CompanyList, colors, fonts, company: str, posting: str) -> None:
        super().__init__(parent)
        self.model, self.colors = model, colors
        self.company, self.posting = company, posting
        self.open_windows[(company, posting)] = self
        self.geometry("780x860")
        self.configure(background=colors["bg"])

        head = ttk.Frame(self, style="Card.TFrame", padding=(16, 12))
        head.pack(fill="x")
        self.head_label = ttk.Label(head, style="Title.Card.TLabel")
        self.head_label.pack(fill="x")
        self.link = LinkLabel(head, colors, fonts)
        self.link.pack(fill="x", pady=(4, 0))
        ttk.Label(self, text="Paste the job posting here and save it, so you can still read it after the ad "
                             "is taken down.", foreground=colors["muted"], font=fonts["small"],
                  padding=(16, 8, 16, 4)).pack(fill="x")

        bottom = ttk.Frame(self, padding=(16, 8, 16, 12))
        bottom.pack(side="bottom", fill="x")
        ttk.Button(bottom, text="Save", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(bottom, text="Close", command=self.close).pack(side="left", padx=(6, 0))
        self.status = ttk.Label(bottom, foreground=colors["muted"])
        self.status.pack(side="left", padx=(12, 0))

        box = tk.Frame(self, background=appearance.COLORS["field"], highlightthickness=1, highlightbackground=colors["border"],
                       highlightcolor=colors["border"])
        box.pack(fill="both", expand=True, padx=16)
        self.text = tk.Text(box, wrap="word", undo=True, font=fonts["body"], padx=12, pady=10, relief="flat",
                            borderwidth=0, highlightthickness=0, background=appearance.COLORS["field"],
                            foreground=colors["ink"], insertbackground=colors["ink"])
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.saved_text = companies.read_posting_text(company, posting)
        self.text.insert("1.0", self.saved_text)
        self.text.edit_reset()
        self.text.bind("<<Paste>>", self._paste)
        self.text.bind("<Control-s>", lambda e: (self.save(), "break")[1])
        self.text.bind("<<Modified>>", lambda e: self._update_status())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._update_head()
        self._update_status()
        self.text.focus_set()

    def _row_posting(self) -> tuple[dict[str, str] | None, dict[str, str] | None]:
        row = self.model.find(self.company)
        posting = next((p for p in self.model.postings(row) if p["title"] == self.posting), None)
        return row, posting

    def _update_head(self) -> None:
        self.title(f"Job posting - {shown(self.posting)} - {shown(self.company)}")
        self.head_label.configure(text=f"{shown(self.posting)}   ·   {shown(self.company)}")
        _, posting = self._row_posting()
        self.link.set(posting["url"] if posting else "")

    def _update_status(self) -> None:
        self.text.edit_modified(False)
        if self.modified():
            self.status.configure(text="Not saved", foreground=self.colors["error"])
        elif self.saved_text:
            self.status.configure(text=f"Saved in output/{companies.folder_name(self.company)}/"
                                       f"{companies.folder_name(self.posting)}/{companies.POSTING_TEXT}",
                                  foreground=self.colors["muted"])
        else:
            self.status.configure(text="No text saved yet", foreground=self.colors["muted"])

    def _paste(self, _event) -> str:
        """Paste with characters this Tk can't draw (• – “ ...) turned into plain lookalikes."""
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            self.text.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        self.text.insert("insert", shown(clip))
        self.text.see("insert")
        return "break"

    def modified(self) -> bool:
        return self.text.get("1.0", "end-1c") != self.saved_text

    def save(self) -> bool:
        row, posting = self._row_posting()
        if posting is None:
            messagebox.showerror("Save posting text", f"'{self.posting}' is no longer a posting of "
                                                      f"{self.company} in the company list.", parent=self)
            return False
        text = self.text.get("1.0", "end-1c")
        try:
            companies.write_posting_text(self.company, self.posting, text)
        except OSError as e:
            messagebox.showerror("Save posting text", f"Could not save:\n{e}", parent=self)
            return False
        self.saved_text = text
        self._update_status()
        self.status.configure(text=self.status.cget("text") + f"  ({datetime.datetime.now():%H:%M})")
        self.model.documents_changed()
        return True

    def close(self) -> bool:
        if self.modified():
            self.lift()
            answer = messagebox.askyesnocancel("Unsaved posting text",
                                               f"Save the text of '{self.posting}' before closing?", parent=self)
            if answer is None or (answer and not self.save()):
                return False
        self.open_windows.pop((self.company, self.posting), None)
        self.destroy()
        return True


class CompaniesTab(ttk.Frame):
    """Table of docs/employers.csv with a detail panel where every field is edited. Changes are saved
    to the file as soon as a field is left. 'Interesting' puts a company on the Documents tab's
    quicklist. `on_open(company, posting)` makes it the company (and job posting, or None) being worked
    on in the Documents tab."""

    COLUMNS = [("Company", 190), ("Fit", 55), ("Track", 105), ("Town", 105), ("Status", 100),
               ("Last contact", 95), ("Quicklist", 75), ("Unsolicited", 85), ("Postings", 70),
               ("Earlier", 62)]
    COLUMNS = [(col, appearance.px(w)) for col, w in COLUMNS]          # pixel widths follow the UI size
    # These give up width first when the table is narrower than the columns (down to MIN_WIDTHS).
    STRETCH = ("Company", "Track", "Town")
    MIN_WIDTHS = {c: appearance.px(w) for c, w in {"Company": 100, "Track": 55, "Town": 55}.items()}
    # Number columns: "Postings" = job postings being worked on (Job postings field), "Earlier" = entries
    # in Past postings (companies.count_past_postings). 0 is shown empty; the first click sorts most first.
    COUNT_COLUMNS = ("Postings", "Earlier")
    SORT_KEYS = {
        "Postings": lambda v: int(v or 0),
        "Earlier": lambda v: int(v or 0),
        "Unsolicited": lambda v: 0 if v else 1,            # companies with a form first
        "Fit": lambda v: {"High": 0, "Medium": 1, "Low": 2}.get(v, 3),
        "Status": lambda v: STATUSES.index(v) if v in STATUSES else len(STATUSES),
    }
    # Single-line fields in the detail panel, in order.
    FIELDS = ["Fit", "Track", "Town", "What they do", "Contact person", "Email", "Homepage", "Unsolicited form",
              "Found via"]
    # The full URL is shown below as a clickable link. "Unsolicited form" = where to send an unsolicited
    # application; the table's Unsolicited column shows "form" when it is filled in.
    LINK_FIELDS = ("Homepage", "Unsolicited form", "Found via")

    def __init__(self, parent, colors, fonts, model: companies.CompanyList, on_open=None) -> None:
        super().__init__(parent)
        self.colors, self.fonts, self.model = colors, fonts, model
        self.on_open = on_open
        self.current: int | None = None          # index (into model.rows) of the company in the panel
        self.sort_column, self.sort_reverse = None, False
        model.listeners.append(self._on_model)
        model.doc_listeners.append(lambda: self._fill_postings())

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
        ttk.Button(top, text="Add company...", style="Accent.TButton", command=self._add).pack(side="right")

        ttk.Label(self, text="In the table: click Quicklist to tick it, click Status or Fit to change them, "
                             "double-click Track / Town / Last contact to edit, double-click Company to rename.",
                  foreground=colors["muted"], font=fonts["small"]).pack(side="bottom", anchor="w", pady=(6, 0))
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        panes.add(self._build_table(panes), weight=3)
        panes.add(self._build_details(panes), weight=2)
        self._fill_tree()

    @property
    def rows(self) -> list[dict[str, str]]:
        return self.model.rows

    def _style(self) -> None:
        s = ttk.Style(self)
        c = self.colors
        s.configure("Treeview", background=c["field"], fieldbackground=c["field"], foreground=c["ink"],
                    rowheight=appearance.px(26), borderwidth=0, font=self.fonts["body"])
        s.map("Treeview", background=[("selected", c["accent"])], foreground=[("selected", c["on_accent"])])
        s.configure("Treeview.Heading", background=c["hover"], foreground=c["ink"], font=self.fonts["bold"],
                    borderwidth=0, relief="flat", padding=(6, 5))
        s.map("Treeview.Heading", background=[("active", c["border"])])
        s.configure("Card.TCheckbutton", background=c["card"])
        s.map("Card.TCheckbutton", background=[("active", c["card"])])

    # ---- left: table
    def _build_table(self, parent) -> tk.Frame:
        # A fixed size request, so the panes split the space by weight instead of growing with the
        # column widths (which _fit_columns sets from the table's actual width).
        border = tk.Frame(parent, background=self.colors["border"], width=appearance.px(820), height=appearance.px(300))
        border.pack_propagate(False)
        inner = ttk.Frame(border, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.tree = ttk.Treeview(inner, columns=[c for c, _ in self.COLUMNS], show="headings",
                                 selectmode="browse")
        for col, width in self.COLUMNS:
            self.tree.heading(col, text=col, anchor="w", command=lambda c=col: self._sort(c))
            self.tree.column(col, width=width, minwidth=self.MIN_WIDTHS.get(col, appearance.px(50)),
                             anchor="center" if col in self.COUNT_COLUMNS + ("Quicklist", "Unsolicited") else "w",
                             stretch=col in self.STRETCH)
        for status, color in STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)
        self.tree.tag_configure("interesting", font=self.fonts["bold"])
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Configure>", lambda e: self._fit_columns(e.width))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Button-1>", self._cell_click)
        self.tree.bind("<Double-1>", self._cell_double)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.tree.bind(seq, lambda e: self._end_cell_edit(), add="+")
        return border

    def _fit_columns(self, width: int) -> None:
        """Make the columns fill the table's width exactly: the STRETCH columns share what the fixed
        ones leave, in proportion to their normal widths and never below MIN_WIDTHS."""
        widths = dict(self.COLUMNS)
        fixed = sum(w for c, w in widths.items() if c not in self.STRETCH)
        base = sum(widths[c] for c in self.STRETCH)
        free = max(width - fixed - 2, sum(self.MIN_WIDTHS.values()))
        for c in self.STRETCH:
            self.tree.column(c, width=max(self.MIN_WIDTHS[c], free * widths[c] // base))

    # ---- right: details of the selected company
    def _build_details(self, parent) -> tk.Frame:
        border = tk.Frame(parent, background=self.colors["border"])
        # The fields scroll when they don't fit (big UI size, small window). The panel is stretched to
        # the visible height when there is room, so the text tabs at the bottom take what is left.
        self.d_canvas = tk.Canvas(border, highlightthickness=0, borderwidth=0, background=self.colors["card"])
        d_scroll = ttk.Scrollbar(border, orient="vertical", command=self.d_canvas.yview)
        self.d_canvas.configure(yscrollcommand=d_scroll.set)
        d = ttk.Frame(self.d_canvas, style="Card.TFrame", padding=(16, 14))
        self.d_window = self.d_canvas.create_window(0, 0, window=d, anchor="nw")
        self.d_canvas.bind("<Configure>", lambda e: self._fit_details())
        d.bind("<Configure>", lambda e: self._fit_details())
        self.d_panel = d
        d.columnconfigure(1, weight=1)
        head = ttk.Frame(d, style="Card.TFrame")
        head.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.d_title = ttk.Label(head, text="Choose a company", style="Title.Card.TLabel")
        self.d_title.pack(side="left")
        self.rename_btn = ttk.Button(head, text="Rename...", padding=(8, 2), command=self._rename)
        self.rename_btn.pack(side="right")

        self.d_interesting = tk.BooleanVar()
        self.interest_box = ttk.Checkbutton(d, text="Interesting: put it on the quicklist in the Documents tab",
                                            variable=self.d_interesting, style="Card.TCheckbutton",
                                            command=self._interest_changed)
        self.interest_box.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 4))

        self.entries: dict[str, ttk.Entry] = {}
        self.links: dict[str, LinkLabel] = {}
        row = 2
        for field in self.FIELDS:
            ttk.Label(d, text=field, style="Field.Card.TLabel").grid(row=row, column=0, sticky="w", pady=(6, 0))
            if field == "Fit":
                w = ttk.Combobox(d, values=FITS, width=12)
                w.bind("<<ComboboxSelected>>", lambda e: self._commit())
                w.grid(row=row, column=1, sticky="w", pady=(6, 0), padx=(12, 0))
            else:
                w = ttk.Entry(d)
                w.grid(row=row, column=1, sticky="ew", pady=(6, 0), padx=(12, 0))
            w.bind("<FocusOut>", lambda e: self._commit())
            w.bind("<Return>", lambda e: self._commit())
            self.entries[field] = w
            if field in self.LINK_FIELDS:
                row += 1
                link = LinkLabel(d, self.colors, self.fonts)
                link.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(2, 0))
                self.links[field] = link
            row += 1

        ttk.Label(d, text="Status", style="Field.Card.TLabel").grid(row=row, column=0, sticky="w", pady=(12, 0))
        status_row = ttk.Frame(d, style="Card.TFrame")
        status_row.grid(row=row, column=1, columnspan=2, sticky="w", pady=(12, 0), padx=(12, 0))
        self.d_status = tk.StringVar()
        self.status_box = ttk.Combobox(status_row, textvariable=self.d_status, state="readonly", values=STATUSES,
                                       width=16)
        self.status_box.pack(side="left")
        self.status_box.bind("<<ComboboxSelected>>", lambda e: self._status_changed())
        row += 1
        # Own row, so it isn't cut off when the panel is narrow (big UI size).
        ttk.Label(d, text="Last contact", style="Field.Card.TLabel").grid(row=row, column=0, sticky="w", pady=(6, 0))
        status_row = ttk.Frame(d, style="Card.TFrame")
        status_row.grid(row=row, column=1, columnspan=2, sticky="w", pady=(6, 0), padx=(12, 0))
        self.d_date = ttk.Entry(status_row, width=12)
        self.d_date.pack(side="left")
        self.d_date.bind("<FocusOut>", lambda e: self._commit())
        self.d_date.bind("<Return>", lambda e: self._commit())
        self.today_btn = ttk.Button(status_row, text="Today", command=self._today)
        self.today_btn.pack(side="left", padx=(6, 0))
        row += 1

        # Job postings, Notes and Past postings share one area (tabs), which gets all the height that is left.
        self.text_tabs = ttk.Notebook(d)
        self.text_tabs.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        d.rowconfigure(row, weight=1, minsize=160)
        row += 1

        # Job postings: title, whether the posting text is saved, link
        page = ttk.Frame(self.text_tabs, style="Card.TFrame", padding=(0, 6, 0, 0))
        self.text_tabs.add(page, text="Job postings")
        pbar = ttk.Frame(page, style="Card.TFrame")
        pbar.pack(fill="x")
        self.posting_btns = {}
        for key, text, command in (("add", "Add...", self._add_posting), ("edit", "Edit...", self._edit_posting),
                                   ("remove", "Remove", self._remove_posting),
                                   ("text", "Posting text", self._posting_text)):
            b = ttk.Button(pbar, text=text, padding=(8, 2), command=command)
            b.pack(side="left", padx=(0, 4))
            self.posting_btns[key] = b
        ttk.Label(page, text="Double-click a posting to read or paste the ad's text.", style="Help.Card.TLabel"
                  ).pack(anchor="w", pady=(4, 0))
        # The table cuts long URLs off, so the selected posting's full link is shown below it too.
        self.p_link = LinkLabel(page, self.colors, self.fonts)
        self.p_link.pack(side="bottom", fill="x", pady=(4, 0))
        pborder = tk.Frame(page, background=self.colors["border"])
        pborder.pack(fill="both", expand=True, pady=(4, 0))
        self.p_tree = ttk.Treeview(pborder, columns=("title", "text", "link"), show="headings", height=3,
                                   selectmode="browse")
        for col, text, width, stretch in (("title", "Posting", 220, True), ("text", "Posting text", 110, False),
                                          ("link", "Link", 320, True)):
            self.p_tree.heading(col, text=text, anchor="w")
            self.p_tree.column(col, width=appearance.px(width), minwidth=appearance.px(40), anchor="w", stretch=stretch)
        self.p_tree.tag_configure("saved", foreground=GOOD)
        self.p_tree.tag_configure("missing", foreground=self.colors["muted"])
        self.p_tree.pack(fill="both", expand=True, padx=1, pady=1)
        self.p_tree.bind("<<TreeviewSelect>>", lambda e: self._posting_buttons())
        self.p_tree.bind("<Double-1>", lambda e: self.p_tree.identify_row(e.y) and self._posting_text())
        self.p_tree.bind("<Button-1>", self._posting_click)

        self.d_notes = self._text_field(self.text_tabs, "Notes")
        self.d_past = self._text_field(
            self.text_tabs, "Past postings",
            "Earlier ads (closed too), one per line: title, when, what they asked for. "
            "Lines starting with 'Note:' are not counted.")
        self.d_past.bind("<KeyRelease>", lambda e: self._past_tab_label(), add="+")

        # Packed before the panel's content, so a too-small window cuts the text area, not the buttons.
        bottom = ttk.Frame(border, style="Card.TFrame", padding=(16, 8, 16, 12))
        bottom.pack(side="bottom", fill="x", padx=1, pady=(0, 1))
        self.open_btn = ttk.Button(bottom, text="Work on it in Documents", style="Accent.TButton",
                                   command=self._open)
        self.open_btn.pack(side="left")
        self.delete_btn = ttk.Button(bottom, text="Delete", command=self._delete)
        self.delete_btn.pack(side="left", padx=(6, 0))
        self.saved = ttk.Label(bottom, style="Status.Card.TLabel")
        self.saved.pack(side="right")
        d_scroll.pack(side="right", fill="y", pady=(1, 0))
        self.d_canvas.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=(1, 0))
        self._bind_details_wheel()
        self._show_details(None)
        return border

    def _fit_details(self) -> None:
        """Panel as wide as the canvas, and as tall as the canvas or its content, whichever is taller."""
        c, d = self.d_canvas, self.d_panel
        width, height = c.winfo_width(), max(c.winfo_height(), d.winfo_reqheight())
        if (int(c.itemcget(self.d_window, "width") or 0), int(c.itemcget(self.d_window, "height") or 0)) \
                != (width, height):
            c.itemconfigure(self.d_window, width=width, height=height)
        c.configure(scrollregion=(0, 0, width, height))

    def _bind_details_wheel(self) -> None:
        """Mouse wheel over the detail panel scrolls it. Text boxes and the postings table scroll
        themselves; dropdowns scroll the panel instead of changing their value."""
        def wheel(e):
            if not str(e.widget).startswith(str(self.d_canvas)) or e.widget.winfo_class() in ("Text", "Treeview"):
                return None
            if self.d_canvas.yview() != (0.0, 1.0):
                self.d_canvas.yview_scroll(-1 if (e.num == 4 or e.delta > 0) else 1, "units")
            return "break"

        def enter(_e):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.d_canvas.bind_all(seq, wheel)

        def leave(e):
            # <Leave> also fires when the pointer moves onto a child; only unbind when it left the panel.
            x, y = e.x_root - self.d_canvas.winfo_rootx(), e.y_root - self.d_canvas.winfo_rooty()
            if not (0 <= x < self.d_canvas.winfo_width() and 0 <= y < self.d_canvas.winfo_height()):
                for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    self.d_canvas.unbind_all(seq)
        self.d_canvas.bind("<Enter>", enter)
        self.d_canvas.bind("<Leave>", leave)
        for box in (self.entries["Fit"], self.status_box):         # don't change Fit/Status by scrolling
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                box.bind(seq, wheel)

    # ---- data
    def refresh(self, force: bool = False) -> None:
        """(Re)load the CSV if it changed on disk, e.g. edited in another program."""
        self.model.refresh(force)
        if not self.model.path.exists():
            self.summary.configure(text=f"docs/{self.model.path.name} not found: 'Add company...' creates it")

    def _on_model(self, source) -> None:
        """The list changed elsewhere (quicklist, file on disk): show the new state."""
        if source is self:
            return
        name = self.rows[self.current]["Company"] if self.current is not None and self.current < len(self.rows) \
            else None
        self.current = None
        if name is not None:
            row = self.model.find(name)
            self.current = self.rows.index(row) if row is not None else None
        self._fill_tree()
        self._show_details(self.current)

    def _save(self) -> None:
        error = self.model.save(source=self)
        if error:
            self.saved.configure(text=f"Could not save: {error}", foreground=self.colors["error"])
        else:
            self.saved.configure(text=f"Saved {datetime.datetime.now():%H:%M}", foreground=self.colors["muted"])

    def _values(self, r: dict[str, str]) -> list[str]:
        values = []
        for c, _ in self.COLUMNS:
            if c == "Quicklist":
                values.append("[x]" if companies.is_interesting(r) else "[  ]")
            elif c == "Unsolicited":
                values.append("form" if companies.has_unsolicited_form(r) else "")
            elif c == "Postings":
                values.append(str(len(self.model.postings(r)) or ""))
            elif c == "Earlier":
                values.append(str(companies.count_past_postings(r) or ""))
            else:
                values.append(shown(r.get(c, "")))
        return values

    def _tags(self, r: dict[str, str]) -> tuple[str, ...]:
        return (r["Status"],) + (("interesting",) if companies.on_quicklist(r) else ())

    def _visible(self, r: dict[str, str]) -> bool:
        if r["Status"] in self.status_filter.hidden:
            return False
        q = self.search.get().strip().lower()
        return not q or any(q in (r.get(c) or "").lower()
                            for c in ("Company", "Town", "Track", "What they do", "Notes", "Past postings", "Contact person",
                                      "Job postings"))

    def _fill_tree(self) -> None:
        self._end_cell_edit()
        self.tree.delete(*self.tree.get_children())
        self.status_filter.counts = Counter(r["Status"] for r in self.rows)
        self.status_filter._update_label()
        order = list(range(len(self.rows)))
        if self.sort_column:
            key = self.SORT_KEYS.get(self.sort_column, str.lower)
            values = {i: self._values(self.rows[i])[[c for c, _ in self.COLUMNS].index(self.sort_column)]
                      for i in order}
            order.sort(key=lambda i: key(values[i]), reverse=self.sort_reverse)
        for i in order:
            r = self.rows[i]
            if self._visible(r):
                self.tree.insert("", "end", iid=str(i), values=self._values(r), tags=self._tags(r))
        if self.current is not None and self.tree.exists(str(self.current)):
            self.tree.selection_set(str(self.current))
            self.tree.see(str(self.current))
        self._update_summary()

    def _update_row(self, index: int | None = None) -> None:
        index = self.current if index is None else index
        if index is not None and self.tree.exists(str(index)):
            r = self.rows[index]
            self.tree.item(str(index), values=self._values(r), tags=self._tags(r))
        self._update_summary()

    # ---- editing in the table itself
    #   Quicklist: click toggles   Status / Fit: click opens a menu
    #   Track / Town / Last contact: double-click edits in place   Company: double-click renames
    TEXT_CELLS = ("Track", "Town", "Last contact")

    def _cell_at(self, e) -> tuple[int, str] | None:
        if self.tree.identify_region(e.x, e.y) != "cell":
            return None
        iid, col = self.tree.identify_row(e.y), self.tree.identify_column(e.x)
        if not iid or not col:
            return None
        return int(iid), self.COLUMNS[int(col[1:]) - 1][0]

    def _cell_click(self, e) -> None:
        self._end_cell_edit()
        cell = self._cell_at(e)
        if cell is None:
            return
        index, col = cell
        if col == "Quicklist":
            if self._on_checkbox(index, e.x):
                self.after_idle(self._toggle_quicklist, index)
        elif col in ("Status", "Fit"):
            self.after_idle(self._cell_menu, index, col, e.x_root, e.y_root)

    def _cell_double(self, e) -> str | None:
        cell = self._cell_at(e)
        if cell is None:
            return None
        index, col = cell
        if col == "Company":
            self.after_idle(self._rename)
        elif col in self.TEXT_CELLS:
            self.after_idle(self._cell_entry, index, col)
        return "break"

    def _on_checkbox(self, index: int, x: int) -> bool:
        """True if x (in the table) is on the [x] drawn in the centre of the row's Quicklist cell,
        so a click elsewhere in the wide cell doesn't tick it."""
        box = self.tree.bbox(str(index), "Quicklist")
        if not box:
            return False
        cell_x, _, cell_w, _ = box
        text = self.tree.set(str(index), "Quicklist")
        half = tkfont.Font(font=self.fonts["body"]).measure(text) / 2 + 4     # a little slack around it
        return abs(x - (cell_x + cell_w / 2)) <= half

    def _toggle_quicklist(self, index: int) -> None:
        r = self.rows[index]
        self._set_cell(index, "Interesting", "" if companies.is_interesting(r) else "yes")

    def _cell_menu(self, index: int, col: str, x: int, y: int) -> None:
        r = self.rows[index]
        values = STATUSES if col == "Status" else FITS
        values = values + ([r[col]] if r[col] and r[col] not in values else [])
        self._menu_var = tk.StringVar(value=r[col])
        menu = tk.Menu(self, tearoff=False, font=self.fonts["body"])
        for v in values:
            menu.add_radiobutton(label=v, value=v, variable=self._menu_var,
                                 command=lambda v=v: self._set_cell(index, col, v))
        menu.tk_popup(x, y)

    def _cell_entry(self, index: int, col: str) -> None:
        box = self.tree.bbox(str(index), col)
        if not box:
            return
        x, y, w, h = box
        entry = tk.Entry(self.tree, relief="flat", font=self.fonts["body"], highlightthickness=1,
                         highlightcolor=self.colors["accent"], highlightbackground=self.colors["accent"])
        entry.insert(0, self.rows[index].get(col, ""))
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=max(w, 120), height=h)
        entry.focus_set()
        entry.bind("<Return>", lambda e: self._end_cell_edit())
        entry.bind("<Escape>", lambda e: self._end_cell_edit(cancel=True))
        entry.bind("<FocusOut>", lambda e: self._end_cell_edit())
        self._cell_edit = (entry, index, col)

    def _end_cell_edit(self, cancel: bool = False) -> None:
        edit, self._cell_edit = getattr(self, "_cell_edit", None), None
        if edit is None:
            return
        entry, index, col = edit
        value = entry.get().strip()
        entry.destroy()
        if not cancel and index < len(self.rows) and value != self.rows[index].get(col, ""):
            self._set_cell(index, col, value)

    def _set_cell(self, index: int, col: str, value: str) -> None:
        """Change one field of a row from the table, and show it in the detail panel too."""
        self._commit(save=False)                 # keep what is typed in the panel
        r = self.rows[index]
        if col == "Status":
            self.model.set_status(r, value)
        else:
            r[col] = value
        self._update_row(index)
        if index == self.current:
            self._show_details(index)
        self._save()

    def _update_summary(self) -> None:
        counts = Counter(r["Status"] for r in self.rows)
        parts = [f"{counts[s]} {s.lower()}" for s in STATUSES if counts[s]]
        parts += [f"{n} {s.lower() or 'no status'}" for s, n in counts.items() if s not in STATUSES]
        quick = sum(companies.on_quicklist(r) for r in self.rows)
        visible = len(self.tree.get_children())
        self.summary.configure(text=f"{visible} of {len(self.rows)} shown   ·   {quick} on the quicklist   ·   "
                                    + "  ·  ".join(parts))

    def _sort(self, column: str) -> None:
        self.sort_reverse = (not self.sort_reverse if self.sort_column == column
                             else column in self.COUNT_COLUMNS)
        self.sort_column = column
        for col, _ in self.COLUMNS:
            arrow = (" ▼" if self.sort_reverse else " ▲") if col == column else ""
            self.tree.heading(col, text=col + arrow)
        self._fill_tree()

    def show_company(self, name: str) -> None:
        """Select a company (from the quicklist's 'Edit...'), clearing search/filter if they hide it."""
        row = self.model.find(name)
        if row is None:
            return
        self._commit()
        self.current = self.rows.index(row)
        if not self._visible(row):
            self.search.set("")
            if row["Status"] in self.status_filter.hidden:
                self.status_filter._set_hidden(set())
        self._fill_tree()
        self._show_details(self.current)

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
        r = self.rows[index] if index is not None and index < len(self.rows) else None
        state = "normal" if r else "disabled"
        self.d_title.configure(text=shown(r["Company"]) if r else "Choose a company")
        self.d_interesting.set(companies.is_interesting(r) if r else False)
        for field, w in self.entries.items():
            w.configure(state="normal")
            w.delete(0, "end")
            w.insert(0, r.get(field, "") if r else "")
            w.configure(state=state)
        self._update_links()
        self.d_status.set(r["Status"] if r else "")
        values = STATUSES + ([r["Status"]] if r and r["Status"] and r["Status"] not in STATUSES else [])
        self.status_box.configure(values=values, state="readonly" if r else "disabled")
        self.d_date.configure(state="normal")
        self.d_date.delete(0, "end")
        self.d_date.insert(0, r["Last contact"] if r else "")
        self.d_date.configure(state=state)
        for box, field in ((self.d_notes, "Notes"), (self.d_past, "Past postings")):
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", r.get(field, "") if r else "")
            box.edit_reset()
            box.configure(state=state)
        self._past_tab_label()
        for b in (self.rename_btn, self.interest_box, self.today_btn, self.delete_btn):
            b.configure(state=state)
        self.open_btn.configure(state=state if self.on_open else "disabled")
        self._fill_postings()

    def _text_field(self, tabs: ttk.Notebook, label: str, help_text: str = "") -> tk.Text:
        """A tab with a multi-line text box, saved when it loses focus."""
        page = ttk.Frame(tabs, style="Card.TFrame", padding=(0, 6, 0, 0))
        tabs.add(page, text=label)
        if help_text:
            ttk.Label(page, text=help_text, style="Help.Card.TLabel").pack(anchor="w", pady=(0, 4))
        box = tk.Frame(page, background=appearance.COLORS["field"], highlightthickness=1,
                       highlightbackground=self.colors["border"], highlightcolor=self.colors["border"])
        box.pack(fill="both", expand=True)
        text = tk.Text(box, wrap="word", undo=True, font=self.fonts["body"], height=4, padx=8, pady=6,
                       relief="flat", borderwidth=0, highlightthickness=0, background=appearance.COLORS["field"],
                       foreground=self.colors["ink"], insertbackground=self.colors["ink"])
        scroll = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.bind("<FocusIn>", lambda e: box.configure(highlightbackground=self.colors["accent"]))
        text.bind("<FocusOut>", lambda e: (box.configure(highlightbackground=self.colors["border"]),
                                           self._commit()))
        return text

    def _past_tab_label(self) -> None:
        """'Past postings (n)' on the tab, counted from what is in the box now."""
        n = companies.count_past_postings({"Past postings": self.d_past.get("1.0", "end-1c")})
        self.text_tabs.tab(2, text=f"Past postings ({n})" if n else "Past postings")

    # ---- job postings of the company in the panel
    def _selected_posting(self) -> dict[str, str] | None:
        sel = self.p_tree.selection()
        postings = self.model.postings(self._row())
        return postings[int(sel[0])] if sel and int(sel[0]) < len(postings) else None

    def _fill_postings(self, select: str | None = None) -> None:
        """Rebuild the postings table (also after a posting text is saved somewhere)."""
        if select is None:
            p = self._selected_posting()
            select = p["title"] if p else None
        self.p_tree.delete(*self.p_tree.get_children())
        r = self._row()
        for i, p in enumerate(self.model.postings(r)):
            has = companies.has_posting_text(r["Company"], p["title"])
            self.p_tree.insert("", "end", iid=str(i), tags=("saved" if has else "missing",),
                               values=(shown(p["title"]), "saved" if has else "not created",
                                       p["url"]))
            if p["title"] == select:
                self.p_tree.selection_set(str(i))
        n = len(self.model.postings(r))
        self.text_tabs.tab(0, text=f"Job postings ({n})" if n else "Job postings")
        self._posting_buttons()

    def _posting_buttons(self) -> None:
        p = self._selected_posting()
        self.p_link.set(p["url"] if p else "")
        self.posting_btns["add"].configure(state="normal" if self._row() else "disabled")
        for key in ("edit", "remove", "text"):
            self.posting_btns[key].configure(state="normal" if p else "disabled")
        self.open_btn.configure(text="Work on this posting in Documents" if p else "Work on it in Documents")

    def _posting_click(self, e) -> None:
        """A click on the URL in the Link column opens the posting's web page."""
        iid = self.p_tree.identify_row(e.y)
        if iid and self.p_tree.identify_column(e.x) == "#3":
            postings = self.model.postings(self._row())
            if int(iid) < len(postings) and postings[int(iid)]["url"]:
                webbrowser.open(postings[int(iid)]["url"])

    def _add_posting(self) -> None:
        r = self._row()
        if r is None:
            return
        self._commit()
        value = ask_posting(self, self.model, r)
        if value is None:
            return
        self.model.add_posting(r, *value)
        self._save()
        self._fill_postings(select=value[0])
        self._update_row()

    def _edit_posting(self) -> None:
        r, p = self._row(), self._selected_posting()
        if r is None or p is None:
            return
        self._commit()
        value = ask_posting(self, self.model, r, p["title"], p["url"], heading="Edit job posting")
        if value is None or value == (p["title"], p["url"]):
            return
        try:
            self.model.edit_posting(r, p["title"], *value)
        except OSError as e:
            messagebox.showerror("Edit job posting", f"Could not move the posting's folder:\n{e}", parent=self)
        PostingTextWindow.renamed(r["Company"], r["Company"], p["title"], value[0])
        self._save()
        self._fill_postings(select=value[0])
        self._update_row()

    def _remove_posting(self) -> None:
        r, p = self._row(), self._selected_posting()
        if r is None or p is None:
            return
        folder = companies.posting_folder(r["Company"], p["title"])
        extra = (f"\n\nIts documents in output/{folder.parent.name}/{folder.name}/ are kept."
                 if folder.is_dir() else "")
        if not messagebox.askyesno("Remove job posting", f"Remove the posting '{p['title']}' from "
                                                         f"{r['Company']}?{extra}", parent=self):
            return
        self._commit(save=False)
        self.model.remove_posting(r, p["title"])
        self._save()
        self._fill_postings()
        self._update_row()

    def _posting_text(self) -> None:
        r, p = self._row(), self._selected_posting()
        if r is not None and p is not None:
            PostingTextWindow.show(self, self.model, self.colors, self.fonts, r["Company"], p["title"])

    def _row(self) -> dict[str, str] | None:
        return self.rows[self.current] if self.current is not None and self.current < len(self.rows) else None

    def _commit(self, save: bool = True) -> bool:
        """Copy the detail panel into the current row; save if anything changed. True if it changed."""
        r = self._row()
        if r is None:
            return False
        new = {field: w.get().strip() for field, w in self.entries.items()}
        new["Last contact"] = self.d_date.get().strip()
        new["Notes"] = self.d_notes.get("1.0", "end-1c").strip()
        new["Past postings"] = self.d_past.get("1.0", "end-1c").strip()
        if all(r.get(k, "") == v for k, v in new.items()):
            return False
        r.update(new)
        self._update_links()
        self._update_row()
        if save:
            self._save()
        return True

    def _status_changed(self) -> None:
        r = self._row()
        if r is None:
            return
        self._commit(save=False)
        self.model.set_status(r, self.d_status.get())
        self.d_date.delete(0, "end")
        self.d_date.insert(0, r["Last contact"])
        self._update_row()
        self._save()

    def _interest_changed(self) -> None:
        r = self._row()
        if r is None:
            return
        self._commit(save=False)
        r["Interesting"] = "yes" if self.d_interesting.get() else ""
        self._update_row()
        self._save()

    def _today(self) -> None:
        self.d_date.delete(0, "end")
        self.d_date.insert(0, datetime.date.today().isoformat())
        self._commit()

    def _update_links(self) -> None:
        for field, link in self.links.items():
            link.set(self.entries[field].get())

    def _open(self) -> None:
        r = self._row()
        if r is not None and self.on_open:
            p = self._selected_posting()
            self._commit()
            self.on_open(r["Company"], p["title"] if p else None)

    # ---- add / rename / delete
    def _ask_name(self, title: str, initial: str = "") -> str | None:
        name = simpledialog.askstring(title, "Company name:", initialvalue=initial, parent=self)
        name = (name or "").strip()
        if not name:
            return None
        if not companies.folder_name(name):
            messagebox.showwarning(title, "The name needs at least one letter or digit.", parent=self)
            return None
        other = self.model.find(name) or next(
            (r for r in self.rows if companies.folder_name(r["Company"]) == companies.folder_name(name)), None)
        if other is not None and other is not self._row():
            messagebox.showwarning(title, f"'{other['Company']}' is already in the list.", parent=self)
            self.show_company(other["Company"])
            return None
        return name

    def _add(self) -> None:
        self._commit()
        name = self._ask_name("Add company")
        if name is None:
            return
        row = self.model.add(name)
        self.current = self.rows.index(row)
        self.search.set("")
        self.status_filter.hidden.discard(row["Status"])
        self._fill_tree()
        self._show_details(self.current)
        self._save()
        self.entries["Fit"].focus_set()

    def _rename(self) -> None:
        r = self._row()
        if r is None:
            return
        self._commit()
        name = self._ask_name("Rename company", r["Company"])
        if name is None or name == r["Company"]:
            return
        PostingTextWindow.renamed(r["Company"], name)
        try:
            self.model.rename(r, name)
        except OSError as e:
            messagebox.showerror("Rename company", f"Could not move the document folder:\n{e}", parent=self)
        self._update_row()
        self.d_title.configure(text=shown(name))
        self._save()

    def _delete(self) -> None:
        r = self._row()
        if r is None:
            return
        folder = companies.company_folder(r["Company"])
        extra = (f"\n\nIts documents in output/{folder.name}/ are kept." if folder.is_dir() else "")
        if not messagebox.askyesno("Delete company", f"Remove '{r['Company']}' from the company list?{extra}",
                                   parent=self):
            return
        self.model.remove(r)
        self.current = None
        self._fill_tree()
        self._show_details(None)
        self._save()
