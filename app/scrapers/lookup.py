"""
TechReadout — Hardware Lookup (web-scraper step)

The old free scrape chain (FlareSolverr, Playwright TPU, Playwright Amazon,
manufacturer sites) was retired in v3.0 because Cloudflare and Amazon anti-bot
measures had made every free path unreliable. First-party/vendor and TechPowerUp
lookups use Scrape.Do (paid); CPU-Monkey is attempted directly between the vendor
source and TechPowerUp. Open WebUI (optional self-hosted LLM) remains the last
automatic step before manual AI Import.

This module is only the scraper fallback. The DB / seed-database lookup and the
lookup cache run first, in the caller (app/routes/api.py); lookup_hardware() is
invoked only when those miss. The concrete per-component-type order is documented
on lookup_hardware() below and is, in summary:

    CPU (Intel): Intel ARK via Scrape.Do → CPU-Monkey → TechPowerUp via Scrape.Do → Open WebUI
    CPU (AMD):   AMD Official via Scrape.Do → CPU-Monkey → TechPowerUp via Scrape.Do → Open WebUI
    GPU:         TechPowerUp via Scrape.Do → Amazon via Scrape.Do → Open WebUI
    Other:       Amazon via Scrape.Do → Open WebUI

Open WebUI results are never auto-accepted regardless of score (api.py caps their
confidence below the auto-accept threshold). Anything the whole chain misses
falls through to manual AI Import (in routes/backup.py).

Public API (preserved for app/routes/api.py compatibility):
    lookup_hardware(query, component_type='auto', lite_mode=False,
                    use_intel_ark=False, use_amd_official=False) -> Optional[Dict]
    score_candidate(query, candidate_name, candidate_manufacturer,
                    component_type='GPU') -> int
"""

import os
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional, Dict

from bs4 import BeautifulSoup
import requests

# Scrape.Do API token from environment
SCRAPEDO_TOKEN = os.environ.get('SCRAPEDO_TOKEN', '')



# =============================================================================
# Scraper Helper Modules
# =============================================================================

from app.scrapers.normalization import (
    clean_cpu_model_name,
    clean_gpu_model_name,
    normalize_gpu_query,
)
from app.scrapers.scoring import enrich_scrape_result, score_candidate
from app.scrapers.validation import (
    acceptable_scrape_hit as _acceptable_scrape_hit,
    coerce_unknowns_to_none,
    has_minimum_specs,
    missing_required_fields,
    present_spec_fields as _present_spec_fields,
    validate_result,
    validation_status,
)


# =============================================================================
# Scrape.Do Budget Tracking
# =============================================================================

class ScrapeDoBudgetExceeded(Exception):
    """Raised when the current lookup exceeded the configured Scrape.Do budget."""


_LOOKUP_BUDGET = ContextVar('lookup_budget', default=None)


def _load_scrapedo_budget_settings():
    depth = 'normal'
    try:
        from app.models import AppSetting
        depth = (AppSetting.get('scrapedo_lookup_depth', 'normal') or 'normal').strip().lower()
    except Exception:
        depth = 'normal'

    profiles = {
        'conservative': {'depth': 'conservative', 'sequence_limit': 1, 'call_limit': 2},
        'normal': {'depth': 'normal', 'sequence_limit': 1, 'call_limit': 3},
        'thorough': {'depth': 'thorough', 'sequence_limit': 2, 'call_limit': 5},
    }
    return profiles.get(depth, profiles['normal']).copy()


def _begin_lookup_budget():
    budget = _load_scrapedo_budget_settings()
    budget.update({'sequence_count': 0, 'call_count': 0, 'active_sequence': None})
    _LOOKUP_BUDGET.set(budget)
    print(f"[Lookup Budget] Depth={budget['depth']} sequences={budget['sequence_limit']} calls={budget['call_limit']}")
    return budget


def _end_lookup_budget():
    _LOOKUP_BUDGET.set(None)


def _get_lookup_budget():
    budget = _LOOKUP_BUDGET.get()
    return budget if isinstance(budget, dict) else None


def _scrapedo_calls_remaining():
    """Return remaining paid calls for this lookup, or None outside a budget."""
    budget = _get_lookup_budget()
    if not budget:
        return None
    return max(0, int(budget.get('call_limit', 0)) - int(budget.get('call_count', 0)))


def start_scrapedo_sequence(name: str) -> bool:
    budget = _get_lookup_budget()
    if not budget:
        return True
    if budget.get('active_sequence') == name:
        return True
    if budget['sequence_count'] >= budget['sequence_limit']:
        print(f"[Lookup Budget] Sequence limit reached ({budget['sequence_limit']}); skipping {name}")
        return False
    budget['sequence_count'] += 1
    budget['active_sequence'] = name
    print(f"[Lookup Budget] Starting paid sequence {budget['sequence_count']}/{budget['sequence_limit']}: {name}")
    return True


def end_scrapedo_sequence(name: str = None):
    budget = _get_lookup_budget()
    if not budget:
        return
    if name is None or budget.get('active_sequence') == name:
        budget['active_sequence'] = None


def scrapedo_get(api_url: str, timeout: int = 60):
    budget = _get_lookup_budget()
    if budget:
        if budget['call_count'] >= budget['call_limit']:
            print(f"[Lookup Budget] Call limit reached ({budget['call_limit']}); blocking additional Scrape.Do requests")
            raise ScrapeDoBudgetExceeded()
        budget['call_count'] += 1
        print(f"[Lookup Budget] Scrape.Do call {budget['call_count']}/{budget['call_limit']}")
    return requests.get(api_url, timeout=timeout)


def scrapedo_fallback_enabled() -> bool:
    """Return True when Scrape.Do fallback should be used."""
    token = os.environ.get('SCRAPEDO_TOKEN', '').strip()
    if not token:
        return False
    try:
        from app.models import AppSetting
        return AppSetting.get_bool('enable_scrapedo_fallback', True)
    except Exception:
        return True


# =============================================================================
# Component-Type Detection & URL Helpers
# =============================================================================

def detect_component_type(query: str) -> str:
    """Detect if query is for GPU, CPU, Motherboard, or PSU based on keywords."""
    query_lower = query.lower()
    
    # GPU indicators
    gpu_keywords = ['rtx', 'gtx', 'radeon', 'rx ', 'rx5', 'rx6', 'rx7', 'geforce', 'quadro', 'titan', 'arc ', 'vega']
    if any(kw in query_lower for kw in gpu_keywords):
        return 'GPU'
    
    # PSU indicators (check before motherboard since some overlap)
    psu_keywords = ['psu', 'power supply', '80 plus', '80+', 'platinum', 'gold', 'bronze', 'titanium',
                    'modular', 'semi-modular', 'fully modular', 'atx power', 'sfx power']
    psu_wattage = re.search(r'\b(\d{3,4})\s*w\b', query_lower)  # 550w, 750w, 1000w etc
    psu_brands = ['corsair rm', 'corsair hx', 'corsair sf', 'evga supernova', 'seasonic', 'be quiet',
                  'cooler master', 'thermaltake', 'nzxt c', 'fractal design ion', 'superflower',
                  'enermax', 'silverstone', 'phanteks', 'msi mpg']
    
    if any(kw in query_lower for kw in psu_keywords):
        return 'PSU'
    if psu_wattage and any(brand in query_lower for brand in psu_brands):
        return 'PSU'
    if psu_wattage and ('rm' in query_lower or 'hx' in query_lower or 'sf' in query_lower):
        return 'PSU'
    
    # Motherboard indicators (check before CPU since some overlap with chipset names)
    mobo_chipsets = ['b550', 'x570', 'b650', 'x670', 'b450', 'x470', 'a520', 'a620',  # AMD
                     'z690', 'z790', 'b660', 'b760', 'h670', 'h770', 'z590', 'z490',  # Intel
                     'h610', 'h510', 'b560', 'x299', 'x399', 'trx40', 'wrx80',
                     'x79', 'x99', 'c612', 'c602']
    mobo_brands = ['asus', 'msi', 'gigabyte', 'asrock', 'evga', 'biostar', 'supermicro', 'machinist', 'huananzhi', 'jingyue']
    mobo_keywords = ['rog ', 'strix', 'tuf ', 'prime', 'proart',  # ASUS
                     'mag ', 'mpg ', 'meg ', 'tomahawk', 'mortar', 'carbon',  # MSI
                     'aorus', 'gaming x', 'ultra durable',  # Gigabyte
                     'phantom', 'steel legend', 'taichi', 'pro4',  # ASRock
                     'motherboard', 'mainboard', 'lga2011', 'lga2011-3', 'lga 2011', 'lga 2011-3']
    
    # Check for chipset + brand combo or motherboard keywords
    has_chipset = any(chip in query_lower for chip in mobo_chipsets)
    has_brand = any(brand in query_lower for brand in mobo_brands)
    has_mobo_keyword = any(kw in query_lower for kw in mobo_keywords)
    
    if has_mobo_keyword or (has_chipset and has_brand) or (has_chipset and '-' in query):
        return 'Motherboard'
    
    # CPU indicators
    cpu_keywords = ['i3-', 'i5-', 'i7-', 'i9-', 'ryzen', 'xeon', 'epyc', 'threadripper',
                    'pentium', 'celeron', 'athlon', 'phenom', 'opteron', 'sempron', 'fx-']
    if any(kw in query_lower for kw in cpu_keywords):
        return 'CPU'
    # Common bare AMD model searches such as 5800X / 5700X3D / 7945HX.
    if re.search(r'\b\d{4}(?:x3d|xt|x|ge|g|hx|hs|u)\b', query_lower):
        return 'CPU'
    
    # Default to GPU (more common lookup)
    return 'GPU'


def get_search_url(query: str, component_type: str) -> str:
    """Get search URL for the discovery step.

    CPU/GPU use TechPowerUp's own native search endpoint (?q=...) rather than
    scraping Google. TPU's documented search formatters are:
        https://www.techpowerup.com/cpu-specs/?q={query}
        https://www.techpowerup.com/gpu-specs/?q={query}
    This removes the fragile Google-results dependency that broke when Google
    changed its result markup. Motherboards still use manufacturer-site Google
    search (no TPU coverage).
    """
    search_query = requests.utils.quote(query)
    if component_type == 'GPU':
        # TechPowerUp native GPU specs search
        return f"https://www.techpowerup.com/gpu-specs/?q={search_query}"
    elif component_type == 'Motherboard':
        # Google search for motherboard specs (manufacturer sites)
        manufacturer = detect_motherboard_manufacturer(query)
        if manufacturer:
            site = get_manufacturer_site(manufacturer)
            return f"https://www.google.com/search?q=site:{site}+{search_query}+specifications"
        # Generic search if manufacturer unknown
        return f"https://www.google.com/search?q={search_query}+motherboard+specifications"
    else:
        # TechPowerUp native CPU specs search
        return f"https://www.techpowerup.com/cpu-specs/?q={search_query}"


def detect_motherboard_manufacturer(query: str) -> Optional[str]:
    """Detect motherboard manufacturer from query."""
    query_lower = query.lower()
    
    # ASUS patterns
    if any(kw in query_lower for kw in ['asus', 'rog ', 'strix', 'tuf ', 'prime', 'proart']):
        return 'asus'
    
    # MSI patterns
    if any(kw in query_lower for kw in ['msi', 'mag ', 'mpg ', 'meg ', 'tomahawk', 'mortar', 'carbon']):
        return 'msi'
    
    # Gigabyte patterns
    if any(kw in query_lower for kw in ['gigabyte', 'aorus', 'gb-']):
        return 'gigabyte'
    
    # ASRock patterns
    if any(kw in query_lower for kw in ['asrock', 'phantom', 'steel legend', 'taichi']):
        return 'asrock'
    
    # EVGA patterns
    if 'evga' in query_lower:
        return 'evga'

    # Common Chinese X79/X99 board brands
    if 'machinist' in query_lower:
        return 'machinist'
    if 'huananzhi' in query_lower:
        return 'huananzhi'
    if 'jingyue' in query_lower:
        return 'jingyue'
    
    return None


def get_manufacturer_site(manufacturer: str) -> str:
    """Get manufacturer's spec site domain."""
    sites = {
        'asus': 'asus.com',
        'msi': 'msi.com',
        'gigabyte': 'gigabyte.com',
        'asrock': 'asrock.com',
        'evga': 'evga.com',
        'machinist': 'machinistofficial.com',
        'huananzhi': 'huananzhi.com',
        'jingyue': 'jingyue.com',
    }
    return sites.get(manufacturer, '')

def get_direct_tpu_url(query: str, component_type: str) -> str:
    """Try direct TechPowerUp URL based on common naming patterns."""
    # Convert query to URL slug format
    slug = query.lower().replace(' ', '-').replace('_', '-')
    # Remove "intel" or "amd" prefix - TPU doesn't use them in slugs
    slug = re.sub(r'^intel-?', '', slug)
    slug = re.sub(r'^amd-?', '', slug)
    # Remove special characters except hyphens
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    # Clean up multiple hyphens
    slug = re.sub(r'-+', '-', slug).strip('-')
    
    if component_type == 'GPU':
        # TPU GPU URLs use "geforce-" prefix for NVIDIA and "radeon-" for AMD
        # E.g., "rtx-3060" -> "geforce-rtx-3060", "rx-7900-xtx" -> "radeon-rx-7900-xtx"
        if re.match(r'(rtx|gtx|gt|titan)', slug):
            slug = f"geforce-{slug}"
        elif re.match(r'(rx|r[579])-', slug):
            slug = f"radeon-{slug}"

        # For cards that have VRAM variants (e.g. RTX 5060 Ti 16GB vs 8GB),
        # try the VRAM-suffixed slug first, then fall back to the bare slug.
        vram_match = re.search(r'(\d+)\s*gb', query.lower())
        if vram_match:
            vram_slug = slug + f"-{vram_match.group(1)}-gb"
            tpu_id = _get_tpu_gpu_id(vram_slug)
            if tpu_id:
                return f"https://www.techpowerup.com/gpu-specs/{vram_slug}.{tpu_id}"

        # Check if we have a known TPU spec ID for this GPU
        tpu_id = _get_tpu_gpu_id(slug)
        if tpu_id:
            return f"https://www.techpowerup.com/gpu-specs/{slug}.{tpu_id}"

        return f"https://www.techpowerup.com/gpu-specs/{slug}"
    else:
        return f"https://www.techpowerup.com/cpu-specs/{slug}"


