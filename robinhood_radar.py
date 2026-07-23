"""Push-only Robinhood Chain early-opportunity radar.

Discovery only: no orders, signatures, trade-universe changes, or Telegram
``getUpdates`` calls. GeckoTerminal data is scored and stage-change alerts are
sent with Telegram ``sendMessage``.
"""

from __future__ import annotations

import html
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
EXPLORER_URL = "https://robinhoodchain.blockscout.com"
CHAIN_ID = 4663
QUOTES = frozenset({"ETH", "WETH", "USDC", "USDT", "USDG", "USDE", "DAI"})
STAGES = {"BLOCKED": 0, "WATCH": 1, "EARLY": 2, "BUILDING": 3, "HOT": 4}


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if not raw else raw.strip().lower() in {"1", "true", "yes", "on", "evet"}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return default if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return default if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, low: int, high: int | None = None) -> int:
    value = _integer(os.getenv(name), default)
    value = max(low, value)
    return min(high, value) if high is not None else value


def _env_float(name: str, default: float, low: float) -> float:
    return max(low, _num(os.getenv(name), default))


def _timestamp(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def _usd(value: float) -> str:
    value = max(0.0, value or 0.0)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= limit:
            return f"${value / limit:.2f}{suffix}"
    return f"${value:.0f}"


@dataclass(frozen=True)
class RadarConfig:
    enabled: bool = True
    network_id: str = "robinhood"
    interval: int = 180
    timeout: int = 15
    pages: int = 2
    top_n: int = 8
    min_liquidity: float = 25_000.0
    min_alert_score: int = 64
    max_fdv_liquidity: float = 200.0
    cooldown: int = 21_600
    state_file: str = "/data/robinhood_radar_state.json"
    startup_report: bool = True
    token: str = ""
    chat_id: str = ""
    gecko_base: str = GECKO_BASE
    rpc_url: str = RPC_URL
    explorer_url: str = EXPLORER_URL

    @classmethod
    def from_env(cls) -> "RadarConfig":
        return cls(
            enabled=_bool("ROBINHOOD_RADAR_ENABLED", True),
            network_id=os.getenv("ROBINHOOD_RADAR_NETWORK_ID", "robinhood").strip() or "robinhood",
            interval=_env_int("ROBINHOOD_RADAR_INTERVAL_SECONDS", 180, 60, 3600),
            timeout=_env_int("ROBINHOOD_RADAR_REQUEST_TIMEOUT_SECONDS", 15, 3, 60),
            pages=_env_int("ROBINHOOD_RADAR_PAGES_PER_FEED", 2, 1, 5),
            top_n=_env_int("ROBINHOOD_RADAR_TOP_N", 8, 1, 20),
            min_liquidity=_env_float("ROBINHOOD_RADAR_MIN_LIQUIDITY_USD", 25_000, 0),
            min_alert_score=_env_int("ROBINHOOD_RADAR_MIN_ALERT_SCORE", 64, 0, 100),
            max_fdv_liquidity=_env_float("ROBINHOOD_RADAR_MAX_FDV_LIQUIDITY_RATIO", 200, 10),
            cooldown=_env_int("ROBINHOOD_RADAR_ALERT_COOLDOWN_SECONDS", 21_600, 0),
            state_file=os.getenv("ROBINHOOD_RADAR_STATE_FILE", "/data/robinhood_radar_state.json").strip()
            or "/data/robinhood_radar_state.json",
            startup_report=_bool("ROBINHOOD_RADAR_SEND_STARTUP_REPORT", True),
            token=os.getenv("TOKEN", "").strip(),
            chat_id=os.getenv("CHAT_ID", "").strip(),
            gecko_base=os.getenv("ROBINHOOD_RADAR_GECKO_BASE_URL", GECKO_BASE).rstrip("/"),
            rpc_url=os.getenv("ROBINHOOD_RADAR_RPC_URL", RPC_URL).strip() or RPC_URL,
            explorer_url=os.getenv("ROBINHOOD_RADAR_EXPLORER_URL", EXPLORER_URL).rstrip("/"),
        )


@dataclass(frozen=True)
class Pool:
    pool: str
    token: str
    symbol: str
    name: str
    quote: str
    dex: str
    created: int
    price: float
    liquidity: float
    fdv: float
    market_cap: float
    volume_h1: float
    volume_h24: float
    change_m5: float
    change_h1: float
    change_h24: float
    buys_h1: int
    sells_h1: int
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    pool: str
    token: str
    symbol: str
    name: str
    quote: str
    dex: str
    score: int
    stage: str
    age_hours: float
    price: float
    liquidity: float
    fdv: float
    market_cap: float
    volume_h1: float
    volume_h24: float
    change_h1: float
    change_h24: float
    buys_h1: int
    sells_h1: int
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    sources: tuple[str, ...]
    execution_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "adokav-signal-bot/robinhood-radar-v1"})
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8))
    return session


