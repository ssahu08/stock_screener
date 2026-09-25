# NSE/BSE Listing Verification
# ============================
# Cross-check IPO listing status against official exchange sources.
# Used to correct stale Apify data.

import re
from datetime import datetime, date
from typing import Optional, Dict, List
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup


@dataclass
class ListingInfo:
    """Verified listing data from exchange."""
    symbol: str              # NSE symbol (e.g., "HORIZON")
    company_name: str
    listing_date: date
    listing_price: float     # Listing open price
    exchange: str            # "NSE" | "BSE" | "NSE_SME" | "BSE_SME"
    isin: Optional[str] = None
    series: Optional[str] = None  # "EQ" | "SM" | "ST" etc.


class NSEListingChecker:
    """
    Check NSE for recently listed IPOs.
    NSE doesn't have a public API for this, so we scrape:
    - https://www.nseindia.com/market-data/new-listings
    - https://www.nseindia.com/api/ipo-listings (undocumented, may change)
    """

    BASE = "https://www.nseindia.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        # Prime session with cookies
        try:
            self.session.get(self.BASE, timeout=10)
        except Exception:
            pass

    def get_new_listings(self, days_back: int = 30) -> List[ListingInfo]:
        """
        Fetch recently listed companies from NSE.
        Returns list of ListingInfo.
        """
        url = f"{self.BASE}/api/ipo-listings"
        params = {"type": "recent", "limit": 100}
        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return self._parse_nse_api(data, days_back)
        except Exception:
            pass

        # Fallback: HTML scrape
        return self._scrape_nse_html(days_back)

    def _parse_nse_api(self, data: Dict, days_back: int) -> List[ListingInfo]:
        """Parse NSE's undocumented JSON API."""
        results = []
        cutoff = date.today() - __import__('datetime').timedelta(days=days_back)

        for item in data.get("data", []):
            try:
                listing_date = datetime.fromisoformat(item.get("listingDate", "").replace("Z", "+00:00")).date()
                if listing_date < cutoff:
                    continue

                results.append(ListingInfo(
                    symbol=item.get("symbol", ""),
                    company_name=item.get("companyName", ""),
                    listing_date=listing_date,
                    listing_price=float(item.get("listingPrice", 0) or 0),
                    exchange="NSE_SME" if item.get("isSME") else "NSE",
                    isin=item.get("isin"),
                    series=item.get("series"),
                ))
            except Exception:
                continue
        return results

    def _scrape_nse_html(self, days_back: int) -> List[ListingInfo]:
        """Scrape NSE's new listings page."""
        url = f"{self.BASE}/market-data/new-listings"
        try:
            resp = self.session.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # NSE page structure varies; try multiple selectors
            results = []
            cutoff = date.today() - __import__('datetime').timedelta(days=days_back)

            # Try table rows
            for row in soup.select("table tbody tr, .table-responsive tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 4:
                    continue
                try:
                    # Typical columns: Symbol, Company, Series, Listing Date, Listing Price
                    symbol = cells[0].get_text(strip=True)
                    company = cells[1].get_text(strip=True)
                    series = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                    listing_date_str = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                    listing_price_str = cells[4].get_text(strip=True) if len(cells) > 4 else ""

                    listing_date = self._parse_date(listing_date_str)
                    if not listing_date or listing_date < cutoff:
                        continue

                    listing_price = float(re.sub(r"[^\d.]", "", listing_price_str)) if listing_price_str else 0

                    exchange = "NSE_SME" if "SM" in series else "NSE"

                    results.append(ListingInfo(
                        symbol=symbol,
                        company_name=company,
                        listing_date=listing_date,
                        listing_price=listing_price,
                        exchange=exchange,
                        series=series,
                    ))
                except Exception:
                    continue
            return results
        except Exception:
            return []

    @staticmethod
    def _parse_date(s: str) -> Optional[date]:
        """Parse various date formats."""
        if not s:
            return None
        s = s.strip()
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%b %d, %Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None


class BSEListingChecker:
    """
    Check BSE for recently listed IPOs.
    BSE has a more structured page for SME listings.
    """

    BASE = "https://www.bseindia.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def get_new_listings(self, days_back: int = 30) -> List[ListingInfo]:
        """Fetch from BSE's IPO listing page."""
        url = f"{self.BASE}/markets/Equity/EQReports/RecentlyListed.aspx"
        try:
            resp = self.session.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            results = []
            cutoff = date.today() - __import__('datetime').timedelta(days=days_back)

            # BSE table structure
            for row in soup.select("table#ctl00_ContentPlaceHolder1_gvData tr, table.grid tr"):
                cells = row.find_all("td")
                if len(cells) < 5:
                    continue
                try:
                    # Typical: Scrip Code, Company Name, Listing Date, Issue Price, Listing Price
                    scrip = cells[0].get_text(strip=True)
                    company = cells[1].get_text(strip=True)
                    listing_date_str = cells[2].get_text(strip=True)
                    issue_price_str = cells[3].get_text(strip=True)
                    listing_price_str = cells[4].get_text(strip=True)

                    listing_date = self._parse_date(listing_date_str)
                    if not listing_date or listing_date < cutoff:
                        continue

                    listing_price = float(re.sub(r"[^\d.]", "", listing_price_str)) if listing_price_str else 0
                    issue_price = float(re.sub(r"[^\d.]", "", issue_price_str)) if issue_price_str else 0

                    # Determine SME vs mainboard from scrip code or separate column
                    exchange = "BSE_SME" if "SME" in company.upper() or int(scrip or 0) > 500000 else "BSE"

                    results.append(ListingInfo(
                        symbol=scrip,
                        company_name=company,
                        listing_date=listing_date,
                        listing_price=listing_price,
                        exchange=exchange,
                        isin=None,
                        series=None,
                    ))
                except Exception:
                    continue
            return results
        except Exception:
            return []

    @staticmethod
    def _parse_date(s: str) -> Optional[date]:
        if not s:
            return None
        s = s.strip()
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None


class ChittorgarhChecker:
    """
    Chittorgarh.com - reliable for IPO calendar, GMP, subscription, listing gains.
    """

    BASE = "https://www.chittorgarh.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def get_ipo_calendar(self) -> List[Dict]:
        """Fetch IPO calendar with GMP, subscription, listing info."""
        url = f"{self.BASE}/ipo/ipo-dashboard.asp"
        try:
            resp = self.session.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            results = []
            for row in soup.select("table.ipo-table tbody tr, table#ipoTable tbody tr"):
                cells = row.find_all("td")
                if len(cells) < 8:
                    continue
                try:
                    # Columns: Company, Open, Close, Price, Lot, Issue Size, GMP, Subscription, Status
                    company = cells[0].get_text(strip=True)
                    open_date = cells[1].get_text(strip=True)
                    close_date = cells[2].get_text(strip=True)
                    price_band = cells[3].get_text(strip=True)
                    lot = cells[4].get_text(strip=True)
                    issue_size = cells[5].get_text(strip=True)
                    gmp = cells[6].get_text(strip=True)
                    subscription = cells[7].get_text(strip=True)
                    status = cells[8].get_text(strip=True) if len(cells) > 8 else ""

                    results.append({
                        "company": company,
                        "open_date": open_date,
                        "close_date": close_date,
                        "price_band": price_band,
                        "lot_size": lot,
                        "issue_size_cr": issue_size,
                        "gmp": gmp,
                        "subscription": subscription,
                        "status": status,
                        "source": "chittorgarh",
                    })
                except Exception:
                    continue
            return results
        except Exception:
            return []


# -----------------------------------------------------------------------------
# Unified verification
# -----------------------------------------------------------------------------

class IPOListingVerifier:
    """
    Combine Apify + NSE + BSE + Chittorgarh for best accuracy.
    """

    def __init__(self):
        self.nse = NSEListingChecker()
        self.bse = BSEListingChecker()
        self.chittorgarh = ChittorgarhChecker()
        self._cache: Dict[str, List[ListingInfo]] = {}
        self._last_checked: Optional[datetime] = None

    def verify_listings(self, days_back: int = 30) -> Dict[str, ListingInfo]:
        """
        Returns dict: normalized_company_name -> ListingInfo
        Merges NSE + BSE results, deduplicates by company name.
        """
        cache_key = f"listings_{days_back}d"
        if cache_key in self._cache and self._last_checked:
            if (datetime.now() - self._last_checked).seconds < 3600:  # 1 hour cache
                return self._cache[cache_key]

        nse_list = self.nse.get_new_listings(days_back)
        bse_list = self.bse.get_new_listings(days_back)

        merged = {}
        for item in nse_list + bse_list:
            key = self._normalize_company(item.company_name)
            if key not in merged or item.exchange in ("NSE", "BSE"):  # Prefer mainboard
                merged[key] = item

        self._cache[cache_key] = merged
        self._last_checked = datetime.now()
        return merged

    def correct_ipo_status(self, apify_ipo: 'IPOData', days_back: int = 30) -> 'IPOData':
        """
        Given an Apify IPO, check if NSE/BSE shows it as listed.
        Returns corrected IPOData (mutates and returns).
        """
        verified = self.verify_listings(days_back)
        key = self._normalize_company(apify_ipo.company_name)

        if key in verified:
            listing = verified[key]
            # Override with verified data
            apify_ipo.current_status = "LISTED"
            apify_ipo.listing_date = listing.listing_date.isoformat()
            apify_ipo.listing_price = listing.listing_price
            if apify_ipo.issue_price:
                apify_ipo.listing_gain_pct = round(
                    (listing.listing_price - apify_ipo.issue_price) / apify_ipo.issue_price * 100, 2
                )
            apify_ipo.current_gmp = None  # GMP irrelevant post-listing
            apify_ipo.last_updated_utc = datetime.now().isoformat() + "Z"
            apify_ipo._verified_source = listing.exchange
        return apify_ipo

    @staticmethod
    def _normalize_company(name: str) -> str:
        """Normalize for matching: remove suffixes, punctuation, case."""
        s = name.upper()
        for suffix in [" LIMITED", " LTD", " PVT", " PRIVATE", " INDIA", " COMPANY", " CORPORATION", " CORP", " INC", " LLP"]:
            s = s.replace(suffix, "")
        s = re.sub(r"[^A-Z0-9]", "", s)
        return s


# -----------------------------------------------------------------------------
# Quick test
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    verifier = IPOListingVerifier()
    print("Checking NSE...")
    nse = verifier.nse.get_new_listings(60)
    print(f"  NSE: {len(nse)} listings")
    for l in nse[:5]:
        print(f"    {l.company_name} ({l.symbol}) - {l.listing_date} - ₹{l.listing_price} [{l.exchange}]")

    print("Checking BSE...")
    bse = verifier.bse.get_new_listings(60)
    print(f"  BSE: {len(bse)} listings")
    for l in bse[:5]:
        print(f"    {l.company_name} ({l.symbol}) - {l.listing_date} - ₹{l.listing_price} [{l.exchange}]")

    print("Merged verification...")
    merged = verifier.verify_listings(60)
    print(f"  Total unique: {len(merged)}")
    # Check our two specific IPOs
    for name in ["Horizon Reclaim", "Advit Jewels"]:
        key = IPOListingVerifier._normalize_company(name)
        if key in merged:
            l = merged[key]
            print(f"  ✅ {name}: {l.listing_date} @ ₹{l.listing_price} [{l.exchange}]")
        else:
            print(f"  ❌ {name}: NOT FOUND in NSE/BSE listings")