# Common GPU -> TPU spec ID mapping for reliable direct URLs
# Note: TPU slugs sometimes include memory size (e.g., "geforce-rtx-3060-12-gb")
_TPU_GPU_IDS = {
    # RTX 40 series
    'geforce-rtx-4090': 'c3889',
    'geforce-rtx-4080-super': 'c4172',
    'geforce-rtx-4080': 'c3953',
    'geforce-rtx-4070-ti-super': 'c4173',
    'geforce-rtx-4070-ti': 'c3950',
    'geforce-rtx-4070-super': 'c4171',
    'geforce-rtx-4070': 'c3924',
    'geforce-rtx-4060-ti': 'c3977',
    'geforce-rtx-4060': 'c3978',
    # RTX 30 series
    'geforce-rtx-3090-ti': 'c3829',
    'geforce-rtx-3090': 'c3622',
    'geforce-rtx-3080-ti': 'c3735',
    'geforce-rtx-3080': 'c3621',
    'geforce-rtx-3070-ti': 'c3675',
    'geforce-rtx-3070': 'c3674',
    'geforce-rtx-3060-ti': 'c3681',
    'geforce-rtx-3060-12-gb': 'c3682',
    'geforce-rtx-3060': 'c3682',  # Alias without memory size
    'geforce-rtx-3050': 'c3858',
    # RTX 20 series
    'geforce-rtx-2080-ti': 'c3305',
    'geforce-rtx-2080-super': 'c3439',
    'geforce-rtx-2080': 'c3224',
    'geforce-rtx-2070-super': 'c3440',
    'geforce-rtx-2070': 'c3252',
    'geforce-rtx-2060-super': 'c3441',
    'geforce-rtx-2060': 'c3310',
    # GTX 16 series
    'geforce-gtx-1660-ti': 'c3364',
    'geforce-gtx-1660-super': 'c3458',
    'geforce-gtx-1660': 'c3365',
    'geforce-gtx-1650-super': 'c3411',
    'geforce-gtx-1650': 'c3366',
    # GTX 10 series
    'geforce-gtx-1080-ti': 'c2877',
    'geforce-gtx-1080': 'c2839',
    'geforce-gtx-1070-ti': 'c3054',
    'geforce-gtx-1070': 'c2840',
    'geforce-gtx-1060': 'c2862',
    'geforce-gtx-1050-ti': 'c2885',
    'geforce-gtx-1050': 'c2875',
    # AMD RX 7000 series
    'radeon-rx-7900-xtx': 'c3941',
    'radeon-rx-7900-xt': 'c3942',
    'radeon-rx-7800-xt': 'c4045',
    'radeon-rx-7700-xt': 'c4046',
    'radeon-rx-7600': 'c4047',
    # AMD RX 6000 series
    'radeon-rx-6950-xt': 'c3925',
    'radeon-rx-6900-xt': 'c3481',
    'radeon-rx-6800-xt': 'c3694',
    'radeon-rx-6800': 'c3695',
    'radeon-rx-6700-xt': 'c3762',
    'radeon-rx-6600-xt': 'c3773',
    'radeon-rx-6600': 'c3828',
    # AMD RX 5000 series
    'radeon-rx-5700-xt': 'c3339',
    'radeon-rx-5700': 'c3340',
    'radeon-rx-5600-xt': 'c3474',
    'radeon-rx-5500-xt': 'c3468',
    # NVIDIA RTX 50 series (Blackwell) — IDs verified from live TPU spec pages
    'geforce-rtx-5090': 'c4216',
    'geforce-rtx-5080': 'c4217',
    'geforce-rtx-5070': 'c4218',
    'geforce-rtx-5060': 'c4219',
    'geforce-rtx-5070-ti': 'c4243',
    'geforce-rtx-5060-ti-8-gb': 'c4246',
    'geforce-rtx-5060-ti-16-gb': 'c4292',
    'geforce-rtx-5060-ti': 'c4246',    # Default to 8 GB when unspecified
    # AMD RX 9000 series (RDNA 4) — IDs verified from live TPU spec pages
    'radeon-rx-9070-xt': 'c4229',
    'radeon-rx-9070': 'c4250',
}


def _get_tpu_gpu_id(slug: str) -> Optional[str]:
    """Look up the TPU spec ID for a GPU slug."""
    return _TPU_GPU_IDS.get(slug)


# =============================================================================
# CPU-Monkey scraper (CPU-only, direct with Scrape.Do fallback)
# =============================================================================

def _is_intel_cpu_query(query: str) -> bool:
    """Return True when a CPU query clearly identifies an Intel family/model."""
    value = (query or '').lower()
    return any(kw in value for kw in (
        'intel', 'xeon', 'core i', 'core ultra', 'pentium', 'celeron',
        'i3-', 'i5-', 'i7-', 'i9-',
    )) or bool(re.search(r'\be[357]\s*[- ]?\s*\d{4}[a-z]?\s*v?\s*\d+\b', value))


def _is_amd_cpu_query(query: str) -> bool:
    """Return True when a CPU query clearly identifies an AMD family/model.

    The final regex intentionally covers common bare Ryzen-style searches such
    as ``5800X``, ``5700X3D``, and ``7945HX`` without misclassifying Intel
    models such as ``9900K`` or ``12700H``.
    """
    value = (query or '').lower().replace('®', '').replace('™', '')
    if any(kw in value for kw in (
        'amd', 'ryzen', 'threadripper', 'epyc', 'athlon', 'phenom',
        'sempron', 'opteron',
    )):
        return True
    if re.search(r'\bfx\s*[- ]?\s*\d{4}[a-z]?\b', value):
        return True
    if re.search(r'\ba(?:4|6|8|10|12)\s*[- ]?\s*\d{4}[a-z]?\b', value):
        return True
    return bool(re.search(r'\b\d{4}(?:x3d|xt|x|ge|g|hx|hs|u)\b', value))


def _cpu_monkey_slug(query: str) -> Optional[str]:
    """Build CPU-Monkey's stable English CPU slug for Intel or AMD CPUs."""
    value = (query or '').strip().lower()
    if not value:
        return None
    value = value.replace('®', '').replace('™', '')
    value = re.sub(r'\bprocessor\b', ' ', value)
    value = re.sub(r'\bcpu\b', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()

    intel = _is_intel_cpu_query(value)
    amd = _is_amd_cpu_query(value) and not intel

    if intel:
        # CPU-Monkey uses vendor/family prefixes in its slugs. Add them when
        # the user enters a compact Intel model such as e5-2696v4 or i9-9900k.
        if re.search(r'\be[357]\s*[- ]?\s*\d{4}', value) and 'xeon' not in value:
            value = 'xeon ' + value
        if re.search(r'\bi[3579]\s*[- ]?\s*\d{4,5}', value) and 'core' not in value:
            value = 'core ' + value
        if not value.startswith('intel '):
            value = 'intel ' + value

        # Split compact Xeon revisions (e5-2696v4 -> e5 2696 v4).
        value = re.sub(r'\b(e[357])\s*[- ]?\s*(\d{4})([a-z]?)\s*v\s*(\d+)\b',
                       r'\1 \2\3 v\4', value)

    elif amd:
        # A bare AMD model like "5800X" does not contain enough information to
        # construct CPU-Monkey's slug (Ryzen 5/7/9 is part of the URL). AMD's
        # official site search is expected to resolve that form first; TPU is
        # still available afterward. Explicit family names can be slugged.
        amd_family = re.search(
            r'\b(ryzen|threadripper|epyc|athlon|phenom|sempron|opteron|fx|a(?:4|6|8|10|12))\b',
            value, re.I,
        )
        if not amd_family:
            return None
        if value.startswith('threadripper '):
            value = 'ryzen ' + value
        if not value.startswith('amd '):
            value = 'amd ' + value
    else:
        return None

    value = re.sub(r'[^a-z0-9]+', '_', value).strip('_')
    return value or None


def search_cpu_monkey(query: str, allow_scrapedo_fallback: bool = True) -> Optional[Dict]:
    """Look up an Intel or AMD CPU on CPU-Monkey.

    CPU-Monkey follows the vendor's first-party source in the lookup chain. It
    fills Intel OEM/custom-Xeon gaps and provides a strong second source for AMD
    CPUs. Direct HTTP is tried first; a Scrape.Do retry is optional.
    """
    slug = _cpu_monkey_slug(query)
    if not slug:
        return None

    url = f"https://www.cpu-monkey.com/en/cpu-{slug}"
    print(f"[Lookup] CPU-Monkey detail fetch: {url}", flush=True)
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; TechReadOut/3.x; +hardware-spec-lookup)',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    response = None
    try:
        response = requests.get(url, headers=headers, timeout=20)
    except requests.RequestException as exc:
        print(f"[Lookup] CPU-Monkey direct fetch failed: {exc}", flush=True)

    # If the direct request is blocked, use one Scrape.Do request when the
    # service is available. This keeps CPU-Monkey useful without making it
    # dependent on Scrape.Do for the common case.
    if response is None or response.status_code != 200:
        if allow_scrapedo_fallback and scrapedo_fallback_enabled():
            try:
                api_url = (
                    f"https://api.scrape.do?token={SCRAPEDO_TOKEN}"
                    f"&render=false&url={requests.utils.quote(url)}"
                )
                response = scrapedo_get(api_url, timeout=60)
            except ScrapeDoBudgetExceeded:
                return {'error': 'scrapedo_budget_exhausted'}
            except Exception as exc:
                print(f"[Lookup] CPU-Monkey Scrape.Do fetch failed: {exc}", flush=True)
                return None

    if response is None or response.status_code != 200:
        status = getattr(response, 'status_code', 'no response')
        print(f"[Lookup] CPU-Monkey returned HTTP {status}", flush=True)
        return None

    return _parse_cpu_monkey_detail(response.text, url)


def _parse_cpu_monkey_detail(html: str, url: str) -> Optional[Dict]:
    """Parse the subset of CPU-Monkey fields represented by HardwareSpec."""
    soup = BeautifulSoup(html, 'lxml')
    heading = soup.find('h1')
    if not heading:
        return None
    title = ' '.join(heading.get_text(' ', strip=True).split())
    # CPU-Monkey appends site/UI text such as \"Benchmarks & Specs\" to the H1.
    # That text is not part of the CPU model and must never be persisted in
    # HardwareSpec.model.  Handle the title both with and without a dash.
    model = re.sub(
        r'\s*(?:[-–—|:]\s*)?Benchmarks?\s*(?:&|and)\s*Spec(?:s|ifications)\s*$',
        '', title, flags=re.I,
    ).strip()
    # Older/alternate CPU-Monkey layouts have used \"Benchmark, Test and Specs\".
    model = re.sub(
        r'\s*(?:[-–—|:]\s*)?Benchmark(?:s)?\s*,?\s*Tests?\s*(?:&|and)\s*Spec(?:s|ifications)\s*$',
        '', model, flags=re.I,
    ).strip()
    if re.match(r'^AMD\b', model, re.I):
        manufacturer = 'AMD'
    elif re.match(r'^Intel\b', model, re.I):
        manufacturer = 'Intel'
    else:
        manufacturer = 'AMD' if _is_amd_cpu_query(model) else 'Intel'
    model = re.sub(r'^(?:Intel|AMD)\s+', '', model, flags=re.I).strip()
    if not model:
        return None

    # Flatten table-like rows into a case-insensitive property map. The site
    # currently uses normal tables, but the fallback also handles div/list rows
    # where the property and value are separate child elements.
    raw = {}
    for row in soup.select('tr'):
        cells = row.find_all(['th', 'td'])
        if len(cells) >= 2:
            key = ' '.join(cells[0].get_text(' ', strip=True).split()).rstrip(':').lower()
            val = ' '.join(cells[1].get_text(' ', strip=True).split())
            if key and val:
                raw.setdefault(key, val)

    # CPU-Monkey's responsive HTML can expose property/value pairs without tr.
    wanted_labels = (
        'socket', 'cpu cores / threads', 'frequency', 'turbo frequency (1 core)',
        'tdp', 'architecture',
    )
    page_text = soup.get_text('\n', strip=True)
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    for idx, line in enumerate(lines[:-1]):
        key = line.rstrip(':').lower()
        if key in wanted_labels and key not in raw:
            raw[key] = lines[idx + 1]

    specs = {
        'source': 'cpu_monkey',
        'source_url': url,
        'component_type': 'CPU',
        'manufacturer': manufacturer,
        'model': model,
        'raw_data': dict(raw),
    }

    cores_threads = raw.get('cpu cores / threads', '')
    m = re.search(r'(\d+)\s*/\s*(\d+)', cores_threads)
    if m:
        specs['cpu_cores'] = int(m.group(1))
        specs['cpu_threads'] = int(m.group(2))

    base = raw.get('frequency', '')
    m = re.search(r'([\d.]+)\s*ghz', base, re.I)
    if m:
        specs['cpu_base_clock'] = float(m.group(1))

    turbo = raw.get('turbo frequency (1 core)', '')
    m = re.search(r'([\d.]+)\s*ghz', turbo, re.I)
    if m:
        specs['cpu_boost_clock'] = float(m.group(1))

    tdp = raw.get('tdp', '')
    m = re.search(r'(\d+)\s*w', tdp, re.I)
    if m:
        specs['cpu_tdp'] = int(m.group(1))

    socket = raw.get('socket')
    if socket:
        socket = re.sub(r'\s+', ' ', socket).strip()
        # CPU-Monkey calls Coffee Lake Refresh's socket "LGA 1151-2" to
        # distinguish 300-series boards. Intel/TechReadOut use the physical
        # socket name LGA1151, so normalize that alias for compatibility.
        if re.fullmatch(r'LGA\s*1151\s*-\s*2', socket, re.I):
            socket = 'LGA1151'
        else:
            socket = re.sub(r'^(LGA)\s+(\d)', r'\1\2', socket, flags=re.I)
        specs['cpu_socket'] = socket

    architecture = raw.get('architecture')
    if architecture:
        specs['cpu_architecture'] = architecture

    print(f"[Lookup] CPU-Monkey parsed: {manufacturer} {model}", flush=True)
    if not specs.get('cpu_cores') and not specs.get('cpu_socket') and not specs.get('cpu_tdp'):
        print("[Lookup] CPU-Monkey: page had no usable CPU specs", flush=True)
        return None
    return specs


