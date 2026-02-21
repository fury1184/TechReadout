"""
Hardware Lookup - On-demand single item scraping.
Searches multiple sources for hardware specs.

Lookup order (FREE methods first, then paid):
1. BeautifulSoup + requests (FREE - fast, lightweight)
2. Playwright (FREE - local browser, handles JS-heavy sites)
3. Scrape.Do API (PAID - proxy service, last resort)

For Intel CPUs with Intel ARK checkbox enabled:
- Uses Playwright to scrape Intel ARK (FREE, ~10-15 seconds)
"""

import os
import re
from typing import Optional, Dict

from bs4 import BeautifulSoup
import requests

# Scrape.Do API token from environment
SCRAPEDO_TOKEN = os.environ.get('SCRAPEDO_TOKEN', '')

# Playwright availability flag - set on first use
_playwright_available = None


def is_playwright_available() -> bool:
    """Check if Playwright is installed and working."""
    global _playwright_available
    if _playwright_available is not None:
        return _playwright_available
    
    try:
        from playwright.sync_api import sync_playwright
        # Quick test to see if browser is installed
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        _playwright_available = True
        print("[Lookup] Playwright is available")
    except Exception as e:
        _playwright_available = False
        print(f"[Lookup] Playwright not available: {e}")
    
    return _playwright_available


def normalize_model_name(name: str) -> str:
    """Normalize a model name for comparison."""
    if not name:
        return ''
    # Lowercase, remove spaces and common separators
    normalized = name.lower()
    normalized = re.sub(r'[\s\-_]+', '', normalized)
    # Remove common prefixes like "Intel", "AMD", "NVIDIA", etc.
    prefixes = ['intel', 'amd', 'nvidia', 'geforce', 'radeon', 'core', 'xeon', 'ryzen']
    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized


