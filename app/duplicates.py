"""Duplicate detection helpers for TechReadOut inventory/spec entry.

The duplicate detector is intentionally conservative: it warns and recommends,
but does not auto-block or auto-merge. You can really own multiple of the same
part, and location/status/condition can make separate inventory rows useful.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional

from app.models import ComponentType, HardwareSpec, Inventory

_COMMON_WORDS = {
    "intel", "amd", "nvidia", "geforce", "radeon", "core", "xeon", "ryzen",
    "processor", "cpu", "graphics", "card", "gpu", "motherboard", "mainboard",
    "memory", "ram", "kit", "desktop", "server", "workstation", "gaming",
    "the", "and", "with", "for", "new", "used",
}


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_duplicate_text(value: Any) -> str:
    """Normalize a model/manufacturer string for duplicate matching."""
    text = _clean(value)
    text = text.replace("×", "x")
    text = re.sub(r"\b(\d+)\s*x\s*(\d+)\s*gb\b", r"\1x\2gb", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [t for t in text.split() if t not in _COMMON_WORDS]
    return " ".join(tokens)


def compact_duplicate_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_duplicate_text(value))


def _similarity(a: str, b: str) -> int:
    if not a or not b:
        return 0
    if a == b:
        return 100
    if a in b or b in a:
        return 92
    return int(SequenceMatcher(None, a, b).ratio() * 100)


def _component_type_name(component_type_id: Optional[int] = None, component_type_name: Optional[str] = None) -> Optional[str]:
    if component_type_name:
        return component_type_name
    if component_type_id:
        ct = ComponentType.query.get(component_type_id)
        return ct.name if ct else None
    return None


def _match_level(score: int) -> str:
    if score >= 96:
        return "exact"
    if score >= 88:
        return "likely"
    if score >= 74:
        return "possible"
    return "none"


def duplicate_score(candidate_manufacturer: Any, candidate_model: Any, manufacturer: Any, model: Any) -> int:
    """Return 0-100 duplicate confidence for two manufacturer/model pairs."""
    model_a = normalize_duplicate_text(candidate_model)
    model_b = normalize_duplicate_text(model)
    compact_a = compact_duplicate_key(candidate_model)
    compact_b = compact_duplicate_key(model)
    model_score = max(_similarity(model_a, model_b), _similarity(compact_a, compact_b))

    mfr_a = normalize_duplicate_text(candidate_manufacturer)
    mfr_b = normalize_duplicate_text(manufacturer)
    if not mfr_a or not mfr_b:
        mfr_bonus = 0
    else:
        mfr_score = _similarity(mfr_a, mfr_b)
        mfr_bonus = 8 if mfr_score >= 90 else (-8 if mfr_score < 50 else 0)

    return max(0, min(100, model_score + mfr_bonus))


def _spec_payload(spec: HardwareSpec, score: int) -> Dict[str, Any]:
    return {
        "kind": "spec",
        "level": _match_level(score),
        "score": score,
        "id": spec.id,
        "display_name": spec.display_name,
        "manufacturer": spec.manufacturer,
        "model": spec.model,
        "component_type": spec.component_type.name if spec.component_type else None,
        "summary": getattr(spec, "spec_summary", None),
        "inventory_quantity": sum((item.quantity or 0) for item in spec.inventory_items.all()),
    }


def _inventory_payload(item: Inventory, score: int) -> Dict[str, Any]:
    return {
        "kind": "inventory",
        "level": _match_level(score),
        "score": score,
        "id": item.id,
        "display_name": item.display_name,
        "quantity": item.quantity or 0,
        "location": item.location,
        "status": item.status,
        "condition": item.item_condition,
        "component_type": item.component_type.name if item.component_type else None,
        "summary": item.hardware_spec.spec_summary if item.hardware_spec else None,
    }


def find_duplicate_specs(
    *,
    component_type_id: Optional[int] = None,
    component_type_name: Optional[str] = None,
    manufacturer: Optional[str] = None,
    model: Optional[str] = None,
    exclude_spec_id: Optional[int] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Find likely duplicate HardwareSpec rows for the supplied model."""
    if not model:
        return []

    query = HardwareSpec.query
    if component_type_id:
        query = query.filter(HardwareSpec.component_type_id == component_type_id)
    elif component_type_name:
        query = query.join(ComponentType).filter(ComponentType.name == component_type_name)
    if exclude_spec_id:
        query = query.filter(HardwareSpec.id != exclude_spec_id)

    model_key = compact_duplicate_key(model)
    candidates: List[Dict[str, Any]] = []
    for spec in query.limit(500).all():
        score = duplicate_score(spec.manufacturer, spec.model, manufacturer, model)
        # Small boost for obvious exact compact model key matches.
        if model_key and model_key == compact_duplicate_key(spec.model):
            score = max(score, 96)
        level = _match_level(score)
        if level != "none":
            candidates.append(_spec_payload(spec, score))

    candidates.sort(key=lambda d: d["score"], reverse=True)
    return candidates[:limit]