# =============================================================================
# AMD Official scraper (CPU-only, via Scrape.Do)
# =============================================================================

def search_amd_official(query: str) -> Optional[Dict]:
    """Look up an AMD CPU on AMD.com via Scrape.Do.

    AMD exposes detailed first-party processor pages, but its site search and
    specification sections are client-rendered. The flow mirrors Intel ARK:
      1. Search AMD.com for the user's CPU (1 Scrape.Do call).
      2. Select the best processor/support product link.
      3. Fetch the detail page (1 Scrape.Do call).
      4. Parse HardwareSpec-compatible CPU fields.
    """
    if not SCRAPEDO_TOKEN:
        return None

    try:
        search_url = f"https://www.amd.com/en/search?keyword={requests.utils.quote(query)}"
        print(f"[Lookup] AMD Official search: {search_url}", flush=True)
        api_url = (
            f"https://api.scrape.do?token={SCRAPEDO_TOKEN}"
            f"&render=true"
            f"&url={requests.utils.quote(search_url)}"
        )
        response = scrapedo_get(api_url, timeout=90)

        if response.status_code in (402, 403):
            err = response.text.lower()
            if any(w in err for w in ('credit', 'limit', 'quota', 'payment')):
                print("[Lookup] Scrape.Do credits exhausted (AMD search)!", flush=True)
                return {'error': 'credits_exhausted'}
        if response.status_code != 200:
            print(f"[Lookup] AMD Official search returned HTTP {response.status_code}", flush=True)
            return None

        detail_url = _find_amd_product_link(response.text, query)
        if not detail_url:
            print("[Lookup] No AMD processor product link found in search results", flush=True)
            return None

        print(f"[Lookup] AMD Official detail fetch: {detail_url}", flush=True)
        detail_api_url = (
            f"https://api.scrape.do?token={SCRAPEDO_TOKEN}"
            f"&render=true"
            f"&url={requests.utils.quote(detail_url)}"
        )
        detail_response = scrapedo_get(detail_api_url, timeout=90)

        if detail_response.status_code in (402, 403):
            err = detail_response.text.lower()
            if any(w in err for w in ('credit', 'limit', 'quota', 'payment')):
                print("[Lookup] Scrape.Do credits exhausted (AMD detail)!", flush=True)
                return {'error': 'credits_exhausted'}
        if detail_response.status_code != 200:
            print(f"[Lookup] AMD Official detail page returned HTTP {detail_response.status_code}", flush=True)
            return None

        return _parse_amd_official_detail(detail_response.text, detail_url)

    except ScrapeDoBudgetExceeded:
        return {'error': 'scrapedo_budget_exhausted'}
    except Exception as exc:
        print(f"[Lookup] AMD Official error: {exc}", flush=True)
        return None


def _find_amd_product_link(html: str, query: str) -> Optional[str]:
    """Return the best AMD processor product/support link from site search."""
    soup = BeautifulSoup(html, 'lxml')
    query_lower = (query or '').lower()
    query_numbers = re.findall(r'\d{3,5}', query_lower)
    query_tokens = {
        token for token in re.findall(r'[a-z0-9]+', query_lower)
        if len(token) >= 2 and token not in {'amd', 'cpu', 'processor'}
    }

    candidates = []
    for link in soup.find_all('a', href=True):
        href = requests.utils.unquote(link.get('href') or '').strip()
        if not href:
            continue
        if href.startswith('//'):
            href = 'https:' + href
        elif href.startswith('/'):
            href = 'https://www.amd.com' + href
        elif href.startswith('www.amd.com/'):
            href = 'https://' + href
        if not href.startswith('http'):
            continue

        clean_href = href.split('#', 1)[0].split('?', 1)[0]
        lower_href = clean_href.lower()
        is_product = '/en/products/processors/' in lower_href and lower_href.endswith('.html')
        is_support = '/en/support/downloads/drivers.html/processors/' in lower_href and lower_href.endswith('.html')
        if not (is_product or is_support):
            continue

        text = ' '.join(link.get_text(' ', strip=True).split())
        haystack = f"{text} {lower_href.replace('-', ' ')}".lower()
        score = 40 if is_product else 20
        score += 25 * sum(1 for num in query_numbers if num in haystack)
        score += 4 * len(query_tokens & set(re.findall(r'[a-z0-9]+', haystack)))
        if 'processor' in haystack:
            score += 3
        candidates.append((score, clean_href))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _normalize_amd_label(value: str) -> str:
    value = (value or '').replace('™', '').replace('®', '').strip().lower()
    value = re.sub(r'\s+', ' ', value).rstrip(':')
    return value


def _parse_amd_official_detail(html: str, url: str) -> Optional[Dict]:
    """Parse the AMD.com CPU fields represented by TechReadOut HardwareSpec."""
    soup = BeautifulSoup(html, 'lxml')
    raw: Dict[str, str] = {}

    # AMD has used several layouts over time. Collect conventional definition
    # lists and tables first, then fall back to the rendered text sequence.
    for dt in soup.find_all('dt'):
        dd = dt.find_next_sibling('dd')
        if dd:
            key = _normalize_amd_label(dt.get_text(' ', strip=True))
            val = ' '.join(dd.get_text(' ', strip=True).split())
            if key and val:
                raw.setdefault(key, val)

    for row in soup.select('tr'):
        cells = row.find_all(['th', 'td'])
        if len(cells) >= 2:
            key = _normalize_amd_label(cells[0].get_text(' ', strip=True))
            val = ' '.join(cells[1].get_text(' ', strip=True).split())
            if key and val:
                raw.setdefault(key, val)

    known_labels = {
        'name', 'family', 'series', 'former codename', 'architecture',
        '# of cpu cores', '# of threads', 'cpu cores', 'threads',
        'max. boost clock', 'max boost clock', 'base clock',
        'default tdp', 'tdp', 'cpu socket', 'socket',
        'processor technology for cpu cores',
    }
    lines = [' '.join(line.split()) for line in soup.get_text('\n', strip=True).splitlines()]
    lines = [line for line in lines if line]
    for idx, line in enumerate(lines[:-1]):
        key = _normalize_amd_label(line)
        if key in known_labels and key not in raw:
            next_value = lines[idx + 1]
            if _normalize_amd_label(next_value) not in known_labels:
                raw[key] = next_value

    title = None
    if raw.get('name'):
        title = raw['name']
    else:
        heading = soup.find('h1')
        if heading:
            title = ' '.join(heading.get_text(' ', strip=True).split())
    if not title:
        print("[Lookup] AMD Official: could not find model name", flush=True)
        return None

    title = title.replace('™', '').replace('®', '')
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'\s+(?:Desktop|Server|Workstation|Mobile)\s+Processor.*$', '', title, flags=re.I)
    title = re.sub(r'\s+Processor.*$', '', title, flags=re.I)
    model = re.sub(r'^AMD\s+', '', title, flags=re.I).strip()
    if not model:
        return None

    specs = {
        'source': 'amd_official',
        'source_url': url,
        'component_type': 'CPU',
        'manufacturer': 'AMD',
        'model': model,
        'raw_data': dict(raw),
    }

    def _first(*keys):
        for key in keys:
            if raw.get(key):
                return raw[key]
        return ''

    cores = _first('# of cpu cores', 'cpu cores')
    m = re.search(r'(\d+)', cores)
    if m:
        specs['cpu_cores'] = int(m.group(1))

    threads = _first('# of threads', 'threads')
    m = re.search(r'(\d+)', threads)
    if m:
        specs['cpu_threads'] = int(m.group(1))

    base = _first('base clock')
    m = re.search(r'([\d.]+)\s*ghz', base, re.I)
    if m:
        specs['cpu_base_clock'] = float(m.group(1))

    boost = _first('max. boost clock', 'max boost clock')
    m = re.search(r'([\d.]+)\s*ghz', boost, re.I)
    if m:
        specs['cpu_boost_clock'] = float(m.group(1))

    tdp = _first('default tdp', 'tdp')
    m = re.search(r'(\d+)\s*w', tdp, re.I)
    if m:
        specs['cpu_tdp'] = int(m.group(1))

    socket = _first('cpu socket', 'socket')
    if socket:
        # AMD pages normally return AM4/AM5/SP3/etc. Keep any uncommon socket
        # intact while trimming descriptive parentheticals when present.
        specs['cpu_socket'] = re.sub(r'\s*\([^)]*\)\s*$', '', socket).strip()

    architecture = _first('architecture', 'former codename')
    if architecture:
        specs['cpu_architecture'] = architecture

    print(f"[Lookup] AMD Official parsed: AMD {model}", flush=True)
    if not specs.get('cpu_cores') and not specs.get('cpu_socket') and not specs.get('cpu_tdp'):
        print("[Lookup] AMD Official: parsed page but found no usable CPU specs", flush=True)
        return None
    return specs


# =============================================================================
# Intel ARK scraper (CPU-only, via Scrape.Do)
# =============================================================================

def search_intel_ark(query: str) -> Optional[Dict]:
    """
    Look up an Intel CPU on Intel ARK via Scrape.Do (render=true required —
    ARK product pages are JS-rendered).

    Strategy:
      1. Search ARK using its native search endpoint (1 credit).
      2. Find the product page link in the search results HTML.
      3. Fetch the product page via Scrape.Do (1 credit).
      4. Parse and return normalized CPU spec dict.

    Returns None on any failure so the caller can fall through to TPU.
    Only called for Intel CPUs; AMD uses its own first-party lookup.
    """
    if not SCRAPEDO_TOKEN:
        return None

    try:
        # ── Step A: search ARK ────────────────────────────────────────────
        search_url = (
            f"https://ark.intel.com/content/www/us/en/ark/search.html"
            f"?_intl_lang=en&q={requests.utils.quote(query)}"
        )
        print(f"[Lookup] Intel ARK search: {search_url}", flush=True)

        api_url = (
            f"https://api.scrape.do?token={SCRAPEDO_TOKEN}"
            f"&render=true"
            f"&url={requests.utils.quote(search_url)}"
        )
        response = scrapedo_get(api_url, timeout=90)

        if response.status_code in (402, 403):
            err = response.text.lower()
            if any(w in err for w in ('credit', 'limit', 'quota', 'payment')):
                print("[Lookup] Scrape.Do credits exhausted (ARK search)!", flush=True)
                return {'error': 'credits_exhausted'}

        if response.status_code != 200:
            print(f"[Lookup] Intel ARK search returned HTTP {response.status_code}", flush=True)
            return None

        # ── Step B: find product page link ────────────────────────────────
        detail_url = _find_ark_product_link(response.text, query)
        if not detail_url:
            print("[Lookup] No Intel ARK product link found in search results", flush=True)
            return None

        # ── Step C: fetch product page ────────────────────────────────────
        print(f"[Lookup] Intel ARK detail fetch: {detail_url}", flush=True)
        detail_api_url = (
            f"https://api.scrape.do?token={SCRAPEDO_TOKEN}"
            f"&render=true"
            f"&url={requests.utils.quote(detail_url)}"
        )
        detail_response = scrapedo_get(detail_api_url, timeout=90)

        if detail_response.status_code in (402, 403):
            err = detail_response.text.lower()
            if any(w in err for w in ('credit', 'limit', 'quota', 'payment')):
                print("[Lookup] Scrape.Do credits exhausted (ARK detail)!", flush=True)
                return {'error': 'credits_exhausted'}

        if detail_response.status_code != 200:
            print(f"[Lookup] Intel ARK detail page returned HTTP {detail_response.status_code}", flush=True)
            return None

        return _parse_intel_ark_detail(detail_response.text, detail_url)

    except ScrapeDoBudgetExceeded:
        return {'error': 'scrapedo_budget_exhausted'}
    except Exception as e:
        print(f"[Lookup] Intel ARK error: {e}", flush=True)
        return None


