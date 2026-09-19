"""
stats.py — Inventory Breakdown page ("/stats") — v1

Breaks down your owned inventory (not the reference spec catalog) by a few
sensible fields per component type: CPU by socket, Motherboard by socket and
chipset, RAM by type and capacity, GPU by manufacturer and VRAM, Storage by
interface and capacity, PSU by wattage, Case by form factor. All grouping is
by exact value as stored (no numeric bucketing) — v1 scope per design
discussion.

Counts sum Inventory.quantity (physical units), not row count, since one
Inventory row can represent multiple physical items (e.g. a 2x8GB RAM kit
counted as 2 sticks). Inventory items without a resolved hardware_spec_id
(custom/manual entries with no matched spec) have no field values to group
by and are rolled into an "Unknown / custom entry" row per field so the
totals still add up to what's actually in inventory.
"""

from collections import defaultdict

from flask import Blueprint, render_template
from sqlalchemy import func

from app import db
from app.models import ComponentType, HardwareSpec, Inventory

bp = Blueprint('stats', __name__)

UNKNOWN_LABEL = "Unknown / custom entry"

# -----------------------------------------------------------------------------
# Config: component_type name (as stored in ComponentType.name) -> HardwareSpec
# fields to break down by. Add/remove entries here to change what shows up on
# the page — no route changes needed.
# -----------------------------------------------------------------------------
STATS_CONFIG = {
    "CPU": ["cpu_socket", "manufacturer"],
    "Motherboard": ["mobo_socket", "mobo_chipset"],
    "RAM": ["ram_type", "ram_size"],
    "GPU": ["manufacturer", "gpu_memory_size"],
    "Storage": ["storage_interface", "storage_capacity"],
    "PSU": ["psu_wattage"],
    "Case": ["case_form_factor"],
}

# -----------------------------------------------------------------------------
# QUICK PATCH — value normalization before grouping.
#
# Stopgap for known messy spec data (e.g. "LGA 2011-v3" vs "LGA 2011-3" both
# meaning the same socket). Runs only on this page — raw DB values untouched.
#
# BACKLOG: the real fix is normalizing these values at ingestion (seed import
# + scrapers) so bad variants never get written in the first place. This map
# should shrink over time as that lands.
#
# Keyed by field name -> {variant: canonical_form}. Add new fields/variants
# here as they're spotted.
# -----------------------------------------------------------------------------
VALUE_ALIASES = {
    "cpu_socket": {
        "LGA 2011-v3": "LGA 2011-3",
        "LGA2011-3": "LGA 2011-3",
        "LGA2011-V3": "LGA 2011-3",
    },
    "mobo_socket": {
        "LGA 2011-v3": "LGA 2011-3",
        "LGA2011-3": "LGA 2011-3",
        "LGA2011-V3": "LGA 2011-3",
    },
}


def _normalize_value(field_name, value):
    aliases = VALUE_ALIASES.get(field_name)
    if aliases and value in aliases:
        return aliases[value]
    return value


def get_breakdown(component_type_name, field_name):
    """
    Returns a list of (value, count) tuples for the given component type +
    HardwareSpec field, sorted by count descending. Count is summed
    Inventory.quantity, not row count.

    Inventory rows with no hardware_spec_id (custom entries) are grouped
    under UNKNOWN_LABEL so totals still reflect everything in inventory.
    Values are normalized via VALUE_ALIASES (if a mapping exists for this
    field) and merged before sorting.
    """
    field = getattr(HardwareSpec, field_name)

    rows = (
        db.session.query(field, func.coalesce(func.sum(Inventory.quantity), 0))
        .select_from(Inventory)
        .join(ComponentType, ComponentType.id == Inventory.component_type_id)
        .outerjoin(HardwareSpec, HardwareSpec.id == Inventory.hardware_spec_id)
        .filter(ComponentType.name == component_type_name)
        .group_by(field)
        .all()
    )

    merged = defaultdict(int)
    for value, qty in rows:
        qty = int(qty or 0)
        if qty == 0:
            continue
        label = _normalize_value(field_name, value) if value is not None else UNKNOWN_LABEL
        merged[label] += qty

    return sorted(merged.items(), key=lambda pair: pair[1], reverse=True)


@bp.route('/stats')
def stats():
    """
    Inventory breakdown page. Builds a nested dict:
        { component_type: { field_name: [(value, count), ...], ... }, ... }
    Component types / fields with no matching inventory are omitted so the
    template doesn't render blank tables.
    """
    breakdown_data = {}

    for component_type, fields in STATS_CONFIG.items():
        field_results = {}
        for field in fields:
            rows = get_breakdown(component_type, field)
            if rows:
                field_results[field] = rows
        if field_results:
            breakdown_data[component_type] = field_results

    return render_template('stats.html', breakdown_data=breakdown_data)
