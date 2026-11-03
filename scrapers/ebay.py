"""
eBay Browse API client for TechReadout price estimation.
Uses Client Credentials OAuth (app-level, no user login required).
"""
import base64
import os
import statistics
from datetime import datetime, timedelta

import requests

EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"

# In-memory token cache (lives as long as the process)
_token_cache: dict = {"token": None, "expires_at": None}


def _get_access_token() -> str:
    """Return a valid OAuth access token, refreshing if expired."""
    app_id = os.environ.get("EBAY_APP_ID", "").strip()
    app_secret = os.environ.get("EBAY_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise ValueError("EBAY_APP_ID and EBAY_APP_SECRET environment variables must be set")

    now = datetime.utcnow()
    if _token_cache["token"] and _token_cache["expires_at"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    credentials = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
    resp = requests.post(
        EBAY_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    # Subtract 60 s as a safety margin before the token actually expires
    _token_cache["expires_at"] = now + timedelta(seconds=data.get("expires_in", 7200) - 60)
    return _token_cache["token"]


def fetch_ebay_price(query: str) -> dict:
    """
    Search eBay for *query* and return a median used-market price.

    Returns:
        {"price": float, "listing_count": int}

    Raises:
        ValueError  – not enough listings to produce a reliable estimate
        requests.HTTPError – eBay API returned an error response
    """
    token = _get_access_token()

    resp = requests.get(
        EBAY_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        },
        params={
            "q": query,
            "limit": 50,
            # conditionIds: 2000=Very Good, 2500=Good, 3000=Acceptable/Used
            "filter": "conditionIds:{2000|2500|3000}",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    items = data.get("itemSummaries", [])
    if not items:
        raise ValueError(f"No eBay listings found for: {query}")

    prices = []
    for item in items:
        try:
            val = float(item.get("price", {}).get("value", 0))
            if val > 0:
                prices.append(val)
        except (TypeError, ValueError):
            continue

    if len(prices) < 3:
        raise ValueError(f"Too few priced listings ({len(prices)}) for: {query}")

    prices.sort()
    # Strip bottom 10 % and top 10 % to remove outliers
    trim = max(1, len(prices) // 10)
    trimmed = prices[trim:-trim] if len(prices) > 2 * trim else prices

    return {"price": round(statistics.median(trimmed), 2), "listing_count": len(prices)}
