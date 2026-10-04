"""Central validation rules for TechReadOut scraper results.

Rules:
- Unknown values should be represented as ``None`` in Python / ``null`` in JSON.
- Do not guess missing spec fields just to pass validation.
- Required fields that are missing/null should send a result to review.
- Optional fields that are missing/null are allowed, but make the result incomplete.
"""

import re
from typing import Dict, List, Optional

from app.scrapers.normalization import (
    extract_key_identifiers,
    normalize_gpu_query,
    normalize_model_name,
)

UNKNOWN_STRINGS = {'', 'unknown', 'n/a', 'na', 'none', 'null', 'not listed', 'not specified'}

SPEC_FIELDS_BY_TYPE: Dict[str, List[str]] = {
    'CPU': [
        'cpu_socket', 'cpu_cores', 'cpu_threads', 'cpu_base_clock',
        'cpu_boost_clock', 'cpu_tdp', 'cpu_architecture',
    ],
    'GPU': [
        'gpu_memory_size', 'gpu_memory_type', 'gpu_base_clock',
        'gpu_boost_clock', 'gpu_tdp', 'gpu_bus_interface',
    ],
    'RAM': [
        'ram_size', 'ram_type', 'ram_speed', 'ram_cas_latency',
        'ram_modules', 'ram_ecc', 'ram_module_type',
    ],
    'Storage': [
        'storage_capacity', 'storage_type', 'storage_interface',
        'storage_read_speed', 'storage_write_speed',
    ],
    'Motherboard': [
        'mobo_socket', 'mobo_chipset', 'mobo_form_factor',
        'mobo_memory_slots', 'mobo_memory_type', 'mobo_max_memory',
        'mobo_pcie_x16_slots', 'mobo_pcie_x4_slots', 'mobo_pcie_x1_slots',
        'mobo_m2_slots', 'mobo_sata_ports',
    ],
    'PSU': [
        'psu_wattage', 'psu_efficiency', 'psu_modular',
        'psu_form_factor',
    ],
    'Cooler': [
        'cooler_type', 'cooler_tdp_rating', 'cooler_height',
        'cooler_fan_size', 'cooler_socket_support',
    ],
    'Case': [
        'case_form_factor', 'case_max_gpu_length',
        'case_max_cooler_height',
    ],
    'Fan': ['fan_size', 'fan_rpm_max', 'fan_airflow'],
}

# Fields that should usually exist before an AI/scraper result can be trusted.
# Missing/null required fields do not block saving forever; they force review.
REQUIRED_FIELDS_BY_TYPE: Dict[str, List[str]] = {
    'CPU': ['manufacturer', 'model', 'cpu_cores', 'cpu_threads', 'cpu_socket'],
    'GPU': ['manufacturer', 'model', 'gpu_memory_size', 'gpu_memory_type'],
    'RAM': ['manufacturer', 'model', 'ram_size', 'ram_type', 'ram_speed'],
    'Motherboard': ['manufacturer', 'model', 'mobo_socket', 'mobo_memory_type'],
    'Storage': ['manufacturer', 'model', 'storage_capacity', 'storage_type'],
    'PSU': ['manufacturer', 'model', 'psu_wattage'],
    'Cooler': ['manufacturer', 'model', 'cooler_type'],
    'Case': ['manufacturer', 'model', 'case_form_factor'],
}


def is_known_value(value) -> bool:
    """True when a field contains a real value instead of an unknown/null marker."""
    if value is None or value == [] or value == {}:
        return False
    if isinstance(value, str) and value.strip().lower() in UNKNOWN_STRINGS:
        return False
    return True


def coerce_unknowns_to_none(result: dict) -> dict:
    """Return a copy with common textual unknown markers normalized to None."""
    if not isinstance(result, dict):
        return result
    cleaned = {}
    for key, value in result.items():
        if isinstance(value, str) and value.strip().lower() in UNKNOWN_STRINGS:
            cleaned[key] = None
        else:
            cleaned[key] = value
    return cleaned


def present_spec_fields(result: dict, component_type: str) -> list:
    """Return meaningful populated spec fields for a component type."""
    if not result:
        return []
    fields = SPEC_FIELDS_BY_TYPE.get(component_type, [])
    return [field for field in fields if is_known_value(result.get(field))]


# Backwards-compatible private name from the old lookup.py helper.
def _present_spec_fields(result: dict, component_type: str) -> list:
    return present_spec_fields(result, component_type)