def clean_cpu_model_name(full_title: str) -> str:
    """
    Clean up CPU model name from verbose Amazon/retail titles.
    E.g., "Intel Xeon E5-2687W V4 Processor 12-Core 3.0 GHz LGA2011-3 25MB Cache 160W SR2NA"
    -> "Intel Xeon E5-2687W v4"
    """
    if not full_title:
        return full_title
    
    # Common patterns to extract core model name
    patterns = [
        # Intel Xeon patterns (E5-2687W v4, Gold 6248, etc.)
        r'(Intel\s+Xeon\s+(?:Gold|Silver|Bronze|Platinum|W[\s-]*\d+|E[357][\s-]*\d+[\w]*(?:\s*v\d+)?))',
        # Intel Core patterns (i7-9700K, i9-13900K, etc.)
        r'(Intel\s+Core\s+i[3579][\s-]*\d{4,5}[\w]*)',
        # AMD Ryzen patterns
        r'(AMD\s+Ryzen\s+(?:Threadripper\s+)?[3579]\s*\d{3,4}[\w]*)',
        # AMD EPYC patterns
        r'(AMD\s+EPYC\s+\d{4,5}[\w]*)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, full_title, re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()
            # Normalize "V4" to "v4" etc.
            cleaned = re.sub(r'\s+[Vv](\d)', r' v\1', cleaned)
            return cleaned
    
    # Fallback: Take first part before common spec keywords
    stop_words = ['processor', 'cpu', 'desktop', 'server', 'workstation', 
                  'core', 'thread', 'ghz', 'mhz', 'cache', 'socket', 'lga', 'tray', 'box', 'oem']
    words = full_title.split()
    result = []
    for word in words:
        word_lower = word.lower().rstrip(',-')
        if word_lower in stop_words:
            break
        # Stop at specs like "12-Core" or "3.0GHz"
        if re.match(r'^\d+[\-.]', word_lower) and any(s in word_lower for s in ['core', 'ghz', 'mhz', 'mb']):
            break
        result.append(word)
        if len(result) >= 6:  # Max 6 words
            break
    
    if result:
        return ' '.join(result)
    
    return full_title[:50]  # Last resort: truncate


def clean_gpu_model_name(full_title: str) -> str:
    """
    Clean up GPU model name from verbose Amazon/retail titles.
    E.g., "EVGA GeForce GTX 1660 Ti SC Ultra Gaming, 06G-P4-1667-KR, 6GB GDDR6..."
    -> "EVGA GeForce GTX 1660 Ti SC Ultra"
    """
    if not full_title:
        return full_title
    
    # Common patterns to extract core model name
    patterns = [
        # NVIDIA GeForce RTX/GTX
        r'((?:EVGA|ASUS|MSI|Gigabyte|Zotac|PNY|NVIDIA)?\s*GeForce\s+[RG]TX\s+\d{4}(?:\s*Ti)?(?:\s*Super)?)',
        # AMD Radeon
        r'((?:Sapphire|XFX|PowerColor|ASRock|AMD)?\s*Radeon\s+RX\s+\d{4}(?:\s*XT)?(?:\s*XTX)?)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, full_title, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    # Fallback: Take first part before common spec keywords
    stop_words = ['graphics', 'card', 'video', 'gddr', 'memory', 'pci', 'pcie', 'hdmi', 'displayport']
    words = full_title.split()
    result = []
    for word in words:
        word_lower = word.lower().rstrip(',-')
        if word_lower in stop_words or word_lower.startswith('gddr'):
            break
        # Stop at part numbers like "06G-P4-1667-KR"
        if re.match(r'^\d+[A-Z]-', word):
            break
        result.append(word)
        if len(result) >= 8:  # Max 8 words
            break
    
    if result:
        return ' '.join(result)
    
    return full_title[:60]  # Last resort: truncate


def extract_key_identifiers(query: str) -> list:
    """Extract key identifying parts from a query (version numbers, model numbers)."""
    query_lower = query.lower()
    identifiers = []
    
    # Find version indicators like v3, v4, V2, etc.
    versions = re.findall(r'v\d+', query_lower)
    identifiers.extend(versions)
    
    # Find generation/revision numbers
    revisions = re.findall(r'(?:gen|rev|r)\s*\d+', query_lower)
    identifiers.extend(revisions)
    
    # Find GPU suffixes that are significant (Ti, Super, XT, etc.)
    if re.search(r'\bti\b', query_lower):
        identifiers.append('ti')
    if 'super' in query_lower:
        identifiers.append('super')
    if re.search(r'\bxt\b', query_lower):
        identifiers.append('xt')
    if re.search(r'\bxl\b', query_lower):
        identifiers.append('xl')
    
    # Find CPU suffixes (K, F, X, W, etc.) - only at end of model number
    cpu_suffix = re.search(r'\d+([kfxwsue]+)(?:\s|$|v)', query_lower)
    if cpu_suffix:
        identifiers.append(cpu_suffix.group(1))
    
    return identifiers


def normalize_gpu_query(query: str) -> str:
    """
    Normalize GPU query to reference card name for TechPowerUp search.
    Strips AIB partner names and model-specific suffixes.
    E.g., "EVGA GTX 1660 Ti SC Ultra" -> "GTX 1660 Ti"
    """
    query_lower = query.lower()
    
    # AIB partner names to strip
    aib_partners = [
        'evga', 'asus', 'msi', 'gigabyte', 'zotac', 'pny', 'palit', 'gainward',
        'xfx', 'sapphire', 'powercolor', 'asrock', 'biostar', 'colorful', 'galax',
        'inno3d', 'kfa2', 'kuroutoshikou', 'leadtek', 'manli', 'maxsun', 'nvidia',
        'amd', 'intel', 'founders edition'
    ]
    
    # AIB model-specific suffixes to strip
    aib_suffixes = [
        'ftw3', 'ftw', 'xc3', 'xc', 'sc ultra', 'sc gaming', 'sc', 'black gaming',
        'rog strix', 'strix', 'tuf gaming', 'tuf', 'dual', 'phoenix', 'proart',
        'gaming x trio', 'gaming x', 'gaming z trio', 'gaming z', 'suprim x', 'suprim',
        'ventus', 'mech', 'sea hawk', 'aero',
        'aorus master', 'aorus elite', 'aorus', 'eagle', 'gaming oc', 'windforce',
        'amp extreme', 'amp holo', 'amp', 'twin edge',
        'xlr8', 'verto', 'uprising', 'epic-x',
        'gamerock', 'jetstream', 'phoenix',
        'nitro+', 'nitro', 'pulse', 'toxic', 'vapor-x',
        'red devil', 'red dragon', 'hellhound', 'fighter',
        'challenger', 'phantom gaming', 'taichi',
        'black edition', 'black', 'white', 'oc edition', 'oc', 'gaming',
        'ultra', 'edition'
    ]
    
    result = query_lower
    
    # Strip AIB partner names
    for partner in aib_partners:
        result = re.sub(r'\b' + re.escape(partner) + r'\b', '', result, flags=re.I)
    
    # Strip AIB suffixes (sort by length to match longer ones first)
    for suffix in sorted(aib_suffixes, key=len, reverse=True):
        result = re.sub(r'\b' + re.escape(suffix) + r'\b', '', result, flags=re.I)
    
    # Clean up whitespace
    result = ' '.join(result.split())
    
    # If we stripped too much, return original
    if len(result) < 5:
        return query
    
    print(f"[Lookup] Normalized GPU query: '{query}' -> '{result}'")
    return result


def validate_result(query: str, result_model: str, component_type: str = None) -> bool:
    """
    Validate that the result actually matches the query.
    Returns True if the result is a valid match, False if it's a different model.
    """
    if not result_model:
        return False
    
    # For GPUs, use normalized query for validation since TechPowerUp has reference names
    if component_type == 'GPU':
        query = normalize_gpu_query(query)
    
    query_norm = normalize_model_name(query)
    result_norm = normalize_model_name(result_model)
    query_lower = query.lower()
    result_lower = result_model.lower()
    
    # Check if key identifiers from query exist in result
    key_ids = extract_key_identifiers(query)
    
    for key_id in key_ids:
        key_id_clean = key_id.replace(' ', '')
        if key_id_clean not in result_norm and key_id_clean not in result_lower:
            print(f"[Lookup] Validation failed: '{key_id}' not found in result '{result_model}'")
            return False
    
    # Also check that major model number matches
    # E.g., searching for "2687" should match "E5-2687W v4" but not "E5-2680 v4"
    query_nums = re.findall(r'\d{3,}', query)
    result_nums = re.findall(r'\d{3,}', result_model)
    
    for num in query_nums:
        if num not in result_model and num not in ''.join(result_nums):
            print(f"[Lookup] Validation failed: model number '{num}' not found in result '{result_model}'")
            return False
    
    # Check that result doesn't have extra significant identifiers not in query
    result_ids = extract_key_identifiers(result_model)
    query_ids_clean = [k.replace(' ', '').lower() for k in key_ids]
    
    for rid in result_ids:
        rid_lower = rid.lower()
        if rid_lower not in query_ids_clean:
            # Result has identifier not in query - might be wrong model
            # E.g., query "RTX 4070" returning "RTX 4070 Ti"
            # E.g., query "i7-9700" returning "i7-9700K"
            print(f"[Lookup] Validation failed: result has '{rid}' not in query")
            return False
    
    # Additional check for CPU suffix mismatch
    # Query "i7-9700" should NOT match "i7-9700K"
    # Query "i7-9700K" should NOT match "i7-9700" or "i7-9700F"
    if component_type == 'CPU':
        # Extract model number with optional suffix from both
        query_cpu_match = re.search(r'(\d{4,5})([kfxwsue]*)\b', query_lower)
        result_cpu_match = re.search(r'(\d{4,5})([kfxwsue]*)\b', result_lower)
        
        if query_cpu_match and result_cpu_match:
            query_model_num = query_cpu_match.group(1)
            query_suffix = query_cpu_match.group(2)
            result_model_num = result_cpu_match.group(1)
            result_suffix = result_cpu_match.group(2)
            
            # Model numbers must match
            if query_model_num != result_model_num:
                print(f"[Lookup] Validation failed: model number mismatch {query_model_num} vs {result_model_num}")
                return False
            
            # Suffixes must match exactly (both empty, or both same)
            if query_suffix != result_suffix:
                print(f"[Lookup] Validation failed: suffix mismatch '{query_suffix}' vs '{result_suffix}' (query: {query}, result: {result_model})")
                return False
    
    print(f"[Lookup] Validation passed: '{query}' matches '{result_model}'")
    return True


def lookup_hardware(query: str, component_type: str = 'auto', lite_mode: bool = False, use_intel_ark: bool = False) -> Optional[Dict]:
    """
    Search for a specific hardware item and return its specs.
    
    Args:
        query: Model name to search for (e.g., "RTX 4070", "i7-9700K", "ROG STRIX B550-F")
        component_type: 'GPU', 'CPU', 'Motherboard', or 'auto' to detect
        lite_mode: If True, only try primary source (saves credits but may miss some results)
        use_intel_ark: If True, use Intel ARK for Intel CPUs (costs ~6 extra credits)
        
    Returns:
        Dict with specs if found, None otherwise
    """
    # Auto-detect component type from query
    if component_type == 'auto':
        component_type = detect_component_type(query)
    
    print(f"[Lookup] Searching for '{query}' as {component_type}" + (" (LITE MODE)" if lite_mode else "") + (" (Intel ARK enabled)" if use_intel_ark else ""))
    
    # Handle motherboard lookups separately (different sources)
    if component_type == 'Motherboard':
        print("[Lookup] Motherboard detected, searching Amazon + manufacturer sites...")
        result = search_motherboard(query)
        if result:
            if result.get('error') == 'credits_exhausted':
                print("[Lookup] Scrape.Do credits exhausted")
                return {'error': 'credits_exhausted'}
            if result.get('model'):
                if validate_result(query, result.get('model'), 'Motherboard'):
                    print("[Lookup] Success with motherboard search")
                    return result
                else:
                    print("[Lookup] Motherboard result didn't match query")
        print("[Lookup] Motherboard lookup failed")
        return None
    
    # Handle PSU lookups (Amazon)
    if component_type == 'PSU':
        print("[Lookup] PSU detected, searching Amazon...")
        result = search_psu(query)
        if result:
            if result.get('error') == 'credits_exhausted':
                print("[Lookup] Scrape.Do credits exhausted")
                return {'error': 'credits_exhausted'}
            if result.get('model'):
                if validate_result(query, result.get('model'), 'PSU'):
                    print("[Lookup] Success with PSU search")
                    return result
                else:
                    print("[Lookup] PSU result didn't match query")
        print("[Lookup] PSU lookup failed")
        return None
    
    # Handle other component types with generic Amazon search
    if component_type not in ['CPU', 'GPU']:
        print(f"[Lookup] {component_type} detected, trying generic Amazon search...")
        result = search_generic(query, component_type)
        if result:
            if result.get('error') == 'credits_exhausted':
                print("[Lookup] Scrape.Do credits exhausted")
                return {'error': 'credits_exhausted'}
            if result.get('model'):
                if validate_result(query, result.get('model'), component_type):
                    print(f"[Lookup] Success with generic search for {component_type}")
                    return result
                else:
                    print("[Lookup] Generic result didn't match query")
        print(f"[Lookup] {component_type} lookup failed")
        return None
    
    # For Intel CPUs with Intel ARK enabled, use Playwright (FREE) instead of Scrape.Do
    if component_type == 'CPU' and is_intel_cpu(query) and use_intel_ark:
        print("[Lookup] Intel ARK enabled, trying Playwright (FREE)...")
        result = search_intel_ark_playwright(query)
        if result and result.get('model'):
            # Intel ARK is authoritative - be more lenient with validation
            query_nums = re.findall(r'\d{4}', query)  # e.g., "2687" from E5-2687W
            result_model = result.get('model', '')
            if query_nums and any(num in result_model for num in query_nums):
                print(f"[Lookup] Success with Intel ARK (Playwright): {result_model}")
                return result
            elif not query_nums:
                print(f"[Lookup] Success with Intel ARK (Playwright, no model check): {result_model}")
                return result
            else:
                print(f"[Lookup] Intel ARK result '{result_model}' didn't match query numbers {query_nums}")
    
    # For GPUs with AIB partner names, skip manufacturer sites in lite_mode (they use credits)
    if component_type == 'GPU' and not lite_mode:
        manufacturer = detect_gpu_manufacturer(query)
        if manufacturer:
            print(f"[Lookup] Detected GPU manufacturer: {manufacturer}, trying their site first...")
            result = search_gpu_manufacturer(query)
            if result:
                if result.get('error') == 'credits_exhausted':
                    print("[Lookup] Scrape.Do credits exhausted")
                    return {'error': 'credits_exhausted'}
                if result.get('model'):
                    if validate_result(query, result.get('model'), 'GPU'):
                        print("[Lookup] Success with GPU manufacturer site")
                        return result
                    else:
                        print("[Lookup] GPU manufacturer result didn't match query, trying TechPowerUp...")
            print(f"[Lookup] {manufacturer} site lookup failed, falling back to TechPowerUp...")
    
    # =========================================================================
    # FREE METHODS (no API credits)
    # =========================================================================
    
    # Try 1: Direct TechPowerUp URL (BeautifulSoup) - FREE, instant
    print("[Lookup] Method 1: BeautifulSoup (FREE, fast)...")
    result = search_with_requests(query, component_type)
    if result and result.get('model'):
        if validate_result(query, result.get('model'), component_type):
            print("[Lookup] Success with BeautifulSoup")
            return result
        else:
            print("[Lookup] BeautifulSoup result didn't match query, skipping")
    
    # Try 2: Playwright browser (FREE but slower, ~10-15 seconds)
    print("[Lookup] Method 2: Playwright (FREE, slower ~10-15s)...")
    result = search_with_playwright(query, component_type)
    if result and result.get('model'):
        if validate_result(query, result.get('model'), component_type):
            print("[Lookup] Success with Playwright")
            return result
        else:
            print("[Lookup] Playwright result didn't match query, skipping")
    
    # In lite mode, stop here (only free methods)
    if lite_mode:
        print("[Lookup] Lite mode: Stopping after free methods")
        return None
    
    # =========================================================================
    # PAID METHODS (Scrape.Do API credits) - LAST RESORT
    # =========================================================================
    
    if SCRAPEDO_TOKEN:
        # Try 3: Scrape.Do direct URL (~1 credit)
        print("[Lookup] Method 3: Scrape.Do direct (~1 credit)...")
        result = search_with_scrapedo(query, component_type)
        if result:
            if result.get('error') == 'credits_exhausted':
                print("[Lookup] Scrape.Do credits exhausted")
                return {'error': 'credits_exhausted'}
            if result.get('model'):
                if validate_result(query, result.get('model'), component_type):
                    print("[Lookup] Success with Scrape.Do")
                    return result
                else:
                    print("[Lookup] Scrape.Do result didn't match query, skipping")
        
        # Try 4: TechPowerUp site search via Scrape.Do (~2 credits)
        print("[Lookup] Method 4: TechPowerUp site search (~2 credits)...")
        result = search_tpu_via_site_search(query, component_type)
        if result:
            if result.get('error') == 'credits_exhausted':
                print("[Lookup] Scrape.Do credits exhausted")
                return {'error': 'credits_exhausted'}
            if result.get('model'):
                if validate_result(query, result.get('model'), component_type):
                    print("[Lookup] Success with TechPowerUp site search")
                    return result
                else:
                    print("[Lookup] TechPowerUp site search result didn't match query, skipping")
    else:
        print("[Lookup] Scrape.Do not configured (set SCRAPEDO_TOKEN)")
    
    print("[Lookup] All primary methods failed")
    
    # Last resort fallbacks use additional credits - skip in lite_mode
    if lite_mode:
        print("[Lookup] Lite mode: Skipping Amazon fallbacks to save credits")
        return None
    
    # Last resort for GPUs: try Amazon (has AIB-specific listings)
    if component_type == 'GPU':
        print("[Lookup] Trying Amazon as last resort for GPU (~2 credits)...")
        result = search_amazon_gpu(query)
        if result:
            if result.get('error') == 'credits_exhausted':
                print("[Lookup] Scrape.Do credits exhausted")
                return {'error': 'credits_exhausted'}
            if result.get('model'):
                print("[Lookup] Success with Amazon GPU search")
                return result
    
    # Last resort for CPUs: try Amazon
    if component_type == 'CPU':
        print("[Lookup] Trying Amazon as last resort for CPU (~2 credits)...")
        result = search_amazon_cpu(query)
        if result:
            if result.get('error') == 'credits_exhausted':
                print("[Lookup] Scrape.Do credits exhausted")
                return {'error': 'credits_exhausted'}
            if result.get('model'):
                print("[Lookup] Success with Amazon CPU search")
                return result
    
    return None


def search_amazon_gpu(query: str) -> Optional[Dict]:
    """Search Amazon for GPU specs (fallback for AIB cards)."""
    if not SCRAPEDO_TOKEN:
        print("[Lookup] Amazon GPU search requires Scrape.Do")
        return None
    
    try:
        google_url = f"https://www.google.com/search?q=site:amazon.com+{requests.utils.quote(query)}+graphics+card"
        print(f"[Lookup] Amazon GPU search: {google_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
        response = requests.get(api_url, timeout=60)
        
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
        
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(amazon_link)}"
        detail_response = requests.get(detail_api_url, timeout=60)
        
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_gpu(detail_response.text, amazon_link)
        
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
        response = requests.get(api_url, timeout=60)
        
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
        detail_response = requests.get(detail_api_url, timeout=60)
        
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_cpu(detail_response.text, amazon_link)
        
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


# =============================================================================
# Generic/Fallback Search (for any component type)
# =============================================================================

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
        response = requests.get(api_url, timeout=60)
        
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
        detail_response = requests.get(detail_api_url, timeout=60)
        
        # Check for credit exhaustion
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_generic(detail_response.text, amazon_link, component_type)
        
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


def is_intel_cpu(query: str) -> bool:
    """Check if query is for an Intel CPU."""
    query_lower = query.lower()
    intel_keywords = ['i3-', 'i5-', 'i7-', 'i9-', 'xeon', 'pentium', 'celeron', 'core i', 'e3-', 'e5-', 'e7-', 'w-']
    return any(kw in query_lower for kw in intel_keywords)


# =============================================================================
# GPU Manufacturer Lookup
# =============================================================================

def detect_gpu_manufacturer(query: str) -> Optional[str]:
    """Detect GPU AIB manufacturer from query."""
    query_lower = query.lower()
    
    # XFX patterns
    if any(kw in query_lower for kw in ['xfx', 'speedster', 'merc', 'swft', 'qick']):
        return 'xfx'
    
    # EVGA patterns
    if any(kw in query_lower for kw in ['evga', 'ftw', 'xc3', 'kingpin']):
        return 'evga'
    
    # ASUS patterns
    if any(kw in query_lower for kw in ['asus', 'rog ', 'strix', 'tuf ', 'dual', 'proart']):
        return 'asus'
    
    # MSI patterns
    if any(kw in query_lower for kw in ['msi', 'gaming x', 'ventus', 'suprim', 'mech']):
        return 'msi'
    
    # Gigabyte patterns
    if any(kw in query_lower for kw in ['gigabyte', 'aorus', 'eagle', 'gaming oc', 'windforce']):
        return 'gigabyte'
    
    # Sapphire patterns (AMD only)
    if any(kw in query_lower for kw in ['sapphire', 'nitro', 'pulse', 'toxic']):
        return 'sapphire'
    
    # PowerColor patterns (AMD only)
    if any(kw in query_lower for kw in ['powercolor', 'red devil', 'red dragon', 'hellhound', 'fighter']):
        return 'powercolor'
    
    # ASRock patterns
    if any(kw in query_lower for kw in ['asrock', 'phantom', 'taichi', 'challenger']):
        return 'asrock'
    
    # Zotac patterns
    if any(kw in query_lower for kw in ['zotac', 'amp ', 'twin edge', 'trinity']):
        return 'zotac'
    
    # PNY patterns
    if any(kw in query_lower for kw in ['pny', 'xlr8', 'uprising', 'verto']):
        return 'pny'
    
    # Gainward/Palit patterns
    if any(kw in query_lower for kw in ['gainward', 'palit', 'gamerock', 'phantom']):
        return 'gainward'
    
    return None


def get_gpu_manufacturer_search_site(manufacturer: str) -> Optional[str]:
    """Get search domain for GPU manufacturer."""
    sites = {
        'xfx': 'xfxforce.com',
        'evga': 'evga.com',
        'asus': 'asus.com',
        'msi': 'msi.com',
        'gigabyte': 'gigabyte.com',
        'sapphire': 'sapphiretech.com',
        'powercolor': 'powercolor.com',
        'asrock': 'asrock.com',
        'zotac': 'zotac.com',
        'pny': 'pny.com',
        'gainward': 'gainward.com',
    }
    return sites.get(manufacturer)


def search_gpu_manufacturer(query: str) -> Optional[Dict]:
    """Search GPU manufacturer sites for specs."""
    manufacturer = detect_gpu_manufacturer(query)
    
    if not manufacturer:
        print("[Lookup] Could not detect GPU manufacturer from query")
        return None
    
    site = get_gpu_manufacturer_search_site(manufacturer)
    if not site:
        print(f"[Lookup] No site configured for manufacturer: {manufacturer}")
        return None
    
    print(f"[Lookup] Detected {manufacturer.upper()} GPU, searching {site}...")
    
    try:
        # Google site search for the product
        google_url = f"https://www.google.com/search?q=site:{site}+{requests.utils.quote(query)}"
        
        # Try direct request first (some manufacturer sites don't block)
        try:
            response = requests.get(google_url, headers=get_headers(), timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'lxml')
        except:
            # Fall back to Scrape.Do if direct request fails
            if not SCRAPEDO_TOKEN:
                print("[Lookup] Direct request failed and Scrape.Do not configured")
                return None
            
            api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
            response = requests.get(api_url, timeout=60)
            
            if response.status_code in [402, 403]:
                error_text = response.text.lower()
                if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                    return {'error': 'credits_exhausted'}
            
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'lxml')
        
        # Find manufacturer product page link
        product_link = None
        for link in soup.select('a'):
            href = link.get('href', '')
            if site in href and ('shop' in href or 'product' in href or 'gpu' in href or 'graphics' in href):
                match = re.search(r'(https?://[^\s&"]+' + re.escape(site) + r'[^\s&"]*)', href)
                if match:
                    product_link = match.group(1)
                    break
        
        # Try any link to the site if no product-specific link found
        if not product_link:
            for link in soup.select('a'):
                href = link.get('href', '')
                if site in href:
                    match = re.search(r'(https?://[^\s&"]+' + re.escape(site) + r'[^\s&"]*)', href)
                    if match:
                        product_link = match.group(1)
                        break
        
        if not product_link:
            print(f"[Lookup] No {manufacturer} product link found in Google results")
            return None
        
        print(f"[Lookup] Found product page: {product_link}")
        
        # Fetch the product page
        try:
            detail_response = requests.get(product_link, headers=get_headers(), timeout=30)
            detail_response.raise_for_status()
        except:
            # Fall back to Scrape.Do
            if not SCRAPEDO_TOKEN:
                return None
            
            detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(product_link)}"
            detail_response = requests.get(detail_api_url, timeout=60)
            
            if detail_response.status_code in [402, 403]:
                error_text = detail_response.text.lower()
                if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                    return {'error': 'credits_exhausted'}
            
            detail_response.raise_for_status()
        
        return parse_gpu_manufacturer_page(detail_response.text, product_link, manufacturer)
        
    except Exception as e:
        print(f"[Lookup] GPU manufacturer search error: {e}")
        return None


