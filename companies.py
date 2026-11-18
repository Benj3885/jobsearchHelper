"""The company list (docs/employers.csv) and each company's document folder (output/<company>/).

No Tk here; used by the Companies tab, the quicklist on the Documents tab and the editor.

- `CompanyList` holds the rows of the CSV. Every part of the window shares one instance; after a
  change, `save()` writes the file and tells the listeners (each gets the `source` that changed it).
- A company is on the quicklist when "Interesting" is set and its status is not Applied or later.
  Changing the status records the old one in "Previous status", so "Applied" can be undone.
- Documents for a company are saved in output/<folder_name(company)>/. company.json there holds the
  company's name and which document types are marked ready.
- A company can have job postings ("Job postings" column: one per line, "Title | URL"). Documents
  for a posting go in output/<company>/<folder_name(title)>/, with posting.json (title + ready marks)
  and job_posting.txt (the posting's text, pasted in so it can be read after the ad is gone).
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
EMPLOYERS = DOCS / "employers.csv"
OUTPUT = ROOT / "output"

# Steps of the process, in order. Values in the CSV that are not listed here are kept as they are.
STATUSES = ["Not contacted", "Contacted", "Applied", "Interview", "Offer", "Rejected", "Not an option"]
# A change to one of these sets "Last contact" to today.
CONTACT_STATUSES = {"Contacted", "Applied", "Interview", "Offer", "Rejected"}
# Companies with one of these are no longer on the quicklist.
DONE_STATUSES = {"Applied", "Interview", "Offer", "Rejected", "Not an option"}
FITS = ["High", "Medium", "Low"]

# Columns the program uses. Missing ones are added (empty) when the file is read; other columns are kept.
COLUMNS = ["Company", "Fit", "Track", "Town", "What they do", "Status", "Last contact", "Contact person",
           "Email", "Notes", "Source", "Homepage", "Job postings", "Interesting", "Previous status",
           "Previous last contact", "Status changed"]

# Document type -> (file name suffix, extension). Same as the editor's MODES.
DOC_TYPES = {"email": ("_EMAIL", ".txt"), "application": ("_APPLICATION", ".pdf"), "cv": ("_CV", ".pdf")}
META = "company.json"
POSTING_META = "posting.json"
POSTING_TEXT = "job_posting.txt"


# --------------------------------------------------------------------------- employers.csv

def load_companies(path: Path = EMPLOYERS) -> tuple[list[str], list[dict[str, str]]]:
    """(column names, rows). Missing columns from COLUMNS are added (empty)."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    fields += [c for c in COLUMNS if c not in fields]
    return fields, [{c: (r.get(c) or "") for c in fields} for r in rows]