class GeckoClient:
    def __init__(self, config: RadarConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or _session()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.session.get(f"{self.config.gecko_base}{path}", params=params, timeout=self.config.timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def chain_id(self) -> int | None:
        try:
            response = self.session.post(
                self.config.rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            result = (response.json() or {}).get("result")
            return int(result, 16) if isinstance(result, str) else None
        except Exception as exc:
            log.warning("Robinhood RPC check failed: %s", exc)
            return None

    def pools(self) -> list[Pool]:
        include = "base_token,quote_token,dex"
        feeds: list[tuple[str, dict[str, Any], str]] = []
        for page in range(1, self.config.pages + 1):
            feeds.append((f"/networks/{self.config.network_id}/new_pools", {"include": include, "page": page}, "NEW"))
        feeds += [
            (f"/networks/{self.config.network_id}/trending_pools", {"include": include, "duration": "1h"}, "TRENDING_1H"),
            (f"/networks/{self.config.network_id}/trending_pools", {"include": include, "duration": "24h"}, "TRENDING_24H"),
            (f"/networks/{self.config.network_id}/pools", {"include": include, "sort": "h24_volume_usd_desc"}, "TOP_VOLUME"),
        ]
        merged: dict[str, tuple[dict[str, Any], dict[str, dict[str, Any]], set[str]]] = {}
        failures: list[str] = []
        for path, params, source in feeds:
            try:
                payload = self._get(path, params)
            except Exception as exc:
                failures.append(source)
                log.warning("Robinhood feed failed | %s: %s", source, exc)
                continue
            included = {str(x.get("id")): x for x in payload.get("included") or [] if x.get("id")}
            for row in payload.get("data") or []:
                attrs = row.get("attributes") or {}
                address = str(attrs.get("address") or "").lower()
                if not address:
                    continue
                if address in merged:
                    old, refs, sources = merged[address]
                    refs.update(included)
                    sources.add(source)
                    if _num(attrs.get("reserve_in_usd")) > _num((old.get("attributes") or {}).get("reserve_in_usd")):
                        merged[address] = (row, refs, sources)
                else:
                    merged[address] = (row, dict(included), {source})
        if not merged and failures:
            raise RuntimeError("all pool feeds failed")
        return [pool for row, refs, sources in merged.values() if (pool := self._parse(row, refs, sources))]

    @staticmethod
    def _relation(row: dict[str, Any], name: str) -> str:
        return str(((((row.get("relationships") or {}).get(name) or {}).get("data") or {}).get("id")) or "")

    @classmethod
    def _parse(cls, row: dict[str, Any], refs: dict[str, dict[str, Any]], sources: set[str]) -> Pool | None:
        attrs = row.get("attributes") or {}
        base = (refs.get(cls._relation(row, "base_token"), {}).get("attributes") or {})
        quote = (refs.get(cls._relation(row, "quote_token"), {}).get("attributes") or {})
        dex_ref = refs.get(cls._relation(row, "dex"), {})
        dex = dex_ref.get("attributes") or {}
        bs, qs = str(base.get("symbol") or "").upper(), str(quote.get("symbol") or "").upper()
        swapped = bs in QUOTES and qs not in QUOTES
        chosen, paired = (quote, bs) if swapped else (base, qs)
        symbol = str(chosen.get("symbol") or "").upper()
        token = str(chosen.get("address") or "").lower()
        if not symbol or not token or symbol in QUOTES:
            return None
        volume = attrs.get("volume_usd") or {}
        change = attrs.get("price_change_percentage") or {}
        h1 = ((attrs.get("transactions") or {}).get("h1") or {})
        return Pool(
            pool=str(attrs.get("address") or "").lower(), token=token, symbol=symbol,
            name=str(chosen.get("name") or symbol), quote=paired,
            dex=str(dex.get("name") or dex_ref.get("id") or "UNKNOWN"),
            created=_timestamp(attrs.get("pool_created_at")),
            price=_num(attrs.get("quote_token_price_usd" if swapped else "base_token_price_usd")),
            liquidity=_num(attrs.get("reserve_in_usd")),
            fdv=0.0 if swapped else _num(attrs.get("fdv_usd")),
            market_cap=0.0 if swapped else _num(attrs.get("market_cap_usd")),
            volume_h1=_num(volume.get("h1")), volume_h24=_num(volume.get("h24")),
            change_m5=_num(change.get("m5")), change_h1=_num(change.get("h1")),
            change_h24=_num(change.get("h24")), buys_h1=_integer(h1.get("buys")),
            sells_h1=_integer(h1.get("sells")), sources=tuple(sorted(sources)),
        )


def score_pool(pool: Pool, config: RadarConfig, now: int | None = None) -> Candidate:
    now = int(now or time.time())
    age = max(0.0, (now - (pool.created or now)) / 3600)
    score, reasons, risks, blocked = 0.0, [], [], False
    if pool.liquidity < config.min_liquidity:
        blocked, risks = True, [f"likidite eşik altında ({_usd(pool.liquidity)})"]
    elif pool.liquidity < 50_000: score += 10; reasons.append("ilk likidite tabanı")
    elif pool.liquidity < 150_000: score += 18; reasons.append("orta likidite")
    elif pool.liquidity < 500_000: score += 24; reasons.append("güçlü likidite")
    else: score += 28; reasons.append("yüksek likidite")
    if age <= 2: score += 18; reasons.append("ilk 2 saat")
    elif age <= 12: score += 15; reasons.append("erken dönem")
    elif age <= 48: score += 10; reasons.append("ilk 48 saat")
    elif age <= 96: score += 5
    else: risks.append("erken dönem penceresi dışında")
    turnover = pool.volume_h1 / max(pool.liquidity, 1)
    if .05 <= turnover <= 2.5: score += 16; reasons.append("sağlıklı hacim/likidite")
    elif turnover <= 6 and turnover > 2.5: score += 11; reasons.append("çok güçlü hacim"); risks.append("yüksek devir")
    elif turnover > 6: score += 3; risks.append("wash-trade ihtimali")
    elif pool.volume_h1: score += 4
    else: risks.append("1s hacim oluşmadı")
    txs = pool.buys_h1 + pool.sells_h1
    if txs >= 250: score += 12; reasons.append("yüksek işlem katılımı")
    elif txs >= 80: score += 9; reasons.append("katılım güçleniyor")
    elif txs >= 20: score += 5
    else: risks.append("işlem sayısı zayıf")
    if txs:
        ratio = pool.buys_h1 / txs
        if ratio >= .62: score += 10; reasons.append("alımlar belirgin üstün")
        elif ratio >= .54: score += 6; reasons.append("alım tarafı üstün")
        elif ratio < .42: score -= 8; risks.append("satış baskısı")
    if 2 <= pool.change_h1 <= 35: score += 9; reasons.append("kontrollü 1s momentum")
    elif 35 < pool.change_h1 <= 80: score += 5; risks.append("1s hareket uzuyor")
    elif pool.change_h1 > 80: score -= 8; risks.append("1s parabolik hareket")
    elif pool.change_h1 <= -15: score -= 8; risks.append("1s sert geri çekilme")
    if pool.change_m5 > 30: score -= 6; risks.append("5dk ani sıçrama")
    if pool.change_h24 > 300: score -= 8; risks.append("24s aşırı genişleme")
    if pool.fdv and pool.liquidity:
        ratio = pool.fdv / pool.liquidity
        if ratio <= 15: score += 10; reasons.append("FDV/likidite dengeli")
        elif ratio <= 50: score += 6
        elif ratio > config.max_fdv_liquidity: blocked = True; risks.append(f"FDV/likidite aşırı ({ratio:.0f}x)")
        elif ratio > 100: score -= 7; risks.append("FDV likiditeye göre yüksek")
    else: risks.append("FDV doğrulanamadı")
    if "NEW" in pool.sources: score += 5; reasons.append("yeni havuz")
    if "TRENDING_1H" in pool.sources: score += 5; reasons.append("1s trend listesi")
    elif "TRENDING_24H" in pool.sources: score += 3
    if pool.quote in QUOTES: score += 3
    score_int = int(round(max(0, min(100, score))))
    stage = "BLOCKED" if blocked else "HOT" if score_int >= 78 else "BUILDING" if score_int >= 64 else "EARLY" if score_int >= 52 else "WATCH"
    return Candidate(
        pool.pool, pool.token, pool.symbol, pool.name, pool.quote, pool.dex,
        score_int, stage, round(age, 2), pool.price, pool.liquidity, pool.fdv,
        pool.market_cap, pool.volume_h1, pool.volume_h24, pool.change_h1,
        pool.change_h24, pool.buys_h1, pool.sells_h1, tuple(reasons[:6]),
        tuple(risks[:6]), pool.sources, False,
    )


def rank_pools(pools: Iterable[Pool], config: RadarConfig, now: int | None = None) -> list[Candidate]:
    best: dict[str, Candidate] = {}
    for pool in pools:
        item = score_pool(pool, config, now)
        old = best.get(item.token)
        if old is None or (item.score, item.liquidity, item.volume_h1) > (old.score, old.liquidity, old.volume_h1):
            best[item.token] = item
    return sorted(best.values(), key=lambda x: (x.stage != "BLOCKED", x.score, x.liquidity, x.volume_h1), reverse=True)[: config.top_n]


class State:
    def __init__(self, path: str):
        self.path, self.data = Path(path), {"version": 1, "last_scan_at": 0, "tokens": {}}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict): self.data.update(loaded)
        except FileNotFoundError: pass
        except Exception as exc: log.warning("Robinhood state load failed: %s", exc)
        if not isinstance(self.data.get("tokens"), dict): self.data["tokens"] = {}

    @property
    def first_scan(self) -> bool:
        return not bool(self.data.get("last_scan_at"))

    def alerts(self, candidates: Iterable[Candidate], config: RadarConfig, now: int) -> list[Candidate]:
        alerts, tokens = [], self.data.setdefault("tokens", {})
        for item in candidates:
            old = tokens.get(item.token) or {}
            old_stage, old_score, last_alert = str(old.get("stage") or "WATCH"), _integer(old.get("score")), _integer(old.get("last_alert_at"))
            qualified = item.stage in {"BUILDING", "HOT"} and item.score >= config.min_alert_score
            changed = not old or STAGES[item.stage] > STAGES.get(old_stage, 0) or item.score >= old_score + 10
            should_alert = qualified and changed and now - last_alert >= config.cooldown
            if should_alert: alerts.append(item); last_alert = now
            tokens[item.token] = {
                "symbol": item.symbol, "first_seen_at": _integer(old.get("first_seen_at"), now) or now,
                "last_seen_at": now, "stage": item.stage, "score": item.score,
                "last_alert_at": last_alert, "pool": item.pool,
            }
        cutoff = now - 14 * 86400
        self.data["tokens"] = {k: v for k, v in tokens.items() if _integer((v or {}).get("last_seen_at")) >= cutoff}
        self.data["last_scan_at"] = now
        return alerts

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc: log.warning("Robinhood state save failed: %s", exc)


def format_alert(items: Iterable[Candidate], config: RadarConfig, now: int | None = None, startup: bool = False) -> str:
    items = list(items)
    stamp = datetime.fromtimestamp(now or time.time(), tz=timezone.utc).astimezone().strftime("%d.%m %H:%M")
    lines = [f"<b>{'🟢 Robinhood Radar devrede' if startup else '🏹 Robinhood Chain erken fırsat alarmı'}</b>", f"Tarama: {stamp}"]
    if not items: lines.append("Uygun aday yok; radar taramaya devam ediyor.")
    for index, item in enumerate(items[: config.top_n], 1):
        pool_url = f"https://www.geckoterminal.com/{config.network_id}/pools/{item.pool}"
        token_url = f"{config.explorer_url}/token/{item.token}"
        lines += [
            "", f"<b>{index}. {html.escape(item.symbol)} · {item.stage} · {item.score}/100</b>",
            f"Likidite {_usd(item.liquidity)} · 1s hacim {_usd(item.volume_h1)} · Al/Sat {item.buys_h1}/{item.sells_h1}",
            f"Yaş {item.age_hours:.1f}s · 1s %{item.change_h1:+.1f} · 24s %{item.change_h24:+.1f}",
            "Güç: " + html.escape(", ".join(item.reasons[:3]) or "veri oluşuyor"),
        ]
        if item.risks: lines.append("Risk: " + html.escape(", ".join(item.risks[:2])))
        lines.append(f'<a href="{html.escape(pool_url)}">Havuz</a> · <a href="{html.escape(token_url)}">Kontrat</a>')
    lines += ["", "⚠️ <b>Gölge radar:</b> işlem açmaz, işlem evrenini değiştirmez ve kontrat güvenliği garantisi vermez."]
    return "\n".join(lines)[:4090]


class RadarService:
    def __init__(self, config: RadarConfig | None = None, client: GeckoClient | None = None, state: State | None = None):
        self.config = config or RadarConfig.from_env()
        self.client = client or GeckoClient(self.config)
        self.state = state or State(self.config.state_file)
        self.rpc_chain_id: int | None = None
        self.rpc_checked = False

    def scan_once(self, now: int | None = None) -> dict[str, Any]:
        now, errors = int(now or time.time()), []
        if not self.rpc_checked:
            self.rpc_chain_id, self.rpc_checked = self.client.chain_id(), True
            if self.rpc_chain_id not in (None, CHAIN_ID): errors.append(f"RPC_CHAIN_ID_MISMATCH:{self.rpc_chain_id}")
        try: candidates = rank_pools(self.client.pools(), self.config, now)
        except Exception as exc:
            candidates, errors = [], errors + [f"GECKOTERMINAL:{type(exc).__name__}"]
            log.warning("Robinhood scan failed: %s", exc)
        first = self.state.first_scan
        alerts = self.state.alerts(candidates, self.config, now)
        self.state.data.update({"network_id": self.config.network_id, "last_errors": errors})
        self.state.save()
        if alerts: self.send(format_alert(alerts, self.config, now))
        elif first and self.config.startup_report: self.send(format_alert([x for x in candidates if x.stage != "BLOCKED"][:5], self.config, now, True))
        return {
            "generated_at": now, "network_id": self.config.network_id,
            "rpc_chain_id": self.rpc_chain_id, "candidates": [x.to_dict() for x in candidates],
            "alerts": [x.to_dict() for x in alerts], "errors": errors, "can_authorize_trade": False,
        }

    def send(self, text: str) -> bool:
        if not self.config.token or not self.config.chat_id: return False
        try:
            response = self.client.session.post(
                f"https://api.telegram.org/bot{self.config.token}/sendMessage",
                json={"chat_id": self.config.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=self.config.timeout,
            )
            response.raise_for_status(); return True
        except Exception as exc: log.warning("Robinhood Telegram alert failed: %s", exc); return False

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        log.info("Robinhood radar started | interval=%ss min_score=%s", self.config.interval, self.config.min_alert_score)
        while not stop.is_set():
            try: self.scan_once()
            except Exception as exc: log.exception("Robinhood radar unexpected failure: %s", exc)
            stop.wait(self.config.interval)


_LOCK, _THREAD = threading.RLock(), None


def start_background_radar(config: RadarConfig | None = None) -> threading.Thread | None:
    global _THREAD
    config = config or RadarConfig.from_env()
    if not config.enabled or not config.token or not config.chat_id: return None
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive(): return _THREAD
        _THREAD = threading.Thread(target=RadarService(config).run, name="robinhood-chain-radar", daemon=True)
        _THREAD.start(); return _THREAD
