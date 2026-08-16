"""Central validation rules for TechReadOut scraper results.

Rules:
- Unknown values should be represented as ``None`` in Python / ``null`` in JSON.
- Do not guess missing spec fields just to pass validation.
- Required fields that are missing/null should send a result to review.
- Optional fields that are missing/null are allowed, but make the result incomplete.
"""

import re
from typing import Dict, List

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

def validate_result(query: str, result_model: str, component_type: str = None) -> bool:
    """
    Validate that the result actually matches the query.
    Returns True if the result is a valid match, False if it's a different model.
    """
    if not result_model:
        return False
    
    # For GPUs, use normalized query for validation since TechPowerUp has reference names
    if component_type == 'GPU':
        query = normalize_gpu_query(query)
    
    query_norm = normalize_model_name(query)
    result_norm = normalize_model_name(result_model)
    query_lower = query.lower()
    result_lower = result_model.lower()
    
    # Check if key identifiers from query exist in result
    key_ids = extract_key_identifiers(query)
    
    for key_id in key_ids:
        key_id_clean = key_id.replace(' ', '')
        if key_id_clean not in result_norm and key_id_clean not in result_lower:
            print(f"[Lookup] Validation failed: '{key_id}' not found in result '{result_model}'")
            return False
    
    # Also check that major model number matches
    # E.g., searching for "2687" should match "E5-2687W v4" but not "E5-2680 v4"
    query_nums = re.findall(r'\d{3,}', query)
    result_nums = re.findall(r'\d{3,}', result_model)
    
    for num in query_nums:
        if num not in result_model and num not in ''.join(result_nums):
            print(f"[Lookup] Validation failed: model number '{num}' not found in result '{result_model}'")
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
            print(f"[Lookup] Validation failed: result has '{rid}' not in query")
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
                print(f"[Lookup] Validation failed: model number mismatch {query_model_num} vs {result_model_num}")
                return False
            
            # Suffixes must match exactly (both empty, or both same)
            if query_suffix != result_suffix:
                print(f"[Lookup] Validation failed: suffix mismatch '{query_suffix}' vs '{result_suffix}' (query: {query}, result: {result_model})")
                return False
    
    print(f"[Lookup] Validation passed: '{query}' matches '{result_model}'")
    return True


def acceptable_scrape_hit(query: str, result: dict, component_type: str) -> bool:
    """Validate a scraped candidate before ending the fallback chain."""
    result = coerce_unknowns_to_none(result)
    return (
        bool(result)
        and bool(result.get('model'))
        and validate_result(query, result.get('model'), component_type)
        and has_minimum_specs(result, component_type)
    )


# Backwards-compatible private name from the old lookup.py helper.
def _acceptable_scrape_hit(query: str, result: dict, component_type: str) -> bool:
    return acceptable_scrape_hit(query, result, component_type)
