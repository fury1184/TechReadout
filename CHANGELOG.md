# Changelog

All notable changes to TechReadOut. Newest first. The running version lives in `app/version.py`.

Each entry says what changed; anything you must do when upgrading (migrations, new env vars, maintenance commands) is under **Upgrade**. Per-release file lists and deploy steps are in `PATCH_FILES.txt`, which only covers the current release.

## v3.8.10
- **Fixed:** GPU manufacturer was saved as the AIB board brand instead of the chip maker. `parse_amazon_gpu()` (the fallback path used for AIB cards) wrote whatever brand it found in the Amazon title — EVGA, MSI, etc. — straight into `manufacturer`, which lands on the shared `HardwareSpec` record and showed up as a stray peer of NVIDIA/AMD/Intel in `/stats` "By Manufacturer" (e.g. a card bought as "EVGA GTX 1660" getting filed under "MSI" from an unrelated earlier lookup). `manufacturer` is now always the chip maker, consistent with every other GPU source; the AIB brand is kept separately as `board_manufacturer`.
- **Fixed:** the Add Inventory manufacturer field auto-filled with the matched spec's chip maker ("NVIDIA") even when you'd searched for a specific board partner ("EVGA GTX 1660") and left the field blank — and it overwrote anything you'd already typed, every time. It now only fills a blank field, and prefers the AIB brand (from the scraped listing or your query) over the chip maker.
- **New:** `app/scrapers/gpu_brands.py` — single source of truth for GPU chip-vendor vs. AIB board-partner data, replacing two lists that had drifted apart between `scoring.py` and `normalization.py` (the latter alone knew about KFA2, Leadtek, Manli, MaxSun, Kuroutoshikou, and suffixes like Windforce, Vapor-X, Taichi). `scoring.py` and `normalization.py` now both import from it; `normalize_gpu_query()`'s own behavior is unchanged. `detect_gpu_chip_vendor()` also falls back to the model-number family (GTX/RTX → NVIDIA, RX → AMD) for titles that never say the brand word at all.
- `/api/lookup` responses now include `board_manufacturer` for GPU results — from the scraped listing when available, otherwise detected in the search query — on every return path (DB auto-accept, scraper auto-accept, merged auto-accept, and review-queue candidates).
- **New:** `app/maintenance/backfill_gpu_manufacturer.py` — fixes existing `HardwareSpec` rows saved with an AIB brand as `manufacturer` by the bug above. Repoints linked `Inventory` rows to an existing correctly-labeled spec and removes the duplicate where one exists (preserving the discarded board brand into that row's `custom_manufacturer` first, where it was blank); otherwise corrects the manufacturer in place. Anything it can't confidently resolve is listed, not guessed. Dry run by default; `--apply` to commit, same pattern as `backfill_sockets.py`.
- **Fixed:** GPU lookups with a memory size in the query ("msi gtx 1080 aero 8g", "rtx 3060 12gb") never reached the card's known TechPowerUp page: the size stayed in the link, so only "12 gb" with a space ever matched. The size is now taken out before matching and used to pick the memory version, and the TechPowerUp search runs without it ("gtx 1080 8g" matched no search result either).
- **Fixed:** the RTX 4060 Ti had one known-page entry (c3977) that is neither version. It now has the 8 GB page (c3890) and the 16 GB page (c4155); plain "rtx 4060 ti" goes to 8 GB, the same default as the 5060 Ti entry.
- **New:** memory-size check. A result whose memory size differs from the query's (in its name, or its parsed memory) is rejected instead of saved, so a 16GB card can't get the 8 GB page's specs. When the list has other sizes of a card but not the one asked for, the lookup goes to the search rather than guessing the plain entry.
- **New:** wrong-type check. A query that clearly names a GPU with the type set to CPU (or the reverse) stops before any paid call: "This looks like a GPU, but the type is set to CPU. Set the type to GPU (or Auto) and look it up again." It searched the wrong TechPowerUp database for 5 credits before.
- **New:** **Retry anyway** on the Add page when a saved "not found" answers a lookup; it reruns the lookup and skips the saved result (`force_refresh` on `/api/lookup`).
- **Fixed:** the v3.8.9 "The lookup couldn't finish (…)" message never reached the Add page, which showed the generic "No specs found" instead.
- Logs: the "Normalized GPU query" line prints once per lookup, not once per search listing checked, and a search where no listing passes the title check now logs the first three titles it checked.
- **Fixed:** TechPowerUp answered Scrape.Do's headless browser (`render=true`, 5 credits a call) with a bot-check page, so every GPU and CPU lookup through TechPowerUp failed. The plain fetch gets the real page, so all TechPowerUp fetches now go without `render=true`, at 1 credit a call. TechPowerUp's bot-check page is recognized and logged as one.
- **Fixed:** TechPowerUp renamed the heading that holds the card's name (`h1.gpudb-name`, was `h1.gpuname`), so even a real page parsed with no name and failed the final check. The parser reads the new heading and falls back to the page title.
- **Fixed:** when a known TechPowerUp page comes back without specs, the TechPowerUp search is skipped (it would fail the same way), which leaves Amazon the two calls it needs. A known page that turns out to be a different card is logged by name (`Known page for 'X' is Y (...); fix this entry`) and the search still runs.
- **Fixed:** the RTX 4060 known-page entry (`c3978`) opened the AMD Radeon Pro SSG. It's removed until the right ID is confirmed; RTX 4060 lookups go to the search.
- **Fixed:** GPU scoring counts a board maker as matching the chip maker (EVGA, Zotac, PNY, Palit, Gainward, Inno3D, Galax and Colorful build NVIDIA cards; Sapphire, XFX and PowerColor build AMD cards; MSI, ASUS and Gigabyte build both; ASRock builds AMD and Intel). "EVGA GTX 1650 Super" against the saved "NVIDIA GeForce GTX 1650 Super" scores 90 instead of 65, so the saved spec is used without a paid lookup.
- **Fixed:** running out of paid calls or credits threw away the database matches ("Paid lookup budget reached"). They're now shown for review with a note, and a call-limit stop says how to allow more: "Stopped after 3 paid calls (Normal) before finishing. To allow more, set Paid lookup depth to Thorough in Lookup Settings and look it up again."
- **Fixed:** dates showed in UTC, so anything added after 8 PM Eastern showed the next day. Stored timestamps (still UTC) are shown in the `TZ` time zone (new `localtime` template filter, `app/timefmt.py`), and a sale saved without a date gets today's local date instead of UTC's.
- Tests: `tests/test_gpu_lookup.py`, `tests/test_local_time.py`, two saved TechPowerUp pages in `tests/fixtures/`, `tests/test_gpu_brands.py` (new), and `tests/test_gpu_lookup.py` extended for `parse_amazon_gpu`'s manufacturer/board_manufacturer split. 235 tests.
- **Upgrade:** rebuild required, for the new `tzdata` package (`docker compose build app`). Add `- TZ=${TZ:-UTC}` under the app's `environment:` in `docker-compose.yml` and check that `TZ` in `.env` is your zone (e.g. `America/New_York`). No schema change, no migration. GPU searches saved as "not found" before this release can be rerun with Retry anyway. Run the new GPU-manufacturer backfill once after deploying: `docker exec techreadout-app python -m app.maintenance.backfill_gpu_manufacturer` (dry run), then `--apply` to fix any existing GPU specs mislabeled with an AIB brand. `/stats` GPU "By Manufacturer" still reads only the chip vendor — a "By Board Manufacturer" breakdown from `Inventory.custom_manufacturer` is a separate, later change.

## v3.8.9
- **Fixed:** fresh installs failed on every spec query. `migrations/init.sql` never had `ram_ecc`/`ram_module_type` (v3.5.4 added them through an upgrade script only, which a new volume never runs). New `tests/test_schema_sync.py` fails if a model column is missing from `init.sql`.
- **Fixed:** selling part of an inventory row (1 of 2, say) returned a 500. The split-off Sold row was built with `condition=` instead of `item_condition=`. Same fix in `POST /api/inventory`.
- **Fixed:** the Scrape.Do token could land in container logs. requests puts the full URL, `?token=` included, into connection-error and `raise_for_status()` messages. `scrapedo_get()` now re-raises network errors with the token masked and masks `response.url`; new `redact_token()` in `lookup.py`.
- **Fixed:** Check Balance never showed a balance and spent a request on example.com per click (it read headers Scrape.Do doesn't send). `/api/credits` now uses Scrape.Do's `/info` usage endpoint and shows remaining of the monthly limit, plus a warning if the subscription is inactive. Errors no longer return exception text.
- **Fixed:** Inventory Breakdown counted Sold and Dead parts as owned (Missing still counts, like the dashboard value). RAM is broken down by stick size; a 2x16GB kit used to show as two sticks under 32.
- **Fixed:** the build planner's available-parts lists matched sockets by substring, so an LGA 2011 plan listed LGA 2011-3 CPUs and X99 boards (the bug v3.8.5 fixed in `compatibility.py`). It now uses the same exact match, exposed as `compatibility.socket_match()`.
- **Fixed:** build-plan compatibility totals (RAM, VRAM, power) counted the whole inventory row instead of the quantity on the plan. The plan quantity is used now, capped at what the row holds.
- **Fixed:** the lookup review list inserted the query, candidate names, spec previews and unknown source labels as raw HTML. They now go through the page's existing `escapeHtml()`.
- Tests: restored the five files removed in July (scoring, normalization, validation, duplicates, TPU parser; 60 tests, unchanged). Route tests now run the real app on in-memory SQLite: `create_app()` takes an optional config dict and skips its MariaDB-only `CREATE TABLE` statements on other databases (no change in production). New: `tests/conftest.py`, `test_schema_sync.py`, `test_scrapedo.py`, `test_inventory_routes.py`, `test_stats_breakdown.py`, `test_build_planner.py`.
- Removed the stale repo-root `__init__.py` (an old copy of `app/__init__.py`; nothing imports it). `.gitignore` no longer re-includes `app/seeds/__pycache__` or ignores `PATCH_FILES.txt`.
- **Fixed:** the build plan page (`/planner/<id>`) returned a 500 for every plan, including empty ones. The template read `availability.ram.items` (and the same for GPU, CPU, motherboard, cooler, PSU), which Jinja resolves to the dict's `.items()` method rather than the `items` key; it now uses `['items']`.
- **Fixed:** lookups for RAM, Storage, PSU, Case, Cooler, Fan, NIC and Sound Card always came back "not found", and the Amazon fallbacks for GPUs, CPUs and niche motherboards never worked. They all found their product page through a Google search, and Google now answers scrapers with a JavaScript-only page (a CAPTCHA reference and no results). Amazon's own search replaces it (`amazon_search()`), and the ASUS official-site step, which could only find pages through Google, is now skipped (`search_motherboard_asus_official()` is a no-op); ASUS boards go straight to Newegg.
- **Fixed:** search results are picked by `pick_listing()` instead of taking the first link: a listing's title must pass `validate_result()` before its product page is fetched, and ties go to the closest title. The Newegg motherboard search used to open a sponsored-brand ad whenever Newegg had no match; it now reads Newegg's real result list.
- **New:** RAM lookups try Newegg first (`search_ram_newegg()`, `parse_newegg_ram()`: size, stick count, type, speed, CAS, ECC and module type from the product page's embedded data, 2 credits), then Amazon.
- **Fixed:** the Amazon RAM parser read specs from the whole page, including other products' ads, and saved e.g. DDR5 and CL17 for a DDR4 CL16 kit. It now uses only the product's spec table and title.
- **Fixed:** the RAM size check rejected a kit when the query gave its total ("32GB DDR4-3200" vs "32GB (2 x 16GB)"). A plain size now matches a kit's total or per-stick size; different kits and different sizes are still rejected.
- **Fixed:** the Newegg product parser's first read of the page's embedded data failed on current pages ("Extra data"), so it always fell back to the spec table. `newegg_initial_state()` decodes it reliably.
- **Fixed:** temporary failures were saved as "not found" for 30 days, silently blocking that search. A lookup that hits a network or Scrape.Do error, a bot-check page, a budget stop or a disabled Scrape.Do now returns `lookup_incomplete` and shows "The lookup couldn't finish (…). Nothing was saved, so you can try again." Real misses are kept for 7 days instead of 30, and using one logs `[Lookup] Skipped: cached miss from <date>`.
- Bot-check and JavaScript-only pages from Amazon and Google are recognized (`looks_blocked()`) and logged as such instead of "no product link found". Every Scrape.Do call logs its HTTP status and real credit cost.
- Tests: `tests/test_lookup_sources.py` and new RAM size cases in `test_ram_validation.py`, run against six pages saved from real Scrape.Do responses in `tests/fixtures/`. 172 tests.
- **Upgrade:** none required: no schema change, no migration, no rebuild. After deploying, click **Clear misses** on the Lookup Cache page once, since many saved "not found" results were caused by the Google block. Recommended: run `docker logs techreadout-app 2>&1 | grep -c "token="` and rotate the Scrape.Do token if it's above 0. Build plans whose components were added with the default quantity of 1 now count 1 unit, so check their quantities.

## v3.8.8
- **Fixed:** RAM lookups auto-accepted the wrong part. `Innodisk 8GB DDR4 2400 ECC DIMM` matched `A-Tech 8GB DDR4-2400 ... Non-ECC` at 92% and auto-accepted: `score_candidate()` gives unknown vendors full points and matched "ecc" inside "Non-ECC", `validate_result()` had no RAM rules (it even passed 16GB against 8GB), and the database auto-accept path in `api.py` never called `validate_result()` at all.
- New RAM identity rules in `app/scrapers/validation.py`: capacity (including kit vs single stick), DDR generation, speed and ECC are compared when both sides state them. `PC4-19200` and `DDR4-2400` are the same speed; `PC3-14900R` is 1866; "Non-ECC" is a negative, and RDIMM/LRDIMM/registered imply ECC. Unstated fields never count as a mismatch.
- `validate_result()` fails RAM results that conflict on any of those, or on vendor.
- `api.py` database candidates: a spec conflict drops the candidate (same pattern as the CPU guard); a vendor-only conflict caps confidence at 89 so it goes to review, since OEM rebrands (HP-labelled Samsung) are real. Uses the Manufacturer field when filled, otherwise the vendor named in the text.
- Add Inventory: **Clear specs** now restores the model, manufacturer and quantity you had before the lookup, and clears the manual spec fields and the looked-up name. Before, a rejected match left its model title and quantity behind.
- RAM manual-entry placeholders are now `e.g. 16` / `e.g. 3200` / `e.g. CL16` / `e.g. 2`; the old DDR5 examples (6000, CL30) looked like filled-in values on empty fields.
- `extract_ram_capacity()` moved to `validation.py` (still importable from `name_normalization`).
- New `tests/test_ram_validation.py`.
- **Upgrade:** none. Specs already saved from a wrong match stay as they are; check any RAM inventory added through lookup recently.

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
