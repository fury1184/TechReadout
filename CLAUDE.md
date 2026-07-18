# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

TechReadOut is a self-hosted Flask app for tracking hardware inventory (homelab / IT), managing host builds, looking up component specs from multiple sources, and analyzing a hardware library over time. Backend is Python 3.12 + Flask + SQLAlchemy on MariaDB 11; frontend is server-rendered Jinja templates with Bootstrap 5.3. Deployed via Docker Compose.

## Commands

### Tests

Pure-logic unit tests (no DB required) live in `tests/` and cover the matching core: `scrapers/normalization.py`, `scrapers/scoring.py`, `scrapers/validation.py`, and the DB-free helpers in `duplicates.py`.

```bash
pip install -r requirements.txt -r requirements-dev.txt   # pytest + import-time deps
python -m pytest                                           # run all tests
python -m pytest tests/test_scoring.py                     # single file
python -m pytest tests/test_scoring.py::TestScoreCandidate # single class
python -m pytest -k vram                                   # by keyword
```

`conftest.py` at the repo root puts the project on `sys.path` so `import app...` resolves. Tests import the `app` package, which pulls in Flask + bs4/requests at import time — hence they're in `requirements`/`requirements-dev`. There is no linter config or build step; the DB-backed route/query layers are not yet covered.

### Running the app

Development happens against the Docker Compose stack.

```bash
docker compose up -d --build           # Build and start app + MariaDB
docker compose up -d --build --force-recreate   # Rebuild after copying patched files (see PATCH_FILES.txt)
docker compose logs -f app             # Tail app logs (lookup chain prints extensively here)
docker compose down                    # Stop stack (mariadb_data volume persists)
```

App runs at `http://localhost:5000`; MariaDB is exposed on `3306`. The `./app` directory is bind-mounted into the container, so Python changes are picked up on app restart (Flask `debug=True`), but new dependencies or Dockerfile changes require a rebuild.

Run the app directly (needs a reachable MariaDB and env vars set — `DATABASE_URL`, `SECRET_KEY`):

```bash
python run.py                          # create_app() + seed import + dev server on 0.0.0.0:5000
```

Seed importer CLI (also runs automatically on container startup via `run.py`):

```bash
python -m app.seeds.seed_db            # Import all seed JSONs (skipped if seed_version matches)
python -m app.seeds.seed_db --force    # Force re-import
python -m app.seeds.seed_db --check    # Show current vs available seed version
```

## Database & migrations

- **Schema is created two ways and both must be kept in sync manually:**
  - `migrations/init.sql` — full `CREATE TABLE` schema, run once by MariaDB's docker-entrypoint on first container init (fresh volume only).
  - `app/models.py` — SQLAlchemy models used at runtime. **Flask-Migrate is initialized but there is no `migrations/` Alembic env**; the `migrations/*.sql` files are hand-written, version-named upgrade scripts (e.g. `v3.5.4_ram_ecc.sql`) run manually against existing databases.
- `app/__init__.py` also idempotently `CREATE TABLE IF NOT EXISTS` for `app_settings` and `lookup_cache` at startup.
- When you add a column: update `app/models.py`, `migrations/init.sql`, **and** add a new `migrations/vX.Y.Z_*.sql` upgrade script for existing installs. The scraper→spec save path in `app/routes/api.py:_save_scraper_result` and the serializers must also learn the new field.

## Architecture

### App factory & blueprints
`app/__init__.py:create_app()` wires everything. Blueprints and their URL prefixes:
- `routes/main.py` (`/`) — inventory, hosts, dashboard, specs, lookup cache, review queue. This is the largest surface.
- `routes/api.py` (`/api`) — JSON lookup + CRUD endpoints consumed by the templates' JS.
- `routes/scraper.py` (`/scraper`) — lookup source info & **Lookup Settings** (toggles for Scrape.Do depth, eBay, Open WebUI).
- `routes/planner.py` (`/planner`) — build planner.
- `routes/backup.py` (`/backup`) — backup/restore, Excel/CSV export, and **AI Import** (manual spec import + JSON import).

### The spec lookup chain (core concept)
Lookup is a fallback chain, orchestrated across two layers. `app/routes/api.py:lookup_hardware` (the `/api/lookup` endpoint) is the orchestrator; `app/scrapers/lookup.py:lookup_hardware` is only the web-scraper step.

Order of resolution:
1. **Local DB / seed search** — done *in api.py* (Strategies 1–4: exact, contains, contained-in, fuzzy word overlap). The seed database (~258 curated specs from `app/seeds/*.json`) lives in `hardware_specs`, so most lookups resolve here for free.
2. **Lookup cache** (`lookup_cache` table, 30-day TTL) — a recorded `miss` short-circuits the scraper only when there are also no DB candidates.
3. **Web scraper** — `app/scrapers/lookup.py:lookup_hardware`: Scrape.Do (paid) against TechPowerUp / Intel ARK / Amazon depending on component type, then **Open WebUI** (optional self-hosted LLM) as the last automatic step.
4. **Manual AI Import** (`/backup/import-specs`) — for anything the chain misses.

