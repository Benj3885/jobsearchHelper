"""Job application (PDF via LaTeX) and email (plain text) builders used by cv_editor.py."""

from __future__ import annotations

import json
import re
import string
import subprocess
import tempfile
from pathlib import Path

import yaml

import build_cv

DATA = build_cv.ROOT / "data"

LANGUAGES = {"da": "Dansk", "en": "English"}

# Fixed words that appear in the generated documents, per language.
LABELS = {
    "da": {"attn": "Att.", "to": "Til", "subject": "Emne"},
    "en": {"attn": "Attn.", "to": "To", "subject": "Subject"},
}


PERSONAL_VARS = ["name", "short_name", "phone", "email", "location"]   # usable as $name etc. in data/*.yaml


def load_defaults(kind: str, lang: str, personal: dict[str, str] | None = None,
                  example: bool = False) -> dict[str, str]:
    """Default box contents for 'application' or 'email' in a language, from data/<kind>.yaml
    (or the made-up data/<kind>.example.yaml when `example` is set or <kind>.yaml doesn't exist).

    $name, $short_name, $phone, $email and $location are filled in from data/personal.yaml."""
    path = build_cv.data_file(DATA / f"{kind}.yaml", DATA / f"{kind}.example.yaml", example)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _fill_personal(data.get(lang) or {}, personal)


def _fill_personal(values: dict, personal: dict[str, str] | None) -> dict[str, str]:
    personal = personal or build_cv.load_personal()
    subst = {k: personal[k] for k in PERSONAL_VARS}
    return {k: string.Template("" if v is None else str(v)).safe_substitute(subst).rstrip("\n")
            for k, v in values.items()}


def to_variables(text: str, personal: dict[str, str]) -> str:
    """Replace personal details in text with $variables, so they never end up in data/*.yaml."""
    pairs = sorted(((personal[k], k) for k in PERSONAL_VARS if personal.get(k)), key=lambda kv: -len(kv[0]))
    for value, key in pairs:
        text = text.replace(value, "${" + key + "}")
    return text


write_yaml = build_cv.write_yaml


def save_defaults(kind: str, lang: str, values: dict[str, str]) -> Path:
    """Replace the default text of 'application' or 'email' for one language with `values`."""
    path = DATA / f"{kind}.yaml"
    source = build_cv.data_file(path, DATA / f"{kind}.example.yaml")   # the other language starts from here
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    data[lang] = _for_yaml(values)
    header = (f"# Default text for a new {kind} in the editor, per language (da = Danish, en = English).\n"
              "# NOT in git (see .gitignore). $name, $short_name, $phone, $email, $location are filled in\n"
              "# from data/personal.yaml.\n"
              "# Written by the editor's template 'Save' button; the previous version is in the .bak file.\n\n")
    return write_yaml(path, data, header)


def _for_yaml(values: dict[str, str]) -> dict[str, str]:
    """Personal details back to $variables; multi-line text ends with a newline (YAML block style)."""
    personal = build_cv.load_personal()
    values = {k: to_variables(v, personal) for k, v in values.items()}
    return {k: (v.rstrip() + "\n" if "\n" in v else v) for k, v in values.items()}


# --------------------------------------------------------------------------- named templates
# Besides the default text above ("Default"), an email or application can start from a named template.
# They live in data/<kind>_templates.yaml (git-ignored): {name: {da: {...}, en: {...}}}.
# A template may exist in one language only.

DEFAULT_TEMPLATE = "Default"


def templates_path(kind: str) -> Path:
    return DATA / f"{kind}_templates.yaml"


def _read_templates(kind: str) -> dict[str, dict]:
    path = templates_path(kind)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _write_templates(kind: str, data: dict[str, dict]) -> Path:
    header = (f"# Named templates for a new {kind} in the editor: name -> language (da/en) -> boxes.\n"
              "# NOT in git (see .gitignore). $name, $short_name, $phone, $email, $location are filled in\n"
              "# from data/personal.yaml. Written by the editor; the previous version is in the .bak file.\n\n")
    return write_yaml(templates_path(kind), data, header)


def template_names(kind: str) -> list[str]:
    """'Default' first, then the named templates alphabetically."""
    return [DEFAULT_TEMPLATE] + sorted(_read_templates(kind), key=str.casefold)


def template_languages(kind: str, name: str) -> list[str]:
    if name == DEFAULT_TEMPLATE:
        return list(LANGUAGES)
    return [lang for lang in LANGUAGES if (_read_templates(kind).get(name) or {}).get(lang)]


def load_template(kind: str, name: str, lang: str,
                  personal: dict[str, str] | None = None) -> dict[str, str] | None:
    """Box contents of a template in a language, or None if it has no text in that language."""
    if name == DEFAULT_TEMPLATE:
        return load_defaults(kind, lang, personal)
    values = (_read_templates(kind).get(name) or {}).get(lang)
    return _fill_personal(values, personal) if isinstance(values, dict) else None


