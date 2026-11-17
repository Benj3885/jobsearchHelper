"""Render data/cv.yaml into LaTeX and compile it to PDF.

Usage:
    python build_cv.py                      # default track (robotics_vision)
    python build_cv.py --track software
    python build_cv.py --template classic   # the plain one-column layout
    python build_cv.py --tex-only           # write .tex without compiling
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA = DATA_DIR / "cv.yaml"                              # not in git (the real CV content)
DATA_EXAMPLE = DATA_DIR / "cv.example.yaml"           # made-up CV content: example PDFs, and used when cv.yaml is missing
PERSONAL = DATA_DIR / "personal.yaml"                    # not in git
PERSONAL_EXAMPLE = DATA_DIR / "personal.example.yaml"    # made-up data, used when personal.yaml is missing
PERSONAL_KEYS = ["name", "short_name", "location", "phone", "email", "github", "linkedin", "file_prefix"]
CONTACT_KEYS = ["location", "phone", "email", "github", "linkedin"]
TEMPLATES = ROOT / "templates"
BUILD = ROOT / "build"            # finished PDFs only
WORK = BUILD / "work"             # temporary: one subfolder per PDF while it is built (see clean_workdir)

class BuildError(Exception):
    """Raised when the CV cannot be rendered or compiled."""


_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_LATEX_RE = re.compile("|".join(re.escape(k) for k in _LATEX_ESCAPES))


def latex_escape(value):
    """Recursively escape LaTeX special characters in all strings."""
    if isinstance(value, str):
        return _LATEX_RE.sub(lambda m: _LATEX_ESCAPES[m.group()], value)
    if isinstance(value, list):
        return [latex_escape(v) for v in value]
    if isinstance(value, dict):
        return {k: latex_escape(v) for k, v in value.items()}
    return value


def build_context(cv: dict, track: str) -> dict:
    if track not in cv["tracks"]:
        raise BuildError(f"Unknown track '{track}'. Choose from: {', '.join(cv['tracks'])}")
    t = cv["tracks"][track]

    skills = dict(cv["skills"])
    if "languages_override" in t:
        skills["languages"] = {**skills["languages"], "text": t["languages_override"]}

    contact = dict(cv["contact"])
    ctx = latex_escape({
        "name": cv["name"],
        "headline": t["headline"],
        "contact": contact,
        "profile": cv["profile"],
        "skills": [skills[key] for key in t["skills_order"]],
        "experience": cv["experience"],
        "projects": cv.get("projects", []),
        "publication": cv.get("publication", ""),
        "education": cv["education"],
        "other": cv.get("other", []),
        "cv_title": t.get("cv_title", t["headline"]),
        "sidebar": cv.get("sidebar", {}),
        "main_sections": t.get("main_sections", cv.get("main_sections", [])),
    })
    # URLs go into \href unescaped (hyperref handles them); only escape %/# for safety.
    ctx["contact"]["github_url"] = contact.get("github", "").replace("%", r"\%").replace("#", r"\#")
    ctx["contact"]["linkedin_url"] = contact.get("linkedin", "").replace("%", r"\%").replace("#", r"\#")
    ctx["projects_first"] = t.get("projects_first", False)
    return ctx


# template name -> (template file, acceptable LaTeX engines in order of preference).
# fontspec templates (system fonts like Carlito) need xelatex or lualatex.
TEMPLATE_CHOICES = {
    "sidebar": ("cv_sidebar.tex.j2", ("lualatex", "xelatex")),   # looks like the user's original CV
    "modern": ("cv_modern.tex.j2", ("lualatex", "xelatex")),     # cleaner take on the sidebar layout
    "merged": ("cv_merged.tex.j2", ("lualatex", "xelatex")),     # modern, with details under each timeline entry
    "classic": ("cv_classic.tex.j2", ("pdflatex",)),
}
LATEXMK_FLAG = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}


def render(ctx: dict, template_file: str) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        block_start_string="((*", block_end_string="*))",
        variable_start_string="(((", variable_end_string=")))",
        comment_start_string="((=", comment_end_string="=))",
        trim_blocks=True, lstrip_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    return env.get_template(template_file).render(**ctx)


def compile_pdf(tex_path: Path, engines: tuple[str, ...]) -> Path:
    """Compile with the first installed engine (via latexmk if present), else tectonic.

    Returns the PDF path next to the .tex file."""
    workdir = tex_path.parent
    flags = ["-interaction=nonstopmode", "-halt-on-error"]
    engine = next((e for e in engines if shutil.which(e)), None)
    if engine and shutil.which("latexmk"):
        cmd = ["latexmk", LATEXMK_FLAG[engine], *flags, tex_path.name]
    elif engine:
        cmd = [engine, *flags, tex_path.name]
    elif shutil.which("tectonic"):
        cmd = ["tectonic", tex_path.name]
    else:
        raise BuildError(f"No LaTeX compiler found ({' or '.join(engines)}, or tectonic). "
                 f"The .tex file is at {tex_path}")
    result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if result.returncode != 0:
        log = result.stdout[-3000:] + result.stderr[-1000:]
        raise BuildError(f"LaTeX failed ({' '.join(cmd)}):\n{log}\nFull log: {tex_path.with_suffix('.log')}")
    return tex_path.with_suffix(".pdf")


EMBED_NAME = "editor-data.json"   # attachment inside PDFs saved from the editor (see cv_editor.py)


def build(cv: dict, template: str, track: str, workdir: Path, name: str, compile: bool = True,
          embed: str | None = None) -> Path:
    """Render `cv` with a template into workdir/<name>.tex and (optionally) compile it.

    Returns the .pdf path (or the .tex path when compile=False), both inside workdir."""
    if template not in TEMPLATE_CHOICES:
        raise BuildError(f"Unknown template '{template}'. Choose from: {', '.join(TEMPLATE_CHOICES)}")
    template_file, engines = TEMPLATE_CHOICES[template]
    return build_document(template_file, engines, build_context(cv, track), workdir, name, compile, embed)


def build_document(template_file: str, engines: tuple[str, ...], ctx: dict, workdir: Path, name: str,
                   compile: bool = True, embed: str | None = None) -> Path:
    """Render any template with an already-escaped context into workdir/<name>.tex and compile it.

    `embed` (JSON text) is attached to the PDF as editor-data.json, so the editor can import it later."""
    workdir.mkdir(parents=True, exist_ok=True)
    ctx = {**ctx, "embed": ""}
    if embed is not None:
        (workdir / EMBED_NAME).write_text(embed, encoding="utf-8")
        ctx["embed"] = EMBED_NAME
    tex = render(ctx, template_file)
    tex_path = workdir / f"{name}.tex"
    tex_path.write_text(tex, encoding="utf-8")
    if not compile:
        return tex_path
    pdf = compile_pdf(tex_path, engines)      # on failure the .tex/.log stay for debugging
    clean_workdir(workdir, keep=pdf)
    return pdf


def clean_workdir(workdir: Path, keep: Path | None = None) -> None:
    """Delete LaTeX by-products (.tex, .log, editor-data.json, …): they contain personal information."""
    for f in workdir.iterdir():
        if f != keep and f.is_file():
            f.unlink()


# --------------------------------------------------------------------------- personal data + YAML files

def data_file(real: Path, example_path: Path, example: bool = False) -> Path:
    """The real (git-ignored) data file, or its made-up example when asked for or when the real one
    doesn't exist yet (e.g. a fresh clone)."""
    return example_path if example or not real.exists() else real


