"""Official U.S. Treasury operation data for macro research.

This module intentionally stops at normalized cash-flow facts.  It does not
convert Treasury operations into bullish/bearish scores and it does not have
trade authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests


FISCALDATA_AUCTIONS_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v1/accounting/od/auctions_query"
)


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _money(value: Any) -> float:
    if value in (None, "", "null", "None"):
        raise ValueError("missing Treasury amount")
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    return float(cleaned)


def _official_host(url: str, allowed: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed:
        raise ValueError(f"unapproved Treasury source: {url}")


@dataclass(frozen=True)
class TreasuryBuybackResult:
    operation_time: str
    settlement_date: str | None
    operation_type: str | None
    maturity_bucket: str | None
    total_par_offered_usd: float
    total_par_accepted_usd: float
    source_url: str
    available_time: str
    ingested_time: str

    def validate(self) -> None:
        operated = datetime.fromisoformat(self.operation_time.replace("Z", "+00:00"))
        available = datetime.fromisoformat(self.available_time.replace("Z", "+00:00"))
        ingested = datetime.fromisoformat(self.ingested_time.replace("Z", "+00:00"))
        if operated.tzinfo is None:
            operated = operated.replace(tzinfo=timezone.utc)
        if available.tzinfo is None:
            available = available.replace(tzinfo=timezone.utc)
        if ingested.tzinfo is None:
            ingested = ingested.replace(tzinfo=timezone.utc)
        if operated.astimezone(timezone.utc) > available.astimezone(timezone.utc):
            raise ValueError("buyback result available before operation")
        if available.astimezone(timezone.utc) > ingested.astimezone(timezone.utc):
            raise ValueError("buyback result claims future availability")
        if self.total_par_offered_usd < 0 or self.total_par_accepted_usd < 0:
            raise ValueError("negative buyback amount")
        if self.total_par_accepted_usd > self.total_par_offered_usd:
            raise ValueError("accepted buyback exceeds offered amount")


class TreasuryBuybackResultsAdapter:
    """Parse one official TreasuryDirect buyback-results XML document.

    TreasuryDirect documents that result files expose total par amount offered
    and accepted, while individual result XML files carry operation timestamps.
    The adapter accepts a pinned XML URL rather than scraping changing HTML.
    """

    allowed_hosts = ("www.treasurydirect.gov", "treasurydirect.gov")

    def __init__(self, result_url: str, session: requests.Session | None = None):
        _official_host(result_url, self.allowed_hosts)
        self.result_url = result_url
        self.session = session or requests.Session()

    @staticmethod
    def _normalized(node: ElementTree.Element) -> dict[str, str]:
        values: dict[str, str] = {}
        for child in node.iter():
            key = child.tag.split("}")[-1].lower().replace("_", "").replace("-", "")
            if child.text and child.text.strip():
                values.setdefault(key, child.text.strip())
        return values

    @staticmethod
    def _pick(values: dict[str, str], *names: str) -> str | None:
        for name in names:
            key = name.lower().replace("_", "").replace("-", "")
            if key in values:
                return values[key]
        return None

    @classmethod
    def parse_xml(
        cls,
        xml_text: str,
        *,
        source_url: str,
        ingested_at: datetime,
    ) -> TreasuryBuybackResult:
        _official_host(source_url, cls.allowed_hosts)
        root = ElementTree.fromstring(xml_text)
        values = cls._normalized(root)
        operation = cls._pick(values, "operationDate", "operationStartDate", "operationStartDtm")
        offered = cls._pick(values, "totalParAmountOffered", "totalParOffered")
        accepted = cls._pick(values, "totalParAmountAccepted", "totalParAccepted")
        if not operation or offered is None or accepted is None:
            raise RuntimeError("Treasury buyback result missing required fields")

        # Result XML may expose a full timestamp or only an operation date.
        op_text = operation.strip()
        operated: datetime | None = None
        for parser in (
            lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")),
            lambda x: datetime.strptime(x[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc),
            lambda x: datetime.strptime(x[:10], "%m/%d/%Y").replace(tzinfo=timezone.utc),
        ):
            try:
                operated = parser(op_text)
                break
            except (ValueError, TypeError):
                continue
        if operated is None:
            raise RuntimeError(f"unrecognized buyback operation timestamp: {operation}")
        if operated.tzinfo is None:
            operated = operated.replace(tzinfo=timezone.utc)
        operated = operated.astimezone(timezone.utc)
        ingested = _utc(ingested_at)

        result = TreasuryBuybackResult(
            operation_time=operated.isoformat(),
            settlement_date=cls._pick(values, "settlementDate"),
            operation_type=cls._pick(values, "operationType"),
            maturity_bucket=cls._pick(values, "maturityBucket", "securityTypeMaturityRange"),
            total_par_offered_usd=_money(offered),
            total_par_accepted_usd=_money(accepted),
            source_url=source_url,
            # We do not invent Treasury's intraday publication timestamp.
            # For live use, availability is the instant our process obtained it.
            available_time=ingested.isoformat(),
            ingested_time=ingested.isoformat(),
        )
        result.validate()
        return result

    def fetch(self, *, as_of: datetime | None = None) -> TreasuryBuybackResult:
        ingested = _utc(as_of)
        response = self.session.get(self.result_url, timeout=20)
        response.raise_for_status()
        return self.parse_xml(response.text, source_url=self.result_url, ingested_at=ingested)


@dataclass(frozen=True)
class TreasuryAuctionIssuance:
    record_date: str
    auction_date: str
    issue_date: str
    cusip: str
    security_type: str
    security_term: str
    total_accepted_usd: float
    source_url: str
    available_time: str
    ingested_time: str

    def validate(self) -> None:
        record = _iso_date(self.record_date)
        auction = _iso_date(self.auction_date)
        issue = _iso_date(self.issue_date)
        available = datetime.fromisoformat(self.available_time.replace("Z", "+00:00"))
        ingested = datetime.fromisoformat(self.ingested_time.replace("Z", "+00:00"))
        if available.tzinfo is None:
            available = available.replace(tzinfo=timezone.utc)
        if ingested.tzinfo is None:
            ingested = ingested.replace(tzinfo=timezone.utc)
        if record > available.astimezone(timezone.utc):
            raise ValueError("auction record published after claimed availability")
        if auction > issue:
            raise ValueError("Treasury auction occurs after issue date")
        if available.astimezone(timezone.utc) > ingested.astimezone(timezone.utc):
            raise ValueError("auction row claims future availability")
        if self.total_accepted_usd < 0:
            raise ValueError("negative Treasury issuance")


class TreasuryAuctionIssuanceAdapter:
    """Official Fiscal Data auction-results adapter.

    `total_accepted` is used as an observed gross issuance fact. Reopenings and
    maturities are retained as rows rather than silently netted here; research
    aggregation must define the exact cash-flow window separately.
    """

    base_url = FISCALDATA_AUCTIONS_URL

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    @classmethod
    def parse_rows(cls, payload: dict[str, Any], *, ingested_at: datetime) -> tuple[TreasuryAuctionIssuance, ...]:
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("Fiscal Data auction payload missing data rows")
        ingested = _utc(ingested_at)
        output: list[TreasuryAuctionIssuance] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            required = ("record_date", "auction_date", "issue_date", "cusip", "security_type", "security_term", "total_accepted")
            if any(row.get(name) in (None, "", "null") for name in required):
                continue
            item = TreasuryAuctionIssuance(
                record_date=str(row["record_date"]),
                auction_date=str(row["auction_date"]),
                issue_date=str(row["issue_date"]),
                cusip=str(row["cusip"]),
                security_type=str(row["security_type"]),
                security_term=str(row["security_term"]),
                total_accepted_usd=_money(row["total_accepted"]),
                source_url=cls.base_url,
                # Fiscal Data's record_date is publication date but not an
                # intraday release timestamp, so live availability is ingestion.
                available_time=ingested.isoformat(),
                ingested_time=ingested.isoformat(),
            )
            item.validate()
            output.append(item)
        if not output:
            raise RuntimeError("no valid Treasury auction issuance rows")
        return tuple(output)

    def fetch_recent(self, *, as_of: datetime | None = None, page_size: int = 100) -> tuple[TreasuryAuctionIssuance, ...]:
        ingested = _utc(as_of)
        response = self.session.get(
            self.base_url,
            params={
                "format": "json",
                "fields": "record_date,cusip,security_type,security_term,auction_date,issue_date,total_accepted",
                "sort": "-record_date,-auction_date",
                "page[size]": max(1, min(int(page_size), 1000)),
                "page[number]": 1,
            },
            timeout=20,
        )
        response.raise_for_status()
        return self.parse_rows(response.json(), ingested_at=ingested)


@dataclass(frozen=True)
class TreasurySupplyWindow:
    gross_issuance_usd: float
    realized_buybacks_usd: float
    net_market_supply_usd: float


def treasury_supply_window(
    issuance: Iterable[TreasuryAuctionIssuance],
    buybacks: Iterable[TreasuryBuybackResult],
) -> TreasurySupplyWindow:
    """Mechanical supply accounting, not a liquidity or risk-on score."""
    gross = sum(row.total_accepted_usd for row in issuance)
    redeemed = sum(row.total_par_accepted_usd for row in buybacks)
    return TreasurySupplyWindow(
        gross_issuance_usd=gross,
        realized_buybacks_usd=redeemed,
        net_market_supply_usd=gross - redeemed,
    )