def template_name_problem(kind: str, name: str, current: str | None = None) -> str:
    """Why `name` can't be used for a new/renamed template ('' if it can)."""
    if not name.strip():
        return "Enter a name."
    if name != name.strip():
        return "The name can't start or end with a space."
    taken = {n.casefold() for n in template_names(kind) if n != current}
    if name.casefold() in taken:
        return f"There is already a template called '{name}'."
    return ""


def save_template(kind: str, name: str, lang: str, values: dict[str, str]) -> Path:
    """Save `values` as the `lang` text of template `name` (created if new)."""
    if name == DEFAULT_TEMPLATE:
        return save_defaults(kind, lang, values)
    data = _read_templates(kind)
    data.setdefault(name, {})[lang] = _for_yaml(values)
    return _write_templates(kind, data)


def rename_template(kind: str, old: str, new: str) -> Path:
    data = _read_templates(kind)
    return _write_templates(kind, {(new if k == old else k): v for k, v in data.items()})


def delete_template(kind: str, name: str) -> Path:
    data = _read_templates(kind)
    data.pop(name, None)
    return _write_templates(kind, data)


# --------------------------------------------------------------------------- import

def read_embedded(pdf: Path) -> dict | None:
    """Return the editor data attached to a PDF saved from the editor, or None if there is none."""
    listing = subprocess.run(["pdfdetach", "-list", str(pdf)], capture_output=True, text=True)
    # Lines look like "1: editor-data.json". (-savefile by name is unreliable, so save by number.)
    number = next((line.split(":", 1)[0].strip() for line in listing.stdout.splitlines()[1:]
                   if line.split(":", 1)[-1].strip() == build_cv.EMBED_NAME), None)
    if listing.returncode != 0 or number is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / build_cv.EMBED_NAME
        r = subprocess.run(["pdfdetach", "-save", number, "-o", str(out), str(pdf)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            return None
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) and "fields" in data else None


def parse_email_txt(text: str) -> tuple[dict[str, str], str]:
    """Read an email .txt saved by the editor back into (fields, language)."""
    lines = text.splitlines()
    fields, lang = {"to": "", "subject": "", "body": ""}, "da"
    i = 0
    while i < len(lines) and lines[i].strip():
        key, _, value = lines[i].partition(":")
        for code, words in LABELS.items():
            if key.strip() == words["to"]:
                fields["to"], lang = value.strip(), code
            elif key.strip() == words["subject"]:
                fields["subject"], lang = value.strip(), code
        i += 1
    if not fields["subject"]:          # not in the editor's format: keep everything as the body
        return {"to": "", "subject": "", "body": text.strip()}, lang
    fields["body"] = "\n".join(lines[i + 1:]).strip()
    return fields, lang


# --------------------------------------------------------------------------- application

# style -> (template file, LaTeX engines). Each style should match a CV template of the same name.
APPLICATION_TEMPLATES = {
    "modern": ("application_modern.tex.j2", ("lualatex", "xelatex")),
    "classic": ("application_classic.tex.j2", ("pdflatex",)),
}


def _inline(text: str) -> str:
    """Escape for LaTeX, then turn **bold** into \\textbf{...}."""
    return re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", build_cv.latex_escape(" ".join(text.split())))


def parse_body(text: str) -> list[dict]:
    """Blank line = new paragraph; lines starting with '- ' form a bullet list."""
    blocks: list[dict] = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = [line.rstrip() for line in chunk.strip().splitlines()]
        if not lines:
            continue
        if all(line.lstrip().startswith("- ") or not line.strip() for line in lines):
            items = [_inline(line.lstrip()[2:]) for line in lines if line.strip()]
            blocks.append({"kind": "list", "items": items})
        else:
            blocks.append({"kind": "paragraph", "text": _inline(" ".join(lines))})
    return blocks


def build_application(form: dict[str, str], person: dict[str, str], workdir: Path, name: str,
                      lang: str = "da", style: str = "modern", embed: str | None = None) -> Path:
    """form: company, recipient, subject, body, closing. person: name, location, phone, email."""
    esc = build_cv.latex_escape
    ctx = {
        "name": esc(person["name"]),
        "location": esc(person["location"]),
        "phone": esc(person["phone"]),
        "email": esc(person["email"]),
        "company": esc(form["company"].strip()),
        "recipient": esc(form["recipient"].strip()),
        "subject": _inline(form["subject"]),
        "attn": esc(LABELS[lang]["attn"]),
        "body": parse_body(form["body"]),
        "closing": _inline(form["closing"]),
    }
    template_file, engines = APPLICATION_TEMPLATES[style]
    return build_cv.build_document(template_file, engines, ctx, workdir, name, embed=embed)


# --------------------------------------------------------------------------- email

def email_text(form: dict[str, str], lang: str = "da") -> str:
    """Plain text ready for copy-pasting: recipient and subject on top, then the body."""
    words = LABELS[lang]
    head = []
    if form["to"].strip():
        head.append(f"{words['to']}: {form['to'].strip()}")
    head.append(f"{words['subject']}: {form['subject'].strip()}")
    return "\n".join(head) + "\n\n" + form["body"].strip() + "\n"