def _find_ark_product_link(html: str, query: str) -> Optional[str]:
    """Return the best Intel ARK/product-specification link from search HTML.

    Intel now uses two product URL layouts in the wild:
      * legacy ARK: /content/www/us/en/ark/products/{id}/...html
      * current:    /content/www/us/en/products/sku/{id}/.../specifications.html

    The previous matcher only accepted the legacy layout, so current Intel
    search results could contain the right CPU (for example Xeon X5660) and
    TechReadOut would still report an ARK miss.
    """
    soup = BeautifulSoup(html, 'lxml')
    query_lower = (query or '').lower().replace('®', '').replace('™', '')
    query_numbers = re.findall(r'\d{3,5}', query_lower)
    query_tokens = {
        token for token in re.findall(r'[a-z0-9]+', query_lower)
        if len(token) >= 2 and token not in {'intel', 'cpu', 'processor'}
    }

    legacy_pattern = re.compile(
        r'/content/www/[a-z]{2}/[a-z]{2}/ark/products/\d+/[^"\'>\s]+\.html',
        re.IGNORECASE,
    )
    current_pattern = re.compile(
        r'/content/www/[a-z]{2}/[a-z]{2}/products/sku/\d+/[^"\'>\s]+/specifications\.html',
        re.IGNORECASE,
    )

    candidates = []
    seen = set()
    for link in soup.find_all('a', href=True):
        raw_href = requests.utils.unquote(link.get('href') or '').strip()
        if not raw_href:
            continue

        match = current_pattern.search(raw_href) or legacy_pattern.search(raw_href)
        if not match:
            continue

        matched_path = match.group(0)
        if raw_href.startswith('//'):
            href = 'https:' + raw_href
        elif raw_href.startswith('http'):
            href = raw_href
        else:
            # Current Intel product pages live on www.intel.com. The legacy
            # /ark/products/ paths work from the same host as well.
            href = 'https://www.intel.com' + (raw_href if raw_href.startswith('/') else '/' + raw_href)

        href = href.split('#', 1)[0]
        if href in seen:
            continue
        seen.add(href)

        text = ' '.join(link.get_text(' ', strip=True).split())
        haystack = f"{text} {matched_path.replace('-', ' ')}".lower()
        score = 10
        score += 30 * sum(1 for num in query_numbers if num in haystack)
        score += 5 * len(query_tokens & set(re.findall(r'[a-z0-9]+', haystack)))
        if current_pattern.search(raw_href):
            score += 3
        candidates.append((score, href))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _normalize_intel_ark_label(value: str) -> str:
    value = (value or '').replace('™', '').replace('®', '').strip().lower()
    value = re.sub(r'[^a-z0-9#+-]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip().rstrip(':')


def _clean_intel_cpu_model(title: str) -> str:
    """Turn Intel's page heading into the concise model stored by TRO."""
    value = (title or '').replace('™', '').replace('®', '')
    value = re.sub(r'\s+', ' ', value).strip()
    value = re.sub(r'^Intel\s+', '', value, flags=re.I)
    # Product headings commonly read "Xeon Processor X5660" or
    # "Core i9-9900K Processor". Remove the generic Processor word while
    # preserving family/model text.
    value = re.sub(r'\bProcessor\b', ' ', value, flags=re.I)
    # Detail headings sometimes append parenthesized cache/frequency specs.
    value = re.sub(r'\s*\([^)]*(?:cache|ghz|qpi|tdp)[^)]*\).*$', '', value, flags=re.I)
    return re.sub(r'\s+', ' ', value).strip(' -')


def _parse_intel_ark_detail(html: str, url: str) -> Optional[Dict]:
    """Parse legacy ARK and current Intel ``/products/sku/`` CPU pages.

    Intel's current product pages no longer consistently use the old
    ``tech-label`` / ``tech-data`` DOM. We therefore collect the legacy DOM
    first, then definition/table layouts, and finally label/value pairs from
    rendered page text. This keeps old ARK pages working while supporting the
    current Intel pages returned for CPUs such as Xeon X5660.
    """
    soup = BeautifulSoup(html, 'lxml')

    # ── Collect raw key→value pairs ───────────────────────────────────────
    raw: Dict[str, str] = {}

    def remember(label: str, value: str):
        key = _normalize_intel_ark_label(label)
        val = ' '.join((value or '').split())
        if key and val:
            raw.setdefault(key, val)

    # Legacy tech-section rows.
    for label in soup.select('span.tech-label, div.tech-label'):
        data_tag = label.find_next_sibling(
            lambda t: 'tech-data' in (t.get('class') or [])
        )
        if not data_tag and label.parent:
            data_tag = label.parent.find(class_='tech-data')
        if data_tag:
            remember(label.get_text(' ', strip=True), data_tag.get_text(' ', strip=True))

    # Legacy data-key rows.
    for li in soup.select('li[data-key]'):
        value_tag = li.select_one('span.value, .value')
        if value_tag:
            remember(li.get('data-key', ''), value_tag.get_text(' ', strip=True))

    # Current/alternate definition list and table layouts.
    for dt in soup.find_all('dt'):
        dd = dt.find_next_sibling('dd')
        if dd:
            remember(dt.get_text(' ', strip=True), dd.get_text(' ', strip=True))
    for row in soup.select('tr'):
        cells = row.find_all(['th', 'td'])
        if len(cells) >= 2:
            remember(cells[0].get_text(' ', strip=True), cells[1].get_text(' ', strip=True))

    # Current Intel rendered pages expose labels and values as adjacent text
    # even when their DOM classes change. This is intentionally narrow so
    # unrelated navigation text cannot become a spec field.
    known_labels = {
        'processor number', 'total cores', 'cores', 'total threads', 'threads',
        'max turbo frequency', 'processor base frequency', 'base frequency',
        'tdp', 'thermal design power', 'sockets supported', 'socket',
        'cache', 'code name', 'product collection',
    }
    lines = [' '.join(line.split()) for line in soup.get_text('\n', strip=True).splitlines()]
    lines = [line for line in lines if line]
    for idx, line in enumerate(lines[:-1]):
        key = _normalize_intel_ark_label(line)
        if key in known_labels and key not in raw:
            next_value = lines[idx + 1]
            if _normalize_intel_ark_label(next_value) not in known_labels:
                remember(line, next_value)

    # ── Model name ────────────────────────────────────────────────────────
    model = None
    for sel in ['h1.product-family-title', 'h1[class*="product"]', 'h1']:
        tag = soup.select_one(sel)
        if tag:
            candidate = _clean_intel_cpu_model(tag.get_text(' ', strip=True))
            if candidate and len(candidate) > 2 and candidate.lower() not in {'product specifications', 'specifications'}:
                model = candidate
                break

    # If a current Intel layout did not expose a usable h1, Processor Number
    # plus the searched product family from the title is still enough to form
    # a safe model name in common Xeon/Core cases.
    if not model and raw.get('processor number'):
        number = raw['processor number'].strip()
        page_title = _clean_intel_cpu_model(soup.title.get_text(' ', strip=True) if soup.title else '')
        if 'xeon' in page_title.lower():
            model = f'Xeon {number}'
        elif 'core' in page_title.lower():
            model = f'Core {number}'
        else:
            model = number

    if not model:
        print('[Lookup] Intel ARK: could not find model name on detail page', flush=True)
        return None

    specs = {
        'source': 'intel_ark',
        'source_url': url,
        'component_type': 'CPU',
        'manufacturer': 'Intel',
        'model': model,
        'raw_data': dict(raw),
    }

    def first(*keys):
        for key in keys:
            normalized = _normalize_intel_ark_label(key)
            if raw.get(normalized):
                return raw[normalized]
        return ''

    # Cores / threads
    m = re.search(r'(\d+)', first('total cores', 'cores'))
    if m:
        specs['cpu_cores'] = int(m.group(1))
    m = re.search(r'(\d+)', first('total threads', 'threads'))
    if m:
        specs['cpu_threads'] = int(m.group(1))

    # Base / turbo clocks
    m = re.search(r'([\d.]+)\s*ghz', first('processor base frequency', 'base frequency'), re.I)
    if m:
        specs['cpu_base_clock'] = float(m.group(1))
    m = re.search(r'([\d.]+)\s*ghz', first('max turbo frequency'), re.I)
    if m:
        specs['cpu_boost_clock'] = float(m.group(1))

    # TDP
    m = re.search(r'(\d+(?:\.\d+)?)\s*w', first('tdp', 'thermal design power'), re.I)
    if m:
        tdp = float(m.group(1))
        specs['cpu_tdp'] = int(tdp) if tdp.is_integer() else tdp

    # Socket. Intel often writes "FCLGA1366,LGA1366"; TRO only needs the
    # canonical socket, so prefer the LGA/BGA/PGA token when present.
    socket = first('sockets supported', 'socket')
    if socket:
        socket_match = re.search(r'(?<!FC)\b(LGA|BGA|PGA)\s*[- ]?(\d{3,4}(?:-\d+)?)\b', socket, re.I)
        if not socket_match:
            socket_match = re.search(r'\bFC(LGA)\s*[- ]?(\d{3,4}(?:-\d+)?)\b', socket, re.I)
        if socket_match:
            specs['cpu_socket'] = f"{socket_match.group(1).upper()}{socket_match.group(2)}"
        else:
            specs['cpu_socket'] = re.sub(r'\s+', ' ', socket).strip()

    cache = first('cache')
    if cache:
        specs['raw_data']['cache'] = cache
    codename = first('code name')
    if codename:
        specs['cpu_architecture'] = re.sub(r'^Products formerly\s+', '', codename, flags=re.I).strip()

    print(f"[Lookup] Intel ARK parsed: Intel {specs.get('model', '?')}", flush=True)
    if not specs.get('cpu_cores') and not specs.get('cpu_tdp') and not specs.get('cpu_socket'):
        print('[Lookup] Intel ARK: parsed page but found no usable CPU specs', flush=True)
        return None
    return specs


# =============================================================================
# Public entry point
# =============================================================================

def lookup_hardware(
    query: str,
    component_type: str = 'auto',
    lite_mode: bool = False,
    use_intel_ark: bool = False,    # deprecated in v3.0; kept for API compat (no-op)
    use_amd_official: bool = False, # kept for API compat; AMD Official is now automatic
) -> Optional[Dict]:
    """
    Web-scrape lookup for one hardware item. Caller (api.py) handles DB cache
    and seed lookup before this is called; this is the scraper fallback only.

    Chain:
        CPU (Intel): Seed DB [caller] → Intel ARK via Scrape.Do → CPU-Monkey → TPU via Scrape.Do → Open WebUI
        CPU (AMD):   Seed DB [caller] → AMD Official via Scrape.Do → CPU-Monkey → TPU via Scrape.Do → Open WebUI
        GPU:         Seed DB [caller] → TPU via Scrape.Do → Amazon → Open WebUI
        Other:       Seed DB [caller] → Amazon via Scrape.Do → Open WebUI

    Open WebUI results are never auto-accepted regardless of score —
    api.py caps their confidence below the auto-accept threshold so they
    always land in the Pending Review queue. If Open WebUI is disabled/unconfigured,
    the chain falls straight through to manual AI Import as before.

    `lite_mode=True` skips the paid fallback (and Open WebUI). `use_intel_ark` and
    `use_amd_official` are accepted for backward compatibility with v2 callers;
    both first-party sources now run automatically when their CPU vendor matches.
    """
    if component_type == 'auto':
        component_type = detect_component_type(query)

    print(f"[Lookup] chain: '{query}' as {component_type}"
          + (' (LITE)' if lite_mode else ''), flush=True)

    if use_intel_ark or use_amd_official:
        print("[Lookup] Note: Intel ARK / AMD Official are now selected automatically by CPU vendor; "
              "legacy source flags no longer change the chain.", flush=True)

    _begin_lookup_budget()
    try:
        if lite_mode:
            print("[Lookup] LITE mode: stopping before paid fallback", flush=True)
            return None

        # CPU-Monkey can work directly, so recognized Intel/AMD CPU lookups can
        # still have an automatic source when Scrape.Do is disabled/unconfigured.
        intel_cpu = component_type == 'CPU' and _is_intel_cpu_query(query)
        amd_cpu = component_type == 'CPU' and _is_amd_cpu_query(query) and not intel_cpu
        if not scrapedo_fallback_enabled():
            if intel_cpu or amd_cpu:
                print("[Lookup] Scrape.Do unavailable; trying CPU-Monkey directly", flush=True)
                result = search_cpu_monkey(query, allow_scrapedo_fallback=False)
                if _acceptable_scrape_hit(query, result, 'CPU'):
                    print("[Lookup] Hit: CPU-Monkey", flush=True)
                    return enrich_scrape_result(query, result, 'CPU')
            print("[Lookup] Scrape.Do disabled or token missing; continuing to Open WebUI", flush=True)
        else:
            try:
                if component_type == 'GPU':
                    if start_scrapedo_sequence('gpu'):
                        print("[Lookup] Step 1: Scrape.Do TPU then Amazon (GPU)", flush=True)
                        # Try TPU first (~1 credit if we have a confirmed ID, else ~2)
                        result = search_with_scrapedo(query, component_type)
                        if _is_terminal_error(result):
                            return result
                        if _acceptable_scrape_hit(query, result, component_type):
                            print("[Lookup] Hit: Scrape.Do TPU", flush=True)
                            return enrich_scrape_result(query, result, component_type)

                        # Fall through to Amazon GPU search
                        result = search_amazon_gpu(query)
                        if _is_terminal_error(result):
                            return result
                        if _acceptable_scrape_hit(query, result, 'GPU'):
                            print("[Lookup] Hit: Scrape.Do Amazon GPU", flush=True)
                            return enrich_scrape_result(query, result, 'GPU')

                elif component_type == 'CPU':
                    if start_scrapedo_sequence('cpu'):
                        first_party_terminal_error = None

                        # ── Intel: ARK first, CPU-Monkey for OEM/custom gaps ──
                        if intel_cpu:
                            print("[Lookup] Step 1a: Intel ARK via Scrape.Do (CPU)", flush=True)
                            result = search_intel_ark(query)
                            first_party_terminal_error = result if _is_terminal_error(result) else None
                            if not first_party_terminal_error and _acceptable_scrape_hit(query, result, 'CPU'):
                                print("[Lookup] Hit: Intel ARK", flush=True)
                                return enrich_scrape_result(query, result, 'CPU')

                            print("[Lookup] Intel ARK miss; trying CPU-Monkey", flush=True)
                            # CPU-Monkey has a deterministic model URL (for example
                            # intel_core_i5_9600k). Direct HTTP is free; if the site
                            # blocks it, use any remaining Scrape.Do call before TPU.
                            # This is a stronger exact-model fallback than spending
                            # the final normal-depth call on a broad TPU search.
                            result = search_cpu_monkey(query, allow_scrapedo_fallback=True)
                            if _acceptable_scrape_hit(query, result, 'CPU'):
                                print("[Lookup] Hit: CPU-Monkey", flush=True)
                                return enrich_scrape_result(query, result, 'CPU')
                            if first_party_terminal_error:
                                return first_party_terminal_error
                            print("[Lookup] CPU-Monkey miss", flush=True)

                        # ── AMD: AMD.com first, then CPU-Monkey, then TPU ──
                        elif amd_cpu:
                            print("[Lookup] Step 1a: AMD Official via Scrape.Do (CPU)", flush=True)
                            result = search_amd_official(query)
                            first_party_terminal_error = result if _is_terminal_error(result) else None
                            if not first_party_terminal_error and _acceptable_scrape_hit(query, result, 'CPU'):
                                print("[Lookup] Hit: AMD Official", flush=True)
                                return enrich_scrape_result(query, result, 'CPU')

                            print("[Lookup] AMD Official miss; trying CPU-Monkey", flush=True)
                            result = search_cpu_monkey(query, allow_scrapedo_fallback=True)
                            if _acceptable_scrape_hit(query, result, 'CPU'):
                                print("[Lookup] Hit: CPU-Monkey", flush=True)
                                return enrich_scrape_result(query, result, 'CPU')
                            if first_party_terminal_error:
                                return first_party_terminal_error
                            print("[Lookup] CPU-Monkey miss", flush=True)

                        # ARK/AMD + CPU-Monkey can consume the normal-depth paid
                        # budget. Do not turn that into a terminal budget error by
                        # blindly starting TPU; continue to Open WebUI instead.
                        remaining = _scrapedo_calls_remaining()
                        if remaining is not None and remaining <= 0:
                            print("[Lookup] Paid CPU budget used; skipping TPU and continuing to Open WebUI", flush=True)
                        else:
                            print("[Lookup] Step 1c: Scrape.Do TPU (CPU)", flush=True)
                            result = search_with_scrapedo(query, component_type)
                            if _is_terminal_error(result):
                                return result
                            if _acceptable_scrape_hit(query, result, component_type):
                                print("[Lookup] Hit: Scrape.Do TPU", flush=True)
                                return enrich_scrape_result(query, result, component_type)

                elif component_type == 'Motherboard':
                    if start_scrapedo_sequence('motherboard'):
                        print("[Lookup] Step 1: Scrape.Do Amazon (Motherboard)", flush=True)
                        result = search_motherboard(query)
                        if _is_terminal_error(result):
                            return result
                        if _acceptable_scrape_hit(query, result, 'Motherboard'):
                            print("[Lookup] Hit: Scrape.Do Motherboard", flush=True)
                            return enrich_scrape_result(query, result, 'Motherboard')

                elif component_type == 'PSU':
                    if start_scrapedo_sequence('psu'):
                        print("[Lookup] Step 1: Scrape.Do Amazon (PSU)", flush=True)
                        result = search_psu(query)
                        if _is_terminal_error(result):
                            return result
                        if _acceptable_scrape_hit(query, result, 'PSU'):
                            print("[Lookup] Hit: Scrape.Do PSU", flush=True)
                            return enrich_scrape_result(query, result, 'PSU')

                else:
                    # RAM, Storage, Cooler, Case, Fan, NIC, Sound Card, etc.
                    if start_scrapedo_sequence(f'generic:{component_type.lower()}'):
                        print(f"[Lookup] Step 1: Scrape.Do Amazon (generic {component_type})", flush=True)
                        result = search_generic(query, component_type)
                        if _is_terminal_error(result):
                            return result
                        if _acceptable_scrape_hit(query, result, component_type):
                            print(f"[Lookup] Hit: Scrape.Do generic {component_type}", flush=True)
                            return enrich_scrape_result(query, result, component_type)

            except ScrapeDoBudgetExceeded:
                return {'error': 'scrapedo_budget_exhausted'}

        # =================================================================
        # Step 2: Open WebUI LLM fallback
        # =================================================================
        from app.scrapers.openwebui import openwebui_enabled, query_openwebui_llm
        if openwebui_enabled():
            print("[Lookup] Step 2: Open WebUI", flush=True)
            result = query_openwebui_llm(query, component_type)
            if _acceptable_scrape_hit(query, result, component_type):
                print("[Lookup] Hit: Open WebUI", flush=True)
                return enrich_scrape_result(query, result, component_type)

        print(f"[Lookup] No hit for '{query}'", flush=True)
        return None
    finally:
        _end_lookup_budget()


def _is_terminal_error(result):
    """Return the result unchanged if it's a budget/credits error worth bubbling up."""
    if not isinstance(result, dict):
        return None
    err = result.get('error')
    return err in ('credits_exhausted', 'scrapedo_budget_exhausted')



# =============================================================================
# Step 1: Scrape.Do path
# =============================================================================

def search_with_scrapedo(query: str, component_type: str) -> Optional[Dict]:
    """
    Fetch a CPU/GPU spec page from TechPowerUp via Scrape.Do.

    Strategy:
      1. If we have a verified slug.cXXXX TPU ID, try direct (1 credit).
         On hit, return immediately.
      2. Otherwise (or on miss), query TechPowerUp's native ?q= search via
         Scrape.Do (1 credit), find the spec page link, and fetch it via
         Scrape.Do (1 credit). Worst case 2 credits.
    """
    if not SCRAPEDO_TOKEN:
        return None

    search_query = query
    if component_type == 'GPU':
        search_query = normalize_gpu_query(query)

    try:
        # ── Step A: direct URL when we have a confirmed ID ────────────────
        direct_url = get_direct_tpu_url(search_query, component_type)
        slug = direct_url.split('/gpu-specs/')[-1].split('/cpu-specs/')[-1]
        has_known_id = '.' in slug

        if has_known_id:
            print(f"[Lookup] Scrape.Do direct (confirmed ID): {direct_url}", flush=True)
            api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&render=true&url={requests.utils.quote(direct_url)}"
            response = scrapedo_get(api_url, timeout=60)

            if response.status_code in (402, 403):
                err = response.text.lower()
                if any(w in err for w in ('credit', 'limit', 'quota', 'payment')):
                    print("[Lookup] Scrape.Do credits exhausted!", flush=True)
                    return {'error': 'credits_exhausted'}

            if response.status_code == 200 and (
                'gpuname' in response.text or 'cpuname' in response.text
                or 'sectioncontainer' in response.text
            ):
                return parse_techpowerup_detail(response.text, component_type, direct_url)

            print("[Lookup] Direct URL miss; falling through to TPU search", flush=True)

        # ── Step B: TechPowerUp native ?q= search via Scrape.Do ───────────
        search_url = get_search_url(search_query, component_type)
        print(f"[Lookup] Scrape.Do TPU search: {search_url}", flush=True)

        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&render=true&url={requests.utils.quote(search_url)}"
        response = scrapedo_get(api_url, timeout=60)

        if response.status_code in (402, 403):
            err = response.text.lower()
            if any(w in err for w in ('credit', 'limit', 'quota', 'payment')):
                print("[Lookup] Scrape.Do credits exhausted!", flush=True)
                return {'error': 'credits_exhausted'}

        response.raise_for_status()

        detail_url = find_tpu_link_in_results(response.text, component_type)
        if not detail_url:
            print("[Lookup] No TechPowerUp spec link in search results", flush=True)
            return None

        # ── Step C: fetch the detail page via Scrape.Do ───────────────────
        print(f"[Lookup] Scrape.Do detail fetch: {detail_url}", flush=True)
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&render=true&url={requests.utils.quote(detail_url)}"
        detail_response = scrapedo_get(detail_api_url, timeout=60)

        if detail_response.status_code in (402, 403):
            err = detail_response.text.lower()
            if any(w in err for w in ('credit', 'limit', 'quota', 'payment')):
                print("[Lookup] Scrape.Do credits exhausted!", flush=True)
                return {'error': 'credits_exhausted'}

        detail_response.raise_for_status()
        return parse_techpowerup_detail(detail_response.text, component_type, detail_url)

    except ScrapeDoBudgetExceeded:
        return {'error': 'scrapedo_budget_exhausted'}
    except Exception as e:
        print(f"[Lookup] Scrape.Do error: {e}", flush=True)
        return None


def find_tpu_link_in_results(html: str, component_type: str) -> Optional[str]:
    """Extract a TechPowerUp spec-page link from TPU's own ?q= search results.

    Markup-tolerant by design: rather than depending on the result listing's
    table/row/card structure (which is not pinned down and may change), it scans
    every anchor and matches the spec-page URL pattern directly.

    Detail-page patterns (verified via Wikidata properties P13844 / P13418):
        cpu-specs/{slug}.c{N}      (c\\d{1,4})
        gpu-specs/{slug}.{c|g}{N}  ([cg]\\d{1,4})

    TPU results pages use root-relative hrefs (e.g. "/cpu-specs/...."), so we
    normalize to an absolute https URL before returning.
    """
    soup = BeautifulSoup(html, 'lxml')

    target = 'gpu-specs' if component_type == 'GPU' else 'cpu-specs'
    # Require a real spec ID suffix (.c#### or .g####) so we don't match the
    # database listing/landing pages, only individual product pages.
    pattern = re.compile(rf'/{target}/[a-z0-9\-]+\.[cg]\d{{1,4}}', re.IGNORECASE)

    for link in soup.find_all('a', href=True):
        href = link['href']
        if pattern.search(href):
            # Normalize root-relative or protocol-relative hrefs to absolute.
            if href.startswith('//'):
                href = 'https:' + href
            elif href.startswith('/'):
                href = 'https://www.techpowerup.com' + href
            elif not href.startswith('http'):
                href = 'https://www.techpowerup.com/' + href.lstrip('/')
            return requests.utils.unquote(href)

    return None

def parse_techpowerup_detail(html: str, component_type: str, url: str) -> Dict:
    """Parse TechPowerUp detail page HTML into specs dict."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': 'techpowerup',
        'source_url': url,
        'component_type': component_type,
        'raw_data': {}
    }
    
    # Get model name from page title
    title = soup.select_one('h1.gpuname, h1.cpuname, .content h1')
    if title:
        full_name = title.text.strip()
        specs['model'] = full_name
        
        # Extract manufacturer
        if 'NVIDIA' in full_name.upper() or 'GeForce' in full_name:
            specs['manufacturer'] = 'NVIDIA'
        elif 'AMD' in full_name.upper() or 'Radeon' in full_name:
            specs['manufacturer'] = 'AMD'
        elif 'Intel' in full_name:
            specs['manufacturer'] = 'Intel'
    
    # Parse specs from definition lists
    for dl in soup.select('.gpuspecs dl, .cpuspecs dl, .sectioncontainer dl'):
        dt = dl.select_one('dt')
        dd = dl.select_one('dd')
        if dt and dd:
            key = dt.text.strip().lower().replace(' ', '_').replace('#', 'num')
            value = dd.text.strip()
            specs['raw_data'][key] = value
    
    # Also try table format
    for section in soup.select('.details, .specs'):
        for row in section.select('tr'):
            cells = row.select('td, th')
            if len(cells) >= 2:
                key = cells[0].text.strip().lower().replace(' ', '_')
                value = cells[1].text.strip()
                if key and value:
                    specs['raw_data'][key] = value
    
    raw = {k.rstrip(':').strip(): v for k, v in specs['raw_data'].items()}
    
    # Extract normalized fields based on component type
    if component_type == 'GPU':
        # Memory size
        for key in ['memory_size', 'vram', 'memory']:
            if key in raw:
                match = re.search(r'(\d+)\s*(MB|GB)', raw[key], re.I)
                if match:
                    size = int(match.group(1))
                    if match.group(2).upper() == 'GB':
                        size *= 1024
                    specs['gpu_memory_size'] = size
                break
        
        # Memory type
        for key in ['memory_type', 'memory']:
            if key in raw:
                for mem_type in ['GDDR7', 'GDDR6X', 'GDDR6', 'GDDR5X', 'GDDR5', 'GDDR4', 'GDDR3', 'HBM3', 'HBM2e', 'HBM2', 'HBM']:
                    if mem_type in raw[key].upper():
                        specs['gpu_memory_type'] = mem_type
                        break
                if 'gpu_memory_type' in specs:
                    break
        
        # TDP
        for key in ['tdp', 'power', 'board_power', 'typical_board_power']:
            if key in raw:
                match = re.search(r'(\d+)\s*W', raw[key])
                if match:
                    specs['gpu_tdp'] = int(match.group(1))
                break

        # Base / core clock (MHz). TPU labels this "GPU Clock" or "Base Clock".
        for key in ['base_clock', 'gpu_clock', 'core_clock', 'base_frequency']:
            if key in raw:
                match = re.search(r'([\d,]+)\s*MHz', raw[key], re.I)
                if match:
                    specs['gpu_base_clock'] = int(match.group(1).replace(',', ''))
                break

        # Boost / game clock (MHz)
        for key in ['boost_clock', 'game_clock', 'boost']:
            if key in raw:
                match = re.search(r'([\d,]+)\s*MHz', raw[key], re.I)
                if match:
                    specs['gpu_boost_clock'] = int(match.group(1).replace(',', ''))
                break

        # Bus interface, e.g. "PCIe 4.0 x16" (column is String(20)).
        for key in ['bus_interface', 'interface']:
            if key in raw:
                match = re.search(r'(PCIe?\s*\d(?:\.\d)?\s*x\s*\d+)', raw[key], re.I)
                specs['gpu_bus_interface'] = (match.group(1) if match else raw[key]).strip()[:20]
                break

    elif component_type == 'CPU':
        # Cores
        for key in ['#_of_cores', 'cores', 'core_count', 'num_cores', 'total_cores', 'cpu_cores']:
            if key in raw:
                match = re.search(r'(\d+)', raw[key])
                if match:
                    specs['cpu_cores'] = int(match.group(1))
                break
        
        # Threads
        for key in ['#_of_threads', 'threads', 'thread_count', 'total_threads']:
            if key in raw:
                match = re.search(r'(\d+)', raw[key])
                if match:
                    specs['cpu_threads'] = int(match.group(1))
                break
        
        # Base clock
        for key in ['frequency', 'base_clock', 'clock', 'base_frequency', 'clock_speed']:
            if key in raw:
                match = re.search(r'([\d.]+)\s*GHz', raw[key])
                if match:
                    specs['cpu_base_clock'] = float(match.group(1))
                break
        
        # Boost clock
        for key in ['turbo_clock', 'boost_clock', 'turbo', 'max_turbo', 'boost']:
            if key in raw:
                match = re.search(r'([\d.]+)\s*GHz', raw[key])
                if match:
                    specs['cpu_boost_clock'] = float(match.group(1))
                break
        
        # TDP
        for key in ['tdp', 'power', 'thermal_design_power']:
            if key in raw:
                match = re.search(r'(\d+)\s*W', raw[key])
                if match:
                    specs['cpu_tdp'] = int(match.group(1))
                break
        
        # Socket
        for key in ['socket', 'package']:
            if key in raw:
                specs['cpu_socket'] = raw[key]
                break
    
    print(f"[Lookup] Parsed: {specs.get('manufacturer', '?')} {specs.get('model', '?')}")
    return specs
# =============================================================================
# Step 2b: Scrape.Do Amazon search helpers (per component type)
# =============================================================================

def search_amazon_gpu(query: str) -> Optional[Dict]:
    """Search Amazon for GPU specs (fallback for AIB cards)."""
    if not SCRAPEDO_TOKEN:
        print("[Lookup] Amazon GPU search requires Scrape.Do")
        return None
    
    try:
        google_url = f"https://www.google.com/search?q=site:amazon.com+{requests.utils.quote(query)}+graphics+card"
        print(f"[Lookup] Amazon GPU search: {google_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
        response = scrapedo_get(api_url, timeout=60)
        
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                return {'error': 'credits_exhausted'}
        
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        amazon_link = None
        for link in soup.select('a'):
            href = link.get('href', '')
            if 'amazon.com' in href and '/dp/' in href:
                match = re.search(r'(https?://(?:www\.)?amazon\.com/[^\s&"]*?/dp/[A-Z0-9]{10})', href)
                if match:
                    amazon_link = match.group(1)
                    break
        
        if not amazon_link:
            print("[Lookup] No Amazon GPU product link found")
            return None
        
        print(f"[Lookup] Amazon GPU product: {amazon_link}")

        # render=true so Amazon's JS-injected spec table is present in the HTML.
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&render=true&url={requests.utils.quote(amazon_link)}"
        detail_response = scrapedo_get(detail_api_url, timeout=60)
        
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_gpu(detail_response.text, amazon_link)
        
    except ScrapeDoBudgetExceeded:
        return {'error': 'scrapedo_budget_exhausted'}
    except Exception as e:
        print(f"[Lookup] Amazon GPU search error: {e}")
        return None


def parse_amazon_gpu(html: str, url: str) -> Optional[Dict]:
    """Parse Amazon GPU product page for specs."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': 'amazon',
        'source_url': url,
        'component_type': 'GPU',
        'raw_data': {}
    }
    
    # Get product title
    title = soup.select_one('#productTitle, #title span, .product-title-word-break')
    if title:
        full_title = title.text.strip()
        specs['model'] = clean_gpu_model_name(full_title)
        specs['raw_data']['full_title'] = full_title  # Keep original for reference
        
        # Detect manufacturer from title
        title_lower = full_title.lower()
        for mfr in ['evga', 'asus', 'msi', 'gigabyte', 'zotac', 'pny', 'sapphire', 'xfx', 'powercolor', 'asrock']:
            if mfr in title_lower:
                specs['manufacturer'] = mfr.upper()
                break
    
    raw = specs['raw_data']
    page_text = soup.get_text().lower()
    
    # Parse technical details
    for table in soup.select('#productDetails_techSpec_section_1, #productDetails_detailBullets_sections1, .prodDetTable'):
        for row in table.select('tr'):
            header = row.select_one('th, td:first-child')
            value = row.select_one('td:last-child, td:nth-child(2)')
            if header and value:
                key = header.text.strip().lower().replace(' ', '_').replace(':', '')
                val = value.text.strip()
                if key and val:
                    raw[key] = val
    
    # VRAM
    vram_match = re.search(r'(\d+)\s*gb\s*(?:gddr\d+|vram|memory)', page_text)
    if vram_match:
        specs['gpu_memory_size'] = int(vram_match.group(1)) * 1024  # Convert to MB
    
    # VRAM Type
    vram_type_match = re.search(r'(gddr\d+x?)', page_text)
    if vram_type_match:
        specs['gpu_memory_type'] = vram_type_match.group(1).upper()
    
    # Boost clock
    boost_match = re.search(r'(?:boost|game)\s*clock[:\s]*(\d{3,4})\s*mhz', page_text)
    if boost_match:
        specs['gpu_boost_clock'] = int(boost_match.group(1))
    
    # Base clock
    base_match = re.search(r'(?:base|core)\s*clock[:\s]*(\d{3,4})\s*mhz', page_text)
    if base_match:
        specs['gpu_base_clock'] = int(base_match.group(1))
    
    # TDP
    tdp_match = re.search(r'(\d{2,3})\s*w(?:att)?\s*(?:tdp|power)', page_text)
    if tdp_match:
        specs['gpu_tdp'] = int(tdp_match.group(1))
    
    if specs.get('model'):
        print(f"[Lookup] Amazon GPU parsed: {specs.get('model')}")
        return specs
    
    return None

