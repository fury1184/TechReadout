# Changelog

All notable changes to TechReadOut. Newest first. The running version lives in `app/version.py`.

Each entry says what changed; anything you must do when upgrading (migrations, new env vars, maintenance commands) is under **Upgrade**. Per-release file lists and deploy steps are in `PATCH_FILES.txt`, which only covers the current release.

## v3.8.7
- **Fixed:** RAM of different capacities no longer canonicalizes to one record. Import Specs skipped `Samsung 8GB PC3-14900R` as a duplicate of `Samsung 16GB 2Rx4 PC3-14900R` because `extract_part_number()` treated the JEDEC rating `PC3-14900R` as a part number (its exclusion missed the R/U/E suffix). Also affected adding specs through `/api`, which uses the same canonicalizer.
- JEDEC speed/module ratings are never part numbers: `PC3-14900R`, `PC3L-12800R`, `PC4-2400T-R`, `PC4-2666V-RB2`, full label strings like `PC3-12800R-11-12-E2`, `DDR3L-1600`, `DDR4-3200R`.
- New `extract_ram_capacity()`; `choose_existing_canonical_name()` skips RAM candidates whose stated capacity differs (including a 2x8GB kit vs a 16GB stick), even when a real part number matches. Only enforced when both names state a capacity.
- New `tests/test_ram_canonicalization.py`.
- Docs consolidated: this `CHANGELOG.md` replaces the README changelog, `README_PATCH.md`, and versioned `PATCH_FILES_*.txt`. Resolved leftover merge-conflict markers in `README.md`.

## v3.8.6
- **CPU/socket names:** `normalize_socket()` strips `FC`/`Socket ` prefixes (`FCLGA1151` → `LGA 1151`). New `canonical_spec_socket()` adds `(300 Series)` to a plain `LGA 1151` when the record is clearly Coffee Lake — 300-series chipset (H310/B360/B365/H370/Q370/Z370/Z390) for boards; Core i3/5/7/9 8xxx/9xxx, Pentium Gold G54–56xx, Celeron G49xx for CPUs. Only adds the marker, never removes it; Xeon E-2100/2200 left plain on purpose (C246-only).
- `models.py` before_insert/before_update socket hook now calls `canonical_spec_socket`, so every save path (scrapers, manual edits, seed, restore) gets the rule.
- ASUS official motherboard parser keeps generation suffixes (`LGA2011-v3` no longer truncated to `LGA2011`).
- New `app/maintenance/backfill_sockets.py` re-runs `canonical_spec_socket` over existing specs. Dry run by default; safe to re-run when the rules change.
- **Fixed** two bugs in the v3.8.4 `backup.py`: `PendingReview` must use `db.session.query()` (its `query` column shadows `.query` — export and restore would have crashed), and stray in-function `datetime` imports crashed restore on items without a purchase date. Verified export → wipe → restore → restore again on a test DB.
- Optional `sql/strip_chipset_vendor_prefix.sql` (see header in the file).
- **Upgrade:** `docker exec techreadout-app python -m app.maintenance.backfill_sockets` (dry run), then `--apply` after review. Delete the stale root-level `/opt/stacks/techreadout/compatibility.py` (duplicate of `app/compatibility.py`; nothing imports it).
- Known (pre-existing): restoring over existing inventory adds the backup's quantity to the matching row rather than skipping it.

## v3.8.5
- **Fixed:** `compatibility._socket_match()` uses an exact match on the canonical socket instead of substring containment. v1 CPUs no longer pass on 300-series boards (and vice versa), and LGA 2011 CPUs no longer pass on LGA 2011-3 (X99) boards.

## v3.8.4
- **Real database backups:** two `mysqldump --single-transaction` sidecars in `docker-compose.yml` (`db-backup-local` → `./data/backups/mysqldump`, `db-backup-nas` → `${NAS_BACKUP_HOST_PATH}`), using `fradelg/mysql-cron-backup`. Daily at 03:00 UTC, 30 kept, fully independent of each other and of the app's JSON export.
- **Fixed:** `import_all_data()` (what Restore calls) was missing `gpu_base_clock`/`gpu_boost_clock` and every `mobo_`/`storage_`/`psu_`/`cooler_`/`case_`/`fan_` field — the same bug fixed in `import_specs_json()` in v3.8.3.
- **Fixed:** Restore never imported build plans. Now restores `BuildPlan` and `BuildPlanComponent` rows with old-to-new id remapping.
- Export/restore now also cover `app_settings` (restore overwrites current settings), `pending_reviews` (embedded candidate `spec_id`s remapped), and `scrape_jobs`. `LookupCache`, `PriceCache`, and `Backup` intentionally excluded.
- **Upgrade:** add `NAS_BACKUP_HOST_PATH` to your real `.env`, then `docker compose up -d`. Run `mountpoint ${NAS_BACKUP_HOST_PATH}` on the host first — if the path isn't the live NAS mount, Docker silently creates a plain local folder there. Check `docker logs techreadout-db-backup-local` / `-nas` for a `.sql.gz` after the first run, and test-restore one dump into a scratch MariaDB once.

