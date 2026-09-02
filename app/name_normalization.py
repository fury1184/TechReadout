"""Conservative hardware name cleanup and canonical matching helpers.

The goal is presentation consistency, not aggressive rewriting.  Normalization
never changes specification values and only proposes manufacturer/model text
changes that can be reviewed before they are applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from app.scrapers.validation import extract_cpu_identity


_GENERIC_VENDOR_SUFFIXES = {
    "technology", "technologies", "inc", "inc.", "corp", "corp.",
    "corporation", "co", "co.", "company", "ltd", "ltd.", "limited",
}

_RAM_NOISE_PHRASES = (
    r"\bdesktop\s+memory\b",
    r"\blaptop\s+memory\b",
    r"\bmemory\s+module\b",
    r"\bsingle\s+stick\b",
    r"\bdual\s+channel\s+kit\b",
    r"\bcomputer\s+memory\b",
)

_TRAILING_RETAIL_WORDS = {
    "black", "white", "red", "blue", "silver", "gray", "grey",
    "retail", "oem", "bulk", "new",
}

# Website/page-title text that can leak into CPU model names when a scraper
# uses an H1 as its model source.  Keep this deliberately narrow so legitimate
# model suffixes (K/KF/X3D/v2/etc.) are never removed.
_CPU_TRAILING_PAGE_NOISE = (
    r"\s*(?:[-–—|:]\s*)?benchmarks?\s*(?:&|and)\s*spec(?:s|ifications)\s*$",
    r"\s*(?:[-–—|:]\s*)?benchmark(?:s)?\s*,?\s*tests?\s*(?:&|and)\s*spec(?:s|ifications)\s*$",
)


def _clean_space(value: Optional[str]) -> str:
    value = (value or "").replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n,;|-_")
    return value


def normalize_manufacturer(manufacturer: Optional[str]) -> Optional[str]:
    """Clean obvious duplicated/corporate manufacturer text conservatively."""
    text = _clean_space(manufacturer)
    if not text:
        return None

    # Collapse consecutive duplicate words: "Kingston Kingston" -> "Kingston".
    words = text.split()
    collapsed = []
    for word in words:
        if not collapsed or collapsed[-1].casefold().rstrip(".,") != word.casefold().rstrip(".,"):
            collapsed.append(word)
    text = " ".join(collapsed)
    return text


def _vendor_tokens(manufacturer: Optional[str]) -> list[str]:
    text = normalize_manufacturer(manufacturer) or ""
    return [
        token for token in re.findall(r"[A-Za-z0-9]+", text)
        if token.casefold() not in _GENERIC_VENDOR_SUFFIXES
    ]


def _strip_leading_vendor(model: str, manufacturer: Optional[str]) -> str:
    """Remove repeated vendor/corporate wording from the start of a model."""
    text = model
    tokens = _vendor_tokens(manufacturer)
    if not tokens:
        return text

    primary = tokens[0]
    # Examples handled: "Kingston Kingston Technology Kingston Fury ..."
    # and "ASUS ASUS ROG ...".  Only strip at the beginning.
    pattern = re.compile(
        rf"^(?:(?:{re.escape(primary)})\b(?:\s+(?:Technology|Technologies|Inc\.?|Corporation|Corp\.?|Co\.?|Ltd\.?))?\s*)+",
        re.IGNORECASE,
    )
    return pattern.sub("", text).strip()


def _normalize_ram_title(model: str) -> str:
    text = model
    for phrase in _RAM_NOISE_PHRASES:
        text = re.sub(phrase, " ", text, flags=re.IGNORECASE)

    # "3600MHz DDR4" -> "DDR4-3600" and "DDR4 3600MHz" -> "DDR4-3600".
    text = re.sub(
        r"\b(\d{3,5})\s*MHz\s+(DDR[345])\b",
        lambda m: f"{m.group(2).upper()}-{m.group(1)}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(DDR[345])\s*[- ]?\s*(\d{3,5})\s*MHz\b",
        lambda m: f"{m.group(1).upper()}-{m.group(2)}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(DDR[345])\s+(\d{3,5})\b",
        lambda m: f"{m.group(1).upper()}-{m.group(2)}",
        text,
        flags=re.IGNORECASE,
    )

    # Normalize CAS notation.
    text = re.sub(r"\bCL\s*[- ]?\s*(\d{1,2})\b", r"CL\1", text, flags=re.IGNORECASE)

    # Remove a trailing retailer color/packaging word, including comma forms
    # such as part-number,Black, but never arbitrary tokens.
    trailing = "|".join(sorted(_TRAILING_RETAIL_WORDS, key=len, reverse=True))
    text = re.sub(rf"[, ]+(?:{trailing})\s*$", "", text, flags=re.IGNORECASE)
    parts = _clean_space(text).split()
    while parts and parts[-1].casefold().rstrip(",") in _TRAILING_RETAIL_WORDS:
        parts.pop()
    return " ".join(parts)


def normalize_model_display(
    manufacturer: Optional[str],
    model: Optional[str],
    component_type: Optional[str] = None,
) -> str:
    """Return a conservative display-name cleanup for a hardware model."""
    text = _clean_space(model)
    if not text:
        return ""

    text = _strip_leading_vendor(text, manufacturer)

    ctype = (component_type or "").casefold()
    if ctype == "cpu":
        for pattern in _CPU_TRAILING_PAGE_NOISE:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    elif ctype == "ram":
        text = _normalize_ram_title(text)

    # Generic punctuation/spacing cleanup after component-specific rules.
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" ,;|-_")
    return text


def extract_part_number(model: Optional[str]) -> Optional[str]:
    """Extract a useful manufacturer part-number token when one is present.

    Conservative by design.  Tokens containing a slash, or long alpha-numeric
    tokens containing both letters and numbers, are preferred.  Marketing
    tokens such as DDR4-3600 are excluded.
    """
    text = model or ""
    tokens = re.findall(r"\b[A-Za-z0-9][A-Za-z0-9._/-]{5,}\b", text)
    candidates = []
    for token in tokens:
        upper = token.upper().strip(".,")
        if re.fullmatch(r"DDR[345]-?\d+", upper):
            continue
        if re.fullmatch(r"PC\d+(?:-\d+)?", upper):
            continue
        has_alpha = bool(re.search(r"[A-Z]", upper))
        has_digit = bool(re.search(r"\d", upper))
        if not (has_alpha and has_digit):
            continue
        score = 0
        if "/" in upper:
            score += 4
        if "-" in upper:
            score += 1
        score += min(len(upper), 20) / 20
        candidates.append((score, upper))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def comparison_key(manufacturer: Optional[str], model: Optional[str]) -> str:
    """Loose name key used only to find an existing canonical display name."""
    vendor = normalize_manufacturer(manufacturer) or ""
    model_text = normalize_model_display(vendor, model)
    value = f"{vendor} {model_text}".casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def choose_existing_canonical_name(
    manufacturer: Optional[str],
    model: Optional[str],
    component_type: Optional[str],
    candidates: Iterable,
) -> tuple[Optional[str], Optional[str]]:
    """Prefer a clean established name when an exact part/name match exists.

    `candidates` may be HardwareSpec-like objects with manufacturer/model attrs.
    Returns (manufacturer, model), or (None, None) when no trustworthy match is
    found.  Part-number equality is considered stronger than fuzzy title text.
    """
    clean_vendor = normalize_manufacturer(manufacturer)
    clean_model = normalize_model_display(clean_vendor, model, component_type)
    part = extract_part_number(model)
    key = comparison_key(clean_vendor, clean_model)

    # CPU generations/revisions are part of the model identity, not optional
    # marketing text.  In particular, first-generation Xeon E5 parts are
    # commonly written without a "v1" suffix, so E5-2680 and E5-2680 v2 must
    # never be canonicalized to the same record just because their extracted
    # part-number token is both "E5-2680".
    is_cpu = (component_type or "").casefold() == "cpu"
    cpu_identity = extract_cpu_identity(clean_model) if is_cpu else None

    best = None
    best_score = -1
    for candidate in candidates:
        cand_vendor = normalize_manufacturer(getattr(candidate, "manufacturer", None))
        cand_model_raw = getattr(candidate, "model", None)
        cand_model = normalize_model_display(cand_vendor, cand_model_raw, component_type)
        if clean_vendor and cand_vendor and clean_vendor.casefold() != cand_vendor.casefold():
            continue

        if is_cpu and cpu_identity is not None:
            candidate_cpu_identity = extract_cpu_identity(cand_model)
            if candidate_cpu_identity is not None and candidate_cpu_identity != cpu_identity:
                continue

        score = 0
        cand_part = extract_part_number(cand_model_raw)
        if part and cand_part and part == cand_part:
            score = 100
        elif key and comparison_key(cand_vendor, cand_model) == key:
            score = 95
        else:
            continue

        # Prefer the shorter established display model if scores tie.
        if score > best_score or (score == best_score and best and len(cand_model) < len(best[1])):
            best = (cand_vendor, cand_model)
            best_score = score

    return best if best else (None, None)


@dataclass(frozen=True)
class NameProposal:
    record_type: str
    record_id: int
    component_type: str
    old_manufacturer: Optional[str]
    old_model: str
    new_manufacturer: Optional[str]
    new_model: str

    @property
    def changed(self) -> bool:
        return (
            (self.old_manufacturer or "") != (self.new_manufacturer or "")
            or self.old_model != self.new_model
        )
