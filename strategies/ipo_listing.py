# IPO Listing Gains Strategy
# ===========================
# Event-driven strategy for IPO listing gain prediction.
# Data source: Apify Indian IPO Tracker (free tier).
#
# Pipeline:
#   1. Fetch live/upcoming/listed IPOs from Apify
#   2. Score each IPO (0-100) on:
#        - GMP (grey market premium) vs issue price
#        - Subscription multiples (QIB/HNI/Retail)
#        - Issue size (smaller = higher gain potential)
#        - Price band position (discount to peers)
#        - Anchor investor quality (if available)
#        - Financial health from parsed prospectus (rev/EBITDA/PAT trends)
#   3. Dashboard tab: sortable table with "Apply?" recommendation
#   4. Post-listing tracker: auto-fetch listing price, compute gain
#   5. Telegram alert on listing day with gain % and hold/sell guidance

import os
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path

import requests
import pandas as pd

from strategies.base import Strategy
from config.keys import APIFY_TOKEN  # add to keys.py


APIFY_BASE = "https://contented-academy--indian-ipo-tracker-api.apify.actor/api"
APIFY_TIMEOUT = 30


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class IPOData:
    ipo_id: str
    company_name: str
    category: str                 # "mainboard" | "sme"
    current_status: str           # "DRHP_FILED" | "SUBSCRIPTION_OPEN" | "ALLOTMENT_DONE" | "LISTED" | "WITHDRAWN"
    current_gmp: Optional[float]  # ₹ per share, or None
    total_subscription_x: Optional[float]
    qib_subscription_x: Optional[float]
    hni_subscription_x: Optional[float]
    retail_subscription_x: Optional[float]
    price_band_lower: Optional[float]
    price_band_upper: Optional[float]
    lot_size: Optional[int]
    min_investment_inr: Optional[int]
    issue_size_cr: Optional[float]
    subscription_open_date: Optional[str]
    subscription_close_date: Optional[str]
    listing_date: Optional[str]
    listing_price: Optional[float]      # filled after listing
    listing_gain_pct: Optional[float]   # (listing_price - issue_price) / issue_price * 100
    last_updated_utc: str
    financials: Optional[Dict] = None  # from prospectus parse if available

    @property
    def issue_price(self) -> Optional[float]:
        if self.price_band_upper and self.price_band_lower:
            return round((self.price_band_lower + self.price_band_upper) / 2, 2)
        return self.price_band_upper

    @property
    def gmp_pct(self) -> Optional[float]:
        if self.current_gmp and self.issue_price:
            return round(self.current_gmp / self.issue_price * 100, 1)
        return None

    @property
    def days_to_listing(self) -> Optional[int]:
        if self.listing_date:
            try:
                ld = datetime.fromisoformat(self.listing_date.replace('Z', '+00:00')).date()
                return (ld - date.today()).days
            except Exception:
                return None
        return None


# -----------------------------------------------------------------------------
# NSE Active IPOs (for cross-checking subscription status)
# -----------------------------------------------------------------------------

class NSEActiveIPOClient:
    """Fetch currently active IPOs from NSE's official API."""

    BASE = "https://www.nseindia.com/api"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        # Prime session
        try:
            self.session.get("https://www.nseindia.com", timeout=10)
        except Exception:
            pass

    def get_active_ipos(self) -> List[Dict]:
        """Fetch currently active (open for subscription) IPOs from NSE."""
        url = f"{self.BASE}/upcoming-issues"
        params = {"csv": "true"}
        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("active", [])
        except Exception:
            pass
        return []

    def get_active_ipos_formatted(self) -> List[Dict]:
        """Return formatted active IPOs for dashboard display."""
        active = self.get_active_ipos()
        results = []
        for item in active:
            d = item.get("data", {})
            results.append({
                "company": d.get("companyName", ""),
                "symbol": d.get("symbol", ""),
                "series": d.get("series", ""),
                "issue_price": d.get("issuePrice", ""),
                "issue_size": d.get("issueSize", ""),
                "start_date": d.get("issueStartDate", ""),
                "end_date": d.get("issueEndDate", ""),
                "status": d.get("status", ""),
                "source": "NSE",
            })
        return results


# -----------------------------------------------------------------------------
# Local Verified Listings Cache (user-maintained)
# -----------------------------------------------------------------------------

