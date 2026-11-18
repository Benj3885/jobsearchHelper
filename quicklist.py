"""Quicklist of interesting companies: a compact block in the left column of the Documents tab.

  Company [No company (general)  v]   dropdown of companies on the quicklist (CompanyList.quicklist)
  Posting [Whole company  v] [+]      optional job posting: its documents go in output/<company>/<posting>/
  [E][A][CV] [P]                      documents of the chosen company/posting: pale = nothing saved,
                                      grey = saved, green = marked ready. Clicking one opens it.
                                      P = the posting's text (green = saved); opens its window.
  [Mark applied] [Undo applied v] [Edit...]
  details                             contact, email (click to copy), notes, link ...

"Mark applied" removes the company from the dropdown; "Undo applied" lists the last 5 applied.
"""

from __future__ import annotations

import datetime
import webbrowser
import tkinter as tk
from tkinter import ttk

import companies
from tabs import PostingTextWindow, ask_posting, shown

ICONS = [("email", "E"), ("application", "A"), ("cv", "CV")]
ICON_W, ICON_H, ICON_GAP = 26, 20, 4
ICON_COLORS = {             # state -> (fill, outline, text)
    "none": ("#F4F6F9", "#DDE2E9", "#BCC4CF"),
    "saved": ("#E3E7ED", "#AEB7C4", "#4F5A6A"),
    "ready": ("#2E7D32", "#2E7D32", "#FFFFFF"),
}
ICON_NAMES = {"email": "Email", "application": "Application", "cv": "CV"}
STATE_WORDS = {"none": "not saved", "saved": "saved", "ready": "ready"}
NO_COMPANY = "No company (general)"
NO_POSTING = "Whole company (no posting)"
NO_TEXT = "   (no posting text)"
POSTING_X = len(ICONS) * (ICON_W + ICON_GAP) + 8      # where the P icon starts


