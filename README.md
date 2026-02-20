# TechReadout

**Version 1.4.8**

A containerized hardware inventory management system with on-demand spec lookup. Track your PC components, manage builds, and maintain inventory across multiple machines.

## Features

- **On-Demand Spec Lookup**: Type a CPU or GPU model (e.g., "RTX 3080", "i7-9700K") and automatically fetch specs
- **Inventory Management**: Track components with quantities, conditions, purchase prices, and locations
- **Inventory Lifecycle**: Assign parts to hosts, unassign back to inventory, mark as sold with profit tracking
- **Host Management**: Track built systems and their assigned components
- **Build Planner**: Plan new builds, check part availability and socket compatibility
- **Backup & Restore**: Timestamped backups with NAS support

## Quick Start

```bash
# 1. Extract and enter directory
unzip techreadout.zip
cd techreadout

# 2. Set up your Scrape.Do API token (required for spec lookup)
echo "SCRAPEDO_TOKEN=your_token_here" > .env

# 3. Start the containers
docker compose up -d

# 4. Access the web UI
open http://localhost:5000
```

## Scrape.Do API Key Setup

The spec lookup feature requires a [Scrape.Do](https://scrape.do) API token because hardware spec sites block automated requests.

### Why is this needed?

- TechPowerUp, Intel ARK, and Amazon block direct scraping requests
- Scrape.Do acts as a proxy that bypasses these blocks
- **Free tier**: 1000 credits (enough for ~250-500 part lookups)
- **Specs are cached**: Once looked up, a part is stored forever in your local database

### Getting your API key

1. Go to [scrape.do](https://scrape.do) and create a free account
2. Copy your API token from the dashboard
3. Create a `.env` file in the techreadout directory:
   ```bash
   echo "SCRAPEDO_TOKEN=your_token_here" > .env
   ```
4. Restart the app: `docker compose restart app`

### Supported Lookups

| Component | Source | Credits |
|-----------|--------|---------|
| Intel CPU | Intel ARK (primary), TechPowerUp (fallback) | ~2-4 |
| AMD CPU | TechPowerUp | ~2 |
| GPU | TechPowerUp (primary), Manufacturer sites (fallback) | ~2-4 |
| Motherboard | Amazon (primary), Manufacturer sites (fallback) | ~2-4 |
| PSU | Amazon | ~2 |
| RAM | Amazon | ~2 |
| Storage | Amazon | ~2 |
| Cooler | Amazon | ~2 |
| Case | Amazon | ~2 |
| Fan | Amazon | ~2 |

**Supported GPU Manufacturers:** XFX, ASUS, MSI, Gigabyte, EVGA, Sapphire, PowerColor, Zotac, PNY

**Supported Motherboard Manufacturers:** ASUS, MSI, Gigabyte, ASRock, EVGA

### Without an API key

The app works fine without Scrape.Do — you just won't get automatic spec lookup. You can still:
- Add items manually with custom names
- Enter specs by hand
- Use all inventory management features

## Component Types

TechReadout supports 12 component categories:

| Type | Tracked Specs | Auto-Lookup |
|------|---------------|-------------|
| CPU | Cores, Threads, Base/Boost Clock, TDP, Socket | ✅ Intel ARK, TechPowerUp |
| GPU | VRAM, Memory Type, TDP | ✅ TechPowerUp |
| RAM | Capacity, Speed, Type, Latency | ❌ Manual |
| Motherboard | Socket, Chipset, Form Factor, Memory Slots, Memory Type, PCIe Slots | ❌ Manual |
| Storage | Capacity, Interface, Type (SSD/HDD/NVMe) | ❌ Manual |
| PSU | Wattage, Efficiency Rating, Modular | ❌ Manual |
| Case | Form Factor, Type | ❌ Manual |
| Cooler | Type, Socket Support, TDP Rating | ❌ Manual |
| Fan | Size, RPM, Airflow, Noise | ❌ Manual |
| NIC | Speed, Interface | ❌ Manual |
| Sound Card | Interface, Channels | ❌ Manual |
| Other | Custom fields | ❌ Manual |

## Architecture

```
┌─────────────────────────────────────┐
│  Web GUI (Flask + Bootstrap 5)      │
│  └─ Responsive sidebar navigation   │
└─────────────────────────────────────┘
           ↓ HTTP
┌─────────────────────────────────────┐
│  Flask Backend (Python 3.12)        │
│  ├─ REST API                        │
│  ├─ BeautifulSoup + Scrape.Do       │
│  └─ SQLAlchemy ORM                  │
└─────────────────────────────────────┘
           ↓ SQL
┌─────────────────────────────────────┐
│  MariaDB 11                         │
│  └─ Inventory + Specs + Hosts       │
└─────────────────────────────────────┘
```

## Inventory Lifecycle

### Adding Items
1. Go to **Inventory** → **Add Item**
2. Type a model name (e.g., "RTX 3080")
3. Click **Lookup** to fetch specs (or skip for manual entry)
4. Fill in purchase details and save

### Assigning to Hosts
- From inventory list, click the **Assign** button on available items
- Select a host and quantity
- Item status changes from "Available" to "In Use"

### Unassigning / Part Out
- **Unassign**: Return individual components to available inventory
- **Part Out Host**: Return all components from a host at once
- Smart merge: Items with same specs and purchase price are combined

### Selling Items
- Mark items as sold with sale price, date, and buyer
- Automatic profit/loss calculation (when purchase price is known)
- Sold items are hidden by default but viewable in history

## Services

| Service | Port | Description |
|---------|------|-------------|
| app | 5000 | Flask web application |
| db | 3306 | MariaDB database |

## Configuration

Environment variables (set in `.env` or `docker-compose.yml`):

| Variable | Required | Description |
|----------|----------|-------------|
| `SCRAPEDO_TOKEN` | For lookup | Scrape.Do API token for spec lookup |
| `DATABASE_URL` | No | Database connection (has default) |
| `SECRET_KEY` | No | Flask secret key (change in production) |

### Changing the Port

Edit `docker-compose.yml`:
```yaml
ports:
  - "8080:5000"  # Change 8080 to your desired port
```

### NAS Backup Setup

```yaml
services:
  app:
    environment:
      - NAS_BACKUP_PATH=/mnt/nas/backups
    volumes:
      - /path/to/nas/share:/mnt/nas
```

## API Endpoints

```
GET  /api/specs              # List hardware specs
GET  /api/specs/<id>         # Get spec details
GET  /api/inventory          # List inventory
POST /api/inventory          # Add inventory item
GET  /api/hosts              # List hosts
GET  /api/component-types    # List component types
GET  /api/stats              # Dashboard statistics
POST /api/lookup             # Lookup specs for a model name
```

## Development

```bash
# Start with logs visible
docker compose up

# Rebuild after code changes
docker compose build --no-cache app
docker compose up -d

# View logs
docker compose logs -f app

# Access database
docker compose exec db mysql -u techreadout -ptechreadout techreadout
```

## Data Persistence

- Database stored in Docker volume `mariadb_data`
- Survives container restarts and rebuilds
- To fully reset: `docker compose down -v` (warning: deletes all data)

## Backup Commands

```bash
# Create SQL backup
docker compose exec db mysqldump -u techreadout -ptechreadout techreadout > backup.sql

# Restore from SQL backup
docker compose exec -T db mysql -u techreadout -ptechreadout techreadout < backup.sql
```

Or use the built-in Backup page in the web UI for JSON backups with timestamps.

## License

MIT

---

## Author

Created by [fury1184](https://github.com/fury1184)

If you find this project useful, consider buying me a coffee:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Me-ff5e5b?logo=ko-fi&logoColor=white)](https://ko-fi.com/fury1184)

## Changelog

### v1.4.8
- Added model name cleanup for Amazon results (removes verbose product titles)
- CPU names cleaned: "Intel Xeon E5-2687W V4 Processor 12-Core..." → "Intel Xeon E5-2687W v4"
- GPU names cleaned: "EVGA GeForce GTX 1660 Ti SC Ultra Gaming, 06G-P4..." → "EVGA GeForce GTX 1660 Ti SC Ultra"
- Added editable model name field after lookup - edit if cleanup isn't perfect
- Model name field syncs to form so changes are saved

### v1.4.7
- **CRITICAL FIX**: Removed duplicate broken code in Intel ARK function causing syntax errors
- Added Amazon as last-resort fallback for CPUs (works for Xeon, server CPUs)
- Added TechPowerUp native search as additional fallback
- Uses TPU's AJAX search endpoint and filter page
- Better regex patterns to find spec page links
- Improved debug logging throughout lookup flow

### v1.4.6
- Fixed Intel ARK link detection for Xeon and server CPUs
- Improved TechPowerUp URL generation (adds intel-/amd- prefix)
- Added "+specifications" to Intel ARK Google search for better results
- Better handling of Google redirect URLs

### v1.4.5
- Reordered GPU lookup: manufacturer sites tried FIRST (has actual AIB specs)
- Added Amazon as fallback for GPUs (works for discontinued brands like EVGA)
- GPU lookup order: Manufacturer → TechPowerUp (normalized) → Amazon
- Note: EVGA exited GPU business in 2022, so their site may not have products

### v1.4.4
- Fixed GPU lookups failing for AIB cards (EVGA, ASUS, MSI, etc.)
- GPU queries now normalized to reference card names for TechPowerUp
- Strips AIB partner names and model suffixes (SC Ultra, FTW3, Gaming X, etc.)
- E.g., "EVGA GTX 1660 Ti SC Ultra" → searches for "GTX 1660 Ti"

### v1.4.3
- Added inventory detail view showing all recorded spec data
- Click item name or eye icon to view full details
- Shows source URL and raw scraped data (collapsible)
- Displays component-specific specs in organized table

### v1.4.2
- Fixed duplicate lookup on form submit wasting API credits
- Lookup now only runs once per model name
- Manual entries are preserved when lookup fails or is dismissed
- Reset lookup state when model name changes

### v1.4.1
- Made version badge more visible (bottom-right corner)

### v1.4.0
- Added spec lookup for all component types (RAM, Storage, Coolers, Cases, Fans)
- Added GPU manufacturer site lookups (XFX, Sapphire, PowerColor, ASUS, MSI, etc.)
- Improved AIO cooler detection with radiator size (360mm, 280mm, 240mm)
- Smarter motherboard chipset detection (checks model name first, uses socket context)
- Smarter form factor detection (E-ATX, Micro-ATX, Mini-ITX)
- Added "Not right? Enter manually" button to dismiss incorrect lookup results
- Added manual spec entry forms for Cooler, Case, and Fan components
- Added version indicator in UI

### v1.3.0
- Added motherboard spec lookup (Amazon + manufacturer sites)
- Added PSU spec lookup (Amazon)
- Added Intel ARK priority for Intel CPUs
- Added result validation to prevent mismatches
- Added credits exhausted detection

### v1.2.0
- Migrated to Scrape.Do API for reliable scraping
- Removed broken Playwright scrapers
- Added comprehensive README with setup instructions

### v1.1.0
- Initial Docker-based release
- CPU/GPU spec lookup via TechPowerUp
- Inventory management with lifecycle tracking
- Host management and build planner
- Backup/restore with NAS support
