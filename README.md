# TechReadout

**v3.5.1** — Hardware inventory and spec tracking for homelabs and IT environments.

TechReadout is a self-hosted Flask web application for tracking hardware inventory, managing host builds, looking up component specs from multiple sources, and analyzing your hardware library over time.

---

## Features

### Inventory Management
- Track components by type, condition, status, quantity, and purchase price (per unit)
- Assign components to hosts; unassign or part out entire builds
- Sell tracking with profit/loss calculation
- Bulk add with "Add Another" flow for loading batches of the same component type
- Split qty>1 rows into individual units when each item needs separate tracking
- Export inventory to CSV (respects active filters — export what you're looking at)

### Spec Lookup
- Seed database: ships with a curated library of common hardware (CPUs, GPUs, RAM, motherboards, storage, PSUs, coolers, cases, fans, NICs) — most lookups resolve here instantly and for free
- On-demand fallback chain: Scrape.Do (paid) → Open WebUI (optional, self-hosted LLM) → AI Import (manual)
- Confidence-scored matching with human review modal for matches below 90%
- Open WebUI results are never auto-accepted — always routed to the Pending Review queue regardless of score
- AI Import fallback via `/backup/import-specs` for anything the chain misses
- Lookup cache with 30-day TTL; manageable via the Lookup Cache page

### Host Management
- Track hosts with hostname, IP, MAC, OS, purpose, and status
- View estimated build cost per host (sum of `purchase_price × quantity` for assigned components)
- Side-by-side host comparison — select any two from the Hosts list
- Part-out a host to return all components to available inventory in one click
- Edit host details inline including IP address

### Dashboard
- Total inventory value across all non-sold items with a price set
- Component type distribution chart (Chart.js)
- Recently added items
- Pending review queue count with direct link

### Review Queue
- Scrape matches below the 90% confidence threshold are automatically saved to the review queue
- Review, accept, or skip candidates from a dedicated queue page
- Badge in sidebar shows live pending count

### Lookup Cache Management
- Browse all cached lookups with hit/miss status and age
- Bulk clear misses or entries older than 30 days
- Re-lookup individual inventory items to force a fresh scrape

---

## Tech Stack

- **Backend:** Python 3.12, Flask, SQLAlchemy, Flask-Migrate
- **Frontend:** Bootstrap 5.3, Bootstrap Icons, Chart.js (dashboard only)
- **Scraping:** BeautifulSoup4, Requests, Scrape.Do (optional paid fallback)
- **Database:** MariaDB 11
- **Deployment:** Docker + Docker Compose

---

## Quick Start

```bash
git clone https://github.com/fury1184/techreadout.git
cd techreadout
cp .env.example .env        # fill in SCRAPEDO_TOKEN if you have one
docker compose up -d
```

The seed importer runs automatically on first boot and populates ~258 hardware specs across 10 component types. Then open `http://localhost:5000`.

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Flask secret key |
| `DATABASE_URL` | No | Defaults to MariaDB on the bundled `db` service |
| `SCRAPEDO_TOKEN` | No | Scrape.Do API token for paid last-resort fallback |
| `EBAY_APP_ID` | No | eBay production Client ID for price estimates |
| `EBAY_APP_SECRET` | No | eBay production Client Secret for price estimates |
| `OPENWEBUI_API_TOKEN` | No | Bearer token for your Open WebUI instance, for optional automatic LLM-based lookup fallback |
| `SEED_ON_STARTUP` | No | Set to `false` to skip seed import on container start |

---

## API Key Setup

### Scrape.Do (optional)

Scrape.Do is a paid web scraping proxy used as a last-resort fallback when a component isn't found in the seed database. Without it, TechReadout will still work — it just won't be able to fetch specs for items outside the seed library.

1. Sign up at [scrape.do](https://scrape.do) — a free tier is available with a limited monthly credit allowance.
2. After signing in, your API token is shown on the dashboard.
3. Add it to your `.env` file:
   ```
   SCRAPEDO_TOKEN=your_token_here
   ```

### eBay Browse API (optional)

The eBay API is used to fetch median used-market price estimates for inventory items. It requires a free eBay developer account.

1. Sign up at [developer.ebay.com](https://developer.ebay.com).
2. Go to **My Account → Application Keysets** and create a new keyset.
3. Choose **Production** (not Sandbox) to get real pricing data.
4. Copy the **App ID (Client ID)** and **Cert ID (Client Secret)** — these are the two values TechReadout needs.
5. Add them to your `.env` file:
   ```
   EBAY_APP_ID=your_app_id_here
   EBAY_APP_SECRET=your_cert_id_here
   ```
6. In TechReadout, go to **Lookup Settings** and enable **eBay pricing**.

> **Note:** TechReadout uses the eBay Browse API with Client Credentials OAuth (app-level access only — no user login or consent flow required). The free Production tier is sufficient.

### Open WebUI (optional)

Open WebUI is an automatic lookup step that asks a self-hosted LLM (via [Open WebUI](https://github.com/open-webui/open-webui)'s OpenAI-compatible endpoint) to guess specs when Scrape.Do doesn't find a match. It runs before manual AI Import, and every result it returns is routed to the Pending Review queue for a human check — it's never auto-accepted, no matter how confident the match looks.

1. Stand up Open WebUI (with Ollama or another backend) on any machine reachable from your Docker host.
2. In Open WebUI, go to **Settings → Account → API Keys** and generate a token.
3. Add it to your `.env` file:
   ```
   OPENWEBUI_API_TOKEN=your_token_here
   ```
4. In TechReadout, go to **Lookup Settings**, enable **Open WebUI**, and enter your instance's chat completions URL (e.g. `http://your-server-ip:3000/api/chat/completions`) and the model name to use.

---

## Changelog

### v3.5.1
- **RAM kit inventory quantity** — RAM specs still describe the kit, but inventory quantity now counts physical modules/sticks. Example: 16GB (2x8GB) defaults to quantity 2.
- **Manual RAM entry** — added a Modules / Sticks field and per-stick capacity display where possible.
- **Consistent RAM quantity rules** — add form, API-created inventory, and import-created inventory all apply the same kit-to-stick rule.

### v3.5.0
- **Improved spec lookup** — richer scraper confidence scoring, source metadata, stricter AI/null handling, and better motherboard/RAM validation.
- **Richer item details** — inventory, specs, and review queue now show reusable summaries and component-specific detail rows.
- **Code cleanup** — scraper normalization, scoring, validation, and hardware serialization helpers were split into shared modules.
- **Open WebUI automatic lookup** — optional lookup step for missing specs. Results are routed to review and capped below auto-accept.

### v3.3.0
- **Dark mode** — theme toggle in the sidebar footer. Defaults to your OS preference on first visit; choice is saved per browser in `localStorage`. Built on Bootstrap 5.3's native `data-bs-theme` support.
- **Safe spec deletion with cascade** — deleting a spec now opens a confirmation dialog listing any linked inventory items, with three options: delete the spec and items together, unlink the items (kept as custom entries with their name preserved) and delete only the spec, or cancel.
- **Bulk delete cascade** — bulk spec deletion now deletes linked inventory items by default (checkbox in the bulk delete dialog; uncheck to skip in-use specs instead).
- **Excel export** — new "Export as Excel (.xlsx)" button on the Backup page generates a workbook with Summary, Inventory, Hosts, and Hardware Specs sheets (requires `openpyxl`, now in requirements).
- **Fixed:** deleting a spec no longer fails with a foreign key `IntegrityError` when `lookup_cache` or pending review entries still reference it — references are cleaned up automatically (single and bulk delete).
- **Fixed:** deleting an inventory item referenced by a Build Plan no longer fails — the plan slot is kept but marked unfulfilled.

### v3.2.0
- **eBay price prompt on add form** — when purchase price is left blank, the add form now intercepts submit, queries the eBay Browse API for a median used-market price, and prompts you to accept or skip before saving. Requires eBay pricing to be enabled in Lookup Settings and `EBAY_APP_ID`/`EBAY_APP_SECRET` set in the environment.
- New `GET /inventory/ebay-price-preview` endpoint powers the add-form prompt; reuses the `PriceCache` 24-hour TTL shared with the detail-page estimate button.

### v3.1.0
- **eBay price estimates** — per-item "Get eBay Estimate" button on inventory detail pages fetches a median used-market price via the eBay Browse API (Client Credentials OAuth, no user login required). Results are cached for 24 hours in a new `price_cache` table. Toggle on/off in Lookup Settings; requires `EBAY_APP_ID` and `EBAY_APP_SECRET` environment variables.
- **`price_is_estimate` flag** — inventory items track whether their purchase price was set from an eBay estimate (shown with a ✱ badge).
- **`price_cache` table** — new DB table added in `v3.1.0_ebay_price.sql` migration.

### v3.0.0
- **Seed database system** — `app/seeds/seed_db.py` imports curated hardware specs from `seeds/*.json` (CPU, GPU, RAM, motherboard, storage, PSU, cooler, case, fan, NIC) into the `hardware_specs` table on container startup
- **Seed version tracking** — current seed version stored in `app_settings`; re-imports skipped unless `--force` is passed
- **Pre-populated spec cache** — fresh installs ship with a baseline spec library (~258 entries), reducing first-run scrape load
- **Scraper chain trimmed** — FlareSolverr, Playwright (TechPowerUp + Amazon + Intel ARK), AMD Official, and manufacturer-site scrapers all retired. Anti-bot measures had made every free path unreliable, and the seed database now covers the bulk of lookups. New chain: seed DB → BeautifulSoup direct on TechPowerUp → Scrape.Do (paid) → AI Import.
- **Container slimmed** — Playwright and Chromium browser dropped from the image; FlareSolverr sidecar removed from `docker-compose.yml`. Build is faster and the runtime image is much smaller.
- **`lookup.py` reduced** from ~5,000 lines to ~2,400 lines

### v2.1.0
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

### v2.0.0
- Human review modal for scrape matches below 90% confidence
- Confidence scoring across all scrape sources
- AI Import fallback integration
- Version tracking in UI

### v1.x
- Initial inventory, spec lookup, host management, build planner, backup/restore

---

## Project Structure

```
app/
├── models.py           — SQLAlchemy models
├── routes/
│   ├── main.py         — Inventory, hosts, dashboard, cache, review queue
│   ├── api.py          — Lookup API endpoints
│   ├── scraper.py      — Spec lookup info & settings
│   ├── planner.py      — Build planner
│   └── backup.py       — Backup, restore, AI import
├── scrapers/
│   └── lookup.py       — BS4 direct + Scrape.Do fallback chain
├── seeds/
│   ├── seed_db.py      — Seed importer (runs on container startup)
│   └── *.json          — Curated hardware spec library (10 component types)
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── inventory/
    ├── hosts/
    ├── specs/
    ├── cache/
    ├── review/
    ├── scraper/
    └── backup/
```

---

## License

MIT