def parse_gpu_manufacturer_page(html: str, url: str, manufacturer: str) -> Optional[Dict]:
    """Parse GPU manufacturer product page for specs."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': f'{manufacturer}_website',
        'source_url': url,
        'component_type': 'GPU',
        'manufacturer': manufacturer.upper() if manufacturer else None,
        'raw_data': {}
    }
    
    # Get product title
    title_selectors = [
        'h1.product-name', 'h1.product-title', '.product-name h1',
        'h1[class*="product"]', 'h1[class*="title"]', 'h1',
        '.product-header h1', '#product-name', 'h2.product-name'
    ]
    for selector in title_selectors:
        title = soup.select_one(selector)
        if title:
            model = title.text.strip()
            # Clean up
            model = re.sub(r'\s+', ' ', model).strip()
            if model and len(model) > 5:
                specs['model'] = model
                break
    
    page_text = soup.get_text()
    page_text_lower = page_text.lower()
    raw = specs['raw_data']
    
    # Method 1: Parse spec tables
    for table in soup.select('table'):
        for row in table.select('tr'):
            cells = row.select('td, th')
            if len(cells) >= 2:
                key = cells[0].text.strip().lower().replace(' ', '_').replace(':', '')
                value = cells[1].text.strip()
                if key and value:
                    raw[key] = value
    
    # Method 2: Parse definition lists and spec sections
    for dl in soup.select('dl, .spec-list, .specs, [class*="specification"]'):
        text = dl.text
        # Look for key: value patterns
        matches = re.findall(r'([A-Za-z][A-Za-z\s]+?)[:：]\s*([^\n]+)', text)
        for key, value in matches:
            key = key.strip().lower().replace(' ', '_')
            value = value.strip()
            if key and value:
                raw[key] = value
    
    # Extract GPU specs from raw data and page text
    
    # VRAM Size
    vram_patterns = [
        r'(\d+)\s*gb\s*(?:gddr|memory)',
        r'memory\s*size[:\s]*(\d+)\s*gb',
        r'(\d+)\s*gb\s*gddr\d',
    ]
    for key in ['memory_size', 'vram', 'memory', 'gddr6_memory']:
        if key in raw:
            match = re.search(r'(\d+)\s*gb', raw[key], re.I)
            if match:
                specs['gpu_memory_size'] = int(match.group(1)) * 1024  # Convert to MB
                break
    if not specs.get('gpu_memory_size'):
        for pattern in vram_patterns:
            match = re.search(pattern, page_text_lower)
            if match:
                specs['gpu_memory_size'] = int(match.group(1)) * 1024
                break
    
    # VRAM Type
    vram_types = ['GDDR6X', 'GDDR6', 'GDDR5X', 'GDDR5', 'HBM2e', 'HBM2', 'HBM3']
    for key in ['memory_type', 'vram_type', 'memory']:
        if key in raw:
            for vt in vram_types:
                if vt.lower() in raw[key].lower():
                    specs['gpu_memory_type'] = vt
                    break
            if specs.get('gpu_memory_type'):
                break
    if not specs.get('gpu_memory_type'):
        for vt in vram_types:
            if vt.lower() in page_text_lower:
                specs['gpu_memory_type'] = vt
                break
    
    # Base Clock
    base_patterns = [
        r'base\s*clock[:\s]*(?:up\s*to\s*)?(\d+)\s*mhz',
        r'(\d{4})\s*mhz\s*base',
    ]
    for key in ['base_clock', 'gpu_clock', 'clock_speed']:
        if key in raw:
            match = re.search(r'(\d{3,4})\s*mhz', raw[key], re.I)
            if match:
                specs['gpu_base_clock'] = int(match.group(1))
                break
    if not specs.get('gpu_base_clock'):
        for pattern in base_patterns:
            match = re.search(pattern, page_text_lower)
            if match:
                specs['gpu_base_clock'] = int(match.group(1))
                break
    
    # Boost Clock
    boost_patterns = [
        r'boost\s*clock[:\s]*(?:up\s*to\s*)?(\d+)\s*mhz',
        r'(\d{4})\s*mhz\s*boost',
        r'game\s*clock[:\s]*(?:up\s*to\s*)?(\d+)\s*mhz',
    ]
    for key in ['boost_clock', 'oc_clock', 'game_clock']:
        if key in raw:
            match = re.search(r'(\d{3,4})\s*mhz', raw[key], re.I)
            if match:
                specs['gpu_boost_clock'] = int(match.group(1))
                break
    if not specs.get('gpu_boost_clock'):
        for pattern in boost_patterns:
            match = re.search(pattern, page_text_lower)
            if match:
                specs['gpu_boost_clock'] = int(match.group(1))
                break
    
    # TDP
    tdp_patterns = [
        r'tdp[:\s]*(\d+)\s*w',
        r'power[:\s]*(\d+)\s*w',
        r'(\d{2,3})\s*w\s*(?:tdp|power)',
        r'recommended\s*psu[:\s]*(\d+)\s*w',
    ]
    for key in ['tdp', 'power', 'power_consumption', 'tgp']:
        if key in raw:
            match = re.search(r'(\d+)\s*w', raw[key], re.I)
            if match:
                tdp = int(match.group(1))
                # If it's PSU recommendation (like 850W), it's not TDP
                if tdp < 600:
                    specs['gpu_tdp'] = tdp
                    break
    if not specs.get('gpu_tdp'):
        for pattern in tdp_patterns:
            match = re.search(pattern, page_text_lower)
            if match:
                tdp = int(match.group(1))
                if tdp < 600:  # Skip PSU recommendations
                    specs['gpu_tdp'] = tdp
                    break
    
    # Memory Bus Width
    bus_patterns = [
        r'memory\s*bus[:\s]*(\d+)[\s-]*bit',
        r'(\d+)[\s-]*bit\s*(?:memory|bus|interface)',
    ]
    for pattern in bus_patterns:
        match = re.search(pattern, page_text_lower)
        if match:
            raw['memory_bus'] = f"{match.group(1)}-bit"
            break
    
    # Stream Processors / CUDA Cores
    sp_patterns = [
        r'stream\s*processors?[:\s]*(\d+)',
        r'cuda\s*cores?[:\s]*(\d+)',
        r'shaders?[:\s]*(\d+)',
        r'(\d{3,5})\s*(?:stream\s*processors?|cuda\s*cores?)',
    ]
    for pattern in sp_patterns:
        match = re.search(pattern, page_text_lower)
        if match:
            raw['stream_processors'] = match.group(1)
            break
    
    if specs.get('model'):
        print(f"[Lookup] GPU manufacturer parsed: {specs.get('model')}")
        found = [f"{k}={v}" for k, v in specs.items() if k.startswith('gpu_') and v]
        print(f"[Lookup] Found specs: {', '.join(found) if found else 'minimal'}")
        return specs
    
    return None


def search_intel_ark(query: str) -> Optional[Dict]:
    """Search Intel ARK for CPU specs using Scrape.Do."""
    if not SCRAPEDO_TOKEN:
        print("[Lookup] Intel ARK requires Scrape.Do (Intel blocks direct requests)")
        return None
    
    try:
        # Use Google site search for Intel ARK (more reliable than direct search)
        google_url = f"https://www.google.com/search?q=site:ark.intel.com+{requests.utils.quote(query)}+specifications"
        
        print(f"[Lookup] Intel ARK via Google: {google_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
        response = requests.get(api_url, timeout=60)
        
        # Check for credit exhaustion
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        response.raise_for_status()
        
        # Parse Google results for Intel ARK link
        soup = BeautifulSoup(response.text, 'lxml')
        html_text = response.text
        
        ark_link = None
        
        # Method 1: Look for direct links
        for link in soup.select('a'):
            href = link.get('href', '')
            
            if 'ark.intel.com' not in href:
                continue
            
            # Extract clean URL from Google redirect
            if '/url?q=' in href:
                href = href.split('/url?q=')[1].split('&')[0]
                href = requests.utils.unquote(href)
            elif href.startswith('/url?'):
                continue  # Skip malformed redirects
            
            # Look for product pages
            if 'ark.intel.com' in href and ('/products/' in href or '/ark/products/' in href):
                ark_link = href
                print(f"[Lookup] Found ARK link (method 1): {ark_link}")
                break
        
        # Method 2: Search in raw HTML with regex
        if not ark_link:
            patterns = [
                r'(https?://ark\.intel\.com/content/www/[a-z]{2}/[a-z]{2}/ark/products/\d+/[^"&\s]+)',
                r'(https?://ark\.intel\.com/[^"&\s]*products/\d+[^"&\s]*)',
            ]
            for pattern in patterns:
                match = re.search(pattern, html_text)
                if match:
                    ark_link = requests.utils.unquote(match.group(1))
                    print(f"[Lookup] Found ARK link (method 2): {ark_link}")
                    break
        
        if not ark_link:
            print("[Lookup] No Intel ARK product link found in Google results")
            return None
        
        print(f"[Lookup] Intel ARK detail: {ark_link}")
        
        # Fetch the detail page through Scrape.Do with JS rendering
        # Intel ARK is JavaScript-heavy, needs render=true (costs 5 credits instead of 1)
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&render=true&url={requests.utils.quote(ark_link)}"
        detail_response = requests.get(detail_api_url, timeout=90)  # Longer timeout for rendering
        
        # Check for credit exhaustion
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        result = parse_intel_ark_page(detail_response.text, ark_link)
        if result and result.get('model'):
            return result
        
        print("[Lookup] Intel ARK page parsing failed")
        return None
        
    except Exception as e:
        print(f"[Lookup] Intel ARK error: {e}")
        return None


def parse_intel_ark_page(html: str, url: str) -> Optional[Dict]:
    """Parse Intel ARK product page."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': 'intel_ark',
        'source_url': url,
        'component_type': 'CPU',
        'manufacturer': 'Intel',
        'raw_data': {}
    }
    
    # Get product name - try multiple selectors
    title_selectors = [
        'h1.product-title',
        '.product-family-title-text', 
        '[data-wap_ref="defined-title"]',
        '.ProductName',
        'h1.ark-headline',
        'h1'
    ]
    for selector in title_selectors:
        title = soup.select_one(selector)
        if title and title.text.strip():
            model_text = title.text.strip()
            # Clean up the model name
            specs['model'] = clean_cpu_model_name(model_text)
            specs['raw_data']['full_title'] = model_text
            break
    
    # Parse specs table - try multiple table structures
    table_selectors = [
        '.ark-product-specs tr',
        '.specs-section tr', 
        '.tech-section tr',
        'table.specs tr',
        '[class*="specification"] tr',
        '.product-specs tr'
    ]
    for selector in table_selectors:
        rows = soup.select(selector)
        if rows:
            for row in rows:
                label = row.select_one('td:first-child, th, .label, [class*="label"]')
                value = row.select_one('td:last-child, .value, [class*="value"]')
                
                if label and value:
                    key = label.text.strip().lower().replace(' ', '_').replace('#', 'num').replace(':', '')
                    val = value.text.strip()
                    if key and val:
                        specs['raw_data'][key] = val
            break
    
    # Also try data attributes (ARK sometimes uses these)
    for elem in soup.select('[data-key]'):
        key = elem.get('data-key', '').lower().replace(' ', '_')
        value = elem.get('data-value', '') or elem.text.strip()
        if key and value:
            specs['raw_data'][key] = value
    
    # Try definition lists
    for dl in soup.select('dl'):
        dts = dl.select('dt')
        dds = dl.select('dd')
        for dt, dd in zip(dts, dds):
            key = dt.text.strip().lower().replace(' ', '_').replace(':', '')
            val = dd.text.strip()
            if key and val:
                specs['raw_data'][key] = val
    
    raw = specs['raw_data']
    page_text = soup.get_text()
    
    # Extract normalized CPU fields
    # Cores
    for key in ['total_cores', 'cores', 'num_of_cores', 'core_count', 'performance_cores', '___of_cores']:
        if key in raw:
            match = re.search(r'(\d+)', raw[key])
            if match:
                specs['cpu_cores'] = int(match.group(1))
                break
    if not specs.get('cpu_cores'):
        match = re.search(r'(?:total\s+)?cores?[:\s]+(\d+)', page_text, re.I)
        if match:
            specs['cpu_cores'] = int(match.group(1))
    
    # Threads
    for key in ['total_threads', 'threads', 'num_of_threads', 'thread_count', '___of_threads']:
        if key in raw:
            match = re.search(r'(\d+)', raw[key])
            if match:
                specs['cpu_threads'] = int(match.group(1))
                break
    if not specs.get('cpu_threads'):
        match = re.search(r'(?:total\s+)?threads?[:\s]+(\d+)', page_text, re.I)
        if match:
            specs['cpu_threads'] = int(match.group(1))
    
    # Base clock
    for key in ['processor_base_frequency', 'base_frequency', 'base_clock', 'clock_speed']:
        if key in raw:
            match = re.search(r'([\d.]+)\s*GHz', raw[key], re.I)
            if match:
                specs['cpu_base_clock'] = float(match.group(1))
                break
    if not specs.get('cpu_base_clock'):
        match = re.search(r'base\s+(?:frequency|clock)[:\s]+([\d.]+)\s*GHz', page_text, re.I)
        if match:
            specs['cpu_base_clock'] = float(match.group(1))
    
    # Boost/Turbo clock
    for key in ['max_turbo_frequency', 'turbo_frequency', 'boost_clock', 'turbo_boost', 'single_core_turbo']:
        if key in raw:
            match = re.search(r'([\d.]+)\s*GHz', raw[key], re.I)
            if match:
                specs['cpu_boost_clock'] = float(match.group(1))
                break
    if not specs.get('cpu_boost_clock'):
        match = re.search(r'(?:max\s+)?turbo\s+(?:frequency|boost)[:\s]+([\d.]+)\s*GHz', page_text, re.I)
        if match:
            specs['cpu_boost_clock'] = float(match.group(1))
    
    # TDP
    for key in ['tdp', 'processor_base_power', 'thermal_design_power', 'max_turbo_power']:
        if key in raw:
            match = re.search(r'(\d+)\s*W', raw[key])
            if match:
                specs['cpu_tdp'] = int(match.group(1))
                break
    if not specs.get('cpu_tdp'):
        match = re.search(r'(?:tdp|thermal\s+design\s+power)[:\s]+(\d+)\s*W', page_text, re.I)
        if match:
            specs['cpu_tdp'] = int(match.group(1))
    
    # Socket
    for key in ['sockets_supported', 'socket', 'package', 'socket_supported']:
        if key in raw:
            specs['cpu_socket'] = raw[key]
            break
    if not specs.get('cpu_socket'):
        match = re.search(r'(?:socket|package)[:\s]+((?:LGA|BGA|FCLGA)[\d\-]+|AM\d+)', page_text, re.I)
        if match:
            specs['cpu_socket'] = match.group(1).upper()
    
    if specs.get('model'):
        found_specs = [f"{k}={v}" for k, v in specs.items() if k.startswith('cpu_') and v]
        print(f"[Lookup] Intel ARK parsed: {specs.get('model')}")
        print(f"[Lookup] Found: {', '.join(found_specs) if found_specs else 'model only'}")
        return specs
    
    # Last resort - try to get model from URL
    if '/products/' in url:
        match = re.search(r'/products/\d+/([^/]+)', url)
        if match:
            model_from_url = match.group(1).replace('-', ' ').title()
            specs['model'] = f"Intel {model_from_url}"
            print(f"[Lookup] Intel ARK parsed from URL: {specs.get('model')}")
            return specs
    
    print("[Lookup] Intel ARK parsing failed - no model found")
    return None


