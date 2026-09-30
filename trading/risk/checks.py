"""Tri-state check result shared by every risk evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Check:
    key: str
    status: CheckStatus
    detail: str

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASS

    def to_dict(self) -> dict:
        return {"key": self.key, "status": self.status.value, "detail": self.detail}


def passed(key: str, detail: str) -> Check:
    return Check(key, CheckStatus.PASS, detail)


def failed(key: str, detail: str) -> Check:
    return Check(key, CheckStatus.FAIL, detail)


def unknown(key: str, detail: str) -> Check:
    return Check(key, CheckStatus.UNKNOWN, detail)