## v3.8.3
- **Fixed:** JSON export writes every `mobo_`/`storage_`/`psu_`/`cooler_`/`case_`/`fan_` field and GPU base/boost clocks; `import_specs_json()` reads them back.
- CPU-Monkey `LGA 1151-2` maps to `LGA 1151 (300 Series)` instead of collapsing to plain `LGA1151`.

## v3.8.2
- Normalized socket names on the Inventory Breakdown page.

## v3.8.1
- **Fixed:** CPU-Monkey lookup built malformed slugs (duplicate "intel") for combined manufacturer+model queries like `intel i5-2500k`.

## v3.8.0
- Search box on the inventory list (model/manufacturer); CSV export respects the search.

## v3.7.0
- **Inventory Breakdown page** — new `/stats` page groups owned inventory by socket, chipset, type, capacity, manufacturer, VRAM, interface, wattage, and form factor per component type. Counts sum physical unit quantity, not row count.
- **Socket value normalization** — known messy variants (e.g. `LGA 2011-3` vs `LGA 2011-v3`) are merged before grouping on the breakdown page.
- **Fixed:** Manual Entry Mode on the Add Inventory page no longer persists across page loads via `localStorage` — it previously carried over silently between sessions and could cause a lookup to be skipped with no indication why.
- **Fixed:** closing the low-confidence review modal (Cancel or X) without selecting a candidate used to leave a frozen "please review" banner with no way forward. It now falls back to showing manual entry and the AI Import suggestion on the main form.
- **Fixed:** review queue dedup (`review_save`) now compares normalized query text instead of an exact string match, so near-identical retries of the same lookup collapse into one pending entry instead of creating a duplicate that looks "stuck" after the first is skipped or accepted.

## v3.6.0 – v3.6.1
- **Motherboard lookup chain overhaul** — new fallback order: manufacturer official site (ASUS, via embedded `__NUXT_DATA__` JSON) → Newegg (via embedded `window.__initialState__` JSON, trust score 85) → Amazon (scoped to niche/clone brands only: Machinist, Huananzhi, Jingyue, with `render=true`) → Scrape.Do/TechPowerUp → Open WebUI.
- **Optional manufacturer field** added to the lookup API and Add Inventory form as a non-blocking nudge when a query alone doesn't resolve.
- **`search_motherboard()` retired** as a no-op, following the same backward-compat pattern as `use_intel_ark`/`use_amd_official`.
- **NAS backup path** moved from env-var-only to a DB-backed `AppSetting`, editable directly from the Backup page — no redeploy needed to change it.
- **Fixed:** CSV export was returning `jsonify()` instead of a real `.csv`/`.zip` file.
- **New `/backup/export-json`** direct-download route.
- **Fixed:** sidebar scroll bug via `display: flex; flex-direction: column; overflow-y: auto` plus `margin-top: auto`.
- MSI and Gigabyte official-site parsers validated but not yet wired into the lookup chain.

## v3.5.8 – v3.5.10
- v3.5.10: matching fixes.
- v3.5.9: more accurate CPU matching.
- v3.5.8: **Fixed:** assigning an already-assigned part to a host marked it verified instead of installed.

## v3.5.7
- Review match modal now updates selection state reliably, auto-selects the top-ranked candidate, and warns when a candidate has no saved spec ID.
- Auto-accept now requires `validate_result()` to confirm the match, so fuzzy recall can't auto-accept a similar-but-wrong part (e.g. a different Xeon SKU).
- Scrape.Do TechPowerUp lookups now pass `render=true` so JS-rendered spec pages resolve reliably.

## v3.5.5
- AI JSON Import templates now request exactly one hardware item and one JSON object.
- JSON arrays are rejected by both browser validation and server-side import.
- Open WebUI integration explicitly requests one item and rejects non-object responses.
- Unknown values remain `null`; the AI/LLM must not guess.

## v3.5.4
- RAM ECC/non-ECC tracking via `ram_ecc`.
- RAM module type tracking via `ram_module_type` for UDIMM/RDIMM/LRDIMM/SODIMM.
- RAM display summaries/details now include ECC and module type when known.
- AI/scraper RAM prompts now require unknown ECC/module type values to be returned as null.
- **Upgrade:** run `migrations/v3.5.4_ram_ecc.sql` against existing databases.

## v3.5.3
- **Centralized app version** — dashboard and lower-right badge now use one shared version source in `app/version.py`.
- **Duplicate detection** — add-inventory form now warns about similar existing specs and inventory rows before saving.
- **Duplicate matching helpers** — new conservative duplicate matcher normalizes manufacturer/model names and reports exact/likely/possible matches without auto-blocking.

## v3.5.1
- **RAM kit inventory quantity** — RAM specs still describe the kit, but inventory quantity now counts physical modules/sticks. Example: 16GB (2x8GB) defaults to quantity 2.
- **Manual RAM entry** — added a Modules / Sticks field and per-stick capacity display where possible.
- **Consistent RAM quantity rules** — add form, API-created inventory, and import-created inventory all apply the same kit-to-stick rule.