def save_companies(fields: list[str], rows: list[dict[str, str]], path: Path = EMPLOYERS) -> None:
    """Write the CSV via a temporary file, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def is_interesting(row: dict[str, str]) -> bool:
    return row.get("Interesting", "").strip().lower() in {"yes", "y", "x", "1", "true", "ja"}


def on_quicklist(row: dict[str, str]) -> bool:
    return is_interesting(row) and row["Status"] not in DONE_STATUSES


class CompanyList:
    """The rows of docs/employers.csv, shared by every part of the window."""

    def __init__(self, path: Path = EMPLOYERS) -> None:
        self.path = path
        self.fields: list[str] = list(COLUMNS)
        self.rows: list[dict[str, str]] = []
        self.mtime: float | None = None
        self.listeners: list = []              # called as listener(source) after every change
        self.doc_listeners: list = []          # called after files in output/ change (no CSV change)
        self.reload(force=True)

    # ---- file
    def reload(self, force: bool = False) -> bool:
        """Read the file again if it changed on disk. True if the rows were replaced."""
        if not self.path.exists():
            changed = bool(self.rows) or self.mtime is not None
            self.fields, self.rows, self.mtime = list(COLUMNS), [], None
            return changed
        mtime = self.path.stat().st_mtime
        if not force and mtime == self.mtime:
            return False
        self.fields, self.rows = load_companies(self.path)
        self.mtime = mtime
        return True

    def refresh(self, force: bool = False) -> None:
        """Reload if the file changed (e.g. edited in a spreadsheet) and tell the listeners."""
        if self.reload(force):
            self.notify(None)

    def save(self, source=None) -> str | None:
        """Write the file and tell the listeners. Returns an error message, or None."""
        error = None
        try:
            save_companies(self.fields, self.rows, self.path)
            self.mtime = self.path.stat().st_mtime
        except OSError as e:
            error = str(e)
        self.notify(source)
        return error

    def notify(self, source=None) -> None:
        for listener in list(self.listeners):
            listener(source)

    def documents_changed(self) -> None:
        """A document or posting text was saved / marked ready: update the indicators."""
        for listener in list(self.doc_listeners):
            listener()

    # ---- rows
    def find(self, name: str | None) -> dict[str, str] | None:
        return next((r for r in self.rows if r["Company"] == name), None) if name else None

    def add(self, name: str) -> dict[str, str]:
        row = {c: "" for c in self.fields}
        row.update(Company=name, Status="Not contacted")
        self.rows.append(row)
        return row

    def remove(self, row: dict[str, str]) -> None:
        self.rows.remove(row)

    def rename(self, row: dict[str, str], new: str) -> None:
        """Rename a company; its document folder moves along if the new name is free."""
        old_dir, new_dir = company_folder(row["Company"]), company_folder(new)
        row["Company"] = new
        if old_dir.is_dir() and old_dir != new_dir and not new_dir.exists():
            old_dir.rename(new_dir)
        if new_dir.is_dir():
            write_meta(new)

    def set_status(self, row: dict[str, str], status: str) -> None:
        """Change the status, remembering the old one (for undo) and when it changed."""
        if status == row["Status"]:
            return
        row["Previous status"] = row["Status"]
        row["Previous last contact"] = row["Last contact"]
        row["Status"] = status
        row["Status changed"] = datetime.datetime.now().isoformat(timespec="seconds")
        if status in CONTACT_STATUSES:
            row["Last contact"] = datetime.date.today().isoformat()

    def undo_applied(self, row: dict[str, str]) -> None:
        """Back to the status it had before (Not contacted if that is unknown)."""
        previous, last_contact = row["Previous status"], row["Previous last contact"]
        self.set_status(row, previous if previous and previous not in DONE_STATUSES else "Not contacted")
        row["Last contact"] = last_contact

    # ---- job postings
    @staticmethod
    def postings(row: dict[str, str] | None) -> list[dict[str, str]]:
        return parse_postings(row.get("Job postings", "")) if row else []

    def posting_problem(self, row: dict[str, str], title: str, ignore: str | None = None) -> str | None:
        """Why `title` can't be used for a new/renamed posting of the company, or None."""
        if not folder_name(title):
            return "The title needs at least one letter or digit."
        if "\n" in title:
            return "The title must be one line."
        for p in self.postings(row):
            if p["title"] != ignore and folder_name(p["title"]) == folder_name(title):
                return f"'{p['title']}' is already a posting of this company."
        return None

    def add_posting(self, row: dict[str, str], title: str, url: str = "") -> None:
        postings = self.postings(row) + [{"title": title.strip(), "url": url.strip()}]
        row["Job postings"] = format_postings(postings)

    def edit_posting(self, row: dict[str, str], old_title: str, title: str, url: str) -> None:
        """Change a posting's title/link; its folder moves along if the new name is free."""
        title, url = title.strip(), url.strip()
        postings = self.postings(row)
        for p in postings:
            if p["title"] == old_title:
                p["title"], p["url"] = title, url
        row["Job postings"] = format_postings(postings)
        old_dir, new_dir = posting_folder(row["Company"], old_title), posting_folder(row["Company"], title)
        if old_dir.is_dir() and old_dir != new_dir and not new_dir.exists():
            old_dir.rename(new_dir)
        if new_dir.is_dir():
            write_meta(row["Company"], title)

    def remove_posting(self, row: dict[str, str], title: str) -> None:
        row["Job postings"] = format_postings([p for p in self.postings(row) if p["title"] != title])

    def quicklist(self) -> list[dict[str, str]]:
        return sorted((r for r in self.rows if on_quicklist(r)), key=lambda r: r["Company"].casefold())

    def recently_applied(self, n: int = 5) -> list[dict[str, str]]:
        """The n companies most recently set to Applied (those without a date last)."""
        applied = [r for r in self.rows if r["Status"] == "Applied"]
        return sorted(applied, key=lambda r: r["Status changed"], reverse=True)[:n]


