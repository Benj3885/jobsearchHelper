# Job search assistant

A Python tool that helps the user with their job search: keeping track of which
companies have been contacted, drafting emails and applications with Claude, and producing the CV
and applications as PDFs with LaTeX.

The user writes in Danish or English; answer in the language they use.

## Layout

| Path | Content |
|---|---|
| `build_cv.py` | Renders `data/cv.yaml` through a Jinja template into LaTeX and compiles it to PDF |
| `cv_editor.py` | Tkinter desktop editor. Left: document type (Email / Application / CV). Middle: file name, Compile/Preview + Save, preview. Right: text boxes for the chosen type |
| `tabs.py` | The editor's other tabs: **Job plan** (shows `docs/job-search-plan.md` formatted; "Edit" opens the Markdown source, "Save" writes it back and keeps `.bak`) and **Companies** (`docs/employers.csv` as a table; Status / Last contact / Notes are edited there and saved straight to the CSV; "Write application" opens the application with the company filled in) |
| `documents.py` | Builders for the job application (LaTeX PDF) and email (plain text) |
| `output/` | Files saved from the editor: `<PREFIX>_<name>_CV.pdf`, `..._APPLICATION.pdf`, `..._EMAIL.txt`. `<PREFIX>` comes from `personal.yaml`; an empty application name uses the company |
| `data/personal.yaml` | **Personal information** (name, contact, file prefix). Git-ignored. Template: `data/personal.example.yaml` (made-up) |
| `data/cv.yaml` | CV content without name/contact (those are merged in from `personal.yaml` by `load_cv`). Edit content here, never in generated `.tex`. Git-ignored |
| `data/application.yaml`, `data/email.yaml` | Default text for a new application / email, per language (`da`, `en`). `$name`, `$short_name`, `$phone`, `$email`, `$location` are filled in from `personal.yaml`. Git-ignored |
| `make_examples.py` | Builds every template's example PDF into `templates/` from made-up data only |
| `data/*.example.yaml` | Made-up versions of every data file (fictional person, town and companies). Used for the example PDFs, and in place of any real data file that is missing (`build_cv.data_file`), e.g. in a fresh clone. Keep their structure in sync with the real files |
| `templates/cv_<name>.tex.j2` | CV templates (Jinja + LaTeX) |
| `templates/application_<name>.tex.j2` | Application letter templates |
| `templates/*_robotics_vision.pdf`, `templates/*_example.pdf` | Example PDF for each template (see below) |
| `docs/` | (git-ignored) Material exported from claude.ai: job search plan, CV, application/email templates (`.md`), employer list (`employers.csv`) |
| `build/` | (git-ignored) Finished PDFs only. Generated, overwritable |
| `build/work/<pdf name>/` | Temporary build folder per PDF. By-products (`.tex`, `.log`, `editor-data.json`) contain personal information and are deleted after a successful build (kept on failure for debugging). The editor's `_preview_*` folders are deleted when it closes |
| `/*.pdf` | (git-ignored) The user's original CV, which the `sidebar` template is modelled on |

## Building the CV

```
python build_cv.py                                   # default template (sidebar), robotics_vision track
python build_cv.py --template modern --track software
python build_cv.py --tex-only                        # render .tex without compiling
```

- The editor window has tabs at the top: Documents (below), Job plan and Companies (`tabs.py`).
  This Tk has no Xft, so only Latin-1 characters display; `tabs.shown()` maps e.g. • – “ to plain lookalikes.
- Editor: `python cv_editor.py` (needs a Python with tkinter: the conda `python`, not `/bin/python3`).
  Each document type compiles into `build/work/_preview_<type>/` and only copies to `output/` on Save.
  Edits stay in the window; the YAML files are not modified. The application takes name/contact from
  the CV boxes. CV mode supports the templates whose fields match its boxes (`TEMPLATES`).
- PDFs built by the editor carry the box contents as an attachment (`editor-data.json`, written by
  `build_document(embed=...)`); Import reads it with `pdfdetach`. Email `.txt` files are parsed directly.
- "Save as default" writes the boxes back to `data/*.yaml` after confirmation, keeping `<file>.yaml.bak`.
  YAML comments are lost when that happens.
- Required fields (`REQUIRED` in `cv_editor.py`, marked with a red *) block **saving**, not compiling,
  while they contain a `[placeholder]`. Emptying or replacing it unblocks. Save checks the compiled
  version, since that is what gets saved.
- Language (Dansk/English) is chosen on the left. It swaps the default text of email and application
  (asking first if edited) and fixed words in the output (`LABELS` in `documents.py`). The CV text is English only.
- The program is built incrementally: each request is one step, not the finished program. The left
  column of the editor has more features coming.
- **Personal information never goes into tracked files.** Contact details live in `data/personal.yaml`; the CV
  content and default email/application text (`cv.yaml`, `application.yaml`, `email.yaml`) are git-ignored too.
  Tracked files (code, templates, `*.example.yaml`, example PDFs, this file) contain only made-up data:
  no real name, town, employer or history. Facts about the user go in `CLAUDE.local.md` (git-ignored).
  "Save as default" splits the CV (`build_cv.save_cv`) and turns personal details in email/application
  text back into `$variables` (`documents.to_variables`). See `.gitignore` for what else is ignored
  (`docs/`, `output/`, `build/`, root PDFs, `*.bak`).
- Keep it simple: no web server. Ask before adding a new library dependency.
- Templates and their engines are registered in `TEMPLATE_CHOICES` in `build_cv.py`.
- Tracks (`robotics_vision`, `software`) are defined under `tracks:` in `cv.yaml` and change
  headline, title, skill order and section order.
- Templates using `fontspec` (system fonts such as Carlito or Lato) need `lualatex` or `xelatex`;
  `classic` uses `pdflatex`. LuaLaTeX is installed; XeLaTeX may not be.
- Jinja uses LaTeX-safe delimiters: `((( var )))`, `((* block *))`, `((= comment =))`. Do not use
  these sequences in LaTeX comments inside a template.
- All strings from the YAML are LaTeX-escaped in `build_context`. Templates must not escape again.
- Start bullets with `\item{}` so text beginning with `[` (placeholders) isn't read as an item label.

## Adding a new template

1. CV: create `templates/cv_<name>.tex.j2` and register it in `TEMPLATE_CHOICES`.
   Application: create `templates/application_<name>.tex.j2` and register it in `APPLICATION_TEMPLATES`
   in `documents.py` (name it after the matching CV style).
   Every template must include the `embed` blocks (`\usepackage{embedfile}` + `\embedfile`), so the
   editor's Import can read saved PDFs back.
2. Build it (CV: both tracks) and look at the rendered PDF (e.g. `pdftoppm -png`) before calling it done.
3. **Every template must have an example PDF**: CV → `templates/cv_<name>_robotics_vision.pdf`,
   application → `templates/application_<name>_example.pdf`. Run `python make_examples.py`
   whenever a template is added or changed; it uses only the made-up `*.example.yaml` data, so examples are
   safe to commit. If a template needs a new field, add it to `cv.example.yaml` too.

## Content guidelines

- Don't invent facts for the CV or applications. Unknowns stay as `[bracket placeholders]`.
- Positioning and the user's background: see `CLAUDE.local.md` and `docs/job-search-plan.md`.
- Keep the user's personal data local; never send it to external services.
