"""Model/query normalization helpers for scraper matching."""

import re

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
    
    return full_title[:50]


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
    
    return full_title[:60]


def extract_key_identifiers(query: str) -> list:
    """Extract key identifying parts from a query (version numbers, model numbers)."""
    query_lower = query.lower()
    identifiers = []
    
    # Find version indicators like v3, v4, V2, etc.
    versions = re.findall(r'v\d+', query_lower)
    identifiers.extend(versions)
    
    # Find generation/revision numbers
    # Negative lookbehind prevents matching 'r' inside compound tokens like DDR4, PCIe4
    revisions = re.findall(r'(?<![a-z])(?:gen|rev|r)\s*\d+\b', query_lower)
    identifiers.extend(revisions)
    
    # Find GPU suffixes that are significant (Ti, Super, XT, etc.)
    if re.search(r'\bti\b', query_lower):
        identifiers.append('ti')
    if re.search(r'\bsuper\b', query_lower):  # word boundary avoids matching 'SuperNOVA'
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
