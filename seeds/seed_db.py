#!/usr/bin/env python3
"""
seed_db.py — TechReadout v3.0 seed database importer

Loads hardware specs from app/seeds/*.json into the hardware_specs table.
Designed to run on container startup (called from run.py) and also as a
standalone CLI tool. Skips entries that already exist by (component_type,
manufacturer, model). Tracks seed version in app_settings to avoid
re-importing unchanged data.

Usage (CLI):
    python -m app.seeds.seed_db                # Import all seed files
    python -m app.seeds.seed_db --force        # Force re-import even if version matches
    python -m app.seeds.seed_db --check        # Show current vs available seed version

Usage (in-process, from run.py):
    from app.seeds.seed_db import run_seed_import
    run_seed_import(app=app)                   # Skips if version already matches
"""

import sys
import json
import argparse
from datetime import datetime, date
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────
SEED_VERSION = "3.3.4"
SEEDS_DIR    = Path(__file__).parent          # JSONs live next to this file
SEED_FILES   = [
    "gpu_specs.json",
    "cpu_specs.json",
    "ram_specs.json",
    "motherboard_specs.json",
    "storage_specs.json",
    "psu_specs.json",
    "cooler_specs.json",
    "case_specs.json",
    "fan_specs.json",
    "nic_specs.json",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    """Print with flush so output shows up promptly under Docker."""
    print(msg, flush=True)


def get_or_create_component_type(db, ComponentType, name: str):
    """Get existing component type or create it."""
    ct = db.session.query(ComponentType).filter_by(name=name).first()
    if not ct:
        ct = ComponentType(name=name)
        db.session.add(ct)
        db.session.flush()
        _log(f"  [Seed] Created component type: {name}")
    return ct


def parse_date(value):
    """Parse date string to date object. Returns None on failure."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def spec_exists(db, HardwareSpec, component_type_id: int, manufacturer: str, model: str) -> bool:
    """Check if a spec entry already exists by (type, manufacturer, model)."""
    return db.session.query(HardwareSpec).filter_by(
        component_type_id=component_type_id,
        manufacturer=manufacturer,
        model=model,
    ).first() is not None


def import_seed_file(db, HardwareSpec, ComponentType, filepath: Path) -> tuple:
    """
    Import a single seed JSON file.
    Returns (inserted, skipped) counts.
    """
    if not filepath.exists():
        _log(f"  [Seed] Skipping missing file: {filepath.name}")
        return 0, 0

    with open(filepath, "r", encoding="utf-8") as f:
        entries = json.load(f)

    inserted = 0
    skipped  = 0

    for entry in entries:
        component_type_name = entry.pop("component_type", None)
        if not component_type_name:
            _log("  [Seed] Warning: entry missing component_type, skipping")
            skipped += 1
            continue

        ct = get_or_create_component_type(db, ComponentType, component_type_name)

        manufacturer = entry.get("manufacturer", "")
        model        = entry.get("model", "")

        if not model:
            _log("  [Seed] Warning: entry missing model, skipping")
            skipped += 1
            continue

        if spec_exists(db, HardwareSpec, ct.id, manufacturer, model):
            skipped += 1
            continue

        # Build the HardwareSpec object
        spec = HardwareSpec(
            component_type_id   = ct.id,
            manufacturer        = manufacturer,
            model               = model,
            release_date        = parse_date(entry.get("release_date")),
            msrp                = entry.get("msrp"),
            source_url          = entry.get("source_url"),
            scraped_at          = datetime.utcnow(),
            raw_data            = {"seed_version": SEED_VERSION},
            # CPU fields
            cpu_socket          = entry.get("cpu_socket"),
            cpu_cores           = entry.get("cpu_cores"),
            cpu_threads         = entry.get("cpu_threads"),
            cpu_base_clock      = entry.get("cpu_base_clock"),
            cpu_boost_clock     = entry.get("cpu_boost_clock"),
            cpu_tdp             = entry.get("cpu_tdp"),
            cpu_architecture    = entry.get("cpu_architecture"),
            # GPU fields
            gpu_memory_size     = entry.get("gpu_memory_size"),
            gpu_memory_type     = entry.get("gpu_memory_type"),
            gpu_base_clock      = entry.get("gpu_base_clock"),
            gpu_boost_clock     = entry.get("gpu_boost_clock"),
            gpu_tdp             = entry.get("gpu_tdp"),
            gpu_bus_interface   = entry.get("gpu_bus_interface"),
            # RAM fields
            ram_size            = entry.get("ram_size"),
            ram_type            = entry.get("ram_type"),
            ram_speed           = entry.get("ram_speed"),
            ram_cas_latency     = entry.get("ram_cas_latency"),
            ram_modules         = entry.get("ram_modules"),
            # Motherboard fields
            mobo_socket         = entry.get("mobo_socket"),
            mobo_chipset        = entry.get("mobo_chipset"),
            mobo_form_factor    = entry.get("mobo_form_factor"),
            mobo_memory_slots   = entry.get("mobo_memory_slots"),
            mobo_memory_type    = entry.get("mobo_memory_type"),
            mobo_max_memory     = entry.get("mobo_max_memory"),
            mobo_pcie_x16_slots = entry.get("mobo_pcie_x16_slots"),
            mobo_pcie_x4_slots  = entry.get("mobo_pcie_x4_slots"),
            mobo_pcie_x1_slots  = entry.get("mobo_pcie_x1_slots"),
            mobo_m2_slots       = entry.get("mobo_m2_slots"),
            mobo_sata_ports     = entry.get("mobo_sata_ports"),
            # Storage fields
            storage_capacity    = entry.get("storage_capacity"),
            storage_interface   = entry.get("storage_interface"),
            storage_type        = entry.get("storage_type"),
            storage_form_factor = entry.get("storage_form_factor"),
            storage_read_speed  = entry.get("storage_read_speed"),
            storage_write_speed = entry.get("storage_write_speed"),
            # PSU fields
            psu_wattage         = entry.get("psu_wattage"),
            psu_efficiency      = entry.get("psu_efficiency"),
            psu_modular         = entry.get("psu_modular"),
            psu_form_factor     = entry.get("psu_form_factor"),
            # Cooler fields
            cooler_type           = entry.get("cooler_type"),
            cooler_socket_support = entry.get("cooler_socket_support"),
            cooler_tdp_rating     = entry.get("cooler_tdp_rating"),
            cooler_fan_size       = entry.get("cooler_fan_size"),
            cooler_height         = entry.get("cooler_height"),
            # Case fields
            case_form_factor       = entry.get("case_form_factor"),
            case_type              = entry.get("case_type"),
            case_max_gpu_length    = entry.get("case_max_gpu_length"),
            case_max_cooler_height = entry.get("case_max_cooler_height"),
            # Fan fields
            fan_size      = entry.get("fan_size"),
            fan_rpm_max   = entry.get("fan_rpm_max"),
            fan_airflow   = entry.get("fan_airflow"),
            fan_noise     = entry.get("fan_noise"),
            fan_connector = entry.get("fan_connector"),
            # NIC fields
            nic_speed     = entry.get("nic_speed"),
            nic_interface = entry.get("nic_interface"),
            nic_ports     = entry.get("nic_ports"),
            # Sound card fields
            sound_interface   = entry.get("sound_interface"),
            sound_channels    = entry.get("sound_channels"),
            sound_sample_rate = entry.get("sound_sample_rate"),
        )

        db.session.add(spec)
        inserted += 1

    db.session.commit()
    return inserted, skipped


# ── Public entry point ────────────────────────────────────────────────────────

def run_seed_import(app=None, force: bool = False, check_only: bool = False) -> dict:
    """
    Run the seed import. Safe to call repeatedly: skipped if SEED_VERSION
    already matches what is stored in app_settings (unless force=True).

    Args:
        app:        An existing Flask app to use. If None, create_app() is called.
        force:      If True, re-import even when the seed version matches.
        check_only: If True, just print versions and return without importing.

    Returns:
        dict summary: {
            "status":   "imported" | "skipped" | "checked" | "error",
            "version":  SEED_VERSION,
            "inserted": int,
            "skipped":  int,
            "error":    str | None,
        }
    """
    # Imports are deferred so this module can be imported before the app is
    # ready (e.g. by run.py before create_app() is called).
    from app import create_app, db
    from app.models import HardwareSpec, ComponentType, AppSetting

    summary = {
        "status":   "unknown",
        "version":  SEED_VERSION,
        "inserted": 0,
        "skipped":  0,
        "error":    None,
    }

    if app is None:
        app = create_app()

    try:
        with app.app_context():
            current_version = AppSetting.get("seed_version", default="none")

            if check_only:
                _log(f"[Seed] Current seed version : {current_version}")
                _log(f"[Seed] Available seed version: {SEED_VERSION}")
                summary["status"] = "checked"
                return summary

            if current_version == SEED_VERSION and not force:
                _log(f"[Seed] Seed version {SEED_VERSION} already imported. Skipping.")
                summary["status"] = "skipped"
                return summary

            _log(f"[Seed] Importing seed data v{SEED_VERSION}...")
            _log(f"[Seed] Seeds directory: {SEEDS_DIR}")

            total_inserted = 0
            total_skipped  = 0

            for filename in SEED_FILES:
                filepath = SEEDS_DIR / filename
                _log(f"\n[Seed] Processing {filename}...")
                inserted, skipped = import_seed_file(db, HardwareSpec, ComponentType, filepath)
                _log(f"  → Inserted: {inserted}  Skipped (already exist): {skipped}")
                total_inserted += inserted
                total_skipped  += skipped

            # Record version after a successful import.
            AppSetting.set("seed_version", SEED_VERSION)
            AppSetting.set("seed_imported_at", datetime.utcnow().isoformat())
            db.session.commit()

            _log(f"\n[Seed] Done.")
            _log(f"[Seed] Total inserted : {total_inserted}")
            _log(f"[Seed] Total skipped  : {total_skipped}")
            _log(f"[Seed] Seed version   : {SEED_VERSION} saved to app_settings")

            summary["status"]   = "imported"
            summary["inserted"] = total_inserted
            summary["skipped"]  = total_skipped
            return summary

    except Exception as e:
        _log(f"[Seed] ERROR during seed import: {e}")
        summary["status"] = "error"
        summary["error"]  = str(e)
        return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    # Make sure project root is on sys.path so `from app import ...` works
    # when this file is invoked directly as `python app/seeds/seed_db.py`.
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    parser = argparse.ArgumentParser(description="TechReadout seed database importer")
    parser.add_argument("--force", action="store_true", help="Re-import even if seed version matches")
    parser.add_argument("--check", action="store_true", help="Show current seed version and exit")
    args = parser.parse_args()

    result = run_seed_import(force=args.force, check_only=args.check)

    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
