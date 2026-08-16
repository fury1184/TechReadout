"""Compatibility and readiness checks for TechReadOut builds/hosts.

This module is intentionally read-only and migration-free. It works from the
existing Inventory -> HardwareSpec data and treats unknown/null values as
unknown instead of guessing.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional


FORM_FACTOR_ORDER = {
    "mini-itx": 1,
    "itx": 1,
    "micro-atx": 2,
    "micro atx": 2,
    "matx": 2,
    "m-atx": 2,
    "atx": 3,
    "e-atx": 4,
    "eatx": 4,
    "xl-atx": 5,
}


BASE_NON_GPU_CPU_WATTS = 50
STORAGE_WATTS_EACH = 10
FAN_WATTS_EACH = 5
PSU_HEADROOM_MULTIPLIER = 1.4


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _display(value: Any) -> str:
    return str(value).strip() if value is not None and str(value).strip() else "unknown"


def _component_name(item: Any) -> str:
    try:
        return (item.component_type.name or "").strip().lower()
    except Exception:
        return ""


def _spec(item: Any) -> Any:
    return getattr(item, "hardware_spec", None)


def _qty(item: Any) -> int:
    try:
        return max(int(getattr(item, "quantity", 1) or 1), 1)
    except Exception:
        return 1


def _items_by_type(items: Iterable[Any]) -> Dict[str, List[Any]]:
    grouped: Dict[str, List[Any]] = {}
    for item in items or []:
        name = _component_name(item)
        if name:
            grouped.setdefault(name, []).append(item)
    return grouped


def _first(grouped: Dict[str, List[Any]], component_type: str) -> Optional[Any]:
    values = grouped.get(component_type.lower()) or []
    return values[0] if values else None


def _issue(status: str, title: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "status": status,
        "title": title,
        "message": message,
        "details": details or {},
    }


def _same_known_value(left: Any, right: Any) -> Optional[bool]:
    """Return True/False when both values are known, otherwise None."""
    if not left or not right:
        return None
    return _norm(left) == _norm(right)


def _socket_match(cpu_socket: Any, mobo_socket: Any) -> Optional[bool]:
    if not cpu_socket or not mobo_socket:
        return None
    left = _norm(cpu_socket)
    right = _norm(mobo_socket)
    return bool(left and right and (left == right or left in right or right in left))


def _form_factor_rank(value: Any) -> Optional[int]:
    if not value:
        return None
    raw = str(value).strip().lower()
    normalized = raw.replace("_", "-")
    return FORM_FACTOR_ORDER.get(normalized) or FORM_FACTOR_ORDER.get(normalized.replace(" ", "-")) or FORM_FACTOR_ORDER.get(_norm(raw))


def _ram_capacity_for_item(item: Any) -> float:
    """Return total represented RAM capacity for an inventory row.

    TRO 3.5.3 counts RAM quantity as physical sticks. If the spec describes a
    kit, use per-stick capacity = ram_size / ram_modules. If module count is
    unknown, fall back to legacy behavior of ram_size * quantity.
    """
    spec = _spec(item)
    if not spec or not getattr(spec, "ram_size", None):
        return 0.0

    ram_size = float(spec.ram_size or 0)
    modules = getattr(spec, "ram_modules", None)
    quantity = _qty(item)

    if modules and int(modules) > 0:
        return (ram_size / int(modules)) * quantity
    return ram_size * quantity


def _total_ram_gb(ram_items: Iterable[Any]) -> float:
    return sum(_ram_capacity_for_item(item) for item in ram_items or [])


def _total_vram_gb(gpu_items: Iterable[Any]) -> float:
    total = 0.0
    for item in gpu_items or []:
        spec = _spec(item)
        if spec and getattr(spec, "gpu_memory_size", None):
            memory = float(spec.gpu_memory_size or 0)
            # Some seeds/scrapers store MB, some manual data may store GB.
            gb = memory / 1024 if memory > 128 else memory
            total += gb * _qty(item)
    return total


def _estimate_power_watts(grouped: Dict[str, List[Any]]) -> Dict[str, Any]:
    cpu_watts = sum((getattr(_spec(item), "cpu_tdp", None) or 0) * _qty(item) for item in grouped.get("cpu", []))
    gpu_watts = sum((getattr(_spec(item), "gpu_tdp", None) or 0) * _qty(item) for item in grouped.get("gpu", []))
    storage_watts = len(grouped.get("storage", [])) * STORAGE_WATTS_EACH
    fan_watts = sum(_qty(item) for item in grouped.get("fan", [])) * FAN_WATTS_EACH

    known_component_watts = cpu_watts + gpu_watts + storage_watts + fan_watts
    estimated_load = known_component_watts + (BASE_NON_GPU_CPU_WATTS if known_component_watts else 0)
    recommended = int(math.ceil((estimated_load * PSU_HEADROOM_MULTIPLIER) / 50.0) * 50) if estimated_load else None

    return {
        "cpu_watts": cpu_watts,
        "gpu_watts": gpu_watts,
        "storage_watts": storage_watts,
        "fan_watts": fan_watts,
        "base_system_watts": BASE_NON_GPU_CPU_WATTS if known_component_watts else 0,
        "estimated_load_watts": estimated_load or None,
        "recommended_psu_watts": recommended,
    }


def check_inventory_items(items: Iterable[Any], requirements: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Check compatibility for a set of Inventory rows.

    `requirements` may include min_ram_gb, min_vram_gb, and cpu_socket. The
    return value is safe for templates and JSON responses.
    """
    requirements = requirements or {}
    item_list = list(items or [])
    grouped = _items_by_type(item_list)
    issues: List[Dict[str, Any]] = []

    cpu = _first(grouped, "cpu")
    mobo = _first(grouped, "motherboard")
    case = _first(grouped, "case")
    psu = _first(grouped, "psu")
    cooler = _first(grouped, "cooler")
    gpu_items = grouped.get("gpu", [])
    ram_items = grouped.get("ram", [])

    cpu_spec = _spec(cpu) if cpu else None
    mobo_spec = _spec(mobo) if mobo else None
    case_spec = _spec(case) if case else None
    psu_spec = _spec(psu) if psu else None
    cooler_spec = _spec(cooler) if cooler else None

    # CPU socket requirement.
    if requirements.get("cpu_socket") and cpu_spec:
        match = _socket_match(cpu_spec.cpu_socket, requirements.get("cpu_socket"))
        if match is False:
            issues.append(_issue("fail", "CPU socket requirement mismatch", f"CPU socket {_display(cpu_spec.cpu_socket)} does not match requested socket {_display(requirements.get('cpu_socket'))}."))
        elif match is None:
            issues.append(_issue("warning", "CPU socket unknown", "Cannot verify CPU against the requested socket because the CPU socket is unknown."))

    # CPU <-> motherboard socket.
    if cpu and mobo:
        if cpu_spec and mobo_spec:
            match = _socket_match(cpu_spec.cpu_socket, mobo_spec.mobo_socket or mobo_spec.cpu_socket)
            if match is True:
                issues.append(_issue("ok", "CPU socket matches motherboard", f"{_display(cpu_spec.cpu_socket)} matches {_display(mobo_spec.mobo_socket or mobo_spec.cpu_socket)}."))
            elif match is False:
                issues.append(_issue("fail", "CPU socket mismatch", f"CPU socket {_display(cpu_spec.cpu_socket)} does not match motherboard socket {_display(mobo_spec.mobo_socket or mobo_spec.cpu_socket)}."))
            else:
                issues.append(_issue("warning", "CPU/motherboard socket unknown", "Cannot verify CPU and motherboard socket compatibility because one or both values are unknown."))
        else:
            issues.append(_issue("warning", "CPU/motherboard specs missing", "Cannot verify CPU and motherboard compatibility without linked spec records."))
    elif cpu or mobo:
        issues.append(_issue("warning", "CPU/motherboard pair incomplete", "Add both a CPU and motherboard to verify socket compatibility."))

    # RAM <-> motherboard memory type/capacity/slots.
    total_ram = _total_ram_gb(ram_items)
    if ram_items and mobo_spec:
        mobo_ram_type = getattr(mobo_spec, "mobo_memory_type", None)
        mobo_max = getattr(mobo_spec, "mobo_max_memory", None)
        mobo_slots = getattr(mobo_spec, "mobo_memory_slots", None)
        ram_types = sorted({getattr(_spec(item), "ram_type", None) for item in ram_items if _spec(item) and getattr(_spec(item), "ram_type", None)})
        ram_sticks = sum(_qty(item) for item in ram_items)

        if mobo_ram_type and ram_types:
            mismatches = [ram_type for ram_type in ram_types if _same_known_value(ram_type, mobo_ram_type) is False]
            if mismatches:
                issues.append(_issue("fail", "RAM type mismatch", f"Motherboard uses {_display(mobo_ram_type)}, but RAM includes {', '.join(mismatches)}."))
            else:
                issues.append(_issue("ok", "RAM type matches motherboard", f"RAM type {_display(mobo_ram_type)} is compatible."))
        else:
            issues.append(_issue("warning", "RAM type unknown", "Cannot verify RAM type because motherboard or RAM type is unknown."))

        if mobo_max and total_ram:
            if total_ram > float(mobo_max):
                issues.append(_issue("fail", "RAM exceeds motherboard maximum", f"Installed/planned RAM is {total_ram:g}GB, but motherboard max is {mobo_max}GB."))
            else:
                issues.append(_issue("ok", "RAM capacity is within motherboard limit", f"{total_ram:g}GB is within the {mobo_max}GB motherboard limit."))
        elif ram_items:
            issues.append(_issue("warning", "RAM capacity limit unknown", "Cannot verify motherboard maximum RAM capacity."))

        if mobo_slots and ram_sticks:
            if ram_sticks > int(mobo_slots):
                issues.append(_issue("fail", "Too many RAM sticks", f"RAM uses {ram_sticks} sticks, but motherboard has {mobo_slots} slots."))
            else:
                issues.append(_issue("ok", "RAM stick count fits", f"{ram_sticks} RAM stick(s) fit in {mobo_slots} motherboard slot(s)."))
    elif ram_items and not mobo:
        issues.append(_issue("warning", "Motherboard missing", "Add a motherboard to verify RAM compatibility."))

    # Build requirement checks.
    min_ram = requirements.get("min_ram_gb")
    if min_ram:
        if total_ram >= float(min_ram):
            issues.append(_issue("ok", "RAM requirement met", f"{total_ram:g}GB available/planned meets the {min_ram}GB requirement."))
        else:
            issues.append(_issue("fail", "RAM requirement not met", f"{total_ram:g}GB available/planned is below the {min_ram}GB requirement."))

    min_vram = requirements.get("min_vram_gb")
    total_vram = _total_vram_gb(gpu_items)
    if min_vram:
        if total_vram >= float(min_vram):
            issues.append(_issue("ok", "VRAM requirement met", f"{total_vram:g}GB VRAM meets the {min_vram}GB requirement."))
        elif gpu_items:
            issues.append(_issue("fail", "VRAM requirement not met", f"{total_vram:g}GB VRAM is below the {min_vram}GB requirement."))
        else:
            issues.append(_issue("warning", "GPU missing", "Add a GPU to verify the VRAM requirement."))

    # Case checks.
    if case_spec and mobo_spec:
        case_rank = _form_factor_rank(getattr(case_spec, "case_form_factor", None))
        mobo_rank = _form_factor_rank(getattr(mobo_spec, "mobo_form_factor", None))
        if case_rank and mobo_rank:
            if mobo_rank <= case_rank:
                issues.append(_issue("ok", "Motherboard fits case form factor", f"{_display(mobo_spec.mobo_form_factor)} motherboard fits {_display(case_spec.case_form_factor)} case support."))
            else:
                issues.append(_issue("fail", "Motherboard may not fit case", f"{_display(mobo_spec.mobo_form_factor)} motherboard may not fit case support {_display(case_spec.case_form_factor)}."))
        else:
            issues.append(_issue("warning", "Case/motherboard form factor unknown", "Cannot verify motherboard form factor against the case."))

    if case_spec and gpu_items:
        max_gpu = getattr(case_spec, "case_max_gpu_length", None)
        if max_gpu:
            checked_any = False
            for item in gpu_items:
                gpu_spec = _spec(item)
                length = getattr(gpu_spec, "gpu_length_mm", None) if gpu_spec else None
                if length:
                    checked_any = True
                    if int(length) > int(max_gpu):
                        issues.append(_issue("fail", "GPU may not fit case", f"{item.display_name} is {length}mm, but case clearance is {max_gpu}mm."))
            if checked_any and not any(i["title"] == "GPU may not fit case" for i in issues):
                issues.append(_issue("ok", "GPU length fits case", f"Known GPU lengths are within {max_gpu}mm case clearance."))
            elif not checked_any:
                issues.append(_issue("warning", "GPU length unknown", "Case GPU clearance is known, but GPU length is unknown."))
        else:
            issues.append(_issue("warning", "Case GPU clearance unknown", "Cannot verify GPU length against the case."))

    if case_spec and cooler_spec:
        max_height = getattr(case_spec, "case_max_cooler_height", None)
        cooler_height = getattr(cooler_spec, "cooler_height", None)
        if max_height and cooler_height:
            if int(cooler_height) <= int(max_height):
                issues.append(_issue("ok", "Cooler height fits case", f"Cooler height {cooler_height}mm fits within {max_height}mm clearance."))
            else:
                issues.append(_issue("fail", "Cooler too tall for case", f"Cooler height {cooler_height}mm exceeds {max_height}mm case clearance."))
        elif case or cooler:
            issues.append(_issue("warning", "Cooler/case clearance unknown", "Cannot verify cooler height against the case."))

    # Cooler checks.
    if cooler_spec and cpu_spec:
        socket_support = getattr(cooler_spec, "cooler_socket_support", None)
        if socket_support and cpu_spec.cpu_socket:
            if _norm(cpu_spec.cpu_socket) in _norm(socket_support):
                issues.append(_issue("ok", "Cooler supports CPU socket", f"Cooler support includes {_display(cpu_spec.cpu_socket)}."))
            else:
                issues.append(_issue("warning", "Cooler socket support needs review", f"Could not find CPU socket {_display(cpu_spec.cpu_socket)} in cooler support list."))
        else:
            issues.append(_issue("warning", "Cooler socket support unknown", "Cannot verify cooler socket compatibility."))

        cooler_tdp = getattr(cooler_spec, "cooler_tdp_rating", None)
        cpu_tdp = getattr(cpu_spec, "cpu_tdp", None)
        if cooler_tdp and cpu_tdp:
            if int(cooler_tdp) >= int(cpu_tdp):
                issues.append(_issue("ok", "Cooler TDP rating covers CPU", f"Cooler rating {cooler_tdp}W covers CPU TDP {cpu_tdp}W."))
            else:
                issues.append(_issue("warning", "Cooler TDP rating may be low", f"Cooler rating {cooler_tdp}W is below CPU TDP {cpu_tdp}W."))

    # PSU estimate.
    power = _estimate_power_watts(grouped)
    if psu_spec and getattr(psu_spec, "psu_wattage", None) and power["recommended_psu_watts"]:
        psu_watts = int(psu_spec.psu_wattage)
        if psu_watts >= int(power["recommended_psu_watts"]):
            issues.append(_issue("ok", "PSU wattage looks sufficient", f"{psu_watts}W PSU meets estimated recommendation of {power['recommended_psu_watts']}W."))
        else:
            issues.append(_issue("fail", "PSU wattage may be too low", f"{psu_watts}W PSU is below estimated recommendation of {power['recommended_psu_watts']}W."))
    elif psu or power["estimated_load_watts"]:
        issues.append(_issue("warning", "PSU sizing incomplete", "Cannot fully verify PSU sizing because PSU wattage or component TDP data is missing."))

    counts = {
        "ok": sum(1 for issue in issues if issue["status"] == "ok"),
        "warning": sum(1 for issue in issues if issue["status"] == "warning"),
        "fail": sum(1 for issue in issues if issue["status"] == "fail"),
        "info": sum(1 for issue in issues if issue["status"] == "info"),
    }

    if counts["fail"]:
        overall = "fail"
    elif counts["warning"]:
        overall = "warning"
    elif counts["ok"]:
        overall = "ok"
    else:
        overall = "unknown"

    # Simple readiness score: start at 100, subtract for failures/warnings and missing core parts.
    core_types = ["cpu", "motherboard", "ram", "psu", "case", "storage"]
    missing_core = [name for name in core_types if not grouped.get(name)]
    score = 100 - counts["fail"] * 22 - counts["warning"] * 7 - len(missing_core) * 8
    score = max(0, min(100, score))

    return {
        "overall": overall,
        "readiness_score": score,
        "counts": counts,
        "issues": issues,
        "totals": {
            "ram_gb": total_ram,
            "vram_gb": total_vram,
            "items": len(item_list),
            "missing_core": missing_core,
        },
        "power": power,
    }


def check_build_plan(plan: Any) -> Dict[str, Any]:
    components = []
    for component in plan.components.all():
        inventory = getattr(component, "inventory", None)
        if inventory:
            components.append(inventory)

    return check_inventory_items(
        components,
        requirements={
            "min_ram_gb": getattr(plan, "min_ram_gb", None),
            "min_vram_gb": getattr(plan, "min_vram_gb", None),
            "cpu_socket": getattr(plan, "cpu_socket", None),
        },
    )