# =============================================================================
# Motherboard Lookup
# =============================================================================

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
        
        # Fall back to manufacturer sites
        print("[Lookup] Amazon failed, trying manufacturer sites...")
        return search_motherboard_manufacturer(query)
        
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
        response = requests.get(api_url, timeout=60)
        
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
        detail_response = requests.get(detail_api_url, timeout=60)
        
        # Check for credit exhaustion
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_motherboard(detail_response.text, amazon_link)
        
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


def search_motherboard_manufacturer(query: str) -> Optional[Dict]:
    """Search for motherboard specs using manufacturer sites via Scrape.Do."""
    if not SCRAPEDO_TOKEN:
        print("[Lookup] Motherboard lookup requires Scrape.Do")
        return None
    
    try:
        manufacturer = detect_motherboard_manufacturer(query)
        
        if manufacturer:
            site = get_manufacturer_site(manufacturer)
            google_url = f"https://www.google.com/search?q=site:{site}+{requests.utils.quote(query)}+specifications"
        else:
            # Generic search
            google_url = f"https://www.google.com/search?q={requests.utils.quote(query)}+motherboard+specifications"
        
        print(f"[Lookup] Motherboard search: {google_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
        response = requests.get(api_url, timeout=60)
        
        # Check for credit exhaustion
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        response.raise_for_status()
        
        # Parse Google results for manufacturer spec page
        soup = BeautifulSoup(response.text, 'lxml')
        
        spec_link = None
        manufacturer_domains = ['asus.com', 'msi.com', 'gigabyte.com', 'asrock.com', 'evga.com']
        
        for link in soup.select('a'):
            href = link.get('href', '')
            # Look for manufacturer product/spec pages
            for domain in manufacturer_domains:
                if domain in href and ('spec' in href.lower() or 'product' in href.lower() or '/mb/' in href.lower()):
                    match = re.search(r'(https?://[^\s&"]+' + re.escape(domain) + r'[^\s&"]*)', href)
                    if match:
                        spec_link = match.group(1)
                        break
            if spec_link:
                break
        
        # If no spec-specific link, try any manufacturer link
        if not spec_link:
            for link in soup.select('a'):
                href = link.get('href', '')
                for domain in manufacturer_domains:
                    if domain in href:
                        match = re.search(r'(https?://[^\s&"]+' + re.escape(domain) + r'[^\s&"]*)', href)
                        if match:
                            spec_link = match.group(1)
                            break
                if spec_link:
                    break
        
        if not spec_link:
            print("[Lookup] No motherboard spec page found in Google results")
            return None
        
        print(f"[Lookup] Motherboard detail: {spec_link}")
        
        # Fetch the detail page through Scrape.Do
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(spec_link)}"
        detail_response = requests.get(detail_api_url, timeout=60)
        
        # Check for credit exhaustion
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_motherboard_page(detail_response.text, spec_link, manufacturer)
        
    except Exception as e:
        print(f"[Lookup] Motherboard search error: {e}")
        return None


