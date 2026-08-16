"""
Open WebUI (/ Ollama) LLM lookup client for TechReadout.

Used as an automatic step in the lookup chain, between Scrape.Do and the
manual "AI Import" catch-all:

    Seed DB [caller] -> Scrape.Do -> Open WebUI (this module) -> manual AI Import

Because an LLM's guess carries no inherent confidence signal the way a
scraped page match does, every Open WebUI result is still scored normally by
score_candidate() in api.py -- but that score is capped below the
auto-accept threshold, so a Open WebUI hit always lands in the Pending Review
queue for a human sanity-check rather than being written straight into
inventory/specs.

Uses the SAME JSON schema as the manual "AI Import" prompt templates
(app/templates/backup/import_specs.html), so a Open WebUI result flows through
the exact same parsing/validation/scoring path as every other source.
"""
import json
import os
import re
from typing import Optional, Dict

import requests

OPENWEBUI_TIMEOUT = 12  # seconds per attempt
OPENWEBUI_RETRIES = 1   # one retry beyond the first attempt, then give up

_SCHEMAS = {
    'CPU': {
        'example': {
            "component_type": "CPU", "manufacturer": "Intel or AMD",
            "model": "Core i7-9700K", "cpu_cores": 8, "cpu_threads": 8,
            "cpu_base_clock": 3.6, "cpu_boost_clock": 4.9,
            "cpu_tdp": 95, "cpu_socket": "LGA1151",
        },
        'fields': "manufacturer, model, cpu_cores, cpu_threads, cpu_base_clock (GHz), "
                  "cpu_boost_clock (GHz), cpu_tdp (Watts), cpu_socket",
    },
    'GPU': {
        'example': {
            "component_type": "GPU", "manufacturer": "NVIDIA, AMD, or Intel",
            "model": "GeForce RTX 4070", "gpu_memory_size": 12288,
            "gpu_memory_type": "GDDR6X", "gpu_base_clock": 1920,
            "gpu_boost_clock": 2475, "gpu_tdp": 200,
        },
        'fields': "manufacturer, model, gpu_memory_size (MB), gpu_memory_type, "
                  "gpu_base_clock (MHz), gpu_boost_clock (MHz), gpu_tdp (Watts)",
    },
    'Motherboard': {
        'example': {
            "component_type": "Motherboard", "manufacturer": "ASUS, MSI, Gigabyte, ASRock, etc.",
            "model": "ROG STRIX B550-F GAMING", "mobo_socket": "AM4",
            "mobo_chipset": "B550", "mobo_form_factor": "ATX",
            "mobo_memory_slots": 4, "mobo_memory_type": "DDR4",
            "mobo_max_memory": 128, "mobo_pcie_x16_slots": 2,
            "mobo_m2_slots": 2, "mobo_sata_ports": 6,
        },
        'fields': "manufacturer, model, mobo_socket, mobo_chipset, mobo_form_factor, "
                  "mobo_memory_slots, mobo_memory_type, mobo_max_memory (GB), "
                  "mobo_pcie_x16_slots, mobo_m2_slots, mobo_sata_ports",
    },
    'RAM': {
        'example': {
            "component_type": "RAM", "manufacturer": "Corsair, G.Skill, Kingston, etc.",
            "model": "Vengeance RGB Pro 32GB", "ram_size": 32, "ram_type": "DDR4",
            "ram_speed": 3600, "ram_cas_latency": "18", "ram_modules": 2, "ram_ecc": None, "ram_module_type": None,
        },
        'fields': "manufacturer, model, ram_size (GB total), ram_type, ram_speed (MHz), "
                  "ram_cas_latency, ram_modules (number of sticks), ram_ecc (true/false/null), ram_module_type (UDIMM/RDIMM/LRDIMM/SODIMM/null)",
    },
    'PSU': {
        'example': {
            "component_type": "PSU", "manufacturer": "Corsair, EVGA, Seasonic, etc.",
            "model": "RM850x", "psu_wattage": 850, "psu_efficiency": "80+ Gold",
            "psu_modular": "Full", "psu_form_factor": "ATX",
        },
        'fields': "manufacturer, model, psu_wattage, psu_efficiency, psu_modular, psu_form_factor",
    },
    'Storage': {
        'example': {
            "component_type": "Storage", "manufacturer": "Samsung, WD, Seagate, etc.",
            "model": "970 EVO Plus 1TB", "storage_capacity": 1000,
            "storage_type": "NVMe SSD", "storage_interface": "PCIe 3.0 x4",
            "storage_form_factor": "M.2 2280", "storage_read_speed": 3500,
            "storage_write_speed": 3300,
        },
        'fields': "manufacturer, model, storage_capacity (GB), storage_type, "
                  "storage_interface, storage_form_factor, storage_read_speed (MB/s), "
                  "storage_write_speed (MB/s)",
    },
    'Cooler': {
        'example': {
            "component_type": "Cooler", "manufacturer": "Noctua, Corsair, be quiet!, etc.",
            "model": "NH-D15", "cooler_type": "Air Tower", "cooler_fan_size": 140,
            "cooler_height": 165, "cooler_tdp_rating": 250,
            "cooler_socket_support": "LGA1700, LGA1200, AM5, AM4",
        },
        'fields': "manufacturer, model, cooler_type, cooler_fan_size (mm), cooler_height (mm), "
                  "cooler_tdp_rating (Watts), cooler_socket_support",
    },
    'Case': {
        'example': {
            "component_type": "Case", "manufacturer": "Fractal, NZXT, Lian Li, etc.",
            "model": "Meshify C", "case_form_factor": "ATX", "case_type": "Mid Tower",
            "case_max_gpu_length": 315, "case_max_cooler_height": 172,
        },
        'fields': "manufacturer, model, case_form_factor, case_type, "
                  "case_max_gpu_length (mm), case_max_cooler_height (mm)",
    },
}

