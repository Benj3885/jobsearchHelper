"""Desktop editor for job search documents: email, job application and CV.

Run with:  python cv_editor.py      (needs a Python with tkinter, e.g. the conda one)

- Pick the document type and the language (Dansk/English) on the left.
  Each type has its own boxes on the right.
- Default text comes from data/cv.yaml, data/application.yaml and data/email.yaml.
  Edits live in the window only; the YAML files are not changed.
- "Compile" builds a temporary file (build/work/_preview_<type>/) and shows it in the middle.
- "Save" copies it to output/: <name>_CV.pdf, <name>_APPLICATION.pdf or <name>_EMAIL.txt,
  asking first if the file exists. PDFs carry the box contents as an attachment, so
  "Import..." can fill the boxes again from a saved PDF (or from an email .txt).
- Email and application start from a template: "Default" (data/email.yaml, data/application.yaml)
  or a named one (data/<type>_templates.yaml), picked, saved, created, renamed and deleted in the
  boxes panel. Saving writes the boxes back to the YAML file (backup in <file>.yaml.bak).
- CV: "Save as default" writes the boxes back to data/cv.yaml and data/personal.yaml.
- The quicklist on the left (quicklist.py) picks a company to work on. Its documents are saved in
  output/<company>/, and picking it again loads them back (boxes and preview). Saving there is
  never blocked; placeholders in required fields only block "Mark ready".
  A job posting of the company can be picked too: its documents go in output/<company>/<posting>/.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

import build_cv
import companies
import documents
import quicklist
import tabs

ROOT = build_cv.ROOT
OUTPUT = ROOT / "output"
TEMPLATES = ["modern", "merged", "sidebar"]         # CV styles whose fields match the CV boxes

# Box definitions per document type: (key, label, help text, height in lines; 1 = single-line entry)
CV_FIELDS = [
    ("name", "Name", "", 1),
    ("headline", "Headline", "Shown under the name (modern template).", 1),
    ("title", "Title", "Big title above the body (sidebar template).", 1),
    ("location", "Location", "", 1),
    ("phone", "Phone", "", 1),
    ("email", "Email", "", 1),
    ("skills", "Skills", "One per line. Group with 'Group: a, b, c'.", 8),
    ("timeline", "Experiences", "One per line: 'period | text'. Oldest first. "
                                "'- ' lines below an entry are its details (merged template).", 10),
    ("body", "Body", "'## Heading' starts a section. Blank line between paragraphs.", 16),
    ("languages", "Languages", "One per line.", 2),
    ("references", "References", "One per line.", 2),
]
FIELDS = CV_FIELDS   # kept for older callers

APPLICATION_FIELDS = [
    ("company", "Company", "Name, phone, email and location are taken from the CV boxes.", 1),
    ("recipient", "Recipient", "Contact person, shown as 'Att.' (Danish) or 'Attn.' (English). Leave empty to omit.", 1),
    ("subject", "Subject", "", 1),
    ("body", "Body", "Blank line between paragraphs. '- ' starts a bullet. **bold** for bold.", 22),
    ("closing", "Closing", "Shown above your name.", 1),
]

EMAIL_FIELDS = [
    ("to", "To", "Optional. Shown at the top of the .txt file.", 1),
    ("subject", "Subject", "", 1),
    ("body", "Body", "Plain text, saved exactly as written.", 24),
]

# Required fields: saving is blocked while they still contain a [placeholder].
# Replacing the placeholder or emptying the field makes the document saveable. Compiling always works.
REQUIRED = {
    "email": {"subject", "body"},
    "application": {"company", "recipient", "subject", "body"},
    "cv": set(),
}
PLACEHOLDER = re.compile(r"\[[^\[\]\n]+\]")


def placeholder_problems(form: dict[str, str], required: set[str]) -> list[str]:
    """Keys of required fields that still contain a [placeholder]."""
    return [key for key in form if key in required and PLACEHOLDER.search(form[key])]


def render_pages(pdf: Path, outdir: Path, width: int) -> None:
    """PDF -> outdir/page-<n>.png, scaled to `width` pixels."""
    for old in outdir.glob("page-*.png"):
        old.unlink()
    subprocess.run(["pdftoppm", "-png", "-scale-to-x", str(width), "-scale-to-y", "-1",
                    str(pdf), str(outdir / "page")], check=True)


# key, button label, file suffix, extension, fields
MODES = [
    ("email", "Email", "_EMAIL", ".txt", EMAIL_FIELDS),
    ("application", "Application", "_APPLICATION", ".pdf", APPLICATION_FIELDS),
    ("cv", "CV", "_CV", ".pdf", CV_FIELDS),
]
DEFAULT_FILE_NAME = ""
# Saved files: <PREFIX>_<name>_CV.pdf. The prefix comes from data/personal.yaml (e.g. JANE_DOE).


def output_file_name(prefix: str, name: str, suffix: str, ext: str) -> str:
    """('JANE_DOE', 'Trackunit') -> 'JANE_DOE_Trackunit_CV.pdf'. Prefix, suffix and extension are never doubled."""
    name = re.sub(re.escape(ext) + "$", "", name.strip(), flags=re.IGNORECASE)
    name = re.sub(re.escape(suffix) + "$", "", name, flags=re.IGNORECASE)
    name = re.sub("^" + re.escape(prefix) + "_?", "", name, flags=re.IGNORECASE).strip("_ ")
    return "_".join(part for part in (prefix, name) if part) + suffix + ext


def _rel(path: Path) -> str:
    """Path for messages: relative to the project folder when it is inside it."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def name_from_company(company: str) -> str:
    """'Trackunit A/S' -> 'Trackunit_AS' (usable in a file name). Empty if it is a [placeholder]."""
    if PLACEHOLDER.search(company):
        return ""
    return companies.folder_name(company)


# --------------------------------------------------------------------------- cv.yaml <-> boxes

def cv_to_form(cv: dict, track: str) -> dict[str, str]:
    t = cv["tracks"][track]
    side = cv.get("sidebar", {})
    skills = []
    for s in side.get("skills", []):
        skills.append(f"{s['name']}: {', '.join(s['items'])}" if isinstance(s, dict) else s)
    sections = t.get("main_sections", cv.get("main_sections", []))
    body = "\n\n".join(
        f"## {sec['heading']}\n\n" + "\n\n".join(" ".join(p.split()) for p in sec["paragraphs"])
        for sec in sections
    )
    return {
        "name": cv["name"],
        "headline": t["headline"],
        "title": t.get("cv_title", t["headline"]),
        "location": cv["contact"].get("location", ""),
        "phone": cv["contact"].get("phone", ""),
        "email": cv["contact"].get("email", ""),
        "skills": "\n".join(skills),
        "timeline": "\n".join(
            "\n".join([f"{x['period']} | {x['text']}"] + [f"- {' '.join(d.split())}" for d in x.get("details", [])])
            for x in side.get("timeline", [])),
        "body": body,
        "languages": "\n".join(side.get("languages", [])),
        "references": "\n".join(side.get("references", [])),
    }


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def form_to_cv(base: dict, track: str, f: dict[str, str]) -> dict:
    cv = copy.deepcopy(base)
    cv["name"] = f["name"]
    cv["contact"].update(location=f["location"], phone=f["phone"], email=f["email"])
    t = cv["tracks"][track]
    t["headline"] = f["headline"]
    t["cv_title"] = f["title"]

    skills = []
    for line in _lines(f["skills"]):
        if ":" in line:
            name, items = line.split(":", 1)
            skills.append({"name": name.strip(), "items": [i.strip() for i in items.split(",") if i.strip()]})
        else:
            skills.append(line)

    timeline = []
    for line in _lines(f["timeline"]):
        if line.startswith("-"):                    # detail paragraph for the entry above
            if not timeline:
                timeline.append({"period": "", "text": ""})
            timeline[-1].setdefault("details", []).append(line.lstrip("-").strip())
            continue
        period, _, text = line.partition("|")
        timeline.append({"period": period.strip(), "text": text.strip()})

    sections, current = [], None
    for block in re.split(r"\n\s*\n", f["body"].strip()):
        block = block.strip()
        if not block:
            continue
        if block.startswith("##"):
            heading, _, rest = block.partition("\n")
            current = {"heading": heading.lstrip("#").strip(), "paragraphs": []}
            sections.append(current)
            block = rest.strip()
            if not block:
                continue
        if current is None:
            current = {"heading": "", "paragraphs": []}
            sections.append(current)
        current["paragraphs"].append(" ".join(block.split()))

    cv["sidebar"] = {
        **cv.get("sidebar", {}),                    # keeps keys without a box, e.g. merged_replaces
        "skills": skills,
        "timeline": timeline,
        "languages": _lines(f["languages"]),
        "references": _lines(f["references"]),
    }
    cv["main_sections"] = sections
    t.pop("main_sections", None)
    return cv