def parse_motherboard_page(html: str, url: str, manufacturer: Optional[str]) -> Optional[Dict]:
    """Parse motherboard spec page from various manufacturers."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': 'manufacturer',
        'source_url': url,
        'component_type': 'Motherboard',
        'manufacturer': manufacturer.upper() if manufacturer else None,
        'raw_data': {}
    }
    
    # Get product name from various possible locations
    title_selectors = [
        'h1.product-name', 'h1.product-title', '.product-name h1',
        'h1[class*="product"]', 'h1[class*="title"]', '.spec-title h1',
        'h1', '.product-header h1', '#product-name'
    ]
    for selector in title_selectors:
        title = soup.select_one(selector)
        if title:
            model = title.text.strip()
            # Clean up common suffixes
            model = re.sub(r'\s*(specifications?|specs|overview|\|.*$)', '', model, flags=re.I).strip()
            if model and len(model) > 3:
                specs['model'] = model
                break
    
    # Extract specs from tables and spec lists
    raw = specs['raw_data']
    
    # Method 1: Look for spec tables
    for table in soup.select('table'):
        for row in table.select('tr'):
            cells = row.select('td, th')
            if len(cells) >= 2:
                key = cells[0].text.strip().lower().replace(' ', '_').replace(':', '')
                value = cells[1].text.strip()
                if key and value:
                    raw[key] = value
    
    # Method 2: Look for definition lists
    for dl in soup.select('dl, .spec-list, .specs-list'):
        dts = dl.select('dt, .spec-label, .label')
        dds = dl.select('dd, .spec-value, .value')
        for dt, dd in zip(dts, dds):
            key = dt.text.strip().lower().replace(' ', '_').replace(':', '')
            value = dd.text.strip()
            if key and value:
                raw[key] = value
    
    # Method 3: Look for labeled divs/spans
    for item in soup.select('[class*="spec"], [class*="feature"]'):
        label = item.select_one('[class*="label"], [class*="name"], [class*="title"], strong, b')
        value = item.select_one('[class*="value"], [class*="detail"], [class*="content"]')
        if label and value:
            key = label.text.strip().lower().replace(' ', '_').replace(':', '')
            val = value.text.strip()
            if key and val:
                raw[key] = val
    
    # Also scan page text for common specs
    page_text = soup.get_text().lower()
    
    # Extract normalized motherboard fields
    
    # Socket
    socket_patterns = [
        r'(lga\s*\d{4}[a-z]*)',
        r'(am[45])',
        r'(socket\s*am[45])',
        r'(tr[x4]?\d*)',
        r'(swrx\d)',
        r'(strx\d)',
    ]
    for key in ['cpu_socket', 'socket', 'cpu', 'processor_socket', 'cpu_support']:
        if key in raw:
            specs['mobo_socket'] = raw[key].split(',')[0].split('/')[0].strip()
            break
    if not specs.get('mobo_socket'):
        for pattern in socket_patterns:
            match = re.search(pattern, page_text, re.I)
            if match:
                specs['mobo_socket'] = match.group(1).upper().replace(' ', '')
                break
    
    # Chipset - check model first, then use socket context
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
    for key in ['chipset', 'chipsets']:
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
            # Unknown socket - search all, prioritize by platform hints
            if 'ryzen' in page_text or 'am5' in page_text or 'am4' in page_text:
                search_chipsets = amd_chipsets + intel_chipsets
            else:
                search_chipsets = intel_chipsets + amd_chipsets
        
        # Search page text for chipsets
        for chipset in search_chipsets:
            pattern = r'\b' + chipset + r'\b'
            if re.search(pattern, page_text, re.I):
                specs['mobo_chipset'] = chipset
                break
    
    # Form factor - check model first (most reliable)
    model_text = specs.get('model', '').lower()
    
    form_factor_priority = [
        ('e-atx', 'E-ATX'), ('eatx', 'E-ATX'), ('extended atx', 'E-ATX'),
        ('micro-atx', 'Micro-ATX'), ('micro atx', 'Micro-ATX'), ('matx', 'Micro-ATX'), ('m-atx', 'Micro-ATX'),
        ('mini-itx', 'Mini-ITX'), ('mini itx', 'Mini-ITX'), ('mitx', 'Mini-ITX'),
        ('mini-dtx', 'Mini-DTX'), ('dtx', 'DTX'),
    ]
    
    # Check model name first
    for pattern, ff in form_factor_priority:
        if pattern in model_text:
            specs['mobo_form_factor'] = ff
            break
    
    # Check for plain ATX in model (must be standalone)
    if not specs.get('mobo_form_factor'):
        if re.search(r'\batx\b', model_text) and not any(x in model_text for x in ['micro', 'mini', 'e-atx', 'eatx', 'extended']):
            specs['mobo_form_factor'] = 'ATX'
    
    # Check raw data fields
    if not specs.get('mobo_form_factor'):
        for key in ['form_factor', 'form', 'size', 'board_size']:
            if key in raw:
                val = raw[key].lower()
                for pattern, ff in form_factor_priority:
                    if pattern in val:
                        specs['mobo_form_factor'] = ff
                        break
                if not specs.get('mobo_form_factor') and 'atx' in val:
                    if not any(x in val for x in ['micro', 'mini', 'extended']):
                        specs['mobo_form_factor'] = 'ATX'
                if specs.get('mobo_form_factor'):
                    break
    
    # Last resort: search page text
    if not specs.get('mobo_form_factor'):
        ff_match = re.search(r'form\s*factor[:\s]*(e-?atx|micro-?atx|mini-?itx|atx)', page_text, re.I)
        if ff_match:
            ff_text = ff_match.group(1).lower()
            for pattern, ff in form_factor_priority:
                if pattern.replace(' ', '-') == ff_text.replace(' ', '-'):
                    specs['mobo_form_factor'] = ff
                    break
            if not specs.get('mobo_form_factor') and 'atx' in ff_text and 'micro' not in ff_text and 'mini' not in ff_text:
                specs['mobo_form_factor'] = 'ATX'
    
    # Memory slots
    for key in ['memory_slots', 'dimm_slots', 'ram_slots', 'memory', 'ddr']:
        if key in raw:
            match = re.search(r'(\d+)\s*(?:x\s*)?(?:dimm|slot)', raw[key], re.I)
            if match:
                specs['mobo_memory_slots'] = int(match.group(1))
                break
            # Try just a number at the start
            match = re.search(r'^(\d+)', raw[key])
            if match and int(match.group(1)) <= 8:
                specs['mobo_memory_slots'] = int(match.group(1))
                break
    
    # Memory type
    memory_types = ['DDR5', 'DDR4', 'DDR3']
    for key in ['memory_type', 'memory', 'ram', 'ddr']:
        if key in raw:
            for mt in memory_types:
                if mt.lower() in raw[key].lower():
                    specs['mobo_memory_type'] = mt
                    break
            if specs.get('mobo_memory_type'):
                break
    if not specs.get('mobo_memory_type'):
        for mt in memory_types:
            if mt.lower() in page_text:
                specs['mobo_memory_type'] = mt
                break
    
    # Max memory
    for key in ['max_memory', 'maximum_memory', 'memory_max', 'memory']:
        if key in raw:
            match = re.search(r'(\d+)\s*gb', raw[key], re.I)
            if match:
                specs['mobo_max_memory'] = int(match.group(1))
                break
    
    # PCIe slots - try to extract counts
    pcie_text = ''
    for key in ['expansion_slots', 'pcie', 'pci_express', 'slots']:
        if key in raw:
            pcie_text = raw[key].lower()
            break
    
    if pcie_text or 'pcie' in page_text or 'pci-e' in page_text:
        # x16 slots
        match = re.search(r'(\d+)\s*x\s*pcie?\s*[x×]?\s*16', pcie_text or page_text, re.I)
        if match:
            specs['mobo_pcie_x16_slots'] = int(match.group(1))
        
        # x4 slots
        match = re.search(r'(\d+)\s*x\s*pcie?\s*[x×]?\s*4(?!\d)', pcie_text or page_text, re.I)
        if match:
            specs['mobo_pcie_x4_slots'] = int(match.group(1))
        
        # x1 slots
        match = re.search(r'(\d+)\s*x\s*pcie?\s*[x×]?\s*1(?!\d)', pcie_text or page_text, re.I)
        if match:
            specs['mobo_pcie_x1_slots'] = int(match.group(1))
    
    # M.2 slots
    for key in ['m.2', 'm2', 'storage', 'ssd']:
        if key in raw:
            match = re.search(r'(\d+)\s*x?\s*m\.?2', raw[key], re.I)
            if match:
                specs['mobo_m2_slots'] = int(match.group(1))
                break
    if not specs.get('mobo_m2_slots'):
        matches = re.findall(r'm\.?2', page_text, re.I)
        if matches:
            specs['mobo_m2_slots'] = len(matches) // 2  # Rough estimate
    
    # SATA ports
    for key in ['sata', 'storage', 'sata_ports']:
        if key in raw:
            match = re.search(r'(\d+)\s*x?\s*sata', raw[key], re.I)
            if match:
                specs['mobo_sata_ports'] = int(match.group(1))
                break
    
    if specs.get('model'):
        print(f"[Lookup] Motherboard parsed: {specs.get('model')}")
        # Print found specs
        found = [f"{k}={v}" for k, v in specs.items() if k.startswith('mobo_') and v]
        print(f"[Lookup] Found specs: {', '.join(found) if found else 'minimal'}")
        return specs
    
    return None


# =============================================================================
# PSU Lookup
# =============================================================================

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
        response = requests.get(api_url, timeout=60)
        
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
        detail_response = requests.get(detail_api_url, timeout=60)
        
        # Check for credit exhaustion
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_amazon_psu(detail_response.text, amazon_link)
        
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


# =============================================================================
# GPU Manufacturer Lookup
# =============================================================================

def detect_gpu_manufacturer(query: str) -> Optional[str]:
    """Detect GPU manufacturer from query."""
    query_lower = query.lower()
    
    # XFX patterns
    if 'xfx' in query_lower or 'speedster' in query_lower or 'merc' in query_lower:
        return 'xfx'
    
    # ASUS patterns
    if any(kw in query_lower for kw in ['asus', 'rog strix', 'tuf gaming', 'dual ', 'proart']):
        return 'asus'
    
    # MSI patterns
    if any(kw in query_lower for kw in ['msi', 'gaming x', 'suprim', 'ventus', 'mech']):
        return 'msi'
    
    # Gigabyte patterns
    if any(kw in query_lower for kw in ['gigabyte', 'aorus', 'eagle', 'gaming oc', 'windforce']):
        return 'gigabyte'
    
    # EVGA patterns
    if any(kw in query_lower for kw in ['evga', 'ftw3', 'xc3', 'sc gaming']):
        return 'evga'
    
    # Sapphire patterns (AMD cards)
    if any(kw in query_lower for kw in ['sapphire', 'nitro', 'pulse', 'toxic']):
        return 'sapphire'
    
    # PowerColor patterns (AMD cards)
    if any(kw in query_lower for kw in ['powercolor', 'red devil', 'red dragon', 'hellhound', 'fighter']):
        return 'powercolor'
    
    # Zotac patterns
    if any(kw in query_lower for kw in ['zotac', 'amp extreme', 'amp holo', 'twin edge']):
        return 'zotac'
    
    # PNY patterns
    if any(kw in query_lower for kw in ['pny', 'xlr8', 'verto']):
        return 'pny'
    
    # Palit/Gainward patterns
    if any(kw in query_lower for kw in ['palit', 'gainward', 'gamerock', 'jetstream']):
        return 'palit'
    
    return None


def get_gpu_manufacturer_site(manufacturer: str) -> str:
    """Get GPU manufacturer's product site domain."""
    sites = {
        'xfx': 'xfxforce.com',
        'asus': 'asus.com',
        'msi': 'msi.com',
        'gigabyte': 'gigabyte.com',
        'evga': 'evga.com',
        'sapphire': 'sapphiretech.com',
        'powercolor': 'powercolor.com',
        'zotac': 'zotac.com',
        'pny': 'pny.com',
        'palit': 'palit.com',
    }
    return sites.get(manufacturer, '')