class Quicklist(ttk.Frame):
    """`on_select(name or None, doc_type or None, posting or None) -> bool` asks the editor to switch
    company/posting; it calls `set_active` itself when the switch happens. `on_edit(name)` shows the
    company in the Companies tab."""

    def __init__(self, parent, model: companies.CompanyList, colors, fonts, on_select, on_edit) -> None:
        super().__init__(parent, style="Card.TFrame", padding=(12, 12))
        self.model, self.colors, self.fonts = model, colors, fonts
        self.on_select, self.on_edit = on_select, on_edit
        self.active: str | None = None
        self.posting: str | None = None                     # job posting of the active company, or None
        self.choices: dict[str, str | None] = {}           # text in the dropdown -> company name
        self.posting_choices: dict[str, str | None] = {}   # text in the posting dropdown -> title
        self.states = {k: "none" for k, _ in ICONS}
        model.listeners.append(lambda source: self.refresh())
        model.doc_listeners.append(self.update_icons)

        head = ttk.Frame(self, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Company", style="Help.Card.TLabel").pack(side="left")
        self.count = ttk.Label(head, style="Help.Card.TLabel")
        self.count.pack(side="right")
        self.choice = tk.StringVar(value=NO_COMPANY)
        self.box = ttk.Combobox(self, textvariable=self.choice, state="readonly")
        self.box.pack(fill="x", pady=(6, 0))
        self.box.bind("<<ComboboxSelected>>", lambda e: self._chosen())

        ttk.Label(self, text="Job posting", style="Help.Card.TLabel").pack(anchor="w", pady=(8, 0))
        prow = ttk.Frame(self, style="Card.TFrame")
        prow.pack(fill="x", pady=(4, 0))
        self.posting_choice = tk.StringVar(value=NO_POSTING)
        self.posting_box = ttk.Combobox(prow, textvariable=self.posting_choice, state="readonly")
        self.posting_box.pack(side="left", fill="x", expand=True)
        self.posting_box.bind("<<ComboboxSelected>>", lambda e: self._posting_chosen())
        self.add_posting_btn = ttk.Button(prow, text="+", width=3, command=self._add_posting)
        self.add_posting_btn.pack(side="left", padx=(4, 0))

        row = ttk.Frame(self, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        self.icons = tk.Canvas(row, width=POSTING_X + ICON_W + 2, height=ICON_H + 2,
                               highlightthickness=0, background=colors["card"], cursor="hand2")
        self.icons.pack(side="left")
        self.icons.bind("<Button-1>", self._icon_click)
        self.icon_note = ttk.Label(row, style="Help.Card.TLabel")
        self.icon_note.pack(side="left", padx=(6, 0))

        actions = ttk.Frame(self, style="Card.TFrame")
        actions.pack(fill="x", pady=(8, 0))
        self.applied_btn = ttk.Button(actions, text="Mark applied", padding=(8, 3), command=self._mark_applied)
        self.applied_btn.pack(side="left")
        self.undo_btn = ttk.Menubutton(actions, text="Undo applied", padding=(8, 3))
        self.undo_menu = tk.Menu(self.undo_btn, tearoff=False, font=fonts["body"])
        self.undo_btn.configure(menu=self.undo_menu)
        self.undo_btn.pack(side="left", padx=(4, 0))
        self.edit_btn = ttk.Button(actions, text="Edit...", padding=(8, 3),
                                   command=lambda: self.active and self.on_edit(self.active))
        self.edit_btn.pack(side="right")

        # Details of the active company (read-only; edited in the Companies tab).
        self.info = tk.Text(self, height=4, wrap="word", relief="flat", borderwidth=0, highlightthickness=0,
                            background=colors["card"], foreground=colors["ink"], font=fonts["small"],
                            padx=0, pady=4, cursor="arrow")
        self.info.pack(fill="x", pady=(6, 0))
        self.info.tag_configure("label", foreground=colors["muted"])
        self.info.tag_configure("link", foreground=colors["accent"], underline=True)
        self.info.tag_bind("link", "<Enter>", lambda e: self.info.configure(cursor="hand2"))
        self.info.tag_bind("link", "<Leave>", lambda e: self.info.configure(cursor="arrow"))
        self.info.tag_bind("email", "<Button-1>", lambda e: self._copy_email())
        self.refresh()

    # ---- dropdown
    def refresh(self) -> None:
        """Rebuild the dropdown from the model, and the icons from the files in output/."""
        items = self.model.quicklist()
        self.choices = {NO_COMPANY: None}
        for r in items:
            self.choices[shown(r["Company"])] = r["Company"]
        self.box.configure(values=list(self.choices))
        self.count.configure(text=f"{len(items)} on the quicklist" if items
                             else "tick 'Quicklist' in the Companies tab")
        self._show_choice()
        self.update_icons()
        self._fill_undo()

    def _show_choice(self) -> None:
        self.choice.set(next((text for text, name in self.choices.items() if name == self.active),
                             shown(self.active) if self.active else NO_COMPANY))
        self._fill_postings()

    def _fill_postings(self) -> None:
        """Posting dropdown for the active company; titles without a saved posting text are marked."""
        self.posting_choices = {NO_POSTING: None}
        for p in self.model.postings(self.model.find(self.active)):
            text = shown(p["title"]) + ("" if companies.has_posting_text(self.active, p["title"]) else NO_TEXT)
            self.posting_choices.setdefault(text, p["title"])
        self.posting_box.configure(values=list(self.posting_choices),
                                   state="readonly" if self.active and len(self.posting_choices) > 1 else "disabled")
        self.add_posting_btn.configure(state="normal" if self.model.find(self.active) else "disabled")
        self.posting_choice.set(next((t for t, title in self.posting_choices.items() if title == self.posting),
                                     shown(self.posting) if self.posting else NO_POSTING))

    def _chosen(self) -> None:
        name = self.choices.get(self.choice.get())
        if not self.on_select(name, None, None):  # switch refused (unsaved changes): show the old one
            self._show_choice()

    def _posting_chosen(self) -> None:
        title = self.posting_choices.get(self.posting_choice.get())
        if not self.on_select(self.active, None, title):
            self._fill_postings()

    def _add_posting(self) -> None:
        """New posting for the active company (the Companies tab can edit it later), then work on it."""
        row = self.model.find(self.active)
        if row is None:
            return
        value = ask_posting(self, self.model, row)
        if value is None:
            return
        self.model.add_posting(row, *value)
        self.model.save(source=self)
        self.on_select(self.active, None, value[0])

    def set_active(self, name: str | None, posting: str | None = None) -> None:
        self.active, self.posting = name, (posting if name else None)
        self._show_choice()
        self.update_icons()

    # ---- document icons
    def update_icons(self) -> None:
        """Re-read which documents exist / are ready (after a save or a 'Mark ready')."""
        self.states = (companies.document_states(self.active, self.posting) if self.active
                       else {k: "none" for k, _ in ICONS})
        has_text = bool(self.posting) and companies.has_posting_text(self.active, self.posting)
        c = self.icons
        c.delete("all")
        for i, (doc_type, letter) in enumerate(ICONS):
            fill, outline, ink = ICON_COLORS[self.states[doc_type]]
            if not self.active:
                fill, outline, ink = ICON_COLORS["none"]
            x = i * (ICON_W + ICON_GAP)
            c.create_rectangle(x + 1, 1, x + ICON_W, ICON_H, fill=fill, outline=outline)
            c.create_text(x + ICON_W // 2 + 1, ICON_H // 2 + 1, text=letter, fill=ink, font=self.fonts["small"])
        if self.posting:              # the posting's text: pale = not created, green = saved
            fill, outline, ink = ICON_COLORS["ready" if has_text else "none"]
            c.create_rectangle(POSTING_X + 1, 1, POSTING_X + ICON_W, ICON_H, fill=fill, outline=outline)
            c.create_text(POSTING_X + ICON_W // 2 + 1, ICON_H // 2 + 1, text="P", fill=ink, font=self.fonts["small"])
        ready = sum(v == "ready" for v in self.states.values())
        note = f"{ready} of 3 ready" if self.active else "saved straight in output/"
        if self.posting:
            note += "" if has_text else ", no posting text"
        self.icon_note.configure(text=note, foreground=self.colors["muted"])
        self._fill_postings()
        self._show_info()

    def _icon_click(self, e) -> None:
        if e.x >= POSTING_X:
            if self.posting:
                self.open_posting_text()
            return
        index = min(len(ICONS) - 1, max(0, e.x // (ICON_W + ICON_GAP)))
        self.on_select(self.active, ICONS[index][0], self.posting)

    def open_posting_text(self) -> None:
        if self.active and self.posting:
            PostingTextWindow.show(self, self.model, self.colors, self.fonts, self.active, self.posting)

    # ---- details
    def _show_info(self) -> None:
        t = self.info
        t.configure(state="normal")
        t.delete("1.0", "end")
        r = self.model.find(self.active)
        self.applied_btn.configure(state="normal" if r is not None and companies.on_quicklist(r) else "disabled")
        self.edit_btn.configure(state="normal" if r is not None else "disabled")
        if r is None:
            t.insert("end", "Pick a company to keep its documents together in output/<company>/.", ("label",))
            t.configure(state="disabled", height=2)
            return

        def line(label: str, value: str, *tags: str) -> None:
            t.insert("end", f"{label}: ", ("label",))
            t.insert("end", shown(value) if value else "-", tags if value else ("label",))
            t.insert("end", "\n")

        place = "   ·   ".join(v for v in (r.get("Town", ""), r.get("Fit", "") and f"Fit {r['Fit']}",
                                          r.get("Track", "")) if v)
        if place:
            t.insert("end", shown(place) + "\n", ("label",))
        line("Contact", r.get("Contact person", ""))
        line("Email", r.get("Email", ""), "link", "email")
        line("Status", r["Status"] + (f" (last contact {r['Last contact']})" if r["Last contact"] else ""))
        posting = next((p for p in self.model.postings(r) if p["title"] == self.posting), None)
        links = [("Posting", posting["url"] if posting else ""), ("Homepage", r.get("Homepage", "")),
                 ("Source", r.get("Source", ""))]
        for label, url in links:
            if url.startswith(("http://", "https://")):
                t.insert("end", f"{label}: ", ("label",))
                t.insert("end", "open link", ("link", f"url-{label}"))
                t.insert("end", "\n")
                t.tag_bind(f"url-{label}", "<Button-1>", lambda e, u=url: webbrowser.open(u))
        t.insert("end", "Notes: ", ("label",))
        t.insert("end", shown(r["Notes"]) if r["Notes"] else "-", () if r["Notes"] else ("label",))
        t.configure(state="disabled", height=min(11, max(4, int(t.count("1.0", "end", "displaylines")[0]))))

    def _copy_email(self) -> None:
        r = self.model.find(self.active)
        if r and r.get("Email"):
            self.clipboard_clear()
            self.clipboard_append(r["Email"])
            self.info.configure(state="normal")
            self.info.insert("end", "\n(email copied)", ("label",))
            self.info.configure(state="disabled")

    # ---- applied
    def _mark_applied(self) -> None:
        r = self.model.find(self.active)
        if r is not None:
            self.model.set_status(r, "Applied")
            self.model.save(source=self)

    def _undo(self, name: str) -> None:
        r = self.model.find(name)
        if r is not None and r["Status"] == "Applied":
            self.model.undo_applied(r)
            self.model.save(source=self)

    def _fill_undo(self) -> None:
        self.undo_menu.delete(0, "end")
        recent = self.model.recently_applied(5)
        for r in recent:
            when = _short_date(r["Status changed"][:10] or r["Last contact"])
            self.undo_menu.add_command(label=f"{shown(r['Company'])}   ({when})" if when else shown(r["Company"]),
                                       command=lambda n=r["Company"]: self._undo(n))
        self.undo_btn.configure(state="normal" if recent else "disabled")


def _short_date(iso: str) -> str:
    try:
        d = datetime.date.fromisoformat(iso)
    except ValueError:
        return iso
    return "today" if d == datetime.date.today() else f"{d.day}/{d.month}"