def find_duplicate_inventory_items(
    *,
    component_type_id: Optional[int] = None,
    component_type_name: Optional[str] = None,
    hardware_spec_id: Optional[int] = None,
    manufacturer: Optional[str] = None,
    model: Optional[str] = None,
    exclude_inventory_id: Optional[int] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Find likely duplicate physical inventory entries."""
    query = Inventory.query
    if component_type_id:
        query = query.filter(Inventory.component_type_id == component_type_id)
    elif component_type_name:
        query = query.join(ComponentType).filter(ComponentType.name == component_type_name)
    if exclude_inventory_id:
        query = query.filter(Inventory.id != exclude_inventory_id)

    candidates: List[Dict[str, Any]] = []
    if hardware_spec_id:
        for item in query.filter(Inventory.hardware_spec_id == hardware_spec_id).limit(limit).all():
            candidates.append(_inventory_payload(item, 100))
        return candidates

    if not model:
        return []

    for item in query.limit(500).all():
        if item.hardware_spec:
            candidate_manufacturer = item.hardware_spec.manufacturer
            candidate_model = item.hardware_spec.model
        else:
            candidate_manufacturer = item.custom_manufacturer
            candidate_model = item.custom_name
        score = duplicate_score(candidate_manufacturer, candidate_model, manufacturer, model)
        level = _match_level(score)
        if level != "none":
            candidates.append(_inventory_payload(item, score))

    candidates.sort(key=lambda d: d["score"], reverse=True)
    return candidates[:limit]


def find_duplicates(
    *,
    component_type_id: Optional[int] = None,
    component_type_name: Optional[str] = None,
    hardware_spec_id: Optional[int] = None,
    manufacturer: Optional[str] = None,
    model: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Find duplicate specs and inventory rows for one prospective item."""
    if hardware_spec_id:
        spec = HardwareSpec.query.get(hardware_spec_id)
        if spec:
            component_type_id = component_type_id or spec.component_type_id
            manufacturer = manufacturer or spec.manufacturer
            model = model or spec.model

    specs = find_duplicate_specs(
        component_type_id=component_type_id,
        component_type_name=component_type_name,
        manufacturer=manufacturer,
        model=model,
        exclude_spec_id=hardware_spec_id,
        limit=limit,
    )
    inventory = find_duplicate_inventory_items(
        component_type_id=component_type_id,
        component_type_name=component_type_name,
        hardware_spec_id=hardware_spec_id,
        manufacturer=manufacturer,
        model=model,
        limit=limit,
    )
    highest = 0
    for entry in [*specs, *inventory]:
        highest = max(highest, int(entry.get("score") or 0))
    return {
        "has_duplicates": bool(specs or inventory),
        "highest_score": highest,
        "specs": specs,
        "inventory": inventory,
    }