def search_amazon_cpu(query: str) -> Optional[Dict]:
    """Search Amazon for CPU specs (fallback for server/workstation CPUs)."""
    if not SCRAPEDO_TOKEN:
        print("[Lookup] Amazon CPU search requires Scrape.Do")
        return None
    
    try:
        google_url = f"https://www.google.com/search?q=site:amazon.com+{requests.utils.quote(query)}+processor+cpu"
        print(f"[Lookup] Amazon CPU search: {google_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
        response = scrapedo_get(api_url, timeout=60)
        
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                return {'error': 'credits_exhausted'}
        
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        amazon_link = None
        for link in soup.select('a'):
            href = link.get('href', '')
            if 'amazon.com' in href and '/dp/' in href:
                match = re.search(r'(https?://(?:www\.)?amazon\.com/[^\s&"]*?/dp/[A-Z0-9]{10})', href)
                if match:
                    amazon_link = match.group(1)
                    break
        
        if not amazon_link:
            print("[Lookup] No Amazon CPU product link found")
            return None
        
        print(f"[Lookup] Amazon CPU product: {amazon_link}")
        
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(amazon_link)}"
        detail_response = scrapedo_get(detail_api_url, timeout=60)
        
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_cpu(detail_response.text, amazon_link)
        
    except ScrapeDoBudgetExceeded:
        return {'error': 'scrapedo_budget_exhausted'}
    except Exception as e:
        print(f"[Lookup] Amazon CPU search error: {e}")
        return None