def search_gpu_manufacturer(query: str) -> Optional[Dict]:
    """Search GPU manufacturer sites for specs."""
    manufacturer = detect_gpu_manufacturer(query)
    
    if not manufacturer:
        print("[Lookup] Could not detect GPU manufacturer from query")
        return None
    
    site = get_gpu_manufacturer_site(manufacturer)
    if not site:
        print(f"[Lookup] No site configured for {manufacturer}")
        return None
    
    print(f"[Lookup] Detected GPU manufacturer: {manufacturer}, searching {site}")
    
    try:
        # Google site search for manufacturer product page
        google_url = f"https://www.google.com/search?q=site:{site}+{requests.utils.quote(query)}"
        
        print(f"[Lookup] GPU manufacturer search: {google_url}")
        
        # Try direct fetch first (some sites don't block)
        try:
            response = requests.get(google_url, headers=get_headers(), timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'lxml')
                product_link = find_gpu_manufacturer_link(soup, site)
                if product_link:
                    # Try direct fetch of product page
                    detail_response = requests.get(product_link, headers=get_headers(), timeout=30)
                    if detail_response.status_code == 200:
                        return parse_gpu_manufacturer_page(detail_response.text, product_link, manufacturer)
        except Exception as e:
            print(f"[Lookup] Direct fetch failed: {e}")
        
        # Fall back to Scrape.Do if available
        if SCRAPEDO_TOKEN:
            api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
            response = requests.get(api_url, timeout=60)
            
            # Check for credit exhaustion
            if response.status_code in [402, 403]:
                error_text = response.text.lower()
                if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                    print("[Lookup] Scrape.Do credits exhausted!")
                    return {'error': 'credits_exhausted'}
            
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'lxml')
            product_link = find_gpu_manufacturer_link(soup, site)
            
            if not product_link:
                print("[Lookup] No GPU product link found in Google results")
                return None
            
            print(f"[Lookup] GPU product page: {product_link}")
            
            # Fetch the detail page through Scrape.Do
            detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(product_link)}"
            detail_response = requests.get(detail_api_url, timeout=60)
            
            # Check for credit exhaustion
            if detail_response.status_code in [402, 403]:
                error_text = detail_response.text.lower()
                if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                    print("[Lookup] Scrape.Do credits exhausted!")
                    return {'error': 'credits_exhausted'}
            
            detail_response.raise_for_status()
            
            return parse_gpu_manufacturer_page(detail_response.text, product_link, manufacturer)
        
        return None
        
    except Exception as e:
        print(f"[Lookup] GPU manufacturer search error: {e}")
        return None


def find_gpu_manufacturer_link(soup: BeautifulSoup, site: str) -> Optional[str]:
    """Find GPU product link in Google search results."""
    for link in soup.select('a'):
        href = link.get('href', '')
        if site in href:
            # Look for product/shop pages
            if any(kw in href.lower() for kw in ['/shop/', '/product/', '/gpu/', '/graphics-card', '/geforce/', '/radeon/']):
                match = re.search(r'(https?://[^\s&"]+' + re.escape(site) + r'[^\s&"]*)', href)
                if match:
                    return match.group(1)
    
    # If no product-specific link, try any link from the site
    for link in soup.select('a'):
        href = link.get('href', '')
        if site in href:
            match = re.search(r'(https?://[^\s&"]+' + re.escape(site) + r'[^\s&"]*)', href)
            if match:
                return match.group(1)
    
    return None


def parse_gpu_manufacturer_page(html: str, url: str, manufacturer: str) -> Optional[Dict]:
    """Parse GPU manufacturer product page for specs."""
    soup = BeautifulSoup(html, 'lxml')
    
    specs = {
        'source': 'manufacturer',
        'source_url': url,
        'component_type': 'GPU',
        'manufacturer': manufacturer.upper() if manufacturer else None,
        'raw_data': {}
    }
    
    # Get product title
    title_selectors = [
        'h1.product-name', 'h1.product-title', 'h1.title', 'h1',
        '.product-name h1', '.product-title', '#product-name',
        '[class*="product-name"]', '[class*="product-title"]'
    ]
    for selector in title_selectors:
        title = soup.select_one(selector)
        if title:
            model = title.text.strip()
            # Clean up common suffixes
            model = re.sub(r'\s*(specifications?|specs|overview|\|.*$)', '', model, flags=re.I).strip()
            if model and len(model) > 5:
                specs['model'] = model
                break
    
    raw = specs['raw_data']
    page_text = soup.get_text().lower()
    
    # Method 1: Parse spec tables
    for table in soup.select('table'):
        for row in table.select('tr'):
            cells = row.select('td, th')
            if len(cells) >= 2:
                key = cells[0].text.strip().lower().replace(' ', '_').replace(':', '')
                value = cells[1].text.strip()
                if key and value and len(key) < 50:
                    raw[key] = value
    
    # Method 2: Parse definition lists and spec sections
    for dl in soup.select('dl, .spec-list, .specs, [class*="specification"]'):
        dts = dl.select('dt, .spec-label, .label, strong, b')
        dds = dl.select('dd, .spec-value, .value')
        for dt, dd in zip(dts, dds):
            key = dt.text.strip().lower().replace(' ', '_').replace(':', '')
            value = dd.text.strip()
            if key and value:
                raw[key] = value
    
    # Method 3: Look for labeled elements
    for item in soup.select('[class*="spec"]'):
        text = item.text.strip()
        if ':' in text:
            parts = text.split(':', 1)
            if len(parts) == 2:
                key = parts[0].strip().lower().replace(' ', '_')
                val = parts[1].strip()
                if key and val and len(key) < 50:
                    raw[key] = val
    
    # Extract normalized GPU fields
    
    # VRAM Size
    vram_patterns = [
        r'(\d+)\s*gb\s*(?:gddr|vram|memory)',
        r'memory\s*size[:\s]*(\d+)\s*gb',
        r'(\d+)\s*gb\s*gddr\d',
    ]
    for key in ['memory_size', 'vram', 'memory', 'gddr6_memory', 'video_memory']:
        if key in raw:
            match = re.search(r'(\d+)\s*gb', raw[key], re.I)
            if match:
                specs['gpu_memory_size'] = int(match.group(1)) * 1024  # Convert to MB
                break
    if not specs.get('gpu_memory_size'):
        for pattern in vram_patterns:
            match = re.search(pattern, page_text, re.I)
            if match:
                specs['gpu_memory_size'] = int(match.group(1)) * 1024
                break
    
    # VRAM Type
    vram_types = ['GDDR6X', 'GDDR6', 'GDDR5X', 'GDDR5', 'HBM2e', 'HBM2', 'HBM3']
    for vt in vram_types:
        if vt.lower() in page_text:
            specs['gpu_memory_type'] = vt
            break
    
    # Base Clock
    for key in ['base_clock', 'gpu_clock', 'core_clock', 'engine_clock']:
        if key in raw:
            match = re.search(r'(\d{3,4})\s*mhz', raw[key], re.I)
            if match:
                specs['gpu_base_clock'] = int(match.group(1))
                break
    if not specs.get('gpu_base_clock'):
        match = re.search(r'base\s*clock[:\s]*(?:up\s*to\s*)?(\d{3,4})\s*mhz', page_text, re.I)
        if match:
            specs['gpu_base_clock'] = int(match.group(1))
    
    # Boost Clock
    for key in ['boost_clock', 'oc_clock', 'game_clock']:
        if key in raw:
            match = re.search(r'(\d{3,4})\s*mhz', raw[key], re.I)
            if match:
                specs['gpu_boost_clock'] = int(match.group(1))
                break
    if not specs.get('gpu_boost_clock'):
        match = re.search(r'boost\s*clock[:\s]*(?:up\s*to\s*)?(\d{3,4})\s*mhz', page_text, re.I)
        if match:
            specs['gpu_boost_clock'] = int(match.group(1))
    
    # TDP
    for key in ['tdp', 'power', 'board_power', 'card_power', 'power_consumption']:
        if key in raw:
            match = re.search(r'(\d{2,3})\s*w', raw[key], re.I)
            if match:
                specs['gpu_tdp'] = int(match.group(1))
                break
    if not specs.get('gpu_tdp'):
        # Look for TDP or power consumption
        match = re.search(r'(?:tdp|board\s*power|power\s*consumption)[:\s]*(\d{2,3})\s*w', page_text, re.I)
        if match:
            specs['gpu_tdp'] = int(match.group(1))
    
    # Memory bus width
    match = re.search(r'(\d{3})\s*-?\s*bit', page_text)
    if match:
        raw['memory_bus'] = f"{match.group(1)}-bit"
    
    # Stream processors / CUDA cores
    match = re.search(r'(?:stream\s*processors?|cuda\s*cores?|shaders?)[:\s]*(\d{3,5})', page_text, re.I)
    if match:
        raw['stream_processors'] = match.group(1)
    
    if specs.get('model'):
        print(f"[Lookup] GPU manufacturer parsed: {specs.get('model')}")
        found = [f"{k}={v}" for k, v in specs.items() if k.startswith('gpu_') and v]
        print(f"[Lookup] Found specs: {', '.join(found) if found else 'minimal'}")
        return specs
    
    return None


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
                     'h610', 'h510', 'b560', 'x299', 'x399', 'trx40', 'wrx80']
    mobo_brands = ['asus', 'msi', 'gigabyte', 'asrock', 'evga', 'biostar', 'supermicro']
    mobo_keywords = ['rog ', 'strix', 'tuf ', 'prime', 'proart',  # ASUS
                     'mag ', 'mpg ', 'meg ', 'tomahawk', 'mortar', 'carbon',  # MSI
                     'aorus', 'gaming x', 'ultra durable',  # Gigabyte
                     'phantom', 'steel legend', 'taichi', 'pro4',  # ASRock
                     'motherboard', 'mainboard']
    
    # Check for chipset + brand combo or motherboard keywords
    has_chipset = any(chip in query_lower for chip in mobo_chipsets)
    has_brand = any(brand in query_lower for brand in mobo_brands)
    has_mobo_keyword = any(kw in query_lower for kw in mobo_keywords)
    
    if has_mobo_keyword or (has_chipset and has_brand) or (has_chipset and '-' in query):
        return 'Motherboard'
    
    # CPU indicators
    cpu_keywords = ['i3-', 'i5-', 'i7-', 'i9-', 'ryzen', 'xeon', 'epyc', 'threadripper', 'pentium', 'celeron', 'athlon']
    if any(kw in query_lower for kw in cpu_keywords):
        return 'CPU'
    
    # Default to GPU (more common lookup)
    return 'GPU'


