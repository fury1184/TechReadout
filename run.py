#!/usr/bin/env python3
"""
TechReadout — Hardware Inventory System (v3.0)
Entry point for the Flask application.

On startup, runs the seed importer once to populate the hardware_specs
table from the bundled JSON seed files. Subsequent boots are no-ops because
the seed_version stored in app_settings matches.

Disable seeding by setting SEED_ON_STARTUP=false (case-insensitive).
"""

import os
from app import create_app
from app.seeds.seed_db import run_seed_import

app = create_app()


def _should_seed() -> bool:
    """Honor SEED_ON_STARTUP env var; default to True."""
    return os.environ.get("SEED_ON_STARTUP", "true").strip().lower() not in {
        "0", "false", "no", "off",
    }


if _should_seed():
    # Idempotent: skipped if seed_version in app_settings already matches.
    # Errors are caught inside run_seed_import and logged; the web server
    # still starts so the user can fix the issue from the UI.
    run_seed_import(app=app)
else:
    print("[Seed] SEED_ON_STARTUP disabled; skipping seed import.", flush=True)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