def parse_amazon_cpu(html: str, url: str) -> Optional[Dict]:
    """Parse Amazon CPU product page for specs."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': 'amazon',
        'source_url': url,
        'component_type': 'CPU',
        'raw_data': {}
    }
    
    # Get product title
    title = soup.select_one('#productTitle, #title span, .product-title-word-break')
    if title:
        full_title = title.text.strip()
        specs['model'] = clean_cpu_model_name(full_title)
        specs['raw_data']['full_title'] = full_title  # Keep original for reference
        
        # Detect manufacturer from title
        title_lower = full_title.lower()
        if 'intel' in title_lower:
            specs['manufacturer'] = 'Intel'
        elif 'amd' in title_lower:
            specs['manufacturer'] = 'AMD'
    
    raw = specs['raw_data']
    page_text = soup.get_text().lower()
    
    # Parse technical details
    for table in soup.select('#productDetails_techSpec_section_1, #productDetails_detailBullets_sections1, .prodDetTable'):
        for row in table.select('tr'):
            header = row.select_one('th, td:first-child')
            value = row.select_one('td:last-child, td:nth-child(2)')
            if header and value:
                key = header.text.strip().lower().replace(' ', '_').replace(':', '')
                val = value.text.strip()
                if key and val:
                    raw[key] = val
    
    # Cores
    cores_match = re.search(r'(\d+)\s*(?:core|cores)', page_text)
    if cores_match:
        specs['cpu_cores'] = int(cores_match.group(1))
    
    # Threads
    threads_match = re.search(r'(\d+)\s*(?:thread|threads)', page_text)
    if threads_match:
        specs['cpu_threads'] = int(threads_match.group(1))
    
    # Base clock
    base_match = re.search(r'(?:base|clock)\s*(?:speed|frequency)?[:\s]*([\d.]+)\s*ghz', page_text)
    if base_match:
        specs['cpu_base_clock'] = float(base_match.group(1))
    
    # Boost/Turbo clock
    boost_match = re.search(r'(?:boost|turbo|max)\s*(?:speed|frequency|clock)?[:\s]*([\d.]+)\s*ghz', page_text)
    if boost_match:
        specs['cpu_boost_clock'] = float(boost_match.group(1))
    
    # TDP
    tdp_match = re.search(r'(\d{2,3})\s*w(?:att)?\s*(?:tdp)?', page_text)
    if tdp_match:
        specs['cpu_tdp'] = int(tdp_match.group(1))
    
    # Socket
    socket_patterns = [
        r'(lga\s*\d{4}[a-z]*)',
        r'(socket\s*[a-z]*\d+)',
        r'(am\d+)',
        r'(fclga\d{4})',
    ]
    for pattern in socket_patterns:
        socket_match = re.search(pattern, page_text)
        if socket_match:
            specs['cpu_socket'] = socket_match.group(1).upper().replace(' ', '')
            break
    
    if specs.get('model'):
        print(f"[Lookup] Amazon CPU parsed: {specs.get('model')}")
        return specs
    
    return None


def is_intel_cpu(query: str) -> bool:
    """Check if query is for an Intel CPU."""
    query_lower = query.lower()
    intel_keywords = ['i3-', 'i5-', 'i7-', 'i9-', 'xeon', 'pentium', 'celeron', 'core i', 'e3-', 'e5-', 'e7-', 'w-']
    return any(kw in query_lower for kw in intel_keywords)


def search_generic(query: str, component_type: str) -> Optional[Dict]:
    """
    Generic search for any component type using Google + various sources.
    Used as fallback when primary sources fail.
    """
    print(f"[Lookup] Trying generic search for {component_type}: {query}")
    
    # Try Amazon first (good for most components)
    result = search_amazon_generic(query, component_type)
    if result:
        if result.get('error') == 'credits_exhausted':
            return result
        if result.get('model'):
            return result
    
    return None


def search_amazon_generic(query: str, component_type: str) -> Optional[Dict]:
    """Search Amazon for any component type."""
    if not SCRAPEDO_TOKEN:
        print("[Lookup] Generic Amazon search requires Scrape.Do")
        return None
    
    try:
        # Build search query based on component type
        type_keywords = {
            'RAM': 'memory RAM',
            'Storage': 'SSD hard drive',
            'Cooler': 'CPU cooler',
            'Case': 'computer case',
            'Fan': 'case fan',
            'NIC': 'network card',
            'Sound Card': 'sound card audio',
        }
        extra_keywords = type_keywords.get(component_type, component_type)
        
        google_url = f"https://www.google.com/search?q=site:amazon.com+{requests.utils.quote(query)}+{requests.utils.quote(extra_keywords)}"
        
        print(f"[Lookup] Generic Amazon search: {google_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
        response = scrapedo_get(api_url, timeout=60)
        
        # Check for credit exhaustion
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        response.raise_for_status()
        
        # Parse Google results for Amazon product link
        soup = BeautifulSoup(response.text, 'lxml')
        
        amazon_link = None
        for link in soup.select('a'):
            href = link.get('href', '')
            if 'amazon.com' in href and '/dp/' in href:
                match = re.search(r'(https?://(?:www\.)?amazon\.com/[^\s&"]*?/dp/[A-Z0-9]{10})', href)
                if match:
                    amazon_link = match.group(1)
                    break
        
        if not amazon_link:
            print("[Lookup] No Amazon product link found")
            return None
        
        print(f"[Lookup] Amazon product: {amazon_link}")
        
        # Fetch the product page through Scrape.Do
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(amazon_link)}"
        detail_response = scrapedo_get(detail_api_url, timeout=60)
        
        # Check for credit exhaustion
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_generic(detail_response.text, amazon_link, component_type)
        
    except ScrapeDoBudgetExceeded:
        return {'error': 'scrapedo_budget_exhausted'}
    except Exception as e:
        print(f"[Lookup] Generic Amazon search error: {e}")
        return None


def parse_amazon_generic(html: str, url: str, component_type: str) -> Optional[Dict]:
    """Parse Amazon product page for generic component specs."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': 'amazon',
        'source_url': url,
        'component_type': component_type,
        'raw_data': {}
    }
    
    # Get product title
    title = soup.select_one('#productTitle, #title span, .product-title-word-break')
    if title:
        model = title.text.strip()
        specs['model'] = model
        
        # Try to detect manufacturer from title
        title_lower = model.lower()
        common_brands = [
            'corsair', 'g.skill', 'kingston', 'crucial', 'samsung', 'seagate', 
            'western digital', 'wd', 'sandisk', 'sabrent', 'noctua', 'be quiet',
            'cooler master', 'nzxt', 'fractal', 'lian li', 'phanteks', 'thermaltake',
            'arctic', 'deepcool', 'scythe', 'asus', 'msi', 'gigabyte', 'intel', 'tp-link'
        ]
        for brand in common_brands:
            if brand in title_lower:
                specs['manufacturer'] = brand.title()
                break
    
    raw = specs['raw_data']
    page_text = soup.get_text().lower()
    
    # Parse technical details table
    for table in soup.select('#productDetails_techSpec_section_1, #productDetails_detailBullets_sections1, .prodDetTable'):
        for row in table.select('tr'):
            header = row.select_one('th, td:first-child')
            value = row.select_one('td:last-child, td:nth-child(2)')
            if header and value:
                key = header.text.strip().lower().replace(' ', '_').replace(':', '')
                val = value.text.strip()
                if key and val:
                    raw[key] = val
    
    # Parse product details list
    for item in soup.select('#detailBullets_feature_div li, .detail-bullet-list span'):
        text = item.text.strip()
        if ':' in text:
            parts = text.split(':', 1)
            if len(parts) == 2:
                key = parts[0].strip().lower().replace(' ', '_')
                val = parts[1].strip()
                if key and val:
                    raw[key] = val
    
    # Component-specific parsing
    if component_type == 'RAM':
        # Capacity
        match = re.search(r'(\d+)\s*gb', page_text)
        if match:
            specs['ram_size'] = int(match.group(1))
        
        # Type
        if 'ddr5' in page_text:
            specs['ram_type'] = 'DDR5'
        elif 'ddr4' in page_text:
            specs['ram_type'] = 'DDR4'
        elif 'ddr3' in page_text:
            specs['ram_type'] = 'DDR3'
        
        # Speed
        match = re.search(r'(\d{4,5})\s*mhz', page_text)
        if match:
            specs['ram_speed'] = int(match.group(1))
        
        # CAS Latency
        match = re.search(r'cl(\d{2})', page_text)
        if match:
            specs['ram_cas_latency'] = f"CL{match.group(1)}"
    
    elif component_type == 'Storage':
        # Type
        if 'nvme' in page_text:
            specs['storage_type'] = 'NVMe SSD'
        elif 'sata' in page_text and 'ssd' in page_text:
            specs['storage_type'] = 'SATA SSD'
        elif 'hdd' in page_text or 'hard drive' in page_text:
            specs['storage_type'] = 'HDD'
        
        # Interface
        if 'pcie 5' in page_text or 'pcie gen 5' in page_text:
            specs['storage_interface'] = 'PCIe 5.0 x4'
        elif 'pcie 4' in page_text or 'pcie gen 4' in page_text:
            specs['storage_interface'] = 'PCIe 4.0 x4'
        elif 'pcie 3' in page_text or 'pcie gen 3' in page_text:
            specs['storage_interface'] = 'PCIe 3.0 x4'
        elif 'sata' in page_text:
            specs['storage_interface'] = 'SATA III'
        
        # Read/Write speeds
        match = re.search(r'read[:\s]*(\d{3,5})\s*mb', page_text)
        if match:
            specs['storage_read_speed'] = int(match.group(1))
        match = re.search(r'write[:\s]*(\d{3,5})\s*mb', page_text)
        if match:
            specs['storage_write_speed'] = int(match.group(1))
    
    elif component_type == 'Cooler':
        # Detect AIO vs Air cooler
        is_aio = any(kw in page_text for kw in ['aio', 'liquid', 'water cooling', 'all-in-one', 'all in one'])
        
        if is_aio:
            # Detect radiator size for AIOs
            radiator_sizes = [
                (420, ['420mm', '420 mm', '3x140', '3 x 140']),
                (360, ['360mm', '360 mm', '3x120', '3 x 120']),
                (280, ['280mm', '280 mm', '2x140', '2 x 140']),
                (240, ['240mm', '240 mm', '2x120', '2 x 120']),
                (140, ['140mm radiator', '1x140']),
                (120, ['120mm radiator', '1x120']),
            ]
            radiator_size = None
            for size, patterns in radiator_sizes:
                if any(p in page_text for p in patterns):
                    radiator_size = size
                    break
            
            if radiator_size:
                specs['cooler_type'] = f'AIO {radiator_size}mm'
            else:
                specs['cooler_type'] = 'AIO Liquid'
            
            # Fan size for AIOs (the individual fans)
            if '140mm' in page_text or '140 mm' in page_text:
                specs['cooler_fan_size'] = 140
            elif '120mm' in page_text or '120 mm' in page_text:
                specs['cooler_fan_size'] = 120
        else:
            # Air cooler
            specs['cooler_type'] = 'Air'
            
            # Height (important for air coolers)
            match = re.search(r'(?:height|tall)[:\s]*(\d{2,3})\s*mm', page_text)
            if match:
                specs['cooler_height'] = int(match.group(1))
            else:
                # Try to find height in raw specs
                for key in ['height', 'cooler_height', 'dimensions']:
                    if key in raw:
                        match = re.search(r'(\d{2,3})\s*mm', raw[key])
                        if match:
                            height = int(match.group(1))
                            if 100 <= height <= 180:  # Reasonable air cooler height
                                specs['cooler_height'] = height
                                break
            
            # Fan size for air coolers
            fan_sizes = [140, 120, 92]
            for size in fan_sizes:
                if f'{size}mm' in page_text or f'{size} mm' in page_text:
                    specs['cooler_fan_size'] = size
                    break
        
        # TDP rating (applies to both)
        match = re.search(r'(\d{2,3})\s*w\s*(?:tdp|cooling)', page_text)
        if match:
            specs['cooler_tdp_rating'] = int(match.group(1))
        
        # Socket support
        sockets = []
        socket_patterns = [
            ('LGA1700', ['lga1700', 'lga 1700']),
            ('LGA1200', ['lga1200', 'lga 1200']),
            ('LGA115x', ['lga1151', 'lga1150', 'lga115x']),
            ('AM5', ['am5']),
            ('AM4', ['am4']),
        ]
        for socket, patterns in socket_patterns:
            if any(p in page_text for p in patterns):
                sockets.append(socket)
        if sockets:
            specs['cooler_socket_support'] = ', '.join(sockets)
    
    elif component_type == 'Case':
        # Form factor
        if 'full tower' in page_text:
            specs['case_form_factor'] = 'Full Tower'
        elif 'mid tower' in page_text or 'mid-tower' in page_text:
            specs['case_form_factor'] = 'Mid Tower'
        elif 'mini tower' in page_text or 'micro' in page_text:
            specs['case_form_factor'] = 'Mini Tower'
        elif 'itx' in page_text:
            specs['case_form_factor'] = 'Mini-ITX'
        
        # GPU clearance
        match = re.search(r'gpu[:\s]*(?:up\s*to\s*)?(\d{3})\s*mm', page_text)
        if match:
            specs['case_max_gpu_length'] = int(match.group(1))
        
        # Cooler clearance
        match = re.search(r'cooler[:\s]*(?:up\s*to\s*)?(\d{2,3})\s*mm', page_text)
        if match:
            specs['case_max_cooler_height'] = int(match.group(1))
    
    elif component_type == 'Fan':
        # Size
        match = re.search(r'(\d{2,3})\s*mm', page_text)
        if match:
            size = int(match.group(1))
            if size in [80, 92, 120, 140, 200]:
                specs['fan_size'] = size
        
        # RPM
        match = re.search(r'(\d{3,4})\s*rpm', page_text)
        if match:
            specs['fan_rpm_max'] = int(match.group(1))
        
        # Airflow
        match = re.search(r'([\d.]+)\s*cfm', page_text)
        if match:
            specs['fan_airflow'] = float(match.group(1))
    
    if specs.get('model'):
        print(f"[Lookup] Generic Amazon parsed: {specs.get('model')}")
        # Count found specs
        spec_fields = [k for k in specs.keys() if k not in ['source', 'source_url', 'component_type', 'raw_data', 'model', 'manufacturer']]
        print(f"[Lookup] Found {len(spec_fields)} spec fields")
        return specs
    
    return None