# --------------------------------------------------------------------------- look & feel

COLORS = {
    "bg": "#EEF1F5",          # window background
    "card": "#FFFFFF",        # panels
    "border": "#D5DCE5",
    "ink": "#1E2530",
    "muted": "#6B7686",
    "accent": "#1F5FA8",
    "accent_hover": "#174C87",
    "hover": "#F1F4F8",
    "preview_bg": "#E3E7ED",
    "page_shadow": "#C3CAD4",
    "error": "#C62828",
}
# First installed family wins. Tk builds without Xft only see X11 core fonts.
FONT_CANDIDATES = ["Inter", "Lato", "Ubuntu", "Noto Sans", "DejaVu Sans", "nimbus sans l", "helvetica"]


def setup_style(root: tk.Tk) -> dict[str, tuple]:
    import tkinter.font as tkfont

    families = {f.lower(): f for f in tkfont.families(root)}
    family = next((families[c.lower()] for c in FONT_CANDIDATES if c.lower() in families), "TkDefaultFont")
    fonts = {
        "body": (family, 10),
        "small": (family, 9),
        "bold": (family, 10, "bold"),
        "title": (family, 13, "bold"),
    }
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name).configure(family=family, size=10)

    c = COLORS
    root.configure(background=c["bg"])
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=c["bg"], foreground=c["ink"], font=fonts["body"],
                bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                troughcolor=c["bg"], focuscolor=c["accent"])
    s.configure("TFrame", background=c["bg"])
    s.configure("Card.TFrame", background=c["card"])
    s.configure("TLabel", background=c["bg"], foreground=c["ink"])
    s.configure("Card.TLabel", background=c["card"])
    s.configure("Field.Card.TLabel", background=c["card"], font=fonts["bold"])
    s.configure("Help.Card.TLabel", background=c["card"], foreground=c["muted"], font=fonts["small"])
    s.configure("Title.Card.TLabel", background=c["card"], font=fonts["title"])
    s.configure("Status.Card.TLabel", background=c["card"], foreground=c["muted"], font=fonts["small"])

    # Buttons: filled accent for the main action, outlined for the rest.
    s.configure("Accent.TButton", background=c["accent"], foreground="#FFFFFF", font=fonts["bold"],
                borderwidth=0, padding=(18, 7), lightcolor=c["accent"], darkcolor=c["accent"])
    s.map("Accent.TButton",
          background=[("disabled", "#9DB3CF"), ("pressed", c["accent_hover"]), ("active", c["accent_hover"])],
          lightcolor=[("active", c["accent_hover"])], darkcolor=[("active", c["accent_hover"])])
    s.configure("TButton", background=c["card"], foreground=c["ink"], padding=(16, 6), borderwidth=1,
                bordercolor=c["border"], lightcolor=c["card"], darkcolor=c["card"])
    s.map("TButton",
          background=[("disabled", c["card"]), ("pressed", c["hover"]), ("active", c["hover"])],
          foreground=[("disabled", "#A9B2BE")],
          bordercolor=[("active", c["accent"])])

    s.configure("TEntry", fieldbackground="#FFFFFF", padding=(8, 5), insertcolor=c["ink"])
    s.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])])
    s.configure("TCombobox", fieldbackground="#FFFFFF", background=c["card"], padding=(8, 4), arrowcolor=c["muted"])
    s.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")], bordercolor=[("focus", c["accent"])],
          selectbackground=[("readonly", "#FFFFFF")], selectforeground=[("readonly", c["ink"])])
    root.option_add("*TCombobox*Listbox.font", fonts["body"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])

    s.configure("Vertical.TScrollbar", background="#C9D0DA", troughcolor=c["card"], bordercolor=c["card"],
                lightcolor="#C9D0DA", darkcolor="#C9D0DA", arrowsize=0, gripcount=0)
    s.map("Vertical.TScrollbar", background=[("active", "#AEB7C4")])
    s.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    s.configure("TPanedwindow", background=c["bg"])
    s.configure("Sash", sashthickness=10, gripcount=0, background=c["bg"], lightcolor=c["bg"], darkcolor=c["bg"])
    # Segmented control (document type): outlined when off, filled when on.
    s.configure("Segment.TButton", background=c["card"], foreground=c["muted"], font=fonts["bold"],
                padding=(6, 7), borderwidth=1, bordercolor=c["border"], lightcolor=c["card"], darkcolor=c["card"])
    s.map("Segment.TButton", background=[("active", c["hover"])], foreground=[("active", c["ink"])])
    s.configure("SegmentOn.TButton", background=c["accent"], foreground="#FFFFFF", font=fonts["bold"],
                padding=(6, 7), borderwidth=1, bordercolor=c["accent"], lightcolor=c["accent"], darkcolor=c["accent"])
    s.map("SegmentOn.TButton", background=[("active", c["accent_hover"])])
    # Top-level tabs (Documents / Job plan / Companies).
    s.configure("TNotebook", background=c["bg"], borderwidth=0, bordercolor=c["border"],
                lightcolor=c["bg"], darkcolor=c["bg"], tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=c["bg"], foreground=c["muted"], font=fonts["bold"],
                padding=(20, 8), borderwidth=0, bordercolor=c["border"], lightcolor=c["bg"])
    s.map("TNotebook.Tab", background=[("selected", c["card"]), ("active", c["hover"])],
          foreground=[("selected", c["accent"]), ("active", c["ink"])],
          lightcolor=[("selected", c["accent"])], padding=[("selected", (20, 8))])
    s.configure("Required.Card.TLabel", background=c["card"], foreground=c["error"], font=fonts["bold"])
    s.configure("Error.Card.TLabel", background=c["card"], foreground=c["error"], font=fonts["small"])
    s.configure("Invalid.TEntry", fieldbackground="#FFFFFF", padding=(8, 5), bordercolor=c["error"],
                lightcolor=c["error"], darkcolor=c["error"])
    s.configure("Blocked.TButton", background=c["card"], foreground=c["error"], padding=(16, 6), borderwidth=1,
                bordercolor=c["error"], lightcolor=c["card"], darkcolor=c["card"])
    s.map("Blocked.TButton", background=[("active", "#FDF1F1")])
    return fonts


# --------------------------------------------------------------------------- window
#
#   [ Documents | Job plan | Companies ]     tabs; Job plan and Companies are in tabs.py
#
#   Documents:
#   ┌────────────────┬───────────────────────────────┬──────────────────────┐
#   │ [Email|Appl|CV]│ File name  [Compile] [Save]   │ options for the type │
#   │                │───────────────────────────────│ boxes for the type   │
#   │ (more later)   │ preview (PDF pages or text)   │                      │
#   └────────────────┴───────────────────────────────┴──────────────────────┘

