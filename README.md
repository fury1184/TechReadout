# TechReadout

Hardware inventory and spec tracking for homelabs and IT environments. See [CHANGELOG.md](CHANGELOG.md) for the current version and release history.

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
- On-demand fallback chain: manufacturer official site (ASUS) → Newegg → Amazon (scoped to niche/clone motherboard brands) → Scrape.Do/TechPowerUp (paid) → Open WebUI (optional, self-hosted LLM) → AI Import (manual)
- Optional manufacturer field on lookup as a non-blocking nudge when a query alone can't resolve
- Confidence-scored matching with human review modal for matches below 90%
- Closing the review modal without picking a candidate now falls back to manual entry + AI Import on the main form instead of leaving no path forward
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

### Inventory Breakdown
- Dedicated `/stats` page showing your owned inventory grouped by the fields that matter most per component type: CPU by socket/manufacturer, Motherboard by socket/chipset, RAM by type/capacity, GPU by manufacturer/VRAM, Storage by interface/capacity, PSU by wattage, Case by form factor
- Counts reflect physical unit quantity (a 2×8GB RAM kit counts as 2), not row count
- Known messy spec variants (e.g. `LGA 2011-3` vs `LGA 2011-v3`) are normalized before grouping so they don't split into separate rows
- Custom/manual inventory entries with no matched spec are rolled into an "Unknown / custom entry" row so totals always match your actual inventory count

### Review Queue
- Scrape matches below the 90% confidence threshold are automatically saved to the review queue
- Review, accept, or skip candidates from a dedicated queue page
- Badge in sidebar shows live pending count
- Save-time dedup now compares normalized query text (lowercase, collapsed whitespace) so a retry like "gt 730" after "MSI GT 730" doesn't spawn a second queue entry for the same item

### Lookup Cache Management
- Browse all cached lookups with hit/miss status and age
- Bulk clear misses or entries older than 30 days
- Re-lookup individual inventory items to force a fresh scrape

---

## Tech Stack

- **Backend:** Python 3.12, Flask, SQLAlchemy, Flask-Migrate
- **Frontend:** Bootstrap 5.3, Bootstrap Icons, Chart.js (dashboard only)
- **Scraping:** BeautifulSoup4, Requests, Scrape.Do (paid fallback, used for general lookups and as part of niche-source fallbacks)
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

Open WebUI is an automatic lookup step that asks a self-hosted LLM (via [Open WebUI](https://github.com/open-webui/open-webui)'s OpenAI-compatible endpoint) to guess specs when the earlier chain steps don't find a match. It runs before manual AI Import, and every result it returns is routed to the Pending Review queue for a human check — it's never auto-accepted, no matter how confident the match looks.

1. Stand up Open WebUI (with Ollama or another backend) on any machine reachable from your Docker host.
2. In Open WebUI, go to **Settings → Account → API Keys** and generate a token.
3. Add it to your `.env` file:
   ```
   OPENWEBUI_API_TOKEN=your_token_here
   ```
4. In TechReadout, go to **Lookup Settings**, enable **Open WebUI**, and enter your instance's chat completions URL (e.g. `http://your-server-ip:3000/api/chat/completions`) and the model name to use.

---

## Changelog

Release history, upgrade notes, and migrations are in [CHANGELOG.md](CHANGELOG.md).

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
│   ├── backup.py       — Backup, restore, AI import
│   └── stats.py        — Inventory Breakdown page
├── scrapers/
│   └── lookup.py       — BS4 direct + Scrape.Do fallback chain, plus dedicated motherboard chain (ASUS/Newegg/Amazon)
├── seeds/
│   ├── seed_db.py      — Seed importer (runs on container startup)
│   └── *.json          — Curated hardware spec library (10 component types)
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── stats.html
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