def search_motherboard(query: str) -> Optional[Dict]:
    """Search for motherboard specs using Amazon via Scrape.Do."""
    if not SCRAPEDO_TOKEN:
        print("[Lookup] Motherboard lookup requires Scrape.Do")
        return None
    
    try:
        # Try Amazon first (more consistent structure)
        result = search_motherboard_amazon(query)
        if result:
            if result.get('error') == 'credits_exhausted':
                return result
            if result.get('model'):
                return result
        
        # No manufacturer-site fallback in v3.0 (it relied on Scrape.Do credits without much win)
        return None
        
    except ScrapeDoBudgetExceeded:
        return {'error': 'scrapedo_budget_exhausted'}
    except Exception as e:
        print(f"[Lookup] Motherboard search error: {e}")
        return None


def search_motherboard_amazon(query: str) -> Optional[Dict]:
    """Search Amazon for motherboard specs."""
    try:
        # Google site search for Amazon product page
        google_url = f"https://www.google.com/search?q=site:amazon.com+{requests.utils.quote(query)}+motherboard"
        
        print(f"[Lookup] Motherboard Amazon search: {google_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
        response = scrapedo_get(api_url, timeout=60)
        
        # Check for credit exhaustion
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        response.raise_for_status()
        
        # Parse Google results for Amazon product link
        soup = BeautifulSoup(response.text, 'lxml')
        
        amazon_link = None
        for link in soup.select('a'):
            href = link.get('href', '')
            # Look for Amazon product pages (dp = detail page)
            if 'amazon.com' in href and '/dp/' in href:
                match = re.search(r'(https?://(?:www\.)?amazon\.com/[^\s&"]*?/dp/[A-Z0-9]{10})', href)
                if match:
                    amazon_link = match.group(1)
                    break
        
        if not amazon_link:
            print("[Lookup] No Amazon product link found")
            return None
        
        print(f"[Lookup] Amazon product: {amazon_link}")
        
        # Fetch the product page through Scrape.Do
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(amazon_link)}"
        detail_response = scrapedo_get(detail_api_url, timeout=60)
        
        # Check for credit exhaustion
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_motherboard(detail_response.text, amazon_link)
        
    except ScrapeDoBudgetExceeded:
        return {'error': 'scrapedo_budget_exhausted'}
    except Exception as e:
        print(f"[Lookup] Amazon motherboard error: {e}")
        return None