def has_minimum_specs(result: dict, component_type: str) -> bool:
    """
    Ensure a result has meaningful spec data beyond just a product title.

    This prevents title-only marketplace pages from stopping the lookup chain
    before a stronger source or Open WebUI fallback can provide real specs.
    """
    if not result or not result.get('model'):
        return False

    # These types should have at least one real hardware field.
    if component_type in ('CPU', 'GPU', 'RAM', 'Storage', 'Motherboard', 'PSU'):
        return bool(_present_spec_fields(result, component_type))

    # For less-structured accessory types, a clean model is currently enough.
    return True


def missing_required_fields(result: dict, component_type: str) -> list:
    """Return required fields that are missing or null/unknown."""
    fields = REQUIRED_FIELDS_BY_TYPE.get(component_type, ['manufacturer', 'model'])
    return [field for field in fields if not is_known_value((result or {}).get(field))]


def validation_status(result: dict, component_type: str) -> dict:
    """Describe whether a result is complete enough to auto-accept or needs review."""
    missing = missing_required_fields(result, component_type)
    present = present_spec_fields(result, component_type)
    return {
        'has_minimum_specs': has_minimum_specs(result, component_type),
        'missing_required_fields': missing,
        'present_spec_fields': present,
        'is_incomplete': bool(missing),
        'needs_review': bool(missing),
    }


def extract_cpu_identity(text: str):
    """Return a strict CPU identity tuple or ``None`` when no known pattern exists.

    The tuple is ``(family, model_number, suffix, revision)``.  Formatting and
    vendor words are ignored, but model numbers, suffixes, and Xeon revisions
    must match exactly.
    """
    value = (text or '').lower()
    value = re.sub(r'[–—]', '-', value)

    # Intel Xeon E5-2696 v4 / E5 2696V4 / Xeon E5-2687W v4
    match = re.search(r'\b(e[357])\s*[- ]?\s*(\d{4})([a-z]?)\s*(?:[- ]?v\s*(\d+))?\b', value)
    if match:
        return (match.group(1), match.group(2), match.group(3) or '', match.group(4) or '')

    # Legacy Intel Xeon X5660 / X5650 / L5640 / W3680 / E5640.
    # These pre-E5 Xeons use a single family letter plus four digits.
    match = re.search(r'\b([xlwe])\s*[- ]?\s*(\d{4})([a-z]?)\b', value)
    if match:
        return ('xeon-' + match.group(1), match.group(2), match.group(3) or '', '')

    # Intel Core i7-9700K / i9 13900KS
    match = re.search(r'\b(i[3579])\s*[- ]?\s*(\d{4,5})([a-z]{0,2})\b', value)
    if match:
        return (match.group(1), match.group(2), match.group(3) or '', '')

    # AMD Ryzen 7 5800X3D / Ryzen 9 7900X
    match = re.search(r'\b(ryzen\s*[3579])\s*[- ]?\s*(\d{4})([a-z0-9]{0,3})\b', value)
    if match:
        return (re.sub(r'\s+', '', match.group(1)), match.group(2), match.group(3) or '', '')

    # AMD EPYC 7402P and similar.
    match = re.search(r'\b(epyc)\s*[- ]?\s*(\d{4})([a-z]?)\b', value)
    if match:
        return (match.group(1), match.group(2), match.group(3) or '', '')

    return None


def cpu_models_compatible(query: str, candidate: str) -> bool:
    """Require exact identity whenever the searched CPU has a recognizable ID."""
    query_id = extract_cpu_identity(query)
    candidate_id = extract_cpu_identity(candidate)
    if query_id is None:
        return True
    return candidate_id is not None and query_id == candidate_id

# ── RAM identity (v3.8.8) ─────────────────────────────────────────────────
# Capacity, DDR generation, speed grade and ECC are identity for a memory
# module, not scoring hints.  Each is parsed from free text and compared only
# when BOTH sides state it: unknown never counts as a mismatch.

