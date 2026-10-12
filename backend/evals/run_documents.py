"""Document-extraction eval: per-field precision/recall on synthetic scanned documents.

From backend:
    python -m evals.run_documents            # offline: a stub model returns the reference answers
    python -m evals.run_documents --live     # real model via the configured provider (API usage)

Offline mode renders every document through the real page-rendering path and the real
schema validation, but the "model" is a stub that replays the reference JSON, so it
proves the plumbing and the scorer, not model accuracy. Only ``--live`` measures a model.
Entities are matched to the reference by fuzzy title (difflib ratio >= 0.6); field
precision/recall are then computed per field (see ``score``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from app.config import get_settings
from app.llm.client import LLMClient, LLMError, _parse_extraction, get_llm_client
from app.llm.vision import ImagePart
from app.schemas.extraction import ExtractionResult
from app.services.documents import prepare_pages

HERE = Path(__file__).parent
MANIFEST = HERE / "documents.json"
MATCH_THRESHOLD = 0.6

ENTITY_KEYS = {"tasks": "title", "decisions": "summary", "risks": "title"}


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def _date(value: object) -> str | None:
    return str(value)[:10] if value else None


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None


@dataclass
class Scores:
    fields: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    quotes_total: int = 0
    quotes_grounded: int = 0
    docs: int = 0
    failed: int = 0

    def merge(self, other: Scores) -> None:
        for name, c in other.fields.items():
            mine = self.fields[name]
            mine.tp, mine.fp, mine.fn = mine.tp + c.tp, mine.fp + c.fp, mine.fn + c.fn
        self.quotes_total += other.quotes_total
        self.quotes_grounded += other.quotes_grounded


def _match(expected: list[dict], predicted: list[dict], key: str) -> list[tuple[int, int]]:
    """Greedy best-first pairing of expected and predicted entities by title similarity."""
    scored = sorted(
        (
            (SequenceMatcher(None, _norm(e[key]), _norm(p[key])).ratio(), i, j)
            for i, e in enumerate(expected)
            for j, p in enumerate(predicted)
        ),
        reverse=True,
    )
    used_e: set[int] = set()
    used_p: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for ratio, i, j in scored:
        if ratio < MATCH_THRESHOLD:
            break
        if i not in used_e and j not in used_p:
            used_e.add(i)
            used_p.add(j)
            pairs.append((i, j))
    return pairs


def score(case: dict, predicted: dict) -> Scores:
    """Score one document. ``predicted`` is ``ExtractionResult.model_dump(mode="json")``.

    Per entity type (tasks, decisions, risks): TP = matched, FP = predicted but unmatched,
    FN = expected but unmatched. For ``task.owner`` and ``task.due_date``: TP = both
    present and equal; FP = predicted a value that is wrong or has no counterpart; FN =
    a reference value that was missing, wrong, or on a task the model did not find.
    ``source_quote`` grounding: share of predicted quotes that appear in the document text.
    """
    out = Scores(docs=1)
    document_text = _norm(" ".join(case["text"]))
    for kind, key in ENTITY_KEYS.items():
        expected, got = case[kind], predicted.get(kind, [])
        pairs = _match(expected, got, key)
        counts = out.fields[kind]
        counts.tp += len(pairs)
        counts.fp += len(got) - len(pairs)
        counts.fn += len(expected) - len(pairs)
        for item in got:
            out.quotes_total += 1
            out.quotes_grounded += _norm(item["source_quote"]) in document_text
        if kind != "tasks":
            continue
        matched_e = {i for i, _ in pairs}
        matched_p = {j for _, j in pairs}
        for name, norm in (("task.owner", _norm), ("task.due_date", _date)):
            field_name = name.split(".")[1]
            c = out.fields[name]
            for i, j in pairs:
                want, have = norm(expected[i][field_name]), norm(got[j].get(field_name))
                if want and have and want == have:
                    c.tp += 1
                else:
                    c.fp += bool(have)
                    c.fn += bool(want)
            c.fn += sum(bool(norm(expected[i][field_name])) for i in range(len(expected))
                        if i not in matched_e)  # fmt: skip
            c.fp += sum(bool(norm(got[j].get(field_name))) for j in range(len(got))
                        if j not in matched_p)  # fmt: skip
    return out


class ReferenceStub(LLMClient):
    """Offline model: checks it received the right page images, replays the reference JSON."""

    def __init__(self) -> None:
        self.case: dict | None = None

    async def extract(self, *args: object, **kwargs: object) -> ExtractionResult:
        raise LLMError("stub only supports extract_images")

    async def extract_images(self, images: list[ImagePart], **kwargs: object) -> ExtractionResult:
        assert self.case is not None
        if len(images) != self.case["pages"] or not all(i.data[:4] == b"\x89PNG" for i in images):
            raise LLMError("stub received unexpected page images")
        quote = " ".join(self.case["text"])[:200]
        reply = {
            "summary": f"Reference answer for {self.case['id']}",
            "tasks": [{**t, "source_quote": quote, "confidence": 0.9} for t in self.case["tasks"]],
            "decisions": [
                {**d, "source_quote": quote, "confidence": 0.9} for d in self.case["decisions"]
            ],
            "risks": [{**r, "source_quote": quote, "confidence": 0.9} for r in self.case["risks"]],
        }
        return _parse_extraction(json.dumps(reply), provider="stub")


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.2f}"


def report(total: Scores) -> dict:
    rows: dict[str, dict] = {}
    print(f"\n{'field':<14}{'precision':>10}{'recall':>8}{'tp':>5}{'fp':>5}{'fn':>5}")
    for name in ("tasks", "task.owner", "task.due_date", "decisions", "risks"):
        c = total.fields[name]
        print(f"{name:<14}{_fmt(c.precision):>10}{_fmt(c.recall):>8}{c.tp:>5}{c.fp:>5}{c.fn:>5}")
        rows[name] = {
            "precision": c.precision, "recall": c.recall, "tp": c.tp, "fp": c.fp, "fn": c.fn,
        }  # fmt: skip
    grounded = total.quotes_grounded / total.quotes_total if total.quotes_total else None
    print(f"\nsource_quote grounded in document text: {_fmt(grounded).strip()}")
    return {"fields": rows, "quote_grounding": grounded, "docs": total.docs, "failed": total.failed}


async def run(live: bool, json_out: Path | None = None) -> int:
    cases = json.loads(MANIFEST.read_text())["cases"]
    if live:
        settings = get_settings()
        provider_key = (
            settings.anthropic_api_key if settings.llm_provider == "anthropic"
            else settings.openai_api_key
        ).get_secret_value()  # fmt: skip
        if not provider_key:
            print(f"--live needs {settings.llm_provider.upper()}_API_KEY; none configured.")
            return 2
        client: LLMClient = get_llm_client()
    else:
        client = ReferenceStub()

    total = Scores()
    for case in cases:
        path = HERE / "documents" / case["file"]
        try:
            pages = prepare_pages(path.name, path.read_bytes())
            if isinstance(client, ReferenceStub):
                client.case = case
            result = await client.extract_images(
                pages, meeting_title=case["id"], meeting_date=case["meeting_date"]
            )
            total.merge(score(case, result.model_dump(mode="json")))
            status = "ok"
        except Exception as exc:  # report the doc as failed without leaking model content
            total.failed += 1
            status = f"FAIL ({type(exc).__name__})"
        total.docs += 1
        print(f"{case['id']} {case['file']:<10} {status}")

    summary = report(total)
    summary["mode"] = "live" if live else "offline-stub"
    print(f"{total.docs} documents, {total.failed} failed ({summary['mode']})")
    if not live:
        print("Offline stub replays the reference answers: this validates the harness only.")
    if json_out:
        json_out.write_text(json.dumps(summary, indent=1) + "\n")
    return 0 if total.failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Calls a real model; incurs API usage")
    parser.add_argument("--json", type=Path, help="Also write the summary to this file")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.live, args.json)))