def parse_amazon_motherboard(html: str, url: str) -> Optional[Dict]:
    """Parse Amazon product page for motherboard specs."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': 'amazon',
        'source_url': url,
        'component_type': 'Motherboard',
        'raw_data': {}
    }
    
    # Get product title
    title = soup.select_one('#productTitle, #title span, .product-title-word-break')
    if title:
        model = title.text.strip()
        # Clean up the title - often has extra description
        # Try to extract just the model name
        specs['model'] = model
        
        # Detect manufacturer from title
        title_lower = model.lower()
        if 'asus' in title_lower or 'rog' in title_lower or 'tuf' in title_lower:
            specs['manufacturer'] = 'ASUS'
        elif 'msi' in title_lower or 'mag' in title_lower or 'mpg' in title_lower:
            specs['manufacturer'] = 'MSI'
        elif 'gigabyte' in title_lower or 'aorus' in title_lower:
            specs['manufacturer'] = 'GIGABYTE'
        elif 'asrock' in title_lower:
            specs['manufacturer'] = 'ASRock'
        elif 'evga' in title_lower:
            specs['manufacturer'] = 'EVGA'
    
    raw = specs['raw_data']
    
    # Method 1: Parse the technical details table
    for table in soup.select('#productDetails_techSpec_section_1, #productDetails_detailBullets_sections1, .prodDetTable, #detailBullets_feature_div'):
        for row in table.select('tr'):
            header = row.select_one('th, td:first-child')
            value = row.select_one('td:last-child, td:nth-child(2)')
            if header and value:
                key = header.text.strip().lower().replace(' ', '_').replace(':', '')
                val = value.text.strip()
                if key and val:
                    raw[key] = val
    
    # Method 2: Parse the product details list
    for item in soup.select('#detailBullets_feature_div li, .detail-bullet-list span'):
        text = item.text.strip()
        if ':' in text:
            parts = text.split(':', 1)
            if len(parts) == 2:
                key = parts[0].strip().lower().replace(' ', '_')
                val = parts[1].strip()
                if key and val:
                    raw[key] = val
    
    # Method 3: Parse feature bullets for specs
    bullets_text = ''
    for bullet in soup.select('#feature-bullets li, .a-unordered-list.a-vertical li'):
        bullets_text += bullet.text.lower() + ' '
    
    # Method 4: Look for "About this item" section
    about = soup.select_one('#feature-bullets, #aplus_feature_div')
    if about:
        bullets_text += about.text.lower()
    
    page_text = soup.get_text().lower()
    
    # Extract normalized motherboard fields
    
    # Socket
    socket_patterns = [
        (r'lga\s*1700', 'LGA1700'),
        (r'lga\s*1200', 'LGA1200'),
        (r'lga\s*1151', 'LGA1151'),
        (r'lga\s*2066', 'LGA2066'),
        (r'socket\s*am5', 'AM5'),
        (r'\bam5\b', 'AM5'),
        (r'socket\s*am4', 'AM4'),
        (r'\bam4\b', 'AM4'),
        (r'strx4', 'sTRX4'),
        (r'swrx8', 'sWRX8'),
    ]
    for key in ['cpu_socket', 'socket', 'cpu_socket_type', 'processor_socket']:
        if key in raw:
            specs['mobo_socket'] = raw[key].strip()
            break
    if not specs.get('mobo_socket'):
        for pattern, socket_name in socket_patterns:
            if re.search(pattern, page_text, re.I):
                specs['mobo_socket'] = socket_name
                break
    
    # Chipset - check model/title first, then use socket context
    # Define chipsets by platform
    amd_chipsets = ['X670E', 'X670', 'B650E', 'B650', 'A620',  # AM5
                    'X570', 'B550', 'A520',  # AM4
                    'X470', 'B450', 'A320', 'X370', 'B350',  # AM4 older
                    'TRX40', 'WRX80', 'TRX50', 'WRX90']  # Threadripper
    intel_chipsets = ['Z890', 'B860', 'H810',  # LGA1851
                      'Z790', 'B760', 'H770', 'H610',  # LGA1700
                      'Z690', 'B660', 'H670',  # LGA1700
                      'Z590', 'B560', 'H570', 'H510',  # LGA1200
                      'Z490', 'B460', 'H470', 'H410',  # LGA1200
                      'Z390', 'Z370', 'B360', 'H370', 'H310']  # LGA1151
    
    # Try to get chipset from raw data first
    for key in ['chipset', 'chipset_type']:
        if key in raw:
            specs['mobo_chipset'] = raw[key].strip().upper()
            break
    
    if not specs.get('mobo_chipset'):
        # Check the MODEL NAME first (most reliable)
        model_text = specs.get('model', '').upper()
        
        # Check AMD chipsets in model (longer matches first)
        for chipset in amd_chipsets:
            if chipset in model_text:
                specs['mobo_chipset'] = chipset
                break
        
        # Check Intel chipsets in model if no AMD found
        if not specs.get('mobo_chipset'):
            for chipset in intel_chipsets:
                if chipset in model_text:
                    specs['mobo_chipset'] = chipset
                    break
    
    if not specs.get('mobo_chipset'):
        # Use socket to determine platform and search page text
        socket = specs.get('mobo_socket', '').upper()
        
        # Determine which chipsets to look for based on socket
        if socket in ['AM5']:
            search_chipsets = ['X670E', 'X670', 'B650E', 'B650', 'A620']
        elif socket in ['AM4']:
            search_chipsets = ['X570', 'B550', 'A520', 'X470', 'B450', 'A320']
        elif socket in ['LGA1851']:
            search_chipsets = ['Z890', 'B860', 'H810']
        elif socket in ['LGA1700']:
            search_chipsets = ['Z790', 'B760', 'H770', 'Z690', 'B660', 'H670', 'H610']
        elif socket in ['LGA1200']:
            search_chipsets = ['Z590', 'B560', 'H570', 'Z490', 'B460', 'H470']
        elif 'STR' in socket or 'TR' in socket:
            search_chipsets = ['TRX50', 'WRX90', 'TRX40', 'WRX80']
        else:
            # Unknown socket - search all, but prioritize by looking for platform hints
            if 'ryzen' in page_text or 'am5' in page_text or 'am4' in page_text:
                search_chipsets = amd_chipsets + intel_chipsets
            else:
                search_chipsets = intel_chipsets + amd_chipsets
        
        # Search page text for chipsets (check model area first if possible)
        for chipset in search_chipsets:
            # Use word boundary to avoid partial matches
            pattern = r'\b' + chipset + r'\b'
            if re.search(pattern, page_text, re.I):
                specs['mobo_chipset'] = chipset
                break
    
    # Form factor - check model/title first (most reliable)
    model_text = specs.get('model', '').lower()
    
    # Check model name first with explicit patterns (order matters - specific first)
    form_factor_priority = [
        ('e-atx', 'E-ATX'), ('eatx', 'E-ATX'), ('extended atx', 'E-ATX'),
        ('micro-atx', 'Micro-ATX'), ('micro atx', 'Micro-ATX'), ('matx', 'Micro-ATX'), ('m-atx', 'Micro-ATX'),
        ('mini-itx', 'Mini-ITX'), ('mini itx', 'Mini-ITX'), ('mitx', 'Mini-ITX'),
        ('mini-dtx', 'Mini-DTX'), ('dtx', 'DTX'),
    ]
    
    # First pass: check model for specific form factors
    for pattern, ff in form_factor_priority:
        if pattern in model_text:
            specs['mobo_form_factor'] = ff
            break
    
    # If not found in model and not specific, check for plain ATX in model (must be standalone)
    if not specs.get('mobo_form_factor'):
        # Use word boundary to avoid matching "micro-atx" when looking for "atx"
        if re.search(r'\batx\b', model_text) and not any(x in model_text for x in ['micro', 'mini', 'e-atx', 'eatx', 'extended']):
            specs['mobo_form_factor'] = 'ATX'
    
    # Check raw data fields
    if not specs.get('mobo_form_factor'):
        for key in ['form_factor', 'compatible_devices']:
            if key in raw:
                val_lower = raw[key].lower()
                for pattern, ff in form_factor_priority:
                    if pattern in val_lower:
                        specs['mobo_form_factor'] = ff
                        break
                if not specs.get('mobo_form_factor') and 'atx' in val_lower:
                    if not any(x in val_lower for x in ['micro', 'mini', 'extended']):
                        specs['mobo_form_factor'] = 'ATX'
                if specs.get('mobo_form_factor'):
                    break
    
    # Last resort: check page text near the product info
    if not specs.get('mobo_form_factor'):
        # Try to find form factor mentioned explicitly
        ff_match = re.search(r'form\s*factor[:\s]*(e-?atx|micro-?atx|mini-?itx|atx)', page_text, re.I)
        if ff_match:
            ff_text = ff_match.group(1).lower().replace(' ', '-')
            for pattern, ff in form_factor_priority:
                if pattern.replace(' ', '-') == ff_text.replace(' ', '-'):
                    specs['mobo_form_factor'] = ff
                    break
            if not specs.get('mobo_form_factor') and 'atx' in ff_text and 'micro' not in ff_text and 'mini' not in ff_text:
                specs['mobo_form_factor'] = 'ATX'
    
    # Memory type
    if 'ddr5' in page_text:
        specs['mobo_memory_type'] = 'DDR5'
    elif 'ddr4' in page_text:
        specs['mobo_memory_type'] = 'DDR4'
    elif 'ddr3' in page_text:
        specs['mobo_memory_type'] = 'DDR3'
    
    # Memory slots
    for key in ['memory_slots', 'ram_memory_technology', 'ram']:
        if key in raw:
            match = re.search(r'(\d+)\s*(?:x|slots?|dimm)', raw[key], re.I)
            if match:
                specs['mobo_memory_slots'] = int(match.group(1))
                break
    if not specs.get('mobo_memory_slots'):
        match = re.search(r'(\d+)\s*(?:x\s*)?(?:dimm|memory\s*slots?)', page_text)
        if match:
            slots = int(match.group(1))
            if slots <= 8:  # Sanity check
                specs['mobo_memory_slots'] = slots
    
    # Max memory
    match = re.search(r'(?:max|maximum|up\s*to)\s*(\d+)\s*gb', page_text, re.I)
    if match:
        specs['mobo_max_memory'] = int(match.group(1))
    
    # PCIe x16 slots
    match = re.search(r'(\d+)\s*(?:x\s*)?pcie?\s*(?:x\s*)?16', page_text, re.I)
    if match:
        specs['mobo_pcie_x16_slots'] = int(match.group(1))
    
    # M.2 slots
    match = re.search(r'(\d+)\s*(?:x\s*)?m\.?2', page_text, re.I)
    if match:
        specs['mobo_m2_slots'] = int(match.group(1))
    
    # SATA ports
    match = re.search(r'(\d+)\s*(?:x\s*)?sata', page_text, re.I)
    if match:
        ports = int(match.group(1))
        if ports <= 12:  # Sanity check
            specs['mobo_sata_ports'] = ports
    
    if specs.get('model'):
        print(f"[Lookup] Amazon motherboard parsed: {specs.get('model')}")
        found = [f"{k}={v}" for k, v in specs.items() if k.startswith('mobo_') and v]
        print(f"[Lookup] Found specs: {', '.join(found) if found else 'minimal'}")
        return specs
    
    return None

def search_psu(query: str) -> Optional[Dict]:
    """Search for PSU specs using Amazon via Scrape.Do."""
    if not SCRAPEDO_TOKEN:
        print("[Lookup] PSU lookup requires Scrape.Do")
        return None
    
    try:
        # Google site search for Amazon product page
        google_url = f"https://www.google.com/search?q=site:amazon.com+{requests.utils.quote(query)}+power+supply"
        
        print(f"[Lookup] PSU Amazon search: {google_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
        response = scrapedo_get(api_url, timeout=60)
        
        # Check for credit exhaustion
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        response.raise_for_status()
        
        # Parse Google results for Amazon product link
        soup = BeautifulSoup(response.text, 'lxml')
        
        amazon_link = None
        for link in soup.select('a'):
            href = link.get('href', '')
            # Look for Amazon product pages (dp = detail page)
            if 'amazon.com' in href and '/dp/' in href:
                match = re.search(r'(https?://(?:www\.)?amazon\.com/[^\s&"]*?/dp/[A-Z0-9]{10})', href)
                if match:
                    amazon_link = match.group(1)
                    break
        
        if not amazon_link:
            print("[Lookup] No Amazon product link found for PSU")
            return None
        
        print(f"[Lookup] Amazon PSU product: {amazon_link}")
        
        # Fetch the product page through Scrape.Do
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(amazon_link)}"
        detail_response = scrapedo_get(detail_api_url, timeout=60)
        
        # Check for credit exhaustion
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_psu(detail_response.text, amazon_link)
        
    except ScrapeDoBudgetExceeded:
        return {'error': 'scrapedo_budget_exhausted'}
    except Exception as e:
        print(f"[Lookup] PSU search error: {e}")
        return None


def parse_amazon_psu(html: str, url: str) -> Optional[Dict]:
    """Parse Amazon product page for PSU specs."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': 'amazon',
        'source_url': url,
        'component_type': 'PSU',
        'raw_data': {}
    }
    
    # Get product title
    title = soup.select_one('#productTitle, #title span, .product-title-word-break')
    if title:
        model = title.text.strip()
        specs['model'] = model
        
        # Detect manufacturer from title
        title_lower = model.lower()
        if 'corsair' in title_lower:
            specs['manufacturer'] = 'Corsair'
        elif 'evga' in title_lower:
            specs['manufacturer'] = 'EVGA'
        elif 'seasonic' in title_lower:
            specs['manufacturer'] = 'Seasonic'
        elif 'be quiet' in title_lower or 'bequiet' in title_lower:
            specs['manufacturer'] = 'be quiet!'
        elif 'cooler master' in title_lower:
            specs['manufacturer'] = 'Cooler Master'
        elif 'thermaltake' in title_lower:
            specs['manufacturer'] = 'Thermaltake'
        elif 'nzxt' in title_lower:
            specs['manufacturer'] = 'NZXT'
        elif 'fractal' in title_lower:
            specs['manufacturer'] = 'Fractal Design'
        elif 'silverstone' in title_lower:
            specs['manufacturer'] = 'SilverStone'
        elif 'asus' in title_lower or 'rog' in title_lower:
            specs['manufacturer'] = 'ASUS'
        elif 'msi' in title_lower:
            specs['manufacturer'] = 'MSI'
        elif 'gigabyte' in title_lower:
            specs['manufacturer'] = 'Gigabyte'
        elif 'super flower' in title_lower or 'superflower' in title_lower:
            specs['manufacturer'] = 'Super Flower'
        elif 'phanteks' in title_lower:
            specs['manufacturer'] = 'Phanteks'
    
    raw = specs['raw_data']
    
    # Method 1: Parse the technical details table
    for table in soup.select('#productDetails_techSpec_section_1, #productDetails_detailBullets_sections1, .prodDetTable'):
        for row in table.select('tr'):
            header = row.select_one('th, td:first-child')
            value = row.select_one('td:last-child, td:nth-child(2)')
            if header and value:
                key = header.text.strip().lower().replace(' ', '_').replace(':', '')
                val = value.text.strip()
                if key and val:
                    raw[key] = val
    
    # Method 2: Parse the product details list
    for item in soup.select('#detailBullets_feature_div li, .detail-bullet-list span'):
        text = item.text.strip()
        if ':' in text:
            parts = text.split(':', 1)
            if len(parts) == 2:
                key = parts[0].strip().lower().replace(' ', '_')
                val = parts[1].strip()
                if key and val:
                    raw[key] = val
    
    page_text = soup.get_text().lower()
    
    # Extract PSU specs
    
    # Wattage
    for key in ['wattage', 'power', 'output_wattage']:
        if key in raw:
            match = re.search(r'(\d+)\s*(?:w|watt)', raw[key], re.I)
            if match:
                specs['psu_wattage'] = int(match.group(1))
                break
    if not specs.get('psu_wattage'):
        match = re.search(r'(\d{3,4})\s*(?:w|watt)', page_text)
        if match:
            wattage = int(match.group(1))
            if 200 <= wattage <= 2000:  # Sanity check
                specs['psu_wattage'] = wattage
    
    # Efficiency rating
    efficiency_ratings = [
        ('80 plus titanium', '80+ Titanium'),
        ('80+ titanium', '80+ Titanium'),
        ('titanium', '80+ Titanium'),
        ('80 plus platinum', '80+ Platinum'),
        ('80+ platinum', '80+ Platinum'),
        ('platinum', '80+ Platinum'),
        ('80 plus gold', '80+ Gold'),
        ('80+ gold', '80+ Gold'),
        ('gold', '80+ Gold'),
        ('80 plus silver', '80+ Silver'),
        ('80+ silver', '80+ Silver'),
        ('80 plus bronze', '80+ Bronze'),
        ('80+ bronze', '80+ Bronze'),
        ('bronze', '80+ Bronze'),
        ('80 plus white', '80+ White'),
        ('80 plus', '80+'),
    ]
    for pattern, rating in efficiency_ratings:
        if pattern in page_text:
            specs['psu_efficiency'] = rating
            break
    
    # Modular type
    if 'fully modular' in page_text or 'full modular' in page_text:
        specs['psu_modular'] = 'Fully Modular'
    elif 'semi-modular' in page_text or 'semi modular' in page_text:
        specs['psu_modular'] = 'Semi-Modular'
    elif 'non-modular' in page_text or 'non modular' in page_text:
        specs['psu_modular'] = 'Non-Modular'
    elif 'modular' in page_text:
        # Generic modular - check context
        if 'cable' in page_text:
            specs['psu_modular'] = 'Modular'
    
    # Form factor
    if 'sfx-l' in page_text:
        specs['psu_form_factor'] = 'SFX-L'
    elif 'sfx' in page_text:
        specs['psu_form_factor'] = 'SFX'
    elif 'atx' in page_text:
        specs['psu_form_factor'] = 'ATX'
    elif 'tfx' in page_text:
        specs['psu_form_factor'] = 'TFX'
    elif 'flex' in page_text:
        specs['psu_form_factor'] = 'Flex ATX'
    
    if specs.get('model'):
        print(f"[Lookup] PSU parsed: {specs.get('model')}")
        found = [f"{k}={v}" for k, v in specs.items() if k.startswith('psu_') and v]
        print(f"[Lookup] Found specs: {', '.join(found) if found else 'minimal'}")
        return specs
    
    return None