class ModeState:
    """Everything that belongs to one document type."""

    def __init__(self, key: str, label: str, suffix: str, ext: str, fields: list) -> None:
        self.key, self.label, self.suffix, self.ext, self.fields = key, label, suffix, ext, fields
        self.widgets: dict[str, tk.Widget] = {}
        self.frame: ttk.Frame | None = None          # boxes on the right
        self.file_name = tk.StringVar(value=DEFAULT_FILE_NAME)
        self.status = "Edit the text and press Compile."
        self.loaded_defaults: dict[str, str] = {}     # what the boxes were last filled with
        self.baseline: dict[str, str] = {}            # boxes as last loaded or saved (unsaved-edits check)
        self.saved_form: dict[str, str] = {}          # content of saved_path
        self.saved_path: Path | None = None          # file in the company folder shown in the boxes
        self.required: set[str] = REQUIRED.get(key, set())
        self.labels = {k: lbl for k, lbl, *_ in fields}
        self.pending_form: dict[str, str] = {}        # form being compiled
        self.compiled_form: dict[str, str] = {}       # form behind state.result (what Save stores)
        self.result: Path | None = None              # last compiled temp file
        self.images: list[tk.PhotoImage] = []        # PDF preview pages
        self.rendered_width = 0                       # width the page images were rendered at
        self.preview_dir = build_cv.WORK / f"_preview_{key}"
        self.styles = (TEMPLATES if key == "cv" else
                       list(documents.APPLICATION_TEMPLATES) if key == "application" else [])
        self.style = tk.StringVar(value=self.styles[0] if self.styles else "")
        # Email/application: the template the boxes start from (documents.template_names)
        self.template = tk.StringVar(value=documents.DEFAULT_TEMPLATE)
        self.active_template = documents.DEFAULT_TEMPLATE   # the one the boxes were last filled from
        self.template_box: ttk.Combobox | None = None
        self.template_buttons: dict[str, ttk.Button] = {}
        self.template_info = tk.StringVar()

    @property
    def is_pdf(self) -> bool:
        return self.ext == ".pdf"


def remove_previews() -> None:
    """Delete the temporary preview folders (PDFs, page images and data with personal information)."""
    for d in build_cv.WORK.glob("_preview_*"):
        shutil.rmtree(d, ignore_errors=True)