def get_search_url(query: str, component_type: str) -> str:
    """Get search URL - use Google site search for reliability."""
    search_query = requests.utils.quote(query)
    if component_type == 'GPU':
        # Google site search for TechPowerUp GPU specs
        return f"https://www.google.com/search?q=site:techpowerup.com/gpu-specs+{search_query}"
    elif component_type == 'Motherboard':
        # Google search for motherboard specs (manufacturer sites)
        manufacturer = detect_motherboard_manufacturer(query)
        if manufacturer:
            site = get_manufacturer_site(manufacturer)
            return f"https://www.google.com/search?q=site:{site}+{search_query}+specifications"
        # Generic search if manufacturer unknown
        return f"https://www.google.com/search?q={search_query}+motherboard+specifications"
    else:
        # Google site search for TechPowerUp CPU specs
        return f"https://www.google.com/search?q=site:techpowerup.com/cpu-specs+{search_query}"


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
    
    return None


def get_manufacturer_site(manufacturer: str) -> str:
    """Get manufacturer's spec site domain."""
    sites = {
        'asus': 'asus.com',
        'msi': 'msi.com',
        'gigabyte': 'gigabyte.com',
        'asrock': 'asrock.com',
        'evga': 'evga.com',
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
        return f"https://www.techpowerup.com/gpu-specs/{slug}"
    else:
        return f"https://www.techpowerup.com/cpu-specs/{slug}"


def search_tpu_via_site_search(query: str, component_type: str) -> Optional[Dict]:
    """Search TechPowerUp using their own search page."""
    if not SCRAPEDO_TOKEN:
        return None
    
    search_type = 'gpu' if component_type == 'GPU' else 'cpu'
    
    try:
        # Method 1: Try the AJAX search endpoint
        search_url = f"https://www.techpowerup.com/{search_type}-specs/?ajaxsrch={requests.utils.quote(query)}"
        
        print(f"[Lookup] TechPowerUp AJAX search: {search_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(search_url)}"
        response = requests.get(api_url, timeout=60)
        
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                return {'error': 'credits_exhausted'}
        
        spec_link = None
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'lxml')
            
            # Look for spec page links in results
            for link in soup.select('a[href]'):
                href = link.get('href', '')
                if f'/{search_type}-specs/' in href and '.' in href.split('/')[-1]:
                    # Found a spec page link (has .cXXXX suffix)
                    if href.startswith('/'):
                        href = f"https://www.techpowerup.com{href}"
                    spec_link = href
                    print(f"[Lookup] Found TPU spec link (AJAX): {spec_link}")
                    break
            
            # Try regex in raw HTML
            if not spec_link:
                pattern = rf'href="(/{search_type}-specs/[^"]+\.[a-z]\d+)"'
                match = re.search(pattern, response.text, re.I)
                if match:
                    spec_link = f"https://www.techpowerup.com{match.group(1)}"
                    print(f"[Lookup] Found TPU spec link (regex): {spec_link}")
        
        # Method 2: Try the main specs page with search parameter
        if not spec_link:
            print("[Lookup] AJAX search failed, trying main specs page...")
            search_url2 = f"https://www.techpowerup.com/{search_type}-specs/?mobile=false&sort=name&mfgr=&released=&chipgen=&fc=&fc2=&page=1&ajax=1&name={requests.utils.quote(query)}"
            
            api_url2 = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(search_url2)}"
            response2 = requests.get(api_url2, timeout=60)
            
            if response2.status_code in [402, 403]:
                error_text = response2.text.lower()
                if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                    return {'error': 'credits_exhausted'}
            
            if response2.status_code == 200:
                # Look for links in this response
                pattern = rf'href="(/{search_type}-specs/[^"]+\.[a-z]\d+)"'
                match = re.search(pattern, response2.text, re.I)
                if match:
                    spec_link = f"https://www.techpowerup.com{match.group(1)}"
                    print(f"[Lookup] Found TPU spec link (filter page): {spec_link}")
        
        if not spec_link:
            print("[Lookup] No TechPowerUp spec link found in search results")
            return None
        
        # Fetch the detail page
        print(f"[Lookup] Fetching TPU detail page: {spec_link}")
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(spec_link)}"
        detail_response = requests.get(detail_api_url, timeout=60)
        
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text:
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_techpowerup_detail(detail_response.text, component_type, spec_link)
        
    except Exception as e:
        print(f"[Lookup] TechPowerUp site search error: {e}")
        return None


def get_headers() -> dict:
    """Get request headers."""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }


# =============================================================================
# Method 1: BeautifulSoup + Requests
# =============================================================================

def search_with_requests(query: str, component_type: str) -> Optional[Dict]:
    """Search using requests + BeautifulSoup (fast, lightweight)."""
    # For GPUs, normalize the query to reference card name
    search_query = query
    if component_type == 'GPU':
        search_query = normalize_gpu_query(query)
    
    try:
        # Try 1: Direct TechPowerUp URL (guess the slug)
        direct_url = get_direct_tpu_url(search_query, component_type)
        print(f"[Lookup] Trying direct URL: {direct_url}")
        
        response = requests.get(direct_url, headers=get_headers(), timeout=30, allow_redirects=True)
        
        # Check if we got a valid specs page (not 404)
        if response.status_code == 200 and ('gpuname' in response.text or 'cpuname' in response.text):
            print(f"[Lookup] Direct URL worked!")
            return parse_techpowerup_detail(response.text, component_type, response.url)
        
        print(f"[Lookup] Direct URL failed (status: {response.status_code})")
        return None
        
    except Exception as e:
        print(f"[Lookup] Requests error: {e}")
        return None


# =============================================================================
# Method 2: Playwright (FREE - local browser for JS-heavy sites)
# =============================================================================

def search_with_playwright(query: str, component_type: str) -> Optional[Dict]:
    """
    Search using Playwright browser automation (FREE - no API credits).
    Slower than requests (~10-15 seconds) but handles JavaScript-rendered pages.
    """
    if not is_playwright_available():
        print("[Lookup] Playwright not available, skipping")
        return None
    
    # For GPUs, normalize the query to reference card name
    search_query = query
    if component_type == 'GPU':
        search_query = normalize_gpu_query(query)
    
    try:
        from playwright.sync_api import sync_playwright
        
        print(f"[Lookup] Playwright starting (this may take 10-15 seconds)...")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = context.new_page()
            page.set_default_timeout(30000)  # 30 second timeout
            
            # Try direct TechPowerUp URL first
            direct_url = get_direct_tpu_url(search_query, component_type)
            print(f"[Lookup] Playwright trying direct URL: {direct_url}")
            
            try:
                page.goto(direct_url, wait_until='domcontentloaded')
                page.wait_for_timeout(2000)  # Wait for any JS rendering
                
                content = page.content()
                if 'gpuname' in content or 'cpuname' in content:
                    print(f"[Lookup] Playwright direct URL worked!")
                    browser.close()
                    return parse_techpowerup_detail(content, component_type, direct_url)
            except Exception as e:
                print(f"[Lookup] Playwright direct URL failed: {e}")
            
            # Try Google search for TechPowerUp
            google_url = f"https://www.google.com/search?q=site:techpowerup.com+{component_type.lower()}-specs+{requests.utils.quote(search_query)}"
            print(f"[Lookup] Playwright trying Google search...")
            
            try:
                page.goto(google_url, wait_until='domcontentloaded')
                page.wait_for_timeout(2000)
                
                # Find TechPowerUp links in results
                links = page.query_selector_all('a[href*="techpowerup.com"]')
                tpu_url = None
                
                for link in links:
                    href = link.get_attribute('href')
                    if href and ('gpu-specs' in href or 'cpu-specs' in href):
                        # Extract actual URL from Google redirect
                        if '/url?q=' in href:
                            tpu_url = href.split('/url?q=')[1].split('&')[0]
                        else:
                            tpu_url = href
                        break
                
                if tpu_url:
                    print(f"[Lookup] Playwright found TPU link: {tpu_url}")
                    page.goto(tpu_url, wait_until='domcontentloaded')
                    page.wait_for_timeout(2000)
                    
                    content = page.content()
                    if 'gpuname' in content or 'cpuname' in content:
                        print(f"[Lookup] Playwright Google search worked!")
                        browser.close()
                        return parse_techpowerup_detail(content, component_type, tpu_url)
                        
            except Exception as e:
                print(f"[Lookup] Playwright Google search failed: {e}")
            
            browser.close()
            
    except Exception as e:
        print(f"[Lookup] Playwright error: {e}")
    
    return None