_RAM_KIT_RE = re.compile(r"\b(\d+)\s*[x×]\s*(\d+)\s*GB\b", re.IGNORECASE)
_RAM_SIZE_RE = re.compile(r"\b(\d+)\s*GB\b", re.IGNORECASE)
_RAM_GEN_RE = re.compile(r"\b(?:DDR([2-5])L?|PC([2-5])L?(?=-\d))", re.IGNORECASE)
_RAM_DDR_SPEED_RE = re.compile(r"\bDDR[2-5]L?\s*[- ]?\s*(\d{3,4})\b(?!\s*-?\s*pin)", re.IGNORECASE)
_RAM_PC_RE = re.compile(r"\bPC[2-5]L?-(\d{4,5})", re.IGNORECASE)
_RAM_MHZ_RE = re.compile(r"\b(\d{3,4})\s*(?:MHz|MT/s)\b", re.IGNORECASE)
_RAM_NON_ECC_RE = re.compile(r"\bnon[\s-]?ecc\b", re.IGNORECASE)
_RAM_ECC_RE = re.compile(r"\becc\b|\bl?rdimm\b|\bregistered\b|\bbuffered\s+ecc\b", re.IGNORECASE)

# JEDEC data rates; PCx-NNNNN module ratings are bandwidth (MB/s) = rate × 8
# and get snapped to the nearest of these (PC3-14900 -> 1866).
_RAM_STANDARD_RATES = (
    400, 533, 667, 800, 1066, 1333, 1600, 1866, 2133, 2400, 2666, 2933,
    3200, 3600, 4000, 4400, 4800, 5200, 5600, 6000, 6400, 6800, 7200, 8000,
)

# Memory vendors recognizable in free text.  Used only to spot a conflict
# when both sides name a vendor; OEM rebrands are why a vendor conflict
# sends the match to review instead of rejecting it outright.
_RAM_VENDORS = {
    "a-tech": "atech", "atech": "atech", "adata": "adata", "apacer": "apacer",
    "corsair": "corsair", "crucial": "crucial", "micron": "micron",
    "g.skill": "gskill", "gskill": "gskill", "hynix": "skhynix",
    "sk hynix": "skhynix", "innodisk": "innodisk", "kingston": "kingston",
    "mushkin": "mushkin", "nemix": "nemix", "patriot": "patriot",
    "pny": "pny", "samsung": "samsung", "silicon power": "siliconpower",
    "teamgroup": "teamgroup", "team group": "teamgroup", "timetec": "timetec",
    "transcend": "transcend", "oloy": "oloy", "nanya": "nanya",
    "elpida": "elpida", "ramaxel": "ramaxel", "hp": "hp", "hpe": "hp",
    "dell": "dell", "lenovo": "lenovo", "supermicro": "supermicro",
}


def extract_ram_capacity(text):
    """(module_count, gb_per_module): "2x8GB" -> (2, 8), "16GB" -> (1, 16)."""
    text = text or ""
    kit = _RAM_KIT_RE.search(text)
    if kit:
        return int(kit.group(1)), int(kit.group(2))
    size = _RAM_SIZE_RE.search(text)
    if size:
        return 1, int(size.group(1))
    return None


def _snap_rate(value):
    nearest = min(_RAM_STANDARD_RATES, key=lambda r: abs(r - value))
    return nearest if abs(nearest - value) <= nearest * 0.015 else None


def extract_ram_generation(text):
    """DDR generation as an int: "DDR4-2400" -> 4, "PC3-14900R" -> 3."""
    m = _RAM_GEN_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def extract_ram_speed(text):
    """Data rate in MT/s. PC4-19200 and DDR4-2400 both give 2400."""
    text = text or ""
    m = _RAM_DDR_SPEED_RE.search(text)
    if m and int(m.group(1)) >= 400:
        return int(m.group(1))
    m = _RAM_PC_RE.search(text)
    if m:
        value = int(m.group(1))
        # DDR4 labels also use the rate directly: PC4-2400T, PC4-2666V.
        if value < 5000:
            return _snap_rate(value) or value
        return _snap_rate(value / 8)
    m = _RAM_MHZ_RE.search(text)
    if m:
        return int(m.group(1))
    return None


def extract_ram_ecc(text):
    """True for ECC/registered, False for Non-ECC, None when not stated."""
    text = text or ""
    if _RAM_NON_ECC_RE.search(text):
        return False
    if _RAM_ECC_RE.search(text):
        return True
    return None