class Editor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        remove_previews()          # left over if the editor crashed last time
        self.title("Job search editor")
        self.geometry("1560x920")
        self.minsize(1150, 650)
        self.fonts = setup_style(self)
        self.personal = build_cv.load_personal()
        self.file_prefix = build_cv.file_prefix(self.personal)
        self.base_cv = build_cv.load_cv(self.personal)
        self.modes = {m[0]: ModeState(*m) for m in MODES}
        self.mode = self.modes["cv"]
        self.model = companies.CompanyList()
        self.company: str | None = None          # company being worked on (quicklist); None = general
        self.posting: str | None = None          # job posting of that company; None = the whole company

        self.language = "da"
        self.track = tk.StringVar(value=next(iter(self.base_cv["tracks"])))
        self.status = tk.StringVar()
        self.suffix_label = tk.StringVar()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        self.documents_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        self.notebook.add(self.documents_tab, text="Documents")
        self.plan_tab = tabs.PlanTab(self.notebook, COLORS, self.fonts)
        self.notebook.add(self.plan_tab, text="Job plan")
        self.companies_tab = tabs.CompaniesTab(self.notebook, COLORS, self.fonts, self.model,
                                               on_open=self.open_company)
        self.notebook.add(self.companies_tab, text="Companies", padding=(0, 10, 0, 0))
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)

        panes = ttk.PanedWindow(self.documents_tab, orient="horizontal")
        panes.pack(fill="both", expand=True)
        panes.add(self._build_left(panes), weight=0)
        panes.add(self._build_preview(panes), weight=3)
        panes.add(self._build_right(panes), weight=2)
        panes.bind("<Configure>", self._initial_columns)

        self._load_cv_track()
        self._fill_defaults(self.modes["application"])
        self._fill_defaults(self.modes["email"])
        self._switch_mode("cv")
        self.model.listeners.append(self._model_changed)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self) -> None:
        if not self.plan_tab.confirm_close() or not tabs.PostingTextWindow.confirm_close_all():
            return
        self.companies_tab._commit()       # notes typed but not yet saved
        remove_previews()
        self.destroy()

    def _tab_changed(self, event) -> None:
        """Reload the plan / company list if the file was changed outside the editor."""
        self.companies_tab._commit()         # edits in its detail panel, before another tab changes the list
        tab = self.nametowidget(self.notebook.select())
        if tab is self.plan_tab:
            tab.refresh()
        else:
            self.companies_tab.refresh()

    # ---- company being worked on
    def open_company(self, name: str, posting: str | None = None) -> None:
        """From the Companies tab: put the company on the quicklist and work on it (or on one posting)."""
        row = self.model.find(name)
        if row is not None and not companies.is_interesting(row):
            row["Interesting"] = "yes"
            self.model.save(source=self)
        self.notebook.select(self.documents_tab)
        self.select_company(name, None, posting)

    def _edit_company(self, name: str) -> None:
        """From the quicklist: show the company in the Companies tab."""
        self.notebook.select(self.companies_tab)
        self.companies_tab.show_company(name)

    def _model_changed(self, source) -> None:
        """The company left the quicklist (applied, renamed, deleted...): stop working on it.
        The same for a posting that was renamed or removed: back to the whole company."""
        if self.company is not None and not any(r["Company"] == self.company for r in self.model.quicklist()):
            self.company = self.posting = None
            self._set_file_names("")
            msg = "No company selected: saving goes to output/ again."
        elif self.posting is not None and not any(
                p["title"] == self.posting for p in self.model.postings(self.model.find(self.company))):
            self.posting = None
            msg = (f"The posting was renamed or removed: saving goes to "
                   f"output/{companies.folder_name(self.company)}/ now.")
        else:
            return
        for state in self.modes.values():
            state.saved_path, state.saved_form = None, {}
        self.quicklist.set_active(self.company, None)
        self._update_company_ui()
        self._set_status(self.mode, msg)

    def _unsaved(self) -> list[ModeState]:
        return [st for st in self.modes.values() if st.baseline and self._form(st) != st.baseline]

    def _where(self) -> str:
        """The folder documents are saved in, for messages: 'output/<company>/[<posting>/]'."""
        return _rel(companies.doc_folder(self.company, self.posting)) + "/" if self.company else "output/"

    def select_company(self, name: str | None, doc_type: str | None = None, posting: str | None = None) -> bool:
        """Work on `name` (None = no company), or on one of its job postings. Loads the saved documents
        into the boxes and the preview; types without a saved document get the default text with the
        company filled in."""
        posting = posting if name else None
        if name == self.company and posting == self.posting:
            if doc_type:
                self._switch_mode(doc_type)
            return True
        if name is not None:
            changed = self._unsaved()
            what = f"'{posting}' at {name}" if posting else name
            if changed and not messagebox.askyesno(
                    "Unsaved changes",
                    f"The {' and '.join(st.label for st in changed)} text has changes that are not saved.\n\n"
                    f"Load {what} and discard them?"):
                return False
        if name != self.company:
            self._set_file_names(companies.folder_name(name) if name else "")
        self.company, self.posting = name, posting
        self.quicklist.set_active(name, posting)
        if name is None:            # keep the boxes; only where they are saved changes
            for state in self.modes.values():
                state.saved_path, state.saved_form = None, {}
        else:
            loaded = []
            for state in self.modes.values():
                path = companies.latest_file(name, state.key, posting)
                if path is not None and self._load_saved(state, path):
                    loaded.append(state.label)
                else:
                    self._reset_document(state)
            note = (f"Loaded {', '.join(loaded)} from {self._where()}" if loaded else
                    f"Nothing saved for this {'posting' if posting else 'company'} yet; "
                    "the boxes have the default text.")
            for state in self.modes.values():
                state.status = note
        self._switch_mode(doc_type or self.mode.key)
        self._update_company_ui()
        return True

    def _set_file_names(self, name: str) -> None:
        for state in self.modes.values():
            state.file_name.set(name)
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, name)

    def _reset_document(self, state: ModeState) -> None:
        """Default text (with the company's details) and no compiled preview."""
        self._fill_defaults(state)
        state.result, state.images, state.rendered_width = None, [], 0
        state.compiled_form, state.saved_form, state.saved_path = {}, {}, None

    def _load_saved(self, state: ModeState, path: Path) -> bool:
        """Fill the boxes and the preview from a document saved in the company folder."""
        if path.suffix.lower() == ".txt":
            fields, lang = documents.parse_email_txt(path.read_text(encoding="utf-8"))
            data = {"type": "email", "lang": lang, "fields": fields}
        else:
            data = documents.read_embedded(path)
        if not data or data.get("type") != state.key:
            return False
        for key, *_ in state.fields:
            self._set(state, key, data["fields"].get(key, ""))
        if data.get("style") in state.styles:
            state.style.set(data["style"])
        if state.key == "cv" and data.get("track") in self.base_cv["tracks"]:
            self.track.set(data["track"])
        if state.key != "cv" and data.get("lang") in documents.LANGUAGES and data["lang"] != self.language:
            self.language = data["lang"]
            self._update_lang_buttons()
        form = self._form(state)
        state.baseline = state.saved_form = state.compiled_form = form
        state.saved_path = path
        state.preview_dir.mkdir(parents=True, exist_ok=True)
        state.result = state.preview_dir / f"preview{path.suffix.lower()}"
        shutil.copyfile(path, state.result)
        state.images, state.rendered_width = [], 0
        if state.is_pdf:
            self._render_async(state, state.result)
        self._validate(state)
        return True

    def _render_async(self, state: ModeState, pdf: Path) -> None:
        width = self._preview_page_width()
        state.rendered_width = width

        def work():
            try:
                render_pages(pdf, state.preview_dir, width)
            except Exception:
                return
            self.after(0, self._rerendered, state, width)

        threading.Thread(target=work, daemon=True).start()

    def _update_company_ui(self) -> None:
        """Folder in front of the file name, and the Ready button (only for a company)."""
        if self.company:
            folder = companies.doc_folder(self.company, self.posting).relative_to(companies.OUTPUT)
            self.folder_label.set(f"{folder.as_posix()}/{self.file_prefix}_")
            self.ready_btn.pack(side="left", padx=(8, 0))
        else:
            self.folder_label.set(f"{self.file_prefix}_")
            self.ready_btn.pack_forget()
        self._validate(self.mode)

    def _toggle_ready(self) -> None:
        state, company, posting = self.mode, self.company, self.posting
        if company is None:
            return
        if companies.is_ready(company, state.key, posting):
            companies.set_ready(company, state.key, False, posting)
            self._set_status(state, f"{state.label} is no longer marked ready.")
        else:
            if state.saved_path is None or not state.saved_path.exists():
                messagebox.showwarning("Mark ready", f"Save the {state.label.lower()} first. "
                                                     "'Ready' is about the saved file.")
                return
            problems = placeholder_problems(state.saved_form, state.required)
            if problems:
                lines = "\n".join(f"  - {state.labels[k]}: {', '.join(PLACEHOLDER.findall(state.saved_form[k])[:3])}"
                                  for k in problems)
                messagebox.showerror("Can't mark ready",
                                     f"The saved {state.label.lower()} still has placeholders in required fields:"
                                     f"\n\n{lines}\n\nReplace or remove them, compile, save, then mark it ready.")
                return
            if self._form(state) != state.saved_form and not messagebox.askyesno(
                    "Mark ready", "The boxes have changed since the last save.\n\n"
                                  "Mark the saved version ready anyway?"):
                return
            companies.set_ready(company, state.key, True, posting)
            self._set_status(state, f"{state.label} marked ready ({state.saved_path.name}).")
        self.model.documents_changed()
        self._validate(state)

    def _initial_columns(self, event) -> None:
        """Set column widths once, when the window has its real size: left 300px, boxes 540px."""
        panes = event.widget
        if event.width < 900:
            return
        panes.unbind("<Configure>")
        panes.sashpos(1, event.width - 540)
        panes.sashpos(0, 300)

    @staticmethod
    def _card(parent, **pack) -> ttk.Frame:
        """White panel with a thin border."""
        border = tk.Frame(parent, background=COLORS["border"])
        border.pack(**pack)
        inner = ttk.Frame(border, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return inner

    # ---- left: document type
    def _build_left(self, parent) -> ttk.Frame:
        self.left_panel = ttk.Frame(parent)
        card = self._card(self.left_panel, fill="x")
        inner = ttk.Frame(card, style="Card.TFrame", padding=(12, 12))
        inner.pack(fill="x")
        ttk.Label(inner, text="Document", style="Help.Card.TLabel").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(inner, style="Card.TFrame")
        row.pack(fill="x")
        self.mode_buttons: dict[str, ttk.Button] = {}
        for i, (key, label, *_rest) in enumerate(MODES):
            row.columnconfigure(i, weight=1, uniform="modes")
            b = ttk.Button(row, text=label, style="Segment.TButton", command=lambda k=key: self._switch_mode(k))
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
            self.mode_buttons[key] = b

        ttk.Label(inner, text="Language", style="Help.Card.TLabel").pack(anchor="w", pady=(14, 6))
        row = ttk.Frame(inner, style="Card.TFrame")
        row.pack(fill="x")
        self.lang_buttons: dict[str, ttk.Button] = {}
        for i, (code, label) in enumerate(documents.LANGUAGES.items()):
            row.columnconfigure(i, weight=1, uniform="langs")
            b = ttk.Button(row, text=label, style="Segment.TButton", command=lambda c=code: self._switch_language(c))
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
            self.lang_buttons[code] = b
        self.lang_note = ttk.Label(inner, text="", style="Help.Card.TLabel", wraplength=260, justify="left")
        self.lang_note.pack(anchor="w", pady=(8, 0))
        self._update_lang_buttons()

        card = self._card(self.left_panel, fill="x", pady=(10, 0))
        self.quicklist = quicklist.Quicklist(card, self.model, COLORS, self.fonts,
                                             on_select=self.select_company, on_edit=self._edit_company)
        self.quicklist.pack(fill="x")
        return self.left_panel

    # ---- language
    def _update_lang_buttons(self) -> None:
        for code, b in self.lang_buttons.items():
            b.configure(style="SegmentOn.TButton" if code == self.language else "Segment.TButton")
        cv_mode = getattr(self, "mode", None) is not None and self.mode.key == "cv"
        self.lang_note.configure(text="The CV text is in English only (data/cv.yaml)." if cv_mode else "")

    def _switch_language(self, code: str) -> None:
        if code == self.language:
            return
        self.language = code
        self._update_lang_buttons()
        # Swap the template text of email and application. Ask before replacing text the user edited.
        states = [self.modes["email"], self.modes["application"]]
        edited = [st for st in states if self._form(st) != st.loaded_defaults]
        if edited:
            names = " and ".join(st.label for st in edited)
            if not messagebox.askyesno(
                    "Change language",
                    f"Replace the {names} text with the {documents.LANGUAGES[code]} text of its template?\n"
                    "Your edits there will be lost. Choose No to keep the current text."):
                states = [st for st in states if st not in edited]
        for st in states:
            self._fill_defaults(st)
            self._mark_dirty(st)
        for st in (self.modes["email"], self.modes["application"]):
            self._update_template_ui(st)

    # ---- middle: file name + buttons above the preview
    def _build_preview(self, parent) -> ttk.Frame:
        outer = ttk.Frame(parent)

        bar = self._card(outer, fill="x", pady=(0, 10))
        # Row 1: file name (the entry stretches with the column)
        row = ttk.Frame(bar, style="Card.TFrame", padding=(14, 12, 14, 6))
        row.pack(fill="x")
        ttk.Label(row, text="File name", style="Field.Card.TLabel").pack(side="left")
        self.folder_label = tk.StringVar(value=f"{self.file_prefix}_")     # "<company folder>/" in front
        ttk.Label(row, textvariable=self.folder_label, style="Help.Card.TLabel").pack(side="left", padx=(10, 2))
        self.name_entry = ttk.Entry(row, width=12)
        self.name_entry.pack(side="left", fill="x", expand=True)
        ttk.Label(row, textvariable=self.suffix_label, style="Help.Card.TLabel").pack(side="left", padx=(4, 0))
        # Row 2: buttons + status, so the buttons stay visible however narrow the column is
        buttons = ttk.Frame(bar, style="Card.TFrame", padding=(14, 0, 14, 4))
        buttons.pack(fill="x")
        self.compile_btn = ttk.Button(buttons, text="Compile", style="Accent.TButton", command=self._compile)
        self.compile_btn.pack(side="left")
        self.save_btn = ttk.Button(buttons, text="Save", command=self._save, state="disabled")
        self.save_btn.pack(side="left", padx=(8, 0))
        self.ready_btn = ttk.Button(buttons, text="Mark ready", command=self._toggle_ready)   # packed for a company
        status = ttk.Label(bar, textvariable=self.status, style="Status.Card.TLabel", padding=(14, 2, 14, 2))
        status.pack(fill="x")
        # Row 3: why saving is blocked (empty when it isn't)
        self.block_label = ttk.Label(bar, text="", style="Error.Card.TLabel", padding=(14, 0, 14, 10))
        self.block_label.pack(fill="x")
        bar.bind("<Configure>", lambda e: (status.configure(wraplength=max(200, e.width - 30)),
                                           self.block_label.configure(wraplength=max(200, e.width - 30))))

        self.preview_card = self._card(outer, fill="both", expand=True)
        # PDF preview
        self.pdf_view = ttk.Frame(self.preview_card, style="Card.TFrame")
        self.preview = tk.Canvas(self.pdf_view, background=COLORS["preview_bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(self.pdf_view, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=scroll.set)
        self.preview.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.preview.bind("<Configure>", self._on_preview_resize)
        self._bind_wheel(self.preview)
        # Text preview (email)
        self.text_view = ttk.Frame(self.preview_card, style="Card.TFrame")
        self.text_preview = tk.Text(self.text_view, wrap="word", font=self.fonts["body"], relief="flat",
                                    borderwidth=0, padx=24, pady=20, background="#FFFFFF",
                                    foreground=COLORS["ink"], highlightthickness=0, state="disabled")
        tscroll = ttk.Scrollbar(self.text_view, orient="vertical", command=self.text_preview.yview)
        self.text_preview.configure(yscrollcommand=tscroll.set)
        self.text_preview.pack(side="left", fill="both", expand=True)
        tscroll.pack(side="right", fill="y")
        return outer

    # ---- right: boxes for each document type
    def _build_right(self, parent) -> ttk.Frame:
        outer = ttk.Frame(parent)
        self.right_card = self._card(outer, fill="both", expand=True)
        for state in self.modes.values():
            state.frame = self._build_mode_form(self.right_card, state)
        return outer

    def _build_mode_form(self, parent, state: ModeState) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame")

        head = ttk.Frame(frame, style="Card.TFrame", padding=(16, 12, 16, 4))
        head.pack(fill="x")
        ttk.Label(head, text=state.label, style="Title.Card.TLabel").pack(side="left")
        ttk.Button(head, text="Import...", command=lambda: self._import(state)).pack(side="right")

        if state.key == "cv":
            actions = ttk.Frame(frame, style="Card.TFrame", padding=(16, 2, 16, 6))
            actions.pack(fill="x")
            ttk.Label(actions, text="Default text (cv.yaml):", style="Help.Card.TLabel").pack(side="left")
            ttk.Button(actions, text="Save as default", command=lambda: self._save_defaults(state)).pack(side="right")
            ttk.Button(actions, text="Reload", command=lambda: self._reload(state)).pack(side="right", padx=(0, 6))
        else:
            self._build_template_bar(frame, state)

        if state.styles:
            opts = ttk.Frame(frame, style="Card.TFrame", padding=(16, 2, 16, 8))
            opts.pack(fill="x")
            ttk.Label(opts, text="Style", style="Help.Card.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Combobox(opts, textvariable=state.style, values=state.styles, state="readonly",
                         width=12).grid(row=1, column=0, sticky="w")
            if state.key == "cv":
                ttk.Label(opts, text="Track", style="Help.Card.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
                track_box = ttk.Combobox(opts, textvariable=self.track, values=list(self.base_cv["tracks"]),
                                         state="readonly", width=18)
                track_box.grid(row=1, column=1, sticky="w", padx=(12, 0))
                track_box.bind("<<ComboboxSelected>>", lambda e: self._load_cv_track())
        tk.Frame(frame, height=1, background=COLORS["border"]).pack(fill="x")

        body = ttk.Frame(frame, style="Card.TFrame")
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0, background=COLORS["card"])
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        form = ttk.Frame(canvas, style="Card.TFrame", padding=(16, 4, 16, 16))
        form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=form, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._bind_wheel(canvas)

        form.columnconfigure(0, weight=1)
        row = 0
        if state.required:
            ttk.Label(form, text="* Required: replace or remove the [placeholders] before saving.",
                      style="Error.Card.TLabel").grid(row=row, column=0, sticky="w", pady=(10, 0))
            row += 1
        for key, label, help_text, height in state.fields:
            label_row = ttk.Frame(form, style="Card.TFrame")
            label_row.grid(row=row, column=0, sticky="w", pady=(12, 0))
            ttk.Label(label_row, text=label, style="Field.Card.TLabel").pack(side="left")
            if key in state.required:
                ttk.Label(label_row, text=" *", style="Required.Card.TLabel").pack(side="left")
            row += 1
            if help_text:
                ttk.Label(form, text=help_text, style="Help.Card.TLabel").grid(row=row, column=0, sticky="w")
                row += 1
            if height == 1:
                w = ttk.Entry(form)
                w.bind("<KeyRelease>", lambda e, s=state: self._mark_dirty(s))
            else:
                box, w = self._text_box(form, height, canvas)
                w.bind("<<Modified>>", lambda e, s=state: self._on_text_modified(e, s))
            (w if height == 1 else box).grid(row=row, column=0, sticky="ew", pady=(4, 0))
            state.widgets[key] = w
            row += 1
        return frame

    def _build_template_bar(self, parent, state: ModeState) -> None:
        """Template dropdown + Reload, then Save / Save as new / Rename / Delete (email, application)."""
        bar = ttk.Frame(parent, style="Card.TFrame", padding=(16, 2, 16, 6))
        bar.pack(fill="x")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Template", style="Help.Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        box = ttk.Combobox(bar, textvariable=state.template, state="readonly", width=24)
        box.grid(row=0, column=1, sticky="ew")
        box.bind("<<ComboboxSelected>>", lambda e: self._choose_template(state))
        state.template_box = box
        ttk.Button(bar, text="Reload", command=lambda: self._reload(state)).grid(row=0, column=2, padx=(6, 0))

        buttons = ttk.Frame(bar, style="Card.TFrame")
        buttons.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for key, text, command in (("save", "Save template", self._save_template),
                                   ("new", "Save as new...", self._new_template),
                                   ("rename", "Rename...", self._rename_template),
                                   ("delete", "Delete", self._delete_template)):
            b = ttk.Button(buttons, text=text, command=lambda c=command: c(state))
            b.pack(side="left", padx=(0, 6))
            state.template_buttons[key] = b
        ttk.Label(bar, textvariable=state.template_info, style="Help.Card.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._update_template_ui(state)

    def _update_template_ui(self, state: ModeState) -> None:
        names = documents.template_names(state.key)
        if state.template.get() not in names:
            state.template.set(documents.DEFAULT_TEMPLATE)
        name = state.template.get()
        state.template_box.configure(values=names)
        default = name == documents.DEFAULT_TEMPLATE
        for key in ("rename", "delete"):
            state.template_buttons[key].configure(state="disabled" if default else "normal")
        file = f"{state.key}.yaml" if default else documents.templates_path(state.key).name
        langs = ", ".join(documents.LANGUAGES[c] for c in documents.template_languages(state.key, name))
        state.template_info.set(f"In data/{file}. Text in: {langs or 'no language yet'}")

    def _ask_template_name(self, state: ModeState, title: str, initial: str = "",
                           current: str | None = None) -> str | None:
        name = initial
        while True:
            name = simpledialog.askstring(title, f"Name of the {state.label.lower()} template:",
                                          initialvalue=name, parent=self)
            if name is None:
                return None
            problem = documents.template_name_problem(state.key, name, current)
            if not problem:
                return name
            messagebox.showwarning(title, problem, parent=self)

    def _choose_template(self, state: ModeState) -> None:
        """A template was picked in the dropdown: fill the boxes with it (asking before losing edits)."""
        name, previous = state.template.get(), state.active_template
        if name == previous:
            return
        if self._form(state) != state.loaded_defaults and not messagebox.askyesno(
                "Use template", f"Replace the {state.label} text with the template '{name}'?\n"
                                "The text in the boxes will be lost."):
            state.template.set(previous)
            return
        self._fill_defaults(state)
        self._mark_dirty(state)
        self._update_template_ui(state)

    def _save_template(self, state: ModeState) -> None:
        name, lang = state.template.get(), documents.LANGUAGES[self.language]
        file = (f"{state.key}.yaml" if name == documents.DEFAULT_TEMPLATE
                else documents.templates_path(state.key).name)
        if not messagebox.askyesno(
                "Save template",
                f"Overwrite the {lang} text of the template '{name}' (data/{file}) with the text in the boxes?\n\n"
                f"The previous file is kept as {file}.bak. Comments in the file are not kept."):
            return
        form = self._form(state)
        documents.save_template(state.key, name, self.language, form)
        state.loaded_defaults = form
        self._update_template_ui(state)
        self._set_status(state, f"Saved the boxes as the {lang} text of the template '{name}'.")

    def _new_template(self, state: ModeState) -> None:
        name = self._ask_template_name(state, "New template")
        if name is None:
            return
        form = self._form(state)
        documents.save_template(state.key, name, self.language, form)
        state.template.set(name)
        state.active_template = name
        state.loaded_defaults = form
        self._update_template_ui(state)
        self._set_status(state, f"Saved the boxes as the new template '{name}' "
                                f"({documents.LANGUAGES[self.language]}).")

    def _rename_template(self, state: ModeState) -> None:
        old = state.template.get()
        new = self._ask_template_name(state, "Rename template", old, current=old)
        if new is None or new == old:
            return
        documents.rename_template(state.key, old, new)
        state.template.set(new)
        state.active_template = new
        self._update_template_ui(state)
        self._set_status(state, f"Renamed the template '{old}' to '{new}'.")

    def _delete_template(self, state: ModeState) -> None:
        name = state.template.get()
        if not messagebox.askyesno("Delete template", f"Delete the {state.label.lower()} template '{name}' "
                                                      "in all languages?\nThe text in the boxes is kept."):
            return
        documents.delete_template(state.key, name)
        state.template.set(documents.DEFAULT_TEMPLATE)
        state.active_template = documents.DEFAULT_TEMPLATE
        state.loaded_defaults = {}          # the boxes no longer match a template: ask before replacing
        self._update_template_ui(state)
        self._set_status(state, f"Deleted the template '{name}'. The boxes keep its text.")

    def _text_box(self, parent, height: int, form_canvas: tk.Canvas) -> tuple[tk.Frame, tk.Text]:
        """A text box with its own scrollbar and a grip along the bottom edge to drag its height.

        The mouse wheel scrolls the box while it can scroll that way, then the form (`form_canvas`)."""
        box = tk.Frame(parent, background="#FFFFFF", highlightthickness=1,
                       highlightbackground=COLORS["border"], highlightcolor=COLORS["border"])
        box.columnconfigure(0, weight=1)
        text = tk.Text(box, height=height, wrap="word", undo=True, font=self.fonts["body"],
                       padx=8, pady=6, relief="flat", borderwidth=0, background="#FFFFFF",
                       foreground=COLORS["ink"], insertbackground=COLORS["ink"],
                       selectbackground=COLORS["accent"], selectforeground="#FFFFFF", highlightthickness=0)
        scroll = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        # Border on the frame, so it surrounds the scrollbar too: red while invalid (set by _validate),
        # accent while focused, grey otherwise.
        box.invalid = False

        box.focused = False

        def paint(focused: bool | None = None):
            if focused is not None:
                box.focused = focused
            color = (COLORS["error"] if box.invalid else
                     COLORS["accent"] if box.focused else COLORS["border"])
            box.configure(highlightbackground=color, highlightcolor=color)
        box.paint = paint
        text.bind("<FocusIn>", lambda e: paint(True), add="+")
        text.bind("<FocusOut>", lambda e: paint(False), add="+")

        def wheel(e):
            step = -1 if (e.num == 4 or e.delta > 0) else 1
            top, bottom = text.yview()
            if (step < 0 and top > 0) or (step > 0 and bottom < 1):
                text.yview_scroll(step, "units")
            else:
                form_canvas.yview_scroll(step, "units")
            return "break"                       # skip Tk's own text scrolling and the form's handler
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            text.bind(seq, wheel)

        grip = tk.Frame(box, height=7, background=COLORS["border"], cursor="sb_v_double_arrow")
        grip.grid(row=1, column=0, columnspan=2, sticky="ew")
        line = tkfont.Font(font=self.fonts["body"]).metrics("linespace")

        def drag(e):
            pixels = e.y_root - text.winfo_rooty()
            text.configure(height=max(2, round(pixels / line)))
        grip.bind("<B1-Motion>", drag)
        grip.bind("<Enter>", lambda e: grip.configure(background="#AEB7C4"))
        grip.bind("<Leave>", lambda e: grip.configure(background=COLORS["border"]))
        return box, text

    def _bind_wheel(self, canvas: tk.Canvas) -> None:
        def wheel(e):
            canvas.yview_scroll(-1 if (e.num == 4 or e.delta > 0) else 1, "units")
        canvas.bind("<Enter>", lambda e: (canvas.bind_all("<MouseWheel>", wheel),
                                          canvas.bind_all("<Button-4>", wheel),
                                          canvas.bind_all("<Button-5>", wheel)))
        canvas.bind("<Leave>", lambda e: (canvas.unbind_all("<MouseWheel>"),
                                          canvas.unbind_all("<Button-4>"),
                                          canvas.unbind_all("<Button-5>")))

    # ---- switching document type
    def _switch_mode(self, key: str) -> None:
        old = self.mode
        old.file_name.set(self.name_entry.get())
        old.status = self.status.get()
        if old.frame:
            old.frame.pack_forget()

        self.mode = state = self.modes[key]
        state.frame.pack(fill="both", expand=True)
        for k, b in self.mode_buttons.items():
            b.configure(style="SegmentOn.TButton" if k == key else "Segment.TButton")
        self._update_lang_buttons()
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, state.file_name.get())
        self.suffix_label.set(f"{state.suffix}{state.ext}")
        self.compile_btn.configure(text="Compile" if state.is_pdf else "Preview")
        self.save_btn.configure(state="normal" if state.result else "disabled")
        self._validate(state)
        self.status.set(state.status)

        self.pdf_view.pack_forget()
        self.text_view.pack_forget()
        if state.is_pdf:
            self.pdf_view.pack(fill="both", expand=True)
            self._show_pages()
            self.after(50, self._rerender_preview)
        else:
            self.text_view.pack(fill="both", expand=True)
            self._show_text(state.result.read_text(encoding="utf-8") if state.result else "")

    # ---- form values
    @staticmethod
    def _get(state: ModeState, key: str) -> str:
        w = state.widgets[key]
        return w.get("1.0", "end-1c") if isinstance(w, tk.Text) else w.get()

    @staticmethod
    def _set(state: ModeState, key: str, value: str) -> None:
        w = state.widgets[key]
        if isinstance(w, tk.Text):
            w.delete("1.0", "end")
            w.insert("1.0", value)
            w.edit_modified(False)
        else:
            w.delete(0, "end")
            w.insert(0, value)

    def _form(self, state: ModeState) -> dict[str, str]:
        return {key: self._get(state, key) for key, *_ in state.fields}

    def _fill_defaults(self, state: ModeState) -> None:
        """Fill the boxes with the CV text, or the chosen email/application template in the language."""
        note = ""
        if state.key == "cv":
            values = cv_to_form(self.base_cv, self.track.get())
        else:
            name = state.template.get()
            values = documents.load_template(state.key, name, self.language, self.personal)
            if values is None:           # the template has no text in this language yet
                values = documents.load_defaults(state.key, self.language, self.personal)
                note = (f"The template '{name}' has no {documents.LANGUAGES[self.language]} text yet: "
                        "the boxes have the Default text. 'Save template' stores them as its text.")
            state.active_template = name
        row = self.model.find(self.company) if getattr(self, "company", None) else None
        if row is not None:                  # fill in what the company list knows
            if state.key == "application":
                values["company"] = row["Company"]
                if row.get("Contact person"):
                    values["recipient"] = row["Contact person"]
            elif state.key == "email" and row.get("Email"):
                values["to"] = row["Email"]
        for key, *_ in state.fields:
            self._set(state, key, values.get(key, ""))
        state.loaded_defaults = state.baseline = self._form(state)
        self._validate(state)
        if note:
            self._set_status(state, note)

    def _load_cv_track(self) -> None:
        state = self.modes["cv"]
        values = cv_to_form(self.base_cv, self.track.get())
        if any(self._get(state, k) for k, *_ in state.fields):
            # Switching track only swaps the track-specific boxes; other edits are kept.
            for key in ("headline", "title"):
                self._set(state, key, values[key])
        else:
            self._fill_defaults(state)
        self._mark_dirty(state)

    def _reload(self, state: ModeState) -> None:
        source = "cv.yaml" if state.key == "cv" else f"the template '{state.template.get()}'"
        if not messagebox.askyesno("Reload", f"Replace all {state.label} text with the content of {source}?"):
            return
        if state.key == "cv":
            self.personal = build_cv.load_personal()
            self.base_cv = build_cv.load_cv(self.personal)
        self._fill_defaults(state)
        self._mark_dirty(state)

    def _save_defaults(self, state: ModeState) -> None:
        """CV: overwrite the text in cv.yaml / personal.yaml with what is in the boxes.
        (Email and application save to their template: _save_template.)"""
        path = documents.DATA / "cv.yaml"
        what = (f"the CV text in {path.name} (track '{self.track.get()}' for headline and title) "
                "and your name and contact details in personal.yaml")
        if not messagebox.askyesno(
                "Save as default",
                f"Overwrite {what} with the text in the boxes?\n\n"
                f"The previous file is kept as {path.name}.bak. Comments in the file are not kept."):
            return
        form = self._form(state)
        self.base_cv = form_to_cv(self.base_cv, self.track.get(), form)
        build_cv.save_cv(self.base_cv)          # name/contact -> personal.yaml, the rest -> cv.yaml
        self.personal = build_cv.load_personal()
        state.loaded_defaults = form
        self._set_status(state, f"Saved the boxes as default text in data/{path.name}")

    def _import(self, state: ModeState) -> None:
        """Fill the boxes from a PDF saved by the editor, or from an email .txt."""
        path = filedialog.askopenfilename(
            parent=self, title="Import file", initialdir=str(next(
                d for d in (companies.doc_folder(self.company, self.posting) if self.company else None,
                            companies.company_folder(self.company) if self.company else None, OUTPUT, ROOT)
                if d is not None and d.exists())),
            filetypes=[("Saved documents", "*.pdf *.txt"), ("PDF", "*.pdf"), ("Text", "*.txt")])
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() == ".txt":
            fields, lang = documents.parse_email_txt(path.read_text(encoding="utf-8"))
            data = {"type": "email", "lang": lang, "fields": fields}
        else:
            data = documents.read_embedded(path)
            if data is None:
                messagebox.showwarning(
                    "Nothing to import",
                    f"{path.name} has no editor data.\n\nOnly PDFs saved from this editor can be imported.")
                return
        target = self.modes.get(data.get("type", ""))
        if target is None:
            messagebox.showwarning("Nothing to import", f"Unknown document type in {path.name}.")
            return
        if not messagebox.askyesno("Import", f"Replace the {target.label} boxes with the text from {path.name}?"):
            return

        for key, *_ in target.fields:
            if key in data["fields"]:
                self._set(target, key, data["fields"][key])
        if data.get("style") in target.styles:
            target.style.set(data["style"])
        if target.key == "cv" and data.get("track") in self.base_cv["tracks"]:
            self.track.set(data["track"])
        if data.get("lang") in documents.LANGUAGES and data["lang"] != self.language:
            self.language = data["lang"]          # only the setting; other documents keep their text
            self._update_lang_buttons()
        name = re.sub(re.escape(target.suffix) + "$", "", path.stem, flags=re.IGNORECASE)
        name = re.sub("^" + re.escape(self.file_prefix) + "_?", "", name, flags=re.IGNORECASE)
        target.file_name.set(name)
        if target is not self.mode:
            self._switch_mode(target.key)
        else:
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, name)
        target.baseline = self._form(target)
        self._mark_dirty(target)
        self._set_status(target, f"Imported {path.name}. Press Compile to see it.")

    def _on_text_modified(self, event, state: ModeState) -> None:
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._mark_dirty(state)

    def _validate(self, state: ModeState) -> None:
        """Red border on required fields with placeholders; update the save indicator."""
        live = placeholder_problems(self._form(state), state.required)
        for key, w in state.widgets.items():
            bad = key in live
            if isinstance(w, tk.Text):
                w.master.invalid = bad          # the border is on the frame around the box (_text_box)
                w.master.paint()
            else:
                w.configure(style="Invalid.TEntry" if bad else "TEntry")
        if state is not self.mode:
            return
        if getattr(self, "company", None):            # saving always works; placeholders block "ready"
            self._validate_ready(state, live)
            return
        compiled = placeholder_problems(state.compiled_form, state.required) if state.result else []
        if live:
            text = "Can't save: placeholders in " + ", ".join(state.labels[k] for k in live)
        elif compiled:
            text = "Can't save yet: compile again to use your changes"
        else:
            text = ""
        self.block_label.configure(text=text)
        self.save_btn.configure(style="Blocked.TButton" if text else "TButton")

    def _validate_ready(self, state: ModeState, live: list[str]) -> None:
        ready = companies.is_ready(self.company, state.key, self.posting)
        saved = placeholder_problems(state.saved_form, state.required) if state.saved_path else []
        if ready:
            text = ""
        elif live:
            text = "Not ready yet: placeholders in " + ", ".join(state.labels[k] for k in live) + \
                   " (saving works)"
        elif saved:
            text = "The saved version has placeholders: compile and save again before marking it ready"
        else:
            text = ""
        self.block_label.configure(text=text)
        self.save_btn.configure(style="TButton")
        blocked = not ready and (state.saved_path is None or bool(saved))
        self.ready_btn.configure(text="Ready (click to undo)" if ready else "Mark ready",
                                 style="Blocked.TButton" if blocked and (live or saved) else
                                 "Accent.TButton" if ready else "TButton")

    def _mark_dirty(self, state: ModeState) -> None:
        self._validate(state)
        if state.result:
            msg = "Text changed since last compile. Save stores the last compiled version."
            state.status = msg
            if state is self.mode:
                self.status.set(msg)

    # ---- compile
    def _compile(self) -> None:
        state = self.mode
        state.preview_dir.mkdir(parents=True, exist_ok=True)

        if not state.is_pdf:     # email: no LaTeX, just write the text file
            out = state.preview_dir / "preview.txt"
            text = documents.email_text(self._form(state), self.language)
            out.write_text(text, encoding="utf-8")
            state.result = out
            state.compiled_form = self._form(state)
            self._show_text(text)
            self._set_status(state, "Preview ready. Not saved yet.")
            self.save_btn.configure(state="normal")
            self._validate(state)
            return

        form = self._form(state)
        state.pending_form = form
        cv_form = self._form(self.modes["cv"])
        person = {k: cv_form[k].strip() for k in ("name", "location", "phone", "email")}
        cv = form_to_cv(self.base_cv, self.track.get(), cv_form) if state.key == "cv" else None
        style, track, lang = state.style.get(), self.track.get(), self.language
        data = {"editor": 1, "type": state.key, "lang": lang, "style": style, "fields": form}
        if state.key == "cv":
            data["track"] = track
        else:
            data["person"] = person
        embed = json.dumps(data, ensure_ascii=False, indent=1)
        page_width = self._preview_page_width()   # fit pages to the preview panel
        self.compile_btn.configure(state="disabled")
        self._set_status(state, "Compiling...")

        def work():
            try:
                if state.key == "cv":
                    pdf = build_cv.build(cv, style, track, state.preview_dir, "preview", embed=embed)
                else:
                    pdf = documents.build_application(form, person, state.preview_dir, "preview", lang,
                                                      style, embed)
                render_pages(pdf, state.preview_dir, page_width)
                state.rendered_width = page_width
                self.after(0, self._compiled, state, pdf, None)
            except Exception as e:  # BuildError, missing pdftoppm, …
                self.after(0, self._compiled, state, None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _compiled(self, state: ModeState, pdf: Path | None, error: str | None) -> None:
        self.compile_btn.configure(state="normal")
        if error:
            self._set_status(state, "Compile failed.")
            messagebox.showerror("Compile failed", error[-3000:])
            return
        state.result = pdf
        state.compiled_form = state.pending_form
        pages = self._load_pages(state)
        self._set_status(state, f"Compiled ({len(pages)} page{'s' if len(pages) != 1 else ''}). Not saved yet.")
        if state is self.mode:
            self.save_btn.configure(state="normal")
            self._show_pages()
        self._validate(state)

    def _load_pages(self, state: ModeState) -> list[Path]:
        pages = sorted(state.preview_dir.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
        state.images = [tk.PhotoImage(file=str(p)) for p in pages]
        return pages

    def _preview_page_width(self) -> int:
        return max(300, self.preview.winfo_width() - 48)

    def _on_preview_resize(self, _event=None) -> None:
        """Redraw now; re-render the pages at the new width once the resizing has stopped."""
        self._show_pages()
        if getattr(self, "_resize_job", None):
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(250, self._rerender_preview)

    def _rerender_preview(self) -> None:
        self._resize_job = None
        state = self.mode
        width = self._preview_page_width()
        if (not state.is_pdf or not state.result or not state.result.exists()
                or abs(width - state.rendered_width) < 8 or self.compile_btn.instate(["disabled"])):
            return
        state.rendered_width = width
        pdf = state.result

        def work():
            try:
                render_pages(pdf, state.preview_dir, width)
            except Exception:
                return
            self.after(0, self._rerendered, state, width)

        threading.Thread(target=work, daemon=True).start()

    def _rerendered(self, state: ModeState, width: int) -> None:
        if state.rendered_width != width:      # a newer resize or compile has taken over
            return
        self._load_pages(state)
        if state is self.mode:
            self._show_pages()

    def _set_status(self, state: ModeState, msg: str) -> None:
        state.status = msg
        if state is self.mode:
            self.status.set(msg)

    def _show_pages(self) -> None:
        c = self.preview
        c.delete("all")
        y, width = 20, c.winfo_width()
        images = self.mode.images if self.mode.is_pdf else []
        if not images:
            c.create_text(width // 2, 60, text="Press Compile to see the PDF here.",
                          fill=COLORS["muted"], font=self.fonts["body"])
        for img in images:
            x = max(20, (width - img.width()) // 2)
            c.create_rectangle(x + 2, y + 3, x + img.width() + 2, y + img.height() + 3,
                               fill=COLORS["page_shadow"], outline="")
            c.create_image(x, y, image=img, anchor="nw")
            y += img.height() + 20
        c.configure(scrollregion=(0, 0, width, y))

    def _show_text(self, text: str) -> None:
        t = self.text_preview
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", text or "Press Preview to see the email text here.")
        t.configure(state="disabled", foreground=COLORS["ink"] if text else COLORS["muted"])

    # ---- save
    def _save(self) -> None:
        state = self.mode
        if not state.result or not state.result.exists():
            messagebox.showwarning("Nothing to save", "Compile first.")
            return
        if self.company:
            self._save_for_company(state)
            return
        problems = placeholder_problems(state.compiled_form, state.required)
        if problems:
            lines = "\n".join(f"  - {state.labels[k]}: {', '.join(PLACEHOLDER.findall(state.compiled_form[k])[:3])}"
                              for k in problems)
            live = placeholder_problems(self._form(state), state.required)
            hint = ("Replace or remove them in the boxes, then compile again." if live else
                    "The boxes are fixed, but the compiled version is older. Compile again, then save.")
            messagebox.showerror("Can't save", f"These required fields still have placeholders:\n\n{lines}\n\n{hint}")
            return
        name = self.name_entry.get()
        if not name.strip() and state.key == "application":
            name = name_from_company(state.compiled_form.get("company", ""))   # empty name -> company
        if re.search(r"[/\\:*?\"<>|]", name):
            messagebox.showwarning("Invalid name", "Enter a file name without / \\ : * ? \" < > |")
            return
        dest = OUTPUT / output_file_name(self.file_prefix, name, state.suffix, state.ext)
        if dest.exists() and not messagebox.askyesno("File exists", f"{_rel(dest)} already exists.\nReplace it?"):
            return
        OUTPUT.mkdir(exist_ok=True)
        shutil.copyfile(state.result, dest)
        state.baseline = state.compiled_form
        self._set_status(state, f"Saved {_rel(dest)}")

    def _save_for_company(self, state: ModeState) -> None:
        """Save into output/<company>/ (or its posting's subfolder). Never blocked; a version with
        placeholders is not ready."""
        company, posting = self.company, self.posting
        folder = companies.doc_folder(company, posting)
        name = self.name_entry.get().strip() or companies.folder_name(company)
        if re.search(r"[/\\:*?\"<>|]", name):
            messagebox.showwarning("Invalid name", "Enter a file name without / \\ : * ? \" < > |")
            return
        dest = folder / output_file_name(self.file_prefix, name, state.suffix, state.ext)
        if dest.exists() and dest != state.saved_path and not messagebox.askyesno(
                "File exists", f"{_rel(dest)} already exists.\nReplace it?"):
            return
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(state.result, dest)
        companies.write_meta(company, posting)
        state.saved_path, state.saved_form = dest, state.compiled_form
        state.baseline = state.compiled_form
        msg = f"Saved {_rel(dest)}"
        problems = placeholder_problems(state.saved_form, state.required)
        if problems:
            if companies.is_ready(company, state.key, posting):
                companies.set_ready(company, state.key, False, posting)
                msg += " (no longer marked ready: it has placeholders)"
            else:
                msg += ". Placeholders left in " + ", ".join(state.labels[k] for k in problems)
        self._set_status(state, msg)
        self.model.documents_changed()
        self._validate(state)


if __name__ == "__main__":
    Editor().mainloop()
