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


_LGA_SOCKET_RE = re.compile(
    r"(?i)^(?:socket\s+)?(?:fc)?lga\s*-?\s*(\d{3,4})"  # optional "Socket "/"FC" prefix, "LGA" + number
    r"(?:\s*-?\s*v?(\d+))?"           # optional generation suffix: -3, -v3, v3
    r"\s*(\(.*\))?$"                  # optional parenthetical, e.g. "(300 Series)"
)


def normalize_socket(socket: Optional[str]) -> Optional[str]:
    """Canonicalize a CPU/motherboard socket name for consistent storage.

    Conservative and LGA-focused: unifies the spacing/casing/dash variants
    actually seen in scraped and seed data, e.g. "LGA1151" / "LGA 1151" ->
    "LGA 1151"; "LGA2011-V3" / "LGA2011-v3" / "LGA 2011-v3" -> "LGA 2011-3".
    A trailing parenthetical such as "(300 Series)" is kept as-is — it marks
    a real electrical-spec distinction (300-series boards aren't compatible
    with earlier Skylake/Kaby Lake LGA1151 boards despite the same physical
    socket), not a formatting variant, so it must never be merged away.

    Values that don't match a known LGA pattern (AM4, AM5, sTRX4, FM2+, ...)
    are returned with whitespace only collapsed, otherwise unchanged, so
    socket families not yet seen in the wild aren't mangled by a guess.
    """
    text = _clean_space(socket)
    if not text:
        return None

    match = _LGA_SOCKET_RE.match(text)
    if not match:
        return text

    number, generation, suffix = match.groups()
    canonical = f"LGA {number}"
    if generation:
        canonical += f"-{generation}"
    if suffix:
        canonical += f" {_clean_space(suffix)}"
    return canonical


# Coffee Lake (8th/9th gen) platform detection for the LGA1151 v1/v2 split.
# Sources disagree on whether they mark it (Intel ARK and ASUS pages don't;
# Newegg and CPU-Monkey do), so the marker is derived from the record itself
# instead of trusted from whichever source answered.
_300_SERIES_CHIPSET_RE = re.compile(r"(?i)\b(?:H310|B360|B365|H370|Q370|Z370|Z390)\b")
_COFFEE_LAKE_CPU_RE = re.compile(
    r"(?i)\b(?:"
    r"i[3579][\s-]?[89]\d{3}[a-z]*"       # Core i3/i5/i7/i9 8xxx/9xxx
    r"|pentium(?:\s+gold)?\s+g5[4-6]\d{2}"  # Pentium Gold G5400/G5500/G5600
    r"|celeron\s+g49\d{2}"                  # Celeron G4900 series
    r")\b"
)
LGA1151_300_SERIES = "LGA 1151 (300 Series)"


def canonical_spec_socket(
    field: str,
    socket: Optional[str],
    *,
    model: Optional[str] = None,
    chipset: Optional[str] = None,
) -> Optional[str]:
    """Canonical socket for a HardwareSpec field ("cpu_socket"/"mobo_socket").

    Runs normalize_socket(), then -- only when the result is plain
    "LGA 1151" -- adds the "(300 Series)" marker if the record is clearly
    a Coffee Lake part: a 300-series chipset for motherboards (chipset
    field or model name), or an 8th/9th-gen Core / Pentium Gold G54-56xx /
    Celeron G49xx model for CPUs. Only ever adds the marker, never removes
    it. Xeon E-2100/2200 are deliberately left plain: they need C246
    boards and don't run on consumer 300-series boards either.
    """
    value = normalize_socket(socket)
    if value != "LGA 1151":
        return value
    if field == "mobo_socket":
        hit = any(t and _300_SERIES_CHIPSET_RE.search(t) for t in (chipset, model))
    else:
        hit = bool(model and _COFFEE_LAKE_CPU_RE.search(model))
    return LGA1151_300_SERIES if hit else value

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
        # JEDEC speed/module ratings identify a speed grade shared by every
        # capacity and vendor, never a specific part.  Covers DDR3L-1600,
        # DDR4-3200R, PC3-14900R, PC3L-12800R, PC4-2400T-R and full label
        # strings like PC3-12800R-11-12-E2 / PC4-2666V-RB2.  (v3.8.7: the old
        # PC\d+(-\d+)? pattern missed the R/U/E suffix, so PC3-14900R was
        # treated as a part number and 8GB/16GB sticks canonicalized together.)
        if re.fullmatch(r"DDR[2-5]L?-?\d+[A-Z]?", upper):
            continue
        if re.fullmatch(r"PC\d+[LU]?-\d+[A-Z]{0,2}(?:-[A-Z0-9]{1,4})*", upper):
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


_RAM_KIT_RE = re.compile(r"\b(\d+)\s*[x×]\s*(\d+)\s*GB\b", re.IGNORECASE)
_RAM_SIZE_RE = re.compile(r"\b(\d+)\s*GB\b", re.IGNORECASE)


def extract_ram_capacity(model: Optional[str]) -> Optional[tuple[int, int]]:
    """Return (module_count, gb_per_module) parsed from a RAM model string.

    "2x8GB ..." -> (2, 8); "16GB 2Rx4 PC3-14900R" -> (1, 16).  Returns None
    when no capacity is written, so callers can treat it as unknown rather
    than as a mismatch.
    """
    text = model or ""
    kit = _RAM_KIT_RE.search(text)
    if kit:
        return int(kit.group(1)), int(kit.group(2))
    size = _RAM_SIZE_RE.search(text)
    if size:
        return 1, int(size.group(1))
    return None


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

    # RAM capacity is part of the identity: an 8GB and a 16GB stick of the
    # same speed grade (or even the same part-number family) are different
    # parts.  Only enforced when both names state a capacity.
    is_ram = (component_type or "").casefold() == "ram"
    ram_capacity = extract_ram_capacity(clean_model) if is_ram else None

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

        if is_ram and ram_capacity is not None:
            candidate_capacity = extract_ram_capacity(cand_model)
            if candidate_capacity is not None and candidate_capacity != ram_capacity:
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