def search_intel_ark_playwright(query: str) -> Optional[Dict]:
    """
    Search Intel ARK using Playwright (FREE - no API credits).
    Intel ARK is JavaScript-heavy so this works better than requests.
    """
    if not is_playwright_available():
        print("[Lookup] Playwright not available for Intel ARK")
        return None
    
    try:
        from playwright.sync_api import sync_playwright
        
        print(f"[Lookup] Playwright Intel ARK search for: {query}")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = context.new_page()
            page.set_default_timeout(45000)  # 45 second timeout for Intel ARK
            
            # Search Google for Intel ARK page
            search_query = f"site:ark.intel.com {query}"
            google_url = f"https://www.google.com/search?q={requests.utils.quote(search_query)}"
            
            print(f"[Lookup] Playwright searching Google for Intel ARK...")
            page.goto(google_url, wait_until='domcontentloaded')
            page.wait_for_timeout(2000)
            
            # Find Intel ARK link
            ark_url = None
            links = page.query_selector_all('a')
            
            for link in links:
                href = link.get_attribute('href') or ''
                if 'ark.intel.com' in href and '/products/' in href:
                    # Extract actual URL from Google redirect
                    if '/url?q=' in href:
                        ark_url = href.split('/url?q=')[1].split('&')[0]
                        ark_url = requests.utils.unquote(ark_url)
                    elif href.startswith('http'):
                        ark_url = href
                    break
            
            if not ark_url:
                print("[Lookup] No Intel ARK link found in Google results")
                browser.close()
                return None
            
            print(f"[Lookup] Playwright loading Intel ARK: {ark_url}")
            page.goto(ark_url, wait_until='networkidle')
            page.wait_for_timeout(3000)  # Extra wait for JS rendering
            
            content = page.content()
            browser.close()
            
            # Parse Intel ARK page
            return parse_intel_ark_page(content, ark_url)
            
    except Exception as e:
        print(f"[Lookup] Playwright Intel ARK error: {e}")
    
    return None


def parse_intel_ark_page(html: str, source_url: str) -> Optional[Dict]:
    """Parse Intel ARK product page."""
    soup = BeautifulSoup(html, 'lxml')
    
    # Try to find product name
    model = None
    for selector in ['h1.product-title', '[data-wap_ref="defined-title"]', '.ProductName', 'h1']:
        elem = soup.select_one(selector)
        if elem and elem.get_text(strip=True):
            model = elem.get_text(strip=True)
            break
    
    if not model:
        # Try to extract from page text
        match = re.search(r'((?:Intel\s+)?(?:Xeon|Core)\s+[^\n]+)', html, re.I)
        if match:
            model = match.group(1).strip()
    
    if not model:
        print("[Lookup] Could not find model name in Intel ARK page")
        return None
    
    # Clean up model name
    model = clean_cpu_model_name(model)
    
    # Extract specs from tables or data attributes
    specs = {}
    
    # Look for spec tables
    for row in soup.select('tr, .ark-product-specs tr, .specs-row'):
        label_elem = row.select_one('th, .label, [class*="label"]')
        value_elem = row.select_one('td, .value, [class*="value"]')
        
        if label_elem and value_elem:
            label = label_elem.get_text(strip=True).lower()
            value = value_elem.get_text(strip=True)
            
            if 'total cores' in label or label == 'cores':
                match = re.search(r'(\d+)', value)
                if match:
                    specs['cpu_cores'] = int(match.group(1))
            elif 'total threads' in label or label == 'threads':
                match = re.search(r'(\d+)', value)
                if match:
                    specs['cpu_threads'] = int(match.group(1))
            elif 'base frequency' in label or 'processor base' in label:
                match = re.search(r'([\d.]+)\s*[gG][hH]z', value)
                if match:
                    specs['cpu_base_clock'] = float(match.group(1))
            elif 'turbo' in label or 'boost' in label or 'max frequency' in label:
                match = re.search(r'([\d.]+)\s*[gG][hH]z', value)
                if match:
                    specs['cpu_boost_clock'] = float(match.group(1))
            elif 'tdp' in label or 'thermal design' in label:
                match = re.search(r'(\d+)\s*[wW]', value)
                if match:
                    specs['cpu_tdp'] = int(match.group(1))
            elif 'socket' in label:
                specs['cpu_socket'] = value
            elif 'cache' in label and 'l3' not in specs:
                specs['cache'] = value
    
    # Fallback: search page text for specs
    page_text = soup.get_text()
    
    if 'cpu_cores' not in specs:
        match = re.search(r'(?:total\s+)?cores?[:\s]+(\d+)', page_text, re.I)
        if match:
            specs['cpu_cores'] = int(match.group(1))
    
    if 'cpu_threads' not in specs:
        match = re.search(r'(?:total\s+)?threads?[:\s]+(\d+)', page_text, re.I)
        if match:
            specs['cpu_threads'] = int(match.group(1))
    
    if 'cpu_base_clock' not in specs:
        match = re.search(r'(?:base|processor)\s+(?:frequency|clock)[:\s]+([\d.]+)\s*GHz', page_text, re.I)
        if match:
            specs['cpu_base_clock'] = float(match.group(1))
    
    if 'cpu_boost_clock' not in specs:
        match = re.search(r'(?:turbo|boost|max)\s+(?:frequency|clock)[:\s]+([\d.]+)\s*GHz', page_text, re.I)
        if match:
            specs['cpu_boost_clock'] = float(match.group(1))
    
    if 'cpu_tdp' not in specs:
        match = re.search(r'TDP[:\s]+(\d+)\s*W', page_text, re.I)
        if match:
            specs['cpu_tdp'] = int(match.group(1))
    
    if 'cpu_socket' not in specs:
        match = re.search(r'Socket[:\s]+([\w\d\-]+)', page_text, re.I)
        if match:
            specs['cpu_socket'] = match.group(1)
    
    result = {
        'component_type': 'CPU',
        'manufacturer': 'Intel',
        'model': model,
        'source_url': source_url,
        **specs
    }
    
    print(f"[Lookup] Intel ARK parsed: {model} - {specs.get('cpu_cores', '?')}C/{specs.get('cpu_threads', '?')}T")
    return result


# =============================================================================
# Method 3: Scrape.Do API (PAID - last resort)
# =============================================================================

def search_with_scrapedo(query: str, component_type: str) -> Optional[Dict]:
    """Search using Scrape.Do proxy API with Google site search."""
    if not SCRAPEDO_TOKEN:
        return None
    
    # For GPUs, normalize the query to reference card name
    search_query = query
    if component_type == 'GPU':
        search_query = normalize_gpu_query(query)
    
    try:
        # First try direct URL through Scrape.Do
        direct_url = get_direct_tpu_url(search_query, component_type)
        print(f"[Lookup] Scrape.Do trying direct: {direct_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(direct_url)}"
        response = requests.get(api_url, timeout=60)
        
        # Check for credit exhaustion (402 Payment Required or 403 with specific message)
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text or 'payment' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        if response.status_code == 200 and ('gpuname' in response.text or 'cpuname' in response.text):
            print(f"[Lookup] Scrape.Do direct URL worked!")
            return parse_techpowerup_detail(response.text, component_type, direct_url)
        
        # Try Google site search
        google_url = get_search_url(search_query, component_type)
        print(f"[Lookup] Scrape.Do trying Google: {google_url}")
        
        api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(google_url)}"
        response = requests.get(api_url, timeout=60)
        
        # Check for credit exhaustion again
        if response.status_code in [402, 403]:
            error_text = response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text or 'payment' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        response.raise_for_status()
        
        # Parse Google results for TechPowerUp link
        detail_url = find_tpu_link_in_google(response.text, component_type)
        if not detail_url:
            print("[Lookup] No TechPowerUp link found in Google results")
            return None
        
        print(f"[Lookup] Found TPU link: {detail_url}")
        
        # Fetch the detail page through Scrape.Do
        detail_api_url = f"https://api.scrape.do?token={SCRAPEDO_TOKEN}&url={requests.utils.quote(detail_url)}"
        detail_response = requests.get(detail_api_url, timeout=60)
        
        # Check for credit exhaustion on detail fetch
        if detail_response.status_code in [402, 403]:
            error_text = detail_response.text.lower()
            if 'credit' in error_text or 'limit' in error_text or 'quota' in error_text or 'payment' in error_text:
                print("[Lookup] Scrape.Do credits exhausted!")
                return {'error': 'credits_exhausted'}
        
        detail_response.raise_for_status()
        
        return parse_techpowerup_detail(detail_response.text, component_type, detail_url)
        
    except Exception as e:
        print(f"[Lookup] Scrape.Do error: {e}")
        return None


def find_tpu_link_in_google(html: str, component_type: str) -> Optional[str]:
    """Extract TechPowerUp specs link from Google search results."""
    soup = BeautifulSoup(html, 'lxml')
    
    # Look for links to TechPowerUp specs pages
    target = 'gpu-specs' if component_type == 'GPU' else 'cpu-specs'
    
    for link in soup.find_all('a', href=True):
        href = link['href']
        # Google wraps URLs, look for the actual URL
        if f'techpowerup.com/{target}/' in href:
            # Extract clean URL
            if href.startswith('/url?q='):
                # Google redirect format
                href = href.split('/url?q=')[1].split('&')[0]
            if href.startswith('http'):
                return requests.utils.unquote(href)
    
    return None


# =============================================================================
# Shared Parsing Functions
# =============================================================================

def find_best_match(html: str, query: str) -> Optional[str]:
    """Find best matching result from search results HTML."""
    soup = BeautifulSoup(html, 'lxml')
    
    # Find results table
    results = soup.select('table.processors tbody tr')
    
    if not results:
        print(f"[Lookup] No results table found")
        return None
    
    query_lower = query.lower()
    best_match = None
    
    for row in results:
        name_cell = row.select_one('td:first-child a')
        if name_cell:
            name = name_cell.text.strip()
            href = name_cell.get('href', '')
            
            # Check if query matches
            name_lower = name.lower()
            if query_lower in name_lower or all(word in name_lower for word in query_lower.split()):
                best_match = href
                print(f"[Lookup] Found match: {name}")
                break
    
    # Fallback to first result
    if not best_match and results:
        first_cell = results[0].select_one('td:first-child a')
        if first_cell:
            best_match = first_cell.get('href', '')
            print(f"[Lookup] Using first result: {first_cell.text.strip()}")
    
    if best_match and best_match.startswith('/'):
        best_match = f"https://www.techpowerup.com{best_match}"
    
    return best_match


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
                for mem_type in ['GDDR6X', 'GDDR6', 'GDDR5X', 'GDDR5', 'GDDR4', 'GDDR3', 'HBM2e', 'HBM2', 'HBM']:
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