Confidence gating (in `api.py`, constants `REVIEW_THRESHOLD = 90`, `OPENWEBUI_CONFIDENCE_CAP = 89`):
- **Auto-accept** only when best confidence ≥ 90 **and** `validate_result()` confirms the model actually matches (guards against fuzzy Strategy-4 false positives).
- **Open WebUI results are capped at 89** so they can *never* auto-accept — they always land in the Pending Review queue regardless of score.
- Otherwise up to 3 scored candidates are returned as `needs_review` and persisted to `pending_reviews` for the review queue.

Scrape.Do calls are budgeted per-request via a `ContextVar` (`_LOOKUP_BUDGET`) with conservative/normal/thorough depth profiles limiting paid sequences and total calls — see `_load_scrapedo_budget_settings` / `start_scrapedo_sequence` in `lookup.py`.

### Scraper helper modules (split out of the once-5000-line lookup.py)
- `app/scrapers/normalization.py` — CPU/GPU model-name cleaning and query normalization.
- `app/scrapers/scoring.py` — `score_candidate` (name-match confidence) and `enrich_scrape_result`.
- `app/scrapers/validation.py` — `validate_result`, `has_minimum_specs`, `coerce_unknowns_to_none`, acceptable-hit gating. Unknown values must stay `null` — the AI/scraper is required never to guess.
- `app/scrapers/ebay.py` — eBay Browse API (Client Credentials OAuth) for used-market price estimates, cached in `price_cache` (24h TTL).
- `app/scrapers/openwebui.py` — Open WebUI OpenAI-compatible chat completions client.

### Data model (`app/models.py`)
- `HardwareSpec` — one wide table with per-component-type column groups (`cpu_*`, `gpu_*`, `ram_*`, `mobo_*`, `storage_*`, `psu_*`, `cooler_*`, `case_*`, `fan_*`, `nic_*`, `sound_*`). Its display/summary/detail/confidence `@property` accessors delegate to `app/serializers/hardware.py` — put presentation logic there, not on the model.
- `Inventory` — a physical item; links to a `HardwareSpec` **or** carries `custom_name`/`custom_manufacturer` for off-library items. `purchase_price` is **per unit**; `profit` multiplies by quantity. RAM inventory quantity counts physical sticks, not kits (see `app/inventory_rules.py:inventory_quantity`).
- `Host` — a build; `Inventory.assigned_to_host_id` assigns components. Build cost = sum of `purchase_price × quantity` of assigned items.
- `BuildPlan` / `BuildPlanComponent`, `LookupCache`, `PendingReview`, `PriceCache`, `AppSetting`, `Backup`, `ScrapeJob`.
- `AppSetting` is a key/value store (`get`/`get_bool`/`set` static helpers) backing all runtime toggles from the Lookup Settings page (scrape depth, eBay on/off, Open WebUI url/model/token gate).

### Serializers & shared helpers
- `app/serializers/hardware.py` — the single source for turning a `HardwareSpec` into dicts (`hardware_spec_to_dict`), human summaries (`spec_summary`), detail rows (`detail_rows`), and source/confidence metadata. Any new spec field surfaces to the UI here.
- `app/compatibility.py` — read-only, migration-free build/host readiness checks (form-factor fit, socket match, etc.). Treats unknown/null as unknown, never guesses.
- `app/duplicates.py` — conservative duplicate detection (normalizes manufacturer/model, reports exact/likely/possible) used by the add-inventory form and `/api/duplicates`.

## Versioning & release conventions
- `app/version.py` is the **single source of truth** for the displayed version (`APP_VERSION`); injected into all templates via a context processor. Bump it here when releasing.
- Note the seed data has its own independent version: `SEED_VERSION` in `app/seeds/seed_db.py` (controls re-import).
- Releases are distributed as file patches: `PATCH_FILES.txt` lists the changed files for the current hotfix and the redeploy steps. `README.md` holds the running changelog — keep it and `PATCH_FILES.txt` current when shipping a version bump.

## Environment variables
Set in `.env` (compose reads it) or the container environment. `SECRET_KEY` and `DATABASE_URL` are the only ones needed to boot. Optional integrations: `SCRAPEDO_TOKEN` (paid scraper fallback), `EBAY_APP_ID` + `EBAY_APP_SECRET` (price estimates), `OPENWEBUI_API_TOKEN` (LLM lookup). `SEED_ON_STARTUP=false` skips the startup seed import.
