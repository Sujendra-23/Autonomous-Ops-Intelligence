"""Generate the synthetic scanned-document eval set (no real data).

From backend: python -m evals.build_documents
Writes evals/documents/*.pdf|png|jpg and evals/documents.json (expected fields). Output is
deterministic: reportlab runs in invariant mode and all noise comes from a seeded RNG.
Requires reportlab, Pillow, pypdfium2 (dev requirements).
"""

from __future__ import annotations

import io
import json
import random
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).with_name("documents")
MANIFEST = Path(__file__).with_name("documents.json")

TEAMS = ["Platform", "Billing", "Mobile", "Data", "Search", "Onboarding"]
PEOPLE = ["Priya", "Marcus", "Elena", "Tomasz", "Aiko", "Dmitri", "Lucia", "Kwame"]
ACTIONS = [
    "Rotate the staging database credentials",
    "Draft the Q2 capacity plan",
    "Migrate the cron jobs to the new scheduler",
    "Publish the incident review for the March outage",
    "Add retry limits to the webhook sender",
    "Review the vendor security questionnaire",
    "Backfill the missing invoice rows",
    "Write the rollout checklist for the mobile release",
    "Benchmark the new search index",
    "Update the on-call escalation policy",
    "Remove the legacy export endpoint",
    "Prepare the customer migration FAQ",
]
DECISIONS = [
    "Adopt weekly release trains starting in April",
    "Keep Postgres as the primary datastore",
    "Defer the dark mode work to next quarter",
    "Use feature flags for every customer-facing launch",
    "Freeze schema changes during the audit window",
]
RISKS = [
    "Vendor delivery may slip past the launch date",
    "Single engineer holds all knowledge of the billing batch job",
    "Load tests have not covered the new search index",
    "Audit evidence collection is behind schedule",
]
FORMATS = ["Mar {d}, 2026", "{d} Mar 2026", "2026-03-{d:02d}"]


def _stamp(rng: random.Random, fmt_index: int) -> tuple[str, str]:
    day = rng.randint(9, 28)
    return FORMATS[fmt_index % 3].format(d=day), date(2026, 3, day).isoformat()


def _doc(i: int) -> dict:
    """Content spec for document ``i``: items, layout, and how it is 'scanned'."""
    rng = random.Random(1000 + i)  # noqa: S311 - synthetic data
    layout = "multipage" if i in {4, 12} else ["table", "minutes"][i % 2]
    negative = i in {7, 16}
    n_tasks = 0 if negative else rng.randint(2, 4)
    actions = rng.sample(ACTIONS, n_tasks)
    tasks = []
    for k, title in enumerate(actions):
        owner = None if (i + k) % 7 == 3 else rng.choice([*PEOPLE, f"{rng.choice(TEAMS)} team"])
        shown, iso = _stamp(rng, i + k)
        tasks.append({"title": title, "owner": owner, "due_text": shown, "due_date": iso})
    decisions = [] if negative or i % 3 == 0 else rng.sample(DECISIONS, rng.randint(1, 2))
    risks = [] if negative or i % 4 == 1 else rng.sample(RISKS, 1)
    return {
        "id": f"doc{i + 1:02d}",
        "layout": layout,
        "scan": ["clean", "scan", "scan", "photo"][i % 4],
        "title": f"{rng.choice(TEAMS)} sync",
        "meeting_date": date(2026, 3, rng.randint(2, 8)).isoformat(),
        "tasks": tasks,
        "decisions": decisions,
        "risks": risks,
        "topics": rng.sample(["Roadmap", "Hiring", "Budget", "Support queue", "Tooling"], 3),
    }


def _pdf(spec: dict) -> tuple[bytes, list[str]]:
    """Render the PDF; return bytes and the visible text lines for quote-grounding."""
    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=letter, invariant=True, title=spec["title"])
    lines: list[str] = []
    story = []

    def para(text: str, style: str = "BodyText") -> None:
        story.append(Paragraph(text, styles[style]))
        story.append(Spacer(1, 6))
        lines.append(text)

    para(f"{spec['title']} - meeting {spec['meeting_date']}", "Title")
    para("Agenda: " + ", ".join(spec["topics"]), "Heading3")
    if spec["layout"] == "table" and spec["tasks"]:
        rows = [["Action", "Owner", "Due"]] + [
            [t["title"], t["owner"] or "TBD", t["due_text"]] for t in spec["tasks"]
        ]
        table = Table(rows, colWidths=[300, 110, 100])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ]
            )
        )
        story += [table, Spacer(1, 10)]
        lines += [f"{t['title']} {t['owner'] or 'TBD'} {t['due_text']}" for t in spec["tasks"]]
    elif spec["tasks"]:
        para("Action items", "Heading3")
        for t in spec["tasks"]:
            who = t["owner"] or "Owner to be confirmed"
            para(f"{who} will {t['title'][0].lower()}{t['title'][1:]} by {t['due_text']}.")
    for heading, key in (("Decisions", "decisions"), ("Risks", "risks")):
        if spec[key]:
            para(heading, "Heading3")
            for entry in spec[key]:
                para(entry)
    if not spec["tasks"]:
        para("Notes: discussion only, nothing was assigned or agreed.")
    if spec["layout"] == "multipage":
        story.insert(len(story) // 2, PageBreak())
    pdf.build(story)
    return buf.getvalue(), lines


def _scan(pdf_bytes: bytes, mode: str, rng: random.Random) -> Image.Image:
    page = pdfium.PdfDocument(pdf_bytes)[0]
    image = page.render(scale=1.3).to_pil().convert("L")
    if mode == "clean":
        return image.convert("RGB")
    image = image.rotate(rng.uniform(-1.8, 1.8), expand=True, fillcolor=255)
    image = image.filter(ImageFilter.GaussianBlur(0.8 if mode == "scan" else 1.3))
    noise = Image.effect_noise(image.size, 14 if mode == "scan" else 24)
    image = Image.blend(image, noise, 0.08 if mode == "scan" else 0.14)
    return image.convert("RGB")


def build() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.iterdir():
        old.unlink()
    cases = []
    for i in range(20):
        spec = _doc(i)
        rng = random.Random(5000 + i)  # noqa: S311 - synthetic data
        pdf_bytes, lines = _pdf(spec)
        # Every 4th doc stays a PDF, the others are raster "scans" saved as PNG or JPEG.
        if i % 4 == 0:
            fname, pages = f"{spec['id']}.pdf", len(pdfium.PdfDocument(pdf_bytes))
            (OUT / fname).write_bytes(pdf_bytes)
        else:
            image = _scan(pdf_bytes, "scan" if spec["scan"] == "clean" else spec["scan"], rng)
            ext = "jpg" if i % 4 == 3 else "png"
            fname, pages = f"{spec['id']}.{ext}", 1
            if ext == "jpg":
                image.save(OUT / fname, quality=60)
            else:  # 16-colour greyscale keeps the PNG scans small
                image.convert("L").quantize(16).save(OUT / fname, optimize=True)
        cases.append(
            {
                "id": spec["id"],
                "file": fname,
                "pages": pages,
                "meeting_date": spec["meeting_date"],
                "tasks": [
                    {"title": t["title"], "owner": t["owner"], "due_date": t["due_date"]}
                    for t in spec["tasks"]
                ],
                "decisions": [{"summary": d} for d in spec["decisions"]],
                "risks": [{"title": r} for r in spec["risks"]],
                "text": lines,
            }
        )
    MANIFEST.write_text(json.dumps({"cases": cases}, indent=1) + "\n")
    print(f"wrote {len(cases)} documents to {OUT}")


if __name__ == "__main__":
    build()
