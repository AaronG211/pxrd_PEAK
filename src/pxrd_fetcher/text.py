"""Text and OCR parsing helpers."""

from __future__ import annotations

import math
import re
from typing import Iterable, List, Optional, Sequence, Tuple


PXRD_KEYWORDS = {
    "pxrd": 1.0,
    "powder x-ray diffraction": 1.0,
    "powder x ray diffraction": 1.0,
    "xrd": 0.7,
    "xrpd": 1.0,
    "diffractogram": 0.6,
    "2theta": 0.5,
    "2 theta": 0.5,
    "2θ": 0.5,
    "bragg": 0.3,
}

NEGATIVE_KEYWORDS = {
    "nmr": -0.8,
    "uv-vis": -0.7,
    "uv vis": -0.7,
    "ftir": -0.7,
    "sem": -0.8,
    "tem": -0.8,
    "xps": -0.6,
    "raman": -0.7,
}

FIGURE_LABEL_RE = re.compile(r"\b(fig(?:ure)?\.?\s*\d+[a-z]?)", re.IGNORECASE)
NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

OCR_TRANSLATION = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "S": "5",
        "B": "8",
        ",": ".",
        "−": "-",
        "–": "-",
        "—": "-",
        "°": "",
        "º": "",
        "·": ".",
        "|": "1",
    }
)

AXIS_TEXT_REPLACEMENTS = {
    "θ": " theta ",
    "ϑ": " theta ",
    "Θ": " theta ",
    "°": " degree ",
    "º": " degree ",
}


def keyword_score(text: str) -> float:
    normalized = " ".join(text.lower().split())
    raw_score = 0.0
    for keyword, weight in PXRD_KEYWORDS.items():
        if keyword in normalized:
            raw_score += weight
    for keyword, weight in NEGATIVE_KEYWORDS.items():
        if keyword in normalized:
            raw_score += weight

    score = 1.0 / (1.0 + math.exp(-raw_score))
    return float(round(score, 4))


def extract_figure_label(text: str) -> Optional[str]:
    match = FIGURE_LABEL_RE.search(text)
    if not match:
        return None
    return match.group(1).strip()


def clean_numeric_token(token: str) -> str:
    cleaned = token.strip().translate(OCR_TRANSLATION)
    cleaned = cleaned.replace(" ", "")
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    if cleaned.count(".") > 1:
        head, tail = cleaned.split(".", 1)
        cleaned = head + "." + tail.replace(".", "")
    if cleaned.count("-") > 1:
        cleaned = "-" + cleaned.replace("-", "")
    if cleaned.startswith("."):
        cleaned = "0" + cleaned
    if cleaned.startswith("-."):
        cleaned = cleaned.replace("-.", "-0.", 1)
    return cleaned


def parse_numeric_token(token: str) -> Optional[float]:
    cleaned = clean_numeric_token(token)
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    if not NUMERIC_RE.match(cleaned):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_numeric_ticks(entries: Sequence[Tuple[str, float]]) -> List[Tuple[float, float]]:
    parsed: List[Tuple[float, float]] = []
    for text, pixel in entries:
        value = parse_numeric_token(text)
        if value is None:
            continue
        parsed.append((pixel, value))
    return parsed


def normalize_axis_label_text(text: str) -> str:
    normalized = text.lower()
    for source, target in AXIS_TEXT_REPLACEMENTS.items():
        normalized = normalized.replace(source, target)
    normalized = normalized.replace("-", " ").replace("_", " ")
    normalized = re.sub(r"[^a-z0-9(). ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def has_two_theta_degree_label(text: str) -> bool:
    normalized = normalize_axis_label_text(text)
    if not normalized:
        return False

    # Check for keywords
    has_theta = "theta" in normalized or "2t" in normalized or "2 t" in normalized
    has_degree = bool(re.search(r"\bdeg(?:ree)?\b", normalized))

    # 1. Clear keyword evidence
    if "two theta" in normalized:
        return True
    if re.search(r"\b2\s*theta\b", normalized):
        return True
    if re.search(r"\b2\s*t[hx][a-z]{0,4}\b", normalized):
        return True

    # 2. Numerical evidence: Typical PXRD tick sequences (e.g., 10 20 30 40 or 5 10 15 20)
    # Extract all numbers from the text
    numbers = [int(n) for n in re.findall(r"\b\d{1,2}\b", normalized)]
    if len(numbers) >= 3:
        # Check for arithmetic progression (common in axis ticks)
        # We look for at least 3 numbers with a common difference of 5 or 10
        for diff in [5, 10]:
            matches = 0
            for i in range(len(numbers) - 1):
                if numbers[i+1] - numbers[i] == diff:
                    matches += 1
            if matches >= 2:
                return True

    # 3. If we only have "degree", we need some numerical evidence or a "2" nearby
    if has_degree:
        if re.search(r"\b2\b", normalized):
            return True
        if re.search(r"\b2.{0,10}deg", normalized):
            return True

    # Catch common OCR mangling like "20 (degree)" or "2 0"
    if re.search(r"\b2\s*0\b", normalized) and has_degree:
        return True

    return False


def merge_context_lines(lines: Iterable[str]) -> str:
    parts = [line.strip() for line in lines if line and line.strip()]
    return " ".join(parts)