def load_personal(example: bool = False) -> dict[str, str]:
    """Name and contact details from data/personal.yaml (or the made-up example)."""
    path = data_file(PERSONAL, PERSONAL_EXAMPLE, example)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {k: "" if data.get(k) is None else str(data[k]) for k in PERSONAL_KEYS}


_TRANSLIT = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "Æ": "AE", "Ø": "OE", "Å": "AA",
                           "ä": "ae", "ö": "oe", "ü": "ue", "é": "e"})


def file_prefix(personal: dict[str, str]) -> str:
    """Prefix for saved files: `file_prefix`, or first + last name ('Jane Example Doe' -> 'JANE_DOE')."""
    if personal.get("file_prefix"):
        return personal["file_prefix"]
    words = personal.get("name", "").split()
    words = words[:1] + words[-1:] if len(words) > 1 else words
    prefix = "_".join(w.translate(_TRANSLIT) for w in words).upper()
    return re.sub(r"[^A-Z0-9_]+", "", prefix) or "CV"


def load_cv(personal: dict[str, str] | None = None, example: bool = False) -> dict:
    """cv.yaml (or the made-up cv.example.yaml) with name and contact details filled in from personal.yaml."""
    cv = yaml.safe_load(data_file(DATA, DATA_EXAMPLE, example).read_text(encoding="utf-8"))
    p = personal or load_personal()
    cv["name"] = p["name"]
    cv["contact"] = {k: p[k] for k in CONTACT_KEYS}
    return cv


def _str_presenter(dumper, value: str):
    """Write multi-line strings as readable YAML blocks (|) instead of quoted lines."""
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _str_presenter)


def write_yaml(path: Path, data: dict, header: str = "") -> Path:
    """Write YAML, keeping a backup of the previous file as <name>.yaml.bak. Comments are not preserved."""
    if path.exists():
        shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))
    text = yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=100)
    path.write_text(header + text, encoding="utf-8")
    return path


def save_cv(cv: dict) -> None:
    """Write a CV dict back: name/contact go to personal.yaml, everything else to cv.yaml."""
    personal = load_personal()
    personal["name"] = cv.get("name", personal["name"])
    personal.update({k: cv.get("contact", {}).get(k, personal[k]) for k in CONTACT_KEYS})
    write_yaml(PERSONAL, personal, "# Personal information. NOT in git (see .gitignore).\n"
                                   "# Written by the editor; the previous version is in personal.yaml.bak.\n\n")
    rest = {k: v for k, v in cv.items() if k not in ("name", "contact")}
    write_yaml(DATA, rest, "# CV content. NOT in git (see .gitignore); name and contact details are in personal.yaml.\n"
                           "# Written by the editor's 'Save as default' button; the previous version is in cv.yaml.bak.\n\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--track", default="robotics_vision")
    parser.add_argument("--template", default="sidebar", choices=TEMPLATE_CHOICES)
    parser.add_argument("--tex-only", action="store_true")
    args = parser.parse_args()

    name = f"cv_{args.template}_{args.track}"
    workdir = WORK / name             # one folder per PDF: build/work/<name>/
    try:
        out = build(load_cv(), args.template, args.track, workdir, name, compile=not args.tex_only)
    except BuildError as e:
        sys.exit(str(e))
    if args.tex_only:
        print(f"Wrote {out.relative_to(ROOT)}")
    if not args.tex_only:
        # Move the finished PDF up into build/, replacing any previous version.
        pdf = Path(shutil.move(out, BUILD / out.name))
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"Wrote {pdf.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
