"""Build the example PDF for every template into templates/, using the made-up personal data.

Run after adding or changing a template:  python make_examples.py

The examples use only made-up data (data/personal.example.yaml, data/cv.example.yaml,
data/application.example.yaml), so they are safe to commit. CV examples use the robotics_vision track; application examples the Danish default text.
"""

from __future__ import annotations

import shutil

import build_cv
import documents

WORK = build_cv.WORK / "_examples"
TEMPLATES = build_cv.ROOT / "templates"


def main() -> None:
    personal = build_cv.load_personal(example=True)
    cv = build_cv.load_cv(personal, example=True)
    for style in build_cv.TEMPLATE_CHOICES:
        name = f"cv_{style}_robotics_vision"
        pdf = build_cv.build(cv, style, "robotics_vision", WORK, name)
        shutil.copyfile(pdf, TEMPLATES / f"{name}.pdf")
        print(f"templates/{name}.pdf")

    person = {k: personal[k] for k in ("name", "location", "phone", "email")}
    form = documents.load_defaults("application", "da", personal, example=True)
    for style in documents.APPLICATION_TEMPLATES:
        pdf = documents.build_application(form, person, WORK, f"application_{style}", "da", style)
        shutil.copyfile(pdf, TEMPLATES / f"application_{style}_example.pdf")
        print(f"templates/application_{style}_example.pdf")
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
