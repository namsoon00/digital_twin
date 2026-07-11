import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.data_freshness import evaluate_notification_data_freshness
from ..domain.events import (
    DomainEvent,
    alerts_detected_event,
    monitoring_cycle_completed_event,
    snapshot_collected_event,
)
from ..domain.fact_changes import fact_signature, research_evidence_fact_payload
from ..domain.investment_research import ResearchEvidence
from ..domain.model_review import ModelReviewJob
from ..domain.notification_rules import (
    DEFAULT_NOTIFICATION_RULES,
    NotificationRuleConfig,
    apply_market_hours_rule,
    apply_similarity_rule,
    apply_state_cooldown_rule,
    default_notification_rule,
    evaluate_notification_rule,
    notification_fingerprint,
)
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, NotificationTemplate, alert_context, render_notification
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.ontology_quality import OntologyQualitySample, build_ontology_quality_sample
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitoringCycleRecordResult
from ..domain.symbol_universe import ListedSymbol, normalize_market, normalize_symbol, utc_now_iso as symbol_utc_now_iso
from .model_review_queue import model_review_payloads_from_event
from .mysql_monitoring import MySQLDependencyError, MySQLMonitorAccountJobStore, ensure_mysql_database_exists, mysql_settings
from .operational_common import (
    MAX_NOTIFICATION_DELIVERY_ATTEMPTS,
    NOTIFICATION_HISTORY_LOOKBACK_LIMIT,
    age_minutes_since,
    json_dumps,
    notification_history_is_recent_in_flight,
    research_evidence_from_row,
    rule_from_row,
    template_from_row,
)
from .settings import read_json, settings_path, utc_now
from .mysql_notification_jobs import MySQLNotificationJobStore
from .mysql_operational_connection import MYSQL_SCHEMA, MySQLConnectionProxy, MySQLOperationalConnection
from .mysql_operational_events import insert_domain_event_with_connection
from .mysql_operational_helpers import (
    _is_duplicate_key_error,
    _json_loads,
    _sent_key_hash,
    research_evidence_change_payload,
)


class MySQLMarketQuoteCache(MySQLOperationalConnection):
    def save(self, provider: str, account_id: str, symbol: str, payload: Dict[str, object]) -> None:
        clean_symbol = str(symbol or "").upper().strip()
        if not clean_symbol or not isinstance(payload, dict):
            return
        stamp = utc_now()
        cached = dict(payload)
        cached.setdefault("updatedAt", stamp)
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO market_quote_cache (provider, account_id, symbol, payload_json, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    str(provider or "").strip().lower() or "unknown",
                    str(account_id or "").strip(),
                    clean_symbol,
                    json_dumps(cached),
                    stamp,
                ),
            )

    def load(self, provider: str, account_id: str, symbol: str) -> Dict[str, object]:
        clean_symbol = str(symbol or "").upper().strip()
        if not clean_symbol:
            return {}
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json, updated_at
                FROM market_quote_cache
                WHERE provider = %s AND account_id = %s AND symbol = %s
                """,
                (
                    str(provider or "").strip().lower() or "unknown",
                    str(account_id or "").strip(),
                    clean_symbol,
                ),
            ).fetchone()
        if not row:
            return {}
        payload = _json_loads(row["payload_json"], {})
        payload.setdefault("updatedAt", row["updated_at"])
        payload.setdefault("symbol", clean_symbol)
        return payload

    def load_many(self, provider: str, account_id: str, symbols: Iterable[str]) -> Dict[str, Dict[str, object]]:
        clean_symbols = []
        seen = set()
        for symbol in symbols or []:
            clean_symbol = str(symbol or "").upper().strip()
            if not clean_symbol or clean_symbol in seen:
                continue
            seen.add(clean_symbol)
            clean_symbols.append(clean_symbol)
        if not clean_symbols:
            return {}
        placeholders = ",".join(["%s"] * len(clean_symbols))
        params = [
            str(provider or "").strip().lower() or "unknown",
            str(account_id or "").strip(),
            *clean_symbols,
        ]
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, payload_json, updated_at
                FROM market_quote_cache
                WHERE provider = %s AND account_id = %s AND symbol IN (""" + placeholders + """)
                """,
                params,
            ).fetchall()
        result: Dict[str, Dict[str, object]] = {}
        for row in rows:
            clean_symbol = str(row["symbol"] or "").upper().strip()
            payload = _json_loads(row["payload_json"], {})
            if not payload:
                continue
            payload.setdefault("updatedAt", row["updated_at"])
            payload.setdefault("symbol", clean_symbol)
            result[clean_symbol] = payload
        return result

    def stale_universe_symbols(
        self,
        provider: str,
        account_id: str,
        markets: Iterable[str] = None,
        limit: int = 200,
        max_age_minutes: int = 240,
    ) -> List[Dict[str, object]]:
        clean_markets = [normalize_market(market) for market in (markets or []) if normalize_market(market)]
        limit_value = max(1, min(1000, int(limit or 200)))
        age_minutes = max(0, int(max_age_minutes or 0))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        clauses = ["su.active = 1"]
        params: List[object] = [
            str(provider or "").strip().lower() or "unknown",
            str(account_id or "").strip(),
        ]
        if clean_markets:
            clauses.append("su.market IN (" + ",".join(["%s"] * len(clean_markets)) + ")")
            params.extend(clean_markets)
        clauses.append("(mq.updated_at IS NULL OR mq.updated_at <= %s)")
        params.append(cutoff_text)
        sql = """
            SELECT su.symbol, su.name, su.market, su.exchange, su.currency, su.sector,
                   su.asset_type, mq.updated_at AS quote_updated_at
            FROM symbol_universe su
            LEFT JOIN market_quote_cache mq
              ON mq.provider = %s AND mq.account_id = %s AND mq.symbol = su.symbol
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY
                CASE WHEN mq.updated_at IS NULL THEN 0 ELSE 1 END,
                COALESCE(mq.updated_at, ''),
                CASE su.market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END,
                su.symbol
            LIMIT %s
        """
        with self.connect() as connection:
            rows = connection.execute(sql, params + [limit_value]).fetchall()
        return [
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "market": row["market"],
                "exchange": row["exchange"],
                "currency": row["currency"],
                "sector": row["sector"],
                "assetType": row["asset_type"],
                "quoteUpdatedAt": row["quote_updated_at"] or "",
            }
            for row in rows
        ]

    def summary(self, provider: str, account_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, payload_json, updated_at
                FROM market_quote_cache
                WHERE provider = %s AND account_id = %s
                ORDER BY updated_at DESC
                """,
                (
                    str(provider or "").strip().lower() or "unknown",
                    str(account_id or "").strip(),
                ),
            ).fetchall()
        markets: Dict[str, int] = {}
        qualities: Dict[str, int] = {}
        latest = ""
        for row in rows:
            latest = latest or row["updated_at"]
            payload = _json_loads(row["payload_json"], {})
            market = str(payload.get("market") or "UNKNOWN")
            quality = str(payload.get("dataQuality") or "unknown")
            markets[market] = markets.get(market, 0) + 1
            qualities[quality] = qualities.get(quality, 0) + 1
        return {
            "provider": str(provider or "").strip().lower() or "unknown",
            "accountId": str(account_id or "").strip(),
            "count": len(rows),
            "latestUpdatedAt": latest,
            "markets": markets,
            "dataQuality": qualities,
        }