_GENERIC_SCHEMA = {
    'example': {"component_type": "Other", "manufacturer": "Brand", "model": "Model name"},
    'fields': "manufacturer, model",
}


def openwebui_enabled() -> bool:
    """Return True when the Open WebUI (Open WebUI) automatic lookup step should run."""
    try:
        from app.models import AppSetting
    except Exception:
        return False
    if not AppSetting.get_bool('openwebui_enabled', False):
        return False
    token = os.environ.get('OPENWEBUI_API_TOKEN', '').strip()
    url = AppSetting.get('openwebui_api_url', '') or ''
    model = AppSetting.get('openwebui_model', '') or ''
    return bool(token and url.strip() and model.strip())


def _build_prompt(query: str, component_type: str) -> str:
    schema = _SCHEMAS.get(component_type, _GENERIC_SCHEMA)
    return (
        f"Give me specs for exactly one {component_type or 'hardware'} item: {query}\n\n"
        f"Respond with ONLY a single JSON object, no other text before or after, "
        f"using EXACTLY this format:\n"
        f"{json.dumps(schema['example'], indent=2)}\n\n"
        f"Required fields: {schema['fields']}\n"
        f"If a value cannot be verified from reliable information, use null.\n"
        f"Do not guess, estimate, infer, or omit unknown fields. Use numbers for numeric fields when known, otherwise null.\n"
        f"Return only one valid JSON object. Do not return an array, comments, markdown, or explanations."
    )


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first {...} JSON object out of a model's (possibly chatty) reply."""
    if not text:
        return None
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def query_openwebui_llm(query: str, component_type: str) -> Optional[Dict]:
    """
    Ask Open WebUI (Open WebUI's OpenAI-compatible endpoint) to guess specs for
    *query*. Returns a result dict shaped like every other scraper result
    (source='openwebui', plus HardwareSpec-compatible fields) on success, or
    None on any failure/timeout/bad JSON -- caller falls through to manual
    AI Import.
    """
    from app.models import AppSetting
    from app.scrapers.validation import coerce_unknowns_to_none

    token = os.environ.get('OPENWEBUI_API_TOKEN', '').strip()
    url = (AppSetting.get('openwebui_api_url', '') or '').strip()
    model = (AppSetting.get('openwebui_model', '') or '').strip()
    if not (token and url and model):
        return None

    prompt = _build_prompt(query, component_type)

    for attempt in range(1 + OPENWEBUI_RETRIES):
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=OPENWEBUI_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data['choices'][0]['message']['content']

            parsed = _extract_json(content)
            if not parsed or not parsed.get('model'):
                print(f"[Open WebUI] No usable JSON in response for '{query}'", flush=True)
                return None

            parsed = coerce_unknowns_to_none(parsed)
            parsed['component_type'] = parsed.get('component_type') or component_type
            parsed['source'] = 'openwebui'
            print(f"[Open WebUI] Parsed: {parsed.get('manufacturer', '')} {parsed.get('model')}", flush=True)
            return parsed

        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
            print(f"[Open WebUI] Attempt {attempt + 1} failed: {e}", flush=True)
            continue

    return None