def extract_ram_vendor(text):
    """Canonical vendor key when the text names a known memory vendor."""
    lower = (text or "").casefold()
    for name in sorted(_RAM_VENDORS, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", lower):
            return _RAM_VENDORS[name]
    return None


def _vendor_key(manufacturer, text):
    if manufacturer and manufacturer.strip():
        known = extract_ram_vendor(manufacturer)
        return known or re.sub(r"[^a-z0-9]+", "", manufacturer.casefold())
    return extract_ram_vendor(text)


def ram_capacity_conflict(query, candidate):
    """True when both texts state a capacity and they can't be the same part.

    When both give a kit ("2x16GB") the kits must match. When one side gives
    only a size ("32GB"), it may mean the kit total or one stick, so it matches
    either (v3.8.9; "32GB DDR4-3200" used to reject "32GB (2 x 16GB)").
    """
    a, b = extract_ram_capacity(query), extract_ram_capacity(candidate)
    if a is None or b is None:
        return False
    a_kit, b_kit = bool(_RAM_KIT_RE.search(query or "")), bool(_RAM_KIT_RE.search(candidate or ""))
    if a_kit == b_kit:
        return a != b
    kit, size = (a, b[1]) if a_kit else (b, a[1])
    return size not in (kit[0] * kit[1], kit[1])


def ram_spec_conflicts(query, candidate):
    """Names of the identity fields that both texts state and that differ."""
    conflicts = ["capacity"] if ram_capacity_conflict(query, candidate) else []
    checks = (
        ("generation", extract_ram_generation),
        ("speed", extract_ram_speed),
        ("ecc", extract_ram_ecc),
    )
    for name, extract in checks:
        a, b = extract(query), extract(candidate)
        if a is not None and b is not None and a != b:
            conflicts.append(name)
    return conflicts


def ram_vendor_conflict(query, candidate, query_manufacturer=None,
                        candidate_manufacturer=None):
    a = _vendor_key(query_manufacturer, query)
    b = _vendor_key(candidate_manufacturer, candidate)
    return bool(a and b and a != b)


def ram_candidate_decision(query, candidate, query_manufacturer=None,
                           candidate_manufacturer=None):
    """'reject' for a different part, 'review' for a vendor conflict, else 'ok'.

    A spec conflict (capacity/generation/speed/ECC) means a different part.
    A vendor conflict alone may be an OEM rebrand (HP-labelled Samsung), so it
    is never auto-accepted but still offered for human review.
    """
    if ram_spec_conflicts(query, candidate):
        return "reject"
    if ram_vendor_conflict(query, candidate, query_manufacturer, candidate_manufacturer):
        return "review"
    return "ok"


# A GPU memory size as people type it: "8g", "8gb", "8 GB", "16GB" (v3.8.10).
# \b on both sides keeps "GDDR6" and "18 Gbps" out.
_GPU_MEMORY_RE = re.compile(r'\b(\d{1,2})\s*gb?\b', re.IGNORECASE)


def gpu_memory_gb(text) -> Optional[int]:
    """Memory size in GB stated in a GPU name or query, else None."""
    match = _GPU_MEMORY_RE.search(text or '')
    return int(match.group(1)) if match else None


def strip_gpu_memory(text: str) -> str:
    """The text without its memory size ("gtx 1080 8g" -> "gtx 1080")."""
    return ' '.join(_GPU_MEMORY_RE.sub(' ', text or '').split())


def gpu_memory_conflict(query: str, result: dict) -> bool:
    """True when the query states a memory size and the result has a different one.

    Checks the result's name ("... RTX 4060 Ti 8 GB") and its parsed
    gpu_memory_size (MB, or GB for small values -- same rule as compatibility.py).
    Nothing else tells an RTX 4060 Ti 8 GB from a 16 GB, so without this a 16GB
    card could be saved with the 8 GB page's specs.
    """
    wanted = gpu_memory_gb(query)
    if not wanted or not result:
        return False
    stated = gpu_memory_gb(result.get('model') or '')
    if stated and stated != wanted:
        return True
    try:
        size = float(result.get('gpu_memory_size') or 0)
    except (TypeError, ValueError):
        return False
    if size <= 0:
        return False
    return round(size / 1024 if size > 128 else size) != wanted


def validate_result(query: str, result_model: str, component_type: str = None,
                    log: bool = True) -> bool:
    """
    Validate that the result actually matches the query.
    Returns True if the result is a valid match, False if it's a different model.
    `log=False` silences the per-check messages (the result pickers screen
    every search listing with this, v3.8.9).
    """
    say = print if log else (lambda *args, **kwargs: None)
    if not result_model:
        return False
    
    # For GPUs, use normalized query for validation since TechPowerUp has reference names
    if component_type == 'GPU':
        if gpu_memory_conflict(query, {'model': result_model}):
            say(f"[Lookup] Validation failed: GPU memory size differs ('{query}' vs '{result_model}')")
            return False
        query = normalize_gpu_query(query, log=log)

    if component_type == 'CPU' and not cpu_models_compatible(query, result_model):
        say(f"[Lookup] Validation failed: strict CPU identity mismatch '{query}' vs '{result_model}'")
        return False

    if component_type == 'RAM':
        conflicts = ram_spec_conflicts(query, result_model)
        if conflicts:
            say(f"[Lookup] Validation failed: RAM {', '.join(conflicts)} mismatch '{query}' vs '{result_model}'")
            return False
        if ram_vendor_conflict(query, result_model):
            say(f"[Lookup] Validation failed: RAM vendor mismatch '{query}' vs '{result_model}'")
            return False
    
    query_norm = normalize_model_name(query)
    result_norm = normalize_model_name(result_model)
    query_lower = query.lower()
    result_lower = result_model.lower()
    
    # Check if key identifiers from query exist in result
    key_ids = extract_key_identifiers(query)
    
    for key_id in key_ids:
        key_id_clean = key_id.replace(' ', '')
        if key_id_clean not in result_norm and key_id_clean not in result_lower:
            say(f"[Lookup] Validation failed: '{key_id}' not found in result '{result_model}'")
            return False
    
    # Also check that major model number matches
    # E.g., searching for "2687" should match "E5-2687W v4" but not "E5-2680 v4"
    query_nums = re.findall(r'\d{3,}', query)
    result_nums = re.findall(r'\d{3,}', result_model)
    
    for num in query_nums:
        if num not in result_model and num not in ''.join(result_nums):
            say(f"[Lookup] Validation failed: model number '{num}' not found in result '{result_model}'")
            return False
    
    # Check that result doesn't have extra significant identifiers not in query
    result_ids = extract_key_identifiers(result_model)
    query_ids_clean = [k.replace(' ', '').lower() for k in key_ids]
    
    for rid in result_ids:
        rid_lower = rid.lower()
        if rid_lower not in query_ids_clean:
            # Result has identifier not in query - might be wrong model
            # E.g., query "RTX 4070" returning "RTX 4070 Ti"
            # E.g., query "i7-9700" returning "i7-9700K"
            say(f"[Lookup] Validation failed: result has '{rid}' not in query")
            return False
    
    # Additional check for CPU suffix mismatch
    # Query "i7-9700" should NOT match "i7-9700K"
    # Query "i7-9700K" should NOT match "i7-9700" or "i7-9700F"
    if component_type == 'CPU':
        # Extract model number with optional suffix from both
        query_cpu_match = re.search(r'(\d{4,5})([kfxwsue]*)\b', query_lower)
        result_cpu_match = re.search(r'(\d{4,5})([kfxwsue]*)\b', result_lower)
        
        if query_cpu_match and result_cpu_match:
            query_model_num = query_cpu_match.group(1)
            query_suffix = query_cpu_match.group(2)
            result_model_num = result_cpu_match.group(1)
            result_suffix = result_cpu_match.group(2)
            
            # Model numbers must match
            if query_model_num != result_model_num:
                say(f"[Lookup] Validation failed: model number mismatch {query_model_num} vs {result_model_num}")
                return False
            
            # Suffixes must match exactly (both empty, or both same)
            if query_suffix != result_suffix:
                say(f"[Lookup] Validation failed: suffix mismatch '{query_suffix}' vs '{result_suffix}' (query: {query}, result: {result_model})")
                return False
    
    say(f"[Lookup] Validation passed: '{query}' matches '{result_model}'")
    return True


def acceptable_scrape_hit(query: str, result: dict, component_type: str) -> bool:
    """Validate a scraped candidate before ending the fallback chain."""
    result = coerce_unknowns_to_none(result)
    if component_type == 'GPU' and gpu_memory_conflict(query, result):
        print(f"[Lookup] Validation failed: '{query}' asks for a different memory size than "
              f"'{result.get('model')}' ({result.get('gpu_memory_size')} MB)", flush=True)
        return False
    return (
        bool(result)
        and bool(result.get('model'))
        and validate_result(query, result.get('model'), component_type)
        and has_minimum_specs(result, component_type)
    )


# Backwards-compatible private name from the old lookup.py helper.
def _acceptable_scrape_hit(query: str, result: dict, component_type: str) -> bool:
    return acceptable_scrape_hit(query, result, component_type)