VERIFIED_CACHE_FILE = Path(__file__).parent.parent / "data" / "ipo_verified_listings.json"


class VerifiedListingsCache:
    """Local JSON cache for user-verified listing corrections."""

    def __init__(self):
        self.cache = self._load()

    def _load(self) -> Dict:
        if VERIFIED_CACHE_FILE.exists():
            try:
                with open(VERIFIED_CACHE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        VERIFIED_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(VERIFIED_CACHE_FILE, "w") as f:
            json.dump(self.cache, f, indent=2, default=str)

    def get(self, ipo_id: str) -> Optional[Dict]:
        return self.cache.get(ipo_id)

    def set(self, ipo_id: str, listing_data: Dict):
        """Mark an IPO as verified listed with actual data."""
        self.cache[ipo_id] = {
            **listing_data,
            "verified_at": datetime.now().isoformat(),
            "verified_by": "user",
        }
        self._save()

    def remove(self, ipo_id: str):
        if ipo_id in self.cache:
            del self.cache[ipo_id]
            self._save()

    def all(self) -> Dict:
        return self.cache


# -----------------------------------------------------------------------------
# NSE/BSE Official Listing Checkers (for auto-verification)
# -----------------------------------------------------------------------------

class NSEListingChecker:
    """
    Check NSE for recently listed IPOs.
    Uses NSE's official API endpoints.
    """

    BASE = "https://www.nseindia.com/api"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        # Prime session with cookies
        try:
            self.session.get("https://www.nseindia.com", timeout=10)
        except Exception:
            pass

    def get_new_listings(self, days_back: int = 30) -> List:
        """
        Fetch recently listed companies from NSE.
        Returns list of ListingInfo-like objects.
        """
        from dataclasses import dataclass
        from datetime import date

        @dataclass
        class ListingInfo:
            company_name: str
            listing_date: date
            listing_price: float
            exchange: str
            symbol: str = ""

        results = []
        cutoff = date.today() - timedelta(days=days_back)

        # Try NSE's past-issues API for recently listed
        url = f"{self.BASE}/past-issues"
        params = {"type": "all", "csv": "false"}
        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("data", []):
                    try:
                        listing_date = datetime.fromisoformat(
                            item.get("listingDate", "").replace("Z", "+00:00")
                        ).date()
                        if listing_date < cutoff:
                            continue
                        results.append(ListingInfo(
                            company_name=item.get("companyName", ""),
                            listing_date=listing_date,
                            listing_price=float(item.get("listingPrice", 0) or 0),
                            exchange="NSE_SME" if item.get("isSME") else "NSE",
                            symbol=item.get("symbol", ""),
                        ))
                    except Exception:
                        continue
        except Exception:
            pass

        # Fallback: try the active IPOs and check which ones have listing dates
        # This catches IPOs that just listed but might not be in past-issues yet
        active = self._get_active_ipos_raw()
        for item in active:
            d = item.get("data", {})
            listing_date_str = d.get("listingDate", "")
            if listing_date_str:
                try:
                    listing_date = datetime.fromisoformat(listing_date_str.replace("Z", "+00:00")).date()
                    if cutoff <= listing_date <= date.today():
                        results.append(ListingInfo(
                            company_name=d.get("companyName", ""),
                            listing_date=listing_date,
                            listing_price=float(d.get("listingPrice", 0) or 0),
                            exchange="NSE_SME" if d.get("series") == "SM" else "NSE",
                            symbol=d.get("symbol", ""),
                        ))
                except Exception:
                    continue

        return results

    def _get_active_ipos_raw(self) -> List[Dict]:
        """Get raw active IPOs from NSE."""
        url = f"{self.BASE}/upcoming-issues"
        params = {"csv": "true"}
        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("active", [])
        except Exception:
            pass
        return []


class BSEListingChecker:
    """
    Check BSE for recently listed IPOs.
    Uses BSE's listing data.
    """

    BASE = "https://api.bseindia.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def get_new_listings(self, days_back: int = 30) -> List:
        """
        Fetch recently listed companies from BSE.
        Returns list of ListingInfo-like objects.
        """
        from dataclasses import dataclass
        from datetime import date

        @dataclass
        class ListingInfo:
            company_name: str
            listing_date: date
            listing_price: float
            exchange: str
            symbol: str = ""

        results = []
        cutoff = date.today() - timedelta(days=days_back)

        # BSE API for recently listed
        # BSE doesn't have a clean public API for this, try known endpoints
        urls = [
            f"{self.BASE}/BseIndiaAPI/api/StockReach_v2/StockReach/GetNewListings",
            f"{self.BASE}/BseIndiaAPI/api/Common/GetNewListings",
        ]

        for url in urls:
            try:
                resp = self.session.get(url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("Table", []) or data.get("data", []) or data:
                        try:
                            company = item.get("SC_NAME", "") or item.get("CompanyName", "")
                            listing_date_str = item.get("LISTING_DATE", "") or item.get("ListingDate", "")
                            listing_price = float(item.get("LISTING_PRICE", 0) or item.get("ListingPrice", 0) or 0)
                            scrip = item.get("SC_CODE", "") or item.get("ScripCode", "")

                            if not company:
                                continue

                            listing_date = None
                            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y"):
                                try:
                                    listing_date = datetime.strptime(listing_date_str, fmt).date()
                                    break
                                except Exception:
                                    continue

                            if listing_date and cutoff <= listing_date <= date.today():
                                results.append(ListingInfo(
                                    company_name=company,
                                    listing_date=listing_date,
                                    listing_price=listing_price,
                                    exchange="BSE_SME" if "SME" in str(item).upper() else "BSE",
                                    symbol=str(scrip),
                                ))
                        except Exception:
                            continue
                    break  # If one URL worked, don't try others
            except Exception:
                continue

        return results

class ApifyIPOClient:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    def _get(self, path: str, params: Dict = None) -> List[Dict]:
        url = f"{APIFY_BASE}{path}"
        resp = requests.get(url, headers=self.headers, params=params, timeout=APIFY_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            raise RuntimeError(f"Apify error: {data['error']}")
        # API returns {data: [...], count: N, _meta: ...} or direct array
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data if isinstance(data, list) else []

    def list_ipos(self, view: str = "live_upcoming_listed",
                  category: str = None, gmp_min: float = None,
                  listing_date_gte: str = None, listing_date_lte: str = None) -> List[IPOData]:
        params = {"view": view}
        if category: params["category"] = category
        if gmp_min: params["gmp_min"] = gmp_min
        if listing_date_gte: params["listing_date_gte"] = listing_date_gte
        if listing_date_lte: params["listing_date_lte"] = listing_date_lte
        raw = self._get("/ipos", params)
        return [self._parse_ipo(r) for r in raw]

    def get_ipo(self, ipo_id: str) -> Optional[IPOData]:
        raw = self._get(f"/ipos/{ipo_id}")
        return self._parse_ipo(raw) if raw else None

    def get_timeline(self, ipo_id: str) -> List[Dict]:
        return self._get(f"/ipos/{ipo_id}/timeline")

    def _parse_ipo(self, r: Dict) -> IPOData:
        # Map API field names to our dataclass
        fin = r.get("financials") or r.get("prospectus_financials")
        return IPOData(
            ipo_id=r.get("ipo_id", ""),
            company_name=r.get("company_name", ""),
            category=r.get("category", "mainboard"),
            current_status=r.get("current_status", "UNKNOWN"),
            current_gmp=self._to_float(r.get("current_gmp")),
            total_subscription_x=self._to_float(r.get("total_subscription_x")),
            qib_subscription_x=self._to_float(r.get("qib_subscription_x")),
            hni_subscription_x=self._to_float(r.get("hni_subscription_x")),
            retail_subscription_x=self._to_float(r.get("retail_subscription_x")),
            price_band_lower=self._to_float(r.get("price_band_lower")),
            price_band_upper=self._to_float(r.get("price_band_upper")),
            lot_size=self._to_int(r.get("lot_size")),
            min_investment_inr=self._to_int(r.get("min_investment_inr")),
            issue_size_cr=self._to_float(r.get("issue_size_cr")),
            subscription_open_date=r.get("subscription_open_date"),
            subscription_close_date=r.get("subscription_close_date"),
            listing_date=r.get("listing_date"),
            listing_price=self._to_float(r.get("listing_price")),
            listing_gain_pct=self._to_float(r.get("listing_gain_pct")),
            last_updated_utc=r.get("last_updated_utc", ""),
            financials=fin,
        )

    @staticmethod
    def _to_float(v) -> Optional[float]:
        if v is None or v == "" or v == "null":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_int(v) -> Optional[int]:
        if v is None or v == "" or v == "null":
            return None
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return None


# -----------------------------------------------------------------------------
# Scoring engine
# -----------------------------------------------------------------------------

class IPOScorer:
    """
    Scores an IPO 0-100 for listing gain probability.
    Weights based on academic literature + practitioner heuristics.
    """

    # Weight components (sum = 100)
    W_GMP = 25           # Grey market premium %
    W_SUBSCRIPTION = 25  # Overall subscription multiple
    W_QIB = 15           # QIB subscription (smart money)
    W_ISSUE_SIZE = 10    # Smaller issue = higher pop potential
    W_ANCHOR = 10        # Anchor investor quality
    W_FINANCIALS = 10    # Profitability, growth
    W_CATEGORY = 5       # Mainboard vs SME

    @classmethod
    def score(cls, ipo: IPOData) -> Dict[str, Any]:
        breakdown = {}
        total = 0.0

        # 1. GMP component (0-25)
        gmp_score = cls._score_gmp(ipo)
        breakdown["gmp"] = gmp_score
        total += gmp_score

        # 2. Overall subscription (0-25)
        sub_score = cls._score_subscription(ipo)
        breakdown["subscription"] = sub_score
        total += sub_score

        # 3. QIB subscription (0-15)
        qib_score = cls._score_qib(ipo)
        breakdown["qib"] = qib_score
        total += qib_score

        # 4. Issue size (0-10) — smaller is better for listing pop
        size_score = cls._score_issue_size(ipo)
        breakdown["issue_size"] = size_score
        total += size_score

        # 5. Anchor quality (0-10) — placeholder, needs prospectus data
        anchor_score = cls._score_anchor(ipo)
        breakdown["anchor"] = anchor_score
        total += anchor_score

        # 6. Financial health (0-10) — placeholder
        fin_score = cls._score_financials(ipo)
        breakdown["financials"] = fin_score
        total += fin_score

        # 7. Category (0-5) — mainboard slightly favored for liquidity
        cat_score = 5.0 if ipo.category == "mainboard" else 3.0
        breakdown["category"] = cat_score
        total += cat_score

        # Recommendation
        if total >= 70:
            rec = "STRONG_APPLY"
        elif total >= 55:
            rec = "APPLY"
        elif total >= 40:
            rec = "TRACK"
        else:
            rec = "AVOID"

        # Expected listing gain range (heuristic)
        gain_low, gain_high = cls._gain_range(total, ipo.gmp_pct)

        return {
            "score": round(total, 1),
            "recommendation": rec,
            "breakdown": {k: round(v, 1) for k, v in breakdown.items()},
            "expected_gain_low_pct": gain_low,
            "expected_gain_high_pct": gain_high,
            "gmp_pct": ipo.gmp_pct,
        }

    @staticmethod
    def _score_gmp(ipo: IPOData) -> float:
        """GMP as % of issue price: >50% = 25, 30-50% = 20, 15-30% = 12, 5-15% = 6, <5% = 2, negative = 0"""
        gmp_pct = ipo.gmp_pct
        if gmp_pct is None:
            return 5.0  # neutral if unknown
        if gmp_pct < 0:
            return 0.0
        if gmp_pct >= 50:
            return 25.0
        if gmp_pct >= 30:
            return 20.0
        if gmp_pct >= 15:
            return 12.0
        if gmp_pct >= 5:
            return 6.0
        return 2.0

    @staticmethod
    def _score_subscription(ipo: IPOData) -> float:
        """Overall subscription multiple: >100x = 25, 50-100 = 20, 20-50 = 14, 10-20 = 8, 5-10 = 4, <5 = 1"""
        sub = ipo.total_subscription_x
        if sub is None:
            return 5.0
        if sub >= 100:
            return 25.0
        if sub >= 50:
            return 20.0
        if sub >= 20:
            return 14.0
        if sub >= 10:
            return 8.0
        if sub >= 5:
            return 4.0
        return 1.0

    @staticmethod
    def _score_qib(ipo: IPOData) -> float:
        """QIB subscription: >100x = 15, 50-100 = 12, 20-50 = 8, 10-20 = 5, <10 = 2"""
        qib = ipo.qib_subscription_x
        if qib is None:
            # If only total known, estimate QIB ~ 40% of total for mainboard
            if ipo.total_subscription_x:
                return IPOScorer._score_qib_est(ipo.total_subscription_x * 0.4)
            return 4.0
        return IPOScorer._score_qib_est(qib)

    @staticmethod
    def _score_qib_est(qib: float) -> float:
        if qib >= 100:
            return 15.0
        if qib >= 50:
            return 12.0
        if qib >= 20:
            return 8.0
        if qib >= 10:
            return 5.0
        return 2.0

    @staticmethod
    def _score_issue_size(ipo: IPOData) -> float:
        """Issue size in crores: <100 = 10, 100-500 = 8, 500-1000 = 5, 1000-3000 = 3, >3000 = 1"""
        sz = ipo.issue_size_cr
        if sz is None:
            return 5.0
        if sz < 100:
            return 10.0
        if sz < 500:
            return 8.0
        if sz < 1000:
            return 5.0
        if sz < 3000:
            return 3.0
        return 1.0

    @staticmethod
    def _score_anchor(ipo: IPOData) -> float:
        """Anchor investor allocation % — needs prospectus parse. Placeholder."""
        # TODO: parse from financials.anchor_allocation_pct or anchor_book
        return 5.0  # neutral

    @staticmethod
    def _score_financials(ipo: IPOData) -> float:
        """Basic profitability check from parsed prospectus. Placeholder."""
        # TODO: parse revenue/EBITDA/PAT trends from financials
        fin = ipo.financials or {}
        if not fin:
            return 5.0
        # Heuristic: profitable + growing revenue = 10, profitable = 7, loss-making = 2
        pat = fin.get("pat") or fin.get("profit_after_tax")
        rev = fin.get("revenue") or fin.get("total_revenue")
        if pat and pat > 0 and rev and rev > 0:
            return 10.0
        if pat and pat > 0:
            return 7.0
        return 2.0

    @staticmethod
    def _gain_range(score: float, gmp_pct: Optional[float]) -> tuple[float, float]:
        """Heuristic mapping from score + GMP to expected listing gain range."""
        base_low = max(0, score - 30) / 2
        base_high = base_low + 20
        if gmp_pct:
            # GMP is market's prediction; listing gain often tracks GMP but with variance
            base_low = max(base_low, gmp_pct * 0.5)
            base_high = max(base_high, gmp_pct * 1.3)
        return round(base_low, 1), round(base_high, 1)


# -----------------------------------------------------------------------------
# Strategy class (fits registry)
# -----------------------------------------------------------------------------

class IPOListingGains(Strategy):
    """
    Event-driven IPO strategy. Not a daily scanner — runs on demand.
    Dashboard shows a table of scored IPOs with Apply/Track/Avoid.
    """

    @property
    def name(self) -> str:
        return "IPO Listing Gains"

    @property
    def key(self) -> str:
        return "ipo_listing_gains"

    @property
    def timeframe(self) -> str:
        return "event"

    @property
    def min_bars(self) -> int:
        return 0

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        # No candle data needed; return dummy frame
        return pd.DataFrame({"dummy": [1]})

    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        # Not used in traditional sense; we use score() instead
        return pd.Series([False])

    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series([False])

    def format_metrics(self, df: pd.DataFrame) -> str:
        return "Event-driven IPO strategy — see dashboard tab"

    def sort_value(self, df: pd.DataFrame) -> float:
        return 0.0

    # --- Public API for dashboard ---

    def fetch_and_score(self, view: str = "live_upcoming_listed",
                        category: str = None) -> Dict[str, Any]:
        """Fetch IPOs from Apify, score them, apply verified cache, return dict with data + metadata."""
        if not APIFY_TOKEN:
            raise RuntimeError("APIFY_TOKEN not set in config/keys.py")

        client = ApifyIPOClient(APIFY_TOKEN)
        ipos = client.list_ipos(view=view, category=category)

        # Apify freshness: most recent last_updated_utc across all IPOs
        apify_freshness = None
        if ipos:
            try:
                apify_freshness = max(
                    datetime.fromisoformat(i.last_updated_utc.replace('Z', '+00:00'))
                    for i in ipos if i.last_updated_utc
                )
            except Exception:
                pass

        # Load user-verified listings
        verified_cache = VerifiedListingsCache()
        verified = verified_cache.all()

        # NSE active IPOs for cross-check
        nse_client = NSEActiveIPOClient()
        nse_active = {self._normalize_company(a["company"]): a for a in nse_client.get_active_ipos_formatted()}

        results = []
        for ipo in ipos:
            # Apply user-verified corrections first
            if ipo.ipo_id in verified:
                v = verified[ipo.ipo_id]
                ipo.current_status = "LISTED"
                ipo.listing_date = v.get("listing_date")
                ipo.listing_price = v.get("listing_price")
                ipo.listing_gain_pct = v.get("listing_gain_pct")
                ipo.current_gmp = None
                ipo._verified_source = "user_cache"

            # Cross-check with NSE active IPOs
            nse_key = self._normalize_company(ipo.company_name)
            nse_match = nse_active.get(nse_key)
            nse_status = nse_match.get("status") if nse_match else None

            scored = IPOScorer.score(ipo)
            results.append({
                "ipo_id": ipo.ipo_id,
                "company": ipo.company_name,
                "category": ipo.category,
                "status": ipo.current_status,
                "issue_price": ipo.issue_price,
                "gmp": ipo.current_gmp,
                "gmp_pct": ipo.gmp_pct,
                "subscription_x": ipo.total_subscription_x,
                "qib_x": ipo.qib_subscription_x,
                "issue_size_cr": ipo.issue_size_cr,
                "sub_open": ipo.subscription_open_date,
                "sub_close": ipo.subscription_close_date,
                "listing_date": ipo.listing_date,
                "days_to_listing": ipo.days_to_listing,
                "verified_source": getattr(ipo, '_verified_source', None),
                "nse_active_status": nse_status,
                "nse_symbol": nse_match.get("symbol") if nse_match else None,
                "nse_series": nse_match.get("series") if nse_match else None,
                **scored,
            })

        # Sort: verified LISTED first, then LISTED, ALLOTMENT_DONE, SUBSCRIPTION_OPEN, DRHP_FILED
        status_order = {"LISTED": 0, "ALLOTMENT_DONE": 1, "SUBSCRIPTION_OPEN": 2, "DRHP_FILED": 3, "WITHDRAWN": 4}
        results.sort(key=lambda x: (status_order.get(x["status"], 9), -x["score"]))

        return {
            "ipos": results,
            "meta": {
                "total": len(results),
                "apify_freshness_utc": apify_freshness.isoformat() if apify_freshness else None,
                "fetched_at_utc": datetime.utcnow().isoformat() + "Z",
                "verified_count": len([r for r in results if r.get("verified_source")]),
                "nse_active_count": len(nse_active),
            }
        }

    def get_nse_active_ipos(self) -> List[Dict]:
        """Get currently active IPOs from NSE for cross-reference."""
        nse_client = NSEActiveIPOClient()
        return nse_client.get_active_ipos_formatted()

    def verify_listing(self, ipo_id: str, listing_date: str, listing_price: float, gain_pct: float):
        """User marks an IPO as verified listed (stored in local cache)."""
        cache = VerifiedListingsCache()
        cache.set(ipo_id, {
            "listing_date": listing_date,
            "listing_price": listing_price,
            "listing_gain_pct": gain_pct,
        })

    def remove_verification(self, ipo_id: str):
        """Remove a user verification."""
        cache = VerifiedListingsCache()
        cache.remove(ipo_id)

    @staticmethod
    def _normalize_company(name: str) -> str:
        s = name.upper()
        for suffix in [" LIMITED", " LTD", " PVT", " PRIVATE", " INDIA", " COMPANY", " CORPORATION", " CORP", " INC", " LLP"]:
            s = s.replace(suffix, "")
        s = re.sub(r"[^A-Z0-9]", "", s)
        return s

    def get_listing_performance(self, days_back: int = 30) -> List[Dict]:
        """Fetch recently listed IPOs and their actual gains."""
        if not APIFY_TOKEN:
            raise RuntimeError("APIFY_TOKEN not set")
        client = ApifyIPOClient(APIFY_TOKEN)
        # Fetch listed IPOs from last N days
        from datetime import timedelta
        since = (date.today() - timedelta(days=days_back)).isoformat()
        ipos = client.list_ipos(view="live_upcoming_listed", category="mainboard",
                                listing_date_gte=since)
        return [
            {
                "company": ipo.company_name,
                "issue_price": ipo.issue_price,
                "listing_price": ipo.listing_price,
                "listing_gain_pct": ipo.listing_gain_pct,
                "listing_date": ipo.listing_date,
                "gmp_at_listing": ipo.current_gmp,
            }
            for ipo in ipos
            if ipo.current_status == "LISTED" and ipo.listing_price is not None
        ]

    # ---------------------------------------------------------------------
    # Automated Weekly Verification
    # ---------------------------------------------------------------------

    def auto_verify_listings(self, send_telegram: bool = False) -> Dict[str, Any]:
        """
        Automated verification job — checks if Apify has updated any IPOs to LISTED status
        with listing_price (Apify eventually populates these).
        
        Run weekly via cron/Task Scheduler. Returns summary.
        """
        from config.keys import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

        results = {
            "updated": [],
            "skipped": [],
            "errors": [],
        }

        try:
            if not APIFY_TOKEN:
                raise RuntimeError("APIFY_TOKEN not set")
            apify_client = ApifyIPOClient(APIFY_TOKEN)
            apify_ipos = apify_client.list_ipos(view="live_upcoming_listed")

            cache = VerifiedListingsCache()

            for ipo in apify_ipos:
                # Skip if already verified
                if ipo.ipo_id in cache.all():
                    results["skipped"].append({
                        "company": ipo.company_name,
                        "reason": "already_verified",
                    })
                    continue

                # Check if Apify now has listing data
                if (ipo.current_status == "LISTED" 
                    and ipo.listing_price is not None 
                    and ipo.listing_date is not None):
                    
                    gain_pct = None
                    if ipo.issue_price and ipo.listing_price:
                        gain_pct = round(
                            (ipo.listing_price - ipo.issue_price) / ipo.issue_price * 100, 2
                        )
                    
                    cache.set(ipo.ipo_id, {
                        "listing_date": ipo.listing_date,
                        "listing_price": ipo.listing_price,
                        "listing_gain_pct": gain_pct,
                    })
                    results["updated"].append({
                        "company": ipo.company_name,
                        "ipo_id": ipo.ipo_id,
                        "listing_date": ipo.listing_date,
                        "listing_price": ipo.listing_price,
                        "gain_pct": gain_pct,
                    })
                else:
                    results["skipped"].append({
                        "company": ipo.company_name,
                        "reason": "no_listing_data_in_apify",
                    })

        except Exception as e:
            results["errors"].append(str(e))

        # Build summary
        summary_lines = [
            f"[BOT] *Weekly IPO Auto-Verification* ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
            f"[OK] Auto-verified from Apify: {len(results['updated'])}",
            f"[SKIP] Skipped: {len(results['skipped'])}",
            f"[ERR] Errors: {len(results['errors'])}",
        ]
        if results["updated"]:
            summary_lines.append("\n*Newly Verified (from Apify):*")
            for u in results["updated"][:10]:
                gain = f" {u['gain_pct']:+.1f}%" if u.get("gain_pct") else ""
                summary_lines.append(f"  • {u['company']} — {u['listing_date']} @ ₹{u['listing_price']}{gain}")
            if len(results["updated"]) > 10:
                summary_lines.append(f"  ... and {len(results['updated']) - 10} more")

        results["summary_msg"] = "\n".join(summary_lines)

        # Send Telegram if requested
        if send_telegram and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            try:
                import requests
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                requests.post(url, json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": results["summary_msg"],
                    "parse_mode": "Markdown",
                }, timeout=10)
            except Exception as e:
                results["errors"].append(f"Telegram send failed: {e}")

        return results




# -----------------------------------------------------------------------------
# CLI Entry Point for Weekly Cron / Windows Task Scheduler
# -----------------------------------------------------------------------------

def run_weekly_verification():
    """CLI entry point: python -m strategies.ipo_listing --verify-weekly"""
    import argparse
    parser = argparse.ArgumentParser(description="Weekly IPO auto-verification (checks Apify for listing updates)")
    parser.add_argument("--verify-weekly", action="store_true", help="Run auto-verification")
    parser.add_argument("--telegram", action="store_true", help="Send Telegram summary")
    args = parser.parse_args()

    if not args.verify_weekly:
        parser.print_help()
        return

    ipo = IPOListingGains()
    result = ipo.auto_verify_listings(send_telegram=args.telegram)
    print(result["summary_msg"])
    if result["errors"]:
        print("\nErrors:", result["errors"])


if __name__ == "__main__":
    run_weekly_verification()
