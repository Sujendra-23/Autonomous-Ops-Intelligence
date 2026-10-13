"""Deterministic text DLP. Findings contain offsets/types, never matched values.

Emails and phones have no checksum. Cards use Luhn, Aadhaar uses Verhoeff;
US SSNs use structural validation. This is a bounded detector, not a guarantee
that arbitrary international identifiers or natural-language PII are recognized.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Literal

Sensitivity = Literal["public", "internal", "confidential"]
RANK = {"public": 0, "internal": 1, "confidential": 2}
POLICY_VERSION = "regex-checksum-v1"

EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+", re.ASCII)
PHONE = re.compile(
    r"(?<!\w)(?:\+[1-9][0-9](?:[ .()-]*[0-9]){6,13}"
    r"|(?:\+?1[ .-]?)?(?:\([2-9][0-9]{2}\)|[2-9][0-9]{2})"
    r"[ .-]?[2-9][0-9]{2}[ .-]?[0-9]{4})(?!\w)"
    r"(?:\s*(?:ext\.?|x)\s*[0-9]{1,6})?",
    re.IGNORECASE,
)
CARD = re.compile(r"(?<!\w)[0-9](?:[ -]?[0-9]){12,18}(?!\w)")
SSN = re.compile(r"(?<!\w)[0-9]{3}-[0-9]{2}-[0-9]{4}(?!\w)")
AADHAAR = re.compile(r"(?<!\w)[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}(?!\w)")
LABELED_ID = re.compile(
    r"\b(?:passport|national\s+id|tax\s+id|driver'?s?\s+licen[cs]e)"
    r"(?:\s+(?:number|no\.?))?\s*[:=#]\s*([A-Z0-9][A-Z0-9-]{4,19})\b",
    re.IGNORECASE,
)
CONFIDENTIAL = re.compile(r"\b(?:confidential|restricted|trade secret)\b", re.IGNORECASE)


def luhn_valid(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit() and c.isascii()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


# Verhoeff's dihedral multiplication/permutation tables.
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def aadhaar_valid(value: str) -> bool:
    digits = re.sub(r"[ -]", "", value)
    if len(digits) != 12 or digits[0] not in "23456789" or len(set(digits)) == 1:
        return False
    checksum = 0
    for i, digit in enumerate(reversed(digits)):
        checksum = _D[checksum][_P[i % 8][int(digit)]]
    return checksum == 0


@dataclass(frozen=True)
class Finding:
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class DLPResult:
    text: str
    label: Sensitivity
    counts: dict[str, int]


def detect(text: str) -> list[Finding]:
    candidates = []
    for kind, pattern in (
        ("EMAIL", EMAIL),
        ("CARD", CARD),
        ("SSN", SSN),
        ("AADHAAR", AADHAAR),
        ("ID", LABELED_ID),
        ("PHONE", PHONE),
    ):
        for match in pattern.finditer(text):
            value = match.group()
            if kind == "CARD" and not luhn_valid(value):
                continue
            if kind == "AADHAAR" and not aadhaar_valid(value):
                continue
            if kind == "SSN":
                area, group, serial = value.split("-")
                if area in {"000", "666"} or int(area) >= 900 or group == "00" or serial == "0000":
                    continue
            start, end = match.span(1) if kind == "ID" else match.span()
            candidates.append(Finding(start, end, kind))
    # Prefer the longest span, then the stronger identifier type over phone.
    selected: list[Finding] = []
    for finding in sorted(candidates, key=lambda f: (-(f.end - f.start), f.kind == "PHONE")):
        if not any(finding.start < f.end and f.start < finding.end for f in selected):
            selected.append(finding)
    return sorted(selected, key=lambda f: f.start)


def classify(text: str, requested: Sensitivity = "internal") -> DLPResult:
    if requested not in RANK:
        raise ValueError("Unknown sensitivity label")
    findings = detect(text)
    label = (
        "confidential"
        if findings
        or re.search(r"\[REDACTED:(?:EMAIL|PHONE|CARD|SSN|AADHAAR|ID)\]", text)
        or CONFIDENTIAL.search(text)
        else requested
    )
    redacted = text
    for finding in reversed(findings):
        redacted = (
            redacted[: finding.start] + f"[REDACTED:{finding.kind}]" + redacted[finding.end :]
        )
    return DLPResult(redacted, label, dict(Counter(f.kind for f in findings)))


def redact(text: str) -> str:
    return classify(text).text


def redact_payload(value):
    """Copy JSON-compatible payloads, redacting strings but preserving schema keys."""
    if isinstance(value, str):
        # Machine UUIDs are references, not Aadhaar/phone candidates.
        try:
            uuid.UUID(value)
        except ValueError:
            return redact(value)
        return value
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_payload(item) for key, item in value.items()}
    return value


def protect_transcript(transcript) -> None:
    """Sanitize text/metadata before persistence or extraction; labels only rise."""
    label = transcript.sensitivity_label or "internal"
    counts = dict(transcript.dlp_findings or {})

    def protect(value):
        nonlocal label
        if isinstance(value, str):
            result = classify(value, label)
            label = max((label, result.label), key=RANK.__getitem__)
            # Preserve counts on retry; do not recount placeholders.
            for kind, count in result.counts.items():
                counts[kind] = max(counts.get(kind, 0), count)
            return result.text
        if isinstance(value, list):
            return [protect(item) for item in value]
        if isinstance(value, dict):
            return {key: protect(item) for key, item in value.items()}
        return value

    for field in ("content", "title", "participants", "calendar_context", "error"):
        setattr(transcript, field, protect(getattr(transcript, field)))
    transcript.sensitivity_label = label
    transcript.dlp_findings = counts
    transcript.dlp_policy_version = POLICY_VERSION