# --------------------------------------------------------------------------- output/<company>/

def folder_name(company: str) -> str:
    """'Trackunit A/S' -> 'Trackunit_AS' (usable as a file or folder name)."""
    return re.sub(r"\s+", "_", re.sub(r"[/\\:*?\"<>|]", "", company.strip())).strip("._")


def company_folder(company: str) -> Path:
    return OUTPUT / (folder_name(company) or "_unnamed")


def posting_folder(company: str, title: str) -> Path:
    return company_folder(company) / (folder_name(title) or "_posting")


def doc_folder(company: str, posting: str | None = None) -> Path:
    """Where the documents go: the company folder, or the posting's subfolder."""
    return posting_folder(company, posting) if posting else company_folder(company)


# ---- job postings ("Job postings" column)

def parse_postings(cell: str) -> list[dict[str, str]]:
    """'Title | https://...' per line -> [{'title': ..., 'url': ...}]. The link is optional."""
    postings = []
    for line in (cell or "").splitlines():
        line = line.strip()
        if not line:
            continue
        title, sep, url = line.rpartition(" | ")
        if not sep or not url.strip().startswith(("http://", "https://")):
            title, url = line, ""
        postings.append({"title": title.strip(), "url": url.strip()})
    return postings


def format_postings(postings: list[dict[str, str]]) -> str:
    return "\n".join(p["title"] + (f" | {p['url']}" if p.get("url") else "") for p in postings)


def posting_text_file(company: str, title: str) -> Path:
    return posting_folder(company, title) / POSTING_TEXT


def has_posting_text(company: str, title: str) -> bool:
    f = posting_text_file(company, title)
    return f.is_file() and f.stat().st_size > 0


def read_posting_text(company: str, title: str) -> str:
    try:
        return posting_text_file(company, title).read_text(encoding="utf-8")
    except OSError:
        return ""


def write_posting_text(company: str, title: str, text: str) -> Path:
    path = posting_text_file(company, title)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    write_meta(company, title)
    return path


# ---- company.json / posting.json

def _meta_file(company: str, posting: str | None) -> Path:
    return doc_folder(company, posting) / (POSTING_META if posting else META)


def read_meta(company: str, posting: str | None = None) -> dict:
    try:
        data = json.loads(_meta_file(company, posting).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_meta(company: str, posting: str | None = None, ready=None) -> None:
    """Write company.json (or the posting's posting.json). ready=None keeps the current marks.
    A posting folder gets its company's company.json too."""
    if posting and not _meta_file(company, None).exists():
        write_meta(company)
    if ready is None:
        ready = read_meta(company, posting).get("ready", [])
    path = _meta_file(company, posting)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"posting": posting} if posting else {"company": company}
    data["ready"] = sorted(set(ready))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def latest_file(company: str, doc_type: str, posting: str | None = None) -> Path | None:
    """The most recently saved document of a type in the company's (or posting's) folder."""
    suffix, ext = DOC_TYPES[doc_type]
    folder = doc_folder(company, posting)
    files = [p for p in folder.glob(f"*{suffix}{ext}") if p.is_file()] if folder.is_dir() else []
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def is_ready(company: str, doc_type: str, posting: str | None = None) -> bool:
    return doc_type in read_meta(company, posting).get("ready", [])


def set_ready(company: str, doc_type: str, ready: bool, posting: str | None = None) -> None:
    marked = set(read_meta(company, posting).get("ready", []))
    (marked.add if ready else marked.discard)(doc_type)
    write_meta(company, posting, marked)


def document_states(company: str, posting: str | None = None) -> dict[str, str]:
    """doc type -> 'none' (nothing saved), 'saved' or 'ready'."""
    ready = set(read_meta(company, posting).get("ready", []))
    states = {}
    for doc_type in DOC_TYPES:
        if latest_file(company, doc_type, posting) is None:
            states[doc_type] = "none"
        else:
            states[doc_type] = "ready" if doc_type in ready else "saved"
    return states