## v3.5.0
- **Improved spec lookup** — richer scraper confidence scoring, source metadata, stricter AI/null handling, and better motherboard/RAM validation.
- **Richer item details** — inventory, specs, and review queue now show reusable summaries and component-specific detail rows.
- **Code cleanup** — scraper normalization, scoring, validation, and hardware serialization helpers were split into shared modules.
- **Open WebUI automatic lookup** — optional lookup step for missing specs. Results are routed to review and capped below auto-accept.

## v3.3.0
- **Dark mode** — theme toggle in the sidebar footer. Defaults to your OS preference on first visit; choice is saved per browser in `localStorage`. Built on Bootstrap 5.3's native `data-bs-theme` support.
- **Safe spec deletion with cascade** — deleting a spec now opens a confirmation dialog listing any linked inventory items, with three options: delete the spec and items together, unlink the items (kept as custom entries with their name preserved) and delete only the spec, or cancel.
- **Bulk delete cascade** — bulk spec deletion now deletes linked inventory items by default (checkbox in the bulk delete dialog; uncheck to skip in-use specs instead).
- **Excel export** — new "Export as Excel (.xlsx)" button on the Backup page generates a workbook with Summary, Inventory, Hosts, and Hardware Specs sheets (requires `openpyxl`, now in requirements).
- **Fixed:** deleting a spec no longer fails with a foreign key `IntegrityError` when `lookup_cache` or pending review entries still reference it — references are cleaned up automatically (single and bulk delete).
- **Fixed:** deleting an inventory item referenced by a Build Plan no longer fails — the plan slot is kept but marked unfulfilled.

## v3.2.0
- **eBay price prompt on add form** — when purchase price is left blank, the add form now intercepts submit, queries the eBay Browse API for a median used-market price, and prompts you to accept or skip before saving. Requires eBay pricing to be enabled in Lookup Settings and `EBAY_APP_ID`/`EBAY_APP_SECRET` set in the environment.
- New `GET /inventory/ebay-price-preview` endpoint powers the add-form prompt; reuses the `PriceCache` 24-hour TTL shared with the detail-page estimate button.

## v3.1.0
- **eBay price estimates** — per-item "Get eBay Estimate" button on inventory detail pages fetches a median used-market price via the eBay Browse API (Client Credentials OAuth, no user login required). Results are cached for 24 hours in a new `price_cache` table. Toggle on/off in Lookup Settings; requires `EBAY_APP_ID` and `EBAY_APP_SECRET` environment variables.
- **`price_is_estimate` flag** — inventory items track whether their purchase price was set from an eBay estimate (shown with a ✱ badge).
- **`price_cache` table** — new DB table added in `v3.1.0_ebay_price.sql` migration.

## v3.0.0
- **Seed database system** — `app/seeds/seed_db.py` imports curated hardware specs from `seeds/*.json` (CPU, GPU, RAM, motherboard, storage, PSU, cooler, case, fan, NIC) into the `hardware_specs` table on container startup
- **Seed version tracking** — current seed version stored in `app_settings`; re-imports skipped unless `--force` is passed
- **Pre-populated spec cache** — fresh installs ship with a baseline spec library (~258 entries), reducing first-run scrape load
- **Scraper chain trimmed** — FlareSolverr, Playwright (TechPowerUp + Amazon + Intel ARK), AMD Official, and manufacturer-site scrapers all retired. Anti-bot measures had made every free path unreliable, and the seed database now covers the bulk of lookups. New chain: seed DB → BeautifulSoup direct on TechPowerUp → Scrape.Do (paid) → AI Import.
- **Container slimmed** — Playwright and Chromium browser dropped from the image; FlareSolverr sidecar removed from `docker-compose.yml`. Build is faster and the runtime image is much smaller.
- **`lookup.py` reduced** from ~5,000 lines to ~2,400 lines

## v2.1.0
- **Bulk inventory add** — "Add Another" checkbox pre-fills type and manufacturer for fast batch entry
- **Inventory split** — split a qty>1 row into individual qty=1 rows
- **Inventory CSV export** — one-click export respecting current filters; includes per-unit price and total cost columns
- **Purchase price is now per unit** — total cost shown where qty > 1; profit calculation updated accordingly
- **Host build cost** — estimated build cost shown on host detail page and host list cards
- **Host edit** — edit hostname, IP, MAC, OS, purpose, status, and description inline
- **Host comparison** — select two hosts from the list for a side-by-side component breakdown
- **Dashboard redesign** — 6 stat cards (added inventory value + pending reviews), Chart.js component distribution chart, recent additions table
- **Lookup cache management** — browse, filter, and bulk-clear the lookup cache; per-item re-lookup button on inventory detail
- **Review queue page** — persists scrape review triggers to DB; dedicated queue page with accept/skip actions; live sidebar badge

## v2.0.0
- Human review modal for scrape matches below 90% confidence
- Confidence scoring across all scrape sources
- AI Import fallback integration
- Version tracking in UI

## v1.x
- Initial inventory, spec lookup, host management, build planner, backup/restore
