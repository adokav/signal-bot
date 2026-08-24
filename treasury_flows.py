"""Official Treasury flow data adapters used by the macro research layer.

This module intentionally separates planned buyback capacity, realized buyback
results and gross Treasury auction issuance.  None of these values is itself a
trading signal and no net-liquidity score is produced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from xml.etree import ElementTree

import requests


def _utc(value: datetime | None = None) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _parse_iso_date(value: str) -> datetime:
    return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _to_float(value: Any) -> float:
    if value in (None, "", "null", "NULL"):
        raise ValueError("missing numeric Treasury value")
    return float(str(value).replace(",", "").replace("$", ""))


@dataclass(frozen=True)
class TreasuryBuybackResult:
    operation_time: str
    settlement_date: str | None
    operation_type: str | None
    security_type: str | None
    maturity_bucket: str | None
    total_par_offered_usd: float
    total_par_accepted_usd: float
    max_par_redeemable_usd: float | None
    issues_eligible: int | None
    issues_accepted: int | None
    source_url: str
    available_time: str
    ingested_time: str

    def validate(self) -> None:
        operation = datetime.fromisoformat(self.operation_time.replace("Z", "+00:00"))
        available = datetime.fromisoformat(self.available_time.replace("Z", "+00:00"))
        ingested = datetime.fromisoformat(self.ingested_time.replace("Z", "+00:00"))
        if available > ingested:
            raise ValueError("Treasury result claims future availability")
        if operation > available:
            raise ValueError("Treasury result available before operation")
        if self.total_par_offered_usd < 0 or self.total_par_accepted_usd < 0:
            raise ValueError("negative Treasury buyback result")
        if self.total_par_accepted_usd > self.total_par_offered_usd:
            raise ValueError("accepted Treasury buyback exceeds amount offered")
        if self.max_par_redeemable_usd is not None and self.total_par_accepted_usd > self.max_par_redeemable_usd:
            raise ValueError("accepted Treasury buyback exceeds announced maximum")


class TreasuryBuybackResultsAdapter:
    """Parse one official TreasuryDirect buyback-results XML document.

    TreasuryDirect documents aggregate result fields including total par
    offered/accepted and, after the 2025 XML revision, operationStatus=Results.
    A caller supplies a pinned official XML URL.  Discovery/index crawling is
    deliberately separate so research can archive the exact source used.
    """

    def __init__(self, result_url: str, session: requests.Session | None = None):
        if not result_url.startswith("https://www.treasurydirect.gov/"):
            raise ValueError("buyback result must use official TreasuryDirect source")
        self.result_url = result_url
        self.session = session or requests.Session()

    @staticmethod
    def _norm(tag: str) -> str:
        return tag.split("}")[-1].lower().replace("_", "").replace("-", "")

    @classmethod
    def _first(cls, root: ElementTree.Element, *names: str) -> str | None:
        wanted = {name.lower().replace("_", "").replace("-", "") for name in names}
        for node in root.iter():
            if cls._norm(node.tag) in wanted and node.text and node.text.strip():
                return node.text.strip()
        return None

    @staticmethod
    def _money_optional(value: str | None) -> float | None:
        if value is None:
            return None
        return _to_float(value)

    @staticmethod
    def _int_optional(value: str | None) -> int | None:
        if value in (None, ""):
            return None
        return int(float(str(value).replace(",", "")))

    @staticmethod
    def _operation_time(value: str) -> str:
        raw = value.strip()
        # Current Treasury XML includes an offset-aware operation DTM.  Keep
        # that exact instant when present; otherwise accept only an ISO date.
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return _parse_iso_date(raw).isoformat()

    @classmethod
    def parse_xml(cls, xml_text: str, *, source_url: str, ingested_at: datetime) -> TreasuryBuybackResult:
        root = ElementTree.fromstring(xml_text)
        status = cls._first(root, "operationStatus")
        if status is not None and status.strip().lower() != "results":
            raise RuntimeError(f"Treasury XML is not a results document: {status}")

        operation = cls._first(root, "operationStartDateTime", "operationStartDTM", "operationDate")
        total_offered = cls._first(root, "totalParAmountOffered")
        total_accepted = cls._first(root, "totalParAmountAccepted")
        if not operation or total_offered is None or total_accepted is None:
            raise RuntimeError("Treasury results XML missing required aggregate fields")

        available = _utc(ingested_at)
        result = TreasuryBuybackResult(
            operation_time=cls._operation_time(operation),
            settlement_date=cls._first(root, "settlementDate"),
            operation_type=cls._first(root, "operationType"),
            security_type=cls._first(root, "securityType"),
            maturity_bucket=cls._first(root, "maturityBucket"),
            total_par_offered_usd=_to_float(total_offered),
            total_par_accepted_usd=_to_float(total_accepted),
            max_par_redeemable_usd=cls._money_optional(cls._first(root, "maxParAmountToBeRedeemed", "maximumParAmountToBeRedeemed")),
            issues_eligible=cls._int_optional(cls._first(root, "numberIssuesEligible", "noIssueEligible", "numberOfIssuesEligible")),
            issues_accepted=cls._int_optional(cls._first(root, "numberIssuesAccepted", "noIssuesAccepted", "numberOfIssuesAccepted")),
            source_url=source_url,
            available_time=available.isoformat(),
            ingested_time=available.isoformat(),
        )
        result.validate()
        return result

    def fetch(self, *, as_of: datetime | None = None) -> TreasuryBuybackResult:
        response = self.session.get(self.result_url, timeout=20)
        response.raise_for_status()
        return self.parse_xml(response.text, source_url=self.result_url, ingested_at=_utc(as_of))


@dataclass(frozen=True)
class TreasuryAuctionIssuance:
    record_date: str
    cusip: str
    security_type: str
    security_term: str
    auction_date: str
    issue_date: str
    reopening: str | None
    offering_amount_usd: float | None
    total_accepted_usd: float
    available_time: str
    ingested_time: str

    def validate(self) -> None:
        record = _parse_iso_date(self.record_date)
        issue = _parse_iso_date(self.issue_date)
        available = datetime.fromisoformat(self.available_time.replace("Z", "+00:00"))
        ingested = datetime.fromisoformat(self.ingested_time.replace("Z", "+00:00"))
        if available > ingested:
            raise ValueError("Treasury auction row claims future availability")
        if record > available:
            raise ValueError("Treasury auction row available before record date")
        if self.total_accepted_usd < 0:
            raise ValueError("negative Treasury auction acceptance")
        if issue.year < 1900:
            raise ValueError("invalid Treasury issue date")


class TreasuryAuctionAdapter:
    """Official FiscalData auction adapter for gross marketable issuance.

    `total_accepted` is treated as gross accepted auction amount.  It is not
    net issuance and must not be combined with buybacks as a net-liquidity
    number until maturities/redemptions and TGA effects are handled separately.
    """

    base_url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
    fields = (
        "record_date,cusip,security_type,security_term,auction_date,issue_date,"
        "reopening,offering_amt,total_accepted"
    )

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    @classmethod
    def parse_rows(cls, payload: dict[str, Any], *, ingested_at: datetime) -> tuple[TreasuryAuctionIssuance, ...]:
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("FiscalData auction payload missing data rows")
        available = _utc(ingested_at)
        parsed: list[TreasuryAuctionIssuance] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            required = ("record_date", "cusip", "security_type", "security_term", "auction_date", "issue_date", "total_accepted")
            if any(row.get(name) in (None, "", "null") for name in required):
                continue
            item = TreasuryAuctionIssuance(
                record_date=str(row["record_date"]),
                cusip=str(row["cusip"]),
                security_type=str(row["security_type"]),
                security_term=str(row["security_term"]),
                auction_date=str(row["auction_date"]),
                issue_date=str(row["issue_date"]),
                reopening=None if row.get("reopening") in (None, "", "null") else str(row.get("reopening")),
                offering_amount_usd=None if row.get("offering_amt") in (None, "", "null") else _to_float(row.get("offering_amt")),
                total_accepted_usd=_to_float(row["total_accepted"]),
                available_time=available.isoformat(),
                ingested_time=available.isoformat(),
            )
            item.validate()
            parsed.append(item)
        if not parsed:
            raise RuntimeError("no valid Treasury auction issuance rows")
        return tuple(parsed)

    def fetch_issue_window(self, *, start_date: str, end_date: str, as_of: datetime | None = None) -> tuple[TreasuryAuctionIssuance, ...]:
        ingested = _utc(as_of)
        response = self.session.get(
            self.base_url,
            params={
                "format": "json",
                "fields": self.fields,
                "filter": f"issue_date:gte:{start_date},issue_date:lte:{end_date}",
                "sort": "issue_date,cusip",
                "page[size]": 1000,
            },
            timeout=20,
        )
        response.raise_for_status()
        return self.parse_rows(response.json(), ingested_at=ingested)


def gross_issuance_total(rows: Iterable[TreasuryAuctionIssuance]) -> float:
    """Sum accepted auction amounts without pretending this is net liquidity."""
    total = 0.0
    for row in rows:
        row.validate()
        total += row.total_accepted_usd
    return total
