import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.data_freshness import evaluate_notification_data_freshness
from ..domain.events import (
    DomainEvent,
    ONTOLOGY_REASONING_REQUESTED,
    RESEARCH_EVIDENCE_COLLECTED,
    alerts_detected_event,
    monitoring_cycle_completed_event,
    snapshot_collected_event,
)
from ..domain.fact_changes import fact_signature, research_evidence_fact_payload
from ..domain.investment_research import ResearchEvidence
from ..domain.investment_strategy_guidance import append_strategy_block, merge_strategy_context
from ..domain.message_types import MARKET_OBSERVATION
from ..domain.market_observations import (
    apply_market_observation_outbox_baselines,
    apply_market_observation_reasoning_baselines,
    hydrate_market_observation_baselines,
    market_observation_reasoning_candidates,
    market_observation_reasoning_symbols,
)
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
from ..domain.ontology_projection_input import (
    compact_monitor_state_for_ontology,
    compact_monitor_state_for_reasoning_base,
    compact_monitor_state_for_reasoning_symbol,
    reasoning_snapshot_symbols,
)
from ..domain.portfolio import AccountSnapshot, AlertEvent, monitor_state_has_live_account_data
from ..domain.repositories import MonitoringCycleRecordResult
from ..domain.symbol_universe import ListedSymbol, normalize_market, normalize_symbol, utc_now_iso as symbol_utc_now_iso
from ..domain.verified_snapshot_reasoning import verified_monitor_snapshot_reasoning_event
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
from .mysql_operational_events import domain_event_from_row, insert_domain_event_with_connection
from .mysql_reasoning_mailbox import MySQLOntologyReasoningMailboxStore
from .mysql_operational_helpers import (
    _is_duplicate_key_error,
    _json_loads,
    _sent_key_hash,
    research_evidence_change_payload,
)


from .mysql_notification_config import MySQLNotificationTemplateStore
from .mysql_market_stores import MySQLModelReviewJobStore
from .mysql_market_time_series import MySQLMarketTimeSeriesStore


def snapshot_state_for_persistence(snapshot: AccountSnapshot, previous: Dict[str, object] = None) -> Dict[str, object]:
    """Keep the last verified account data when a provider request fails.

    A temporary authentication failure is operational state, not a new
    portfolio state. Persisting it over the live snapshot made the next live
    cycle lose its price baseline and blocked the TypeDB ABox projection.
    The retained state carries a small failure marker so connection alerts can
    still count consecutive failures and report recovery.
    """
    current = snapshot.to_monitor_state()
    if snapshot.has_live_account_data():
        current = hydrate_market_observation_baselines(current, previous or {})
    if snapshot.has_live_account_data() or not monitor_state_has_live_account_data(previous or {}):
        return current

    retained = copy.deepcopy(previous)
    current_metadata = dict(current.get("metadata") or {})
    metadata = dict(retained.get("metadata") or {})
    failure = {
        "mode": str(snapshot.mode or ""),
        "status": str(snapshot.status or ""),
        "generatedAt": str(snapshot.generated_at or ""),
        "connectionFailureStreak": int(current_metadata.get("connectionFailureStreak") or 0),
    }
    metadata["connectionFailureStreak"] = failure["connectionFailureStreak"]
    metadata["lastConnectionFailure"] = failure
    retained["metadata"] = metadata
    return retained


def market_observation_followup_symbols(
    events: Iterable[AlertEvent],
    account_id: object,
) -> List[str]:
    """Return raw observation symbols actually queued for notification.

    Only outboxed deterministic observations receive this scheduling marker.
    Suppressed candidates should not consume the live TypeDB priority lane.
    """
    account = str(account_id or "").strip()
    symbols = []
    for event in events or []:
        metadata = dict(getattr(event, "metadata", {}) or {})
        if (
            str(getattr(event, "account_id", "") or "").strip() != account
            or str(getattr(event, "rule", "") or "") != MARKET_OBSERVATION
            or not bool(metadata.get("observationOnly"))
        ):
            continue
        symbol = str(getattr(event, "symbol", "") or "").upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


class MySQLMarketObservationReasoningAnchorStore(MySQLOperationalConnection):
    """Track queued and TypeDB-completed quote anchors independently."""

    def load(self, account_id: str) -> Dict[str, Dict[str, object]]:
        account = str(account_id or "").strip()
        if not account:
            return {}
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, completed_price, completed_at, pending_price,
                       pending_event_id, pending_at
                FROM market_observation_reasoning_anchors
                WHERE account_id = %s
                """,
                (account,),
            ).fetchall()
        return {
            str(row.get("symbol") or "").upper().strip(): {
                "completedPrice": float(row.get("completed_price") or 0),
                "completedAt": str(row.get("completed_at") or ""),
                "pendingPrice": float(row.get("pending_price") or 0),
                "pendingEventId": str(row.get("pending_event_id") or ""),
                "pendingAt": str(row.get("pending_at") or ""),
            }
            for row in rows or []
            if str(row.get("symbol") or "").strip()
        }

    @staticmethod
    def apply_to_state(state: Dict[str, object], anchors: Dict[str, Dict[str, object]]) -> Dict[str, object]:
        updated = copy.deepcopy(state or {})
        metadata = dict(updated.get("metadata") or {})
        baselines = dict(metadata.get("marketObservationBaselines") or {})
        changed = False
        for symbol, anchor in dict(anchors or {}).items():
            baseline = dict(baselines.get(symbol) or {})
            completed_price = float(anchor.get("completedPrice") or 0)
            pending_price = float(anchor.get("pendingPrice") or 0)
            if completed_price > 0:
                baseline["price"] = completed_price
                baseline["reasoningPrice"] = completed_price
                baseline["reasoningCompletedAt"] = str(anchor.get("completedAt") or "")
            if pending_price > 0 and str(anchor.get("pendingEventId") or ""):
                baseline["pendingReasoningPrice"] = pending_price
                baseline["pendingReasoningEventId"] = str(anchor.get("pendingEventId") or "")
                baseline["reasoningQueuedAt"] = str(anchor.get("pendingAt") or "")
            else:
                baseline.pop("pendingReasoningPrice", None)
                baseline.pop("pendingReasoningEventId", None)
            if baseline:
                baselines[symbol] = baseline
                changed = True
        if changed:
            metadata["marketObservationBaselines"] = baselines
            updated["metadata"] = metadata
        return updated

    def mark_pending_with_connection(
        self,
        connection,
        account_id: str,
        event_id: str,
        candidates: Iterable[Dict[str, object]],
        stamp: str,
    ) -> int:
        count = 0
        for candidate in candidates or []:
            item = dict(candidate or {}) if isinstance(candidate, dict) else {}
            observation = item.get("marketObservation") if isinstance(item.get("marketObservation"), dict) else {}
            symbol = str(item.get("symbol") or "").upper().strip()
            try:
                pending_price = float(observation.get("currentPrice") or 0)
                completed_price = float(observation.get("reasoningBaselinePrice") or observation.get("baselinePrice") or 0)
            except (TypeError, ValueError):
                continue
            if not symbol or pending_price <= 0:
                continue
            connection.execute(
                """
                INSERT INTO market_observation_reasoning_anchors (
                    account_id, symbol, completed_price, completed_at,
                    pending_price, pending_event_id, pending_at, updated_at
                ) VALUES (%s, %s, %s, '', %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    completed_price = CASE
                        WHEN completed_price > 0 THEN completed_price
                        ELSE VALUES(completed_price)
                    END,
                    pending_price = VALUES(pending_price),
                    pending_event_id = VALUES(pending_event_id),
                    pending_at = VALUES(pending_at),
                    updated_at = VALUES(updated_at)
                """,
                (
                    str(account_id or ""), symbol, completed_price, pending_price,
                    str(event_id or ""), str(stamp or utc_now()), str(stamp or utc_now()),
                ),
            )
            count += 1
        return count

    def complete(
        self,
        event_ids: Iterable[str],
        account_ids: Iterable[str] = None,
        symbols: Iterable[str] = None,
    ) -> Dict[str, object]:
        events = sorted({str(value or "").strip() for value in event_ids or [] if str(value or "").strip()})
        accounts = sorted({str(value or "").strip() for value in account_ids or [] if str(value or "").strip()})
        subjects = sorted({str(value or "").upper().strip() for value in symbols or [] if str(value or "").strip()})
        if not events:
            return {"status": "not-required", "completedCount": 0}
        clauses = ["pending_event_id IN (" + ", ".join(["%s"] * len(events)) + ")"]
        params = list(events)
        if accounts:
            clauses.append("account_id IN (" + ", ".join(["%s"] * len(accounts)) + ")")
            params.extend(accounts)
        if subjects:
            clauses.append("symbol IN (" + ", ".join(["%s"] * len(subjects)) + ")")
            params.extend(subjects)
        stamp = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE market_observation_reasoning_anchors
                SET completed_price = pending_price, completed_at = %s,
                    pending_price = 0, pending_event_id = '', pending_at = '', updated_at = %s
                WHERE """ + " AND ".join(clauses),
                tuple([stamp, stamp] + params),
            )
        return {
            "status": "completed",
            "completedCount": int(getattr(cursor, "rowcount", 0) or 0),
            "eventIds": events,
        }


class MySQLMonitorStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None):
        super().__init__(settings)
        self.payload = {"previous": self.load_previous(), "sent": self.load_sent()}

    def load_previous(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT account_id, payload_json FROM monitor_snapshots").fetchall()
        previous = {}
        for row in rows:
            previous[row["account_id"]] = _json_loads(row["payload_json"], {})
        return previous

    def snapshot_metadata(self, account_id: str) -> Dict[str, object]:
        """Read the snapshot boundary without decoding its full payload.

        Isolated ontology scheduling calls this before a TypeDB generation.
        Keeping it narrow prevents a large research archive from becoming a
        prerequisite for learning that a newer source snapshot is still due.
        """
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT account_id, mode, status, generated_at, updated_at
                FROM monitor_snapshots
                WHERE account_id = %s
                LIMIT 1
                """,
                (str(account_id or ""),),
            ).fetchone() or {}
        return {
            "accountId": str(row.get("account_id") or account_id or ""),
            "mode": str(row.get("mode") or ""),
            "status": str(row.get("status") or ""),
            "generatedAt": str(row.get("generated_at") or ""),
            "updatedAt": str(row.get("updated_at") or ""),
        }

    def load_history(self, account_id: str, limit: int = 6) -> List[Dict[str, object]]:
        with self.connect() as connection:
            # New rows carry a compact temporal projection.  Do not deserialize
            # the research archive for every historical snapshot in the live
            # TypeDB path.  A small raw fallback keeps pre-migration history
            # usable until fresh monitor cycles replace it.
            rows = connection.execute(
                """
                SELECT projection_payload_json AS payload_json FROM monitor_snapshot_history
                WHERE account_id = %s
                  AND projection_payload_json <> ''
                ORDER BY generated_at DESC
                LIMIT %s
                """,
                (str(account_id or ""), max(1, int(limit or 6))),
            ).fetchall()
            if not rows:
                rows = connection.execute(
                    """
                    SELECT payload_json FROM monitor_snapshot_history
                    WHERE account_id = %s
                    ORDER BY generated_at DESC
                    LIMIT %s
                    """,
                    (str(account_id or ""), min(6, max(1, int(limit or 6)))),
                ).fetchall()
        history = [_json_loads(row["payload_json"], {}) for row in reversed(rows)]
        return [item for item in history if item]

    def load_sent(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT sent_key, sent_at FROM monitor_sent").fetchall()
        return {row["sent_key"]: row["sent_at"] for row in rows}

    @property
    def previous(self) -> Dict[str, object]:
        return self.payload["previous"]

    @property
    def sent(self) -> Dict[str, object]:
        return self.payload["sent"]

    def refresh_market_observation_reasoning_anchors(self, account_id: str) -> Dict[str, object]:
        account = str(account_id or "").strip()
        state = self.previous.get(account) or {}
        if not account or not isinstance(state, dict):
            return state
        try:
            anchors = MySQLMarketObservationReasoningAnchorStore(self.runtime_settings).load(account)
        except Exception:
            return state
        updated = MySQLMarketObservationReasoningAnchorStore.apply_to_state(state, anchors)
        self.previous[account] = updated
        return updated

    def upsert_snapshot_state_with_connection(
        self,
        connection,
        account_id: str,
        state: Dict[str, object],
        stamp: str = "",
        previous_state: Dict[str, object] = None,
    ) -> None:
        updated_at = stamp or utc_now()
        generated_at = str(state.get("generatedAt") or updated_at)
        temporal_projection = compact_monitor_state_for_ontology(
            state,
            settings=self.runtime_settings,
        )
        connection.execute(
            """
            INSERT INTO monitor_snapshots (
                account_id, account_label, provider, mode, status, generated_at, payload_json, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE account_label = VALUES(account_label), provider = VALUES(provider),
                mode = VALUES(mode), status = VALUES(status), generated_at = VALUES(generated_at),
                payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
            """,
            (
                account_id,
                str(state.get("accountLabel") or ""),
                str(state.get("provider") or ""),
                str(state.get("mode") or ""),
                str(state.get("status") or ""),
                generated_at,
                json_dumps(state),
                updated_at,
            ),
        )
        self.upsert_reasoning_snapshot_inputs_with_connection(
            connection,
            account_id,
            state,
            generated_at=generated_at,
            stamp=updated_at,
            previous_state=previous_state,
        )
        connection.execute(
            """
            INSERT INTO monitor_snapshot_history (
                account_id, generated_at, payload_json, projection_payload_json, created_at
            )
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json),
                projection_payload_json = VALUES(projection_payload_json), created_at = VALUES(created_at)
            """,
            (account_id, generated_at, json_dumps(state), json_dumps(temporal_projection), updated_at),
        )

    def upsert_reasoning_snapshot_inputs_with_connection(
        self,
        connection,
        account_id: str,
        state: Dict[str, object],
        *,
        generated_at: str = "",
        stamp: str = "",
        previous_state: Dict[str, object] = None,
    ) -> None:
        """Persist target-scoped TypeDB input beside its verified source row.

        The durable mailbox event is inserted in the same monitoring-cycle
        transaction.  Writing this cache here gives the reasoning worker a
        small, revision-aligned input without asking it to decode the source
        provider archive after it has already claimed live queue work.
        """

        current = dict(state or {}) if isinstance(state, dict) else {}
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            return
        updated_at = str(stamp or utc_now())
        source_generated_at = str(generated_at or current.get("generatedAt") or updated_at)
        base = compact_monitor_state_for_reasoning_base(
            current,
            settings=self.runtime_settings,
        )
        base["accountId"] = str(base.get("accountId") or normalized_account_id)
        base["generatedAt"] = str(base.get("generatedAt") or source_generated_at)
        compact_previous = compact_monitor_state_for_ontology(
            previous_state,
            settings=self.runtime_settings,
        ) if isinstance(previous_state, dict) else {}
        previous_generated_at = str(compact_previous.get("generatedAt") or "")
        if compact_previous and previous_generated_at and previous_generated_at < source_generated_at:
            metadata = dict(base.get("metadata") or {})
            metadata["previousMonitorState"] = compact_previous
            base["metadata"] = metadata
        inputs = [("", base)]
        for symbol in sorted(reasoning_snapshot_symbols(current)):
            inputs.append((
                symbol,
                compact_monitor_state_for_reasoning_symbol(
                    current,
                    symbol,
                    settings=self.runtime_settings,
                ),
            ))
        for symbol, payload in inputs:
            connection.execute(
                """
                INSERT INTO monitor_snapshot_reasoning_inputs (
                    account_id, generated_at, symbol, payload_json, updated_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE generated_at = VALUES(generated_at),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    normalized_account_id,
                    source_generated_at,
                    str(symbol or ""),
                    json_dumps(payload),
                    updated_at,
                ),
            )
        cached_symbols = [str(symbol or "") for symbol, _payload in inputs]
        placeholders = ", ".join(["%s"] * len(cached_symbols))
        connection.execute(
            "DELETE FROM monitor_snapshot_reasoning_inputs "
            "WHERE account_id = %s AND symbol NOT IN (" + placeholders + ")",
            tuple([normalized_account_id] + cached_symbols),
        )

    def upsert_snapshot_state(
        self,
        account_id: str,
        state: Dict[str, object],
        previous_state: Dict[str, object] = None,
    ) -> None:
        with self.transaction() as connection:
            self.upsert_snapshot_state_with_connection(
                connection,
                account_id,
                state,
                previous_state=previous_state,
            )

    def backfill_reasoning_snapshot_inputs(self, account_ids: Iterable[str] = None) -> int:
        """Seed current target-scoped inputs once during a rolling rollout.

        This is a cache-only migration helper. It locks and reads the current
        source row in the same transaction as the cache write, so it cannot
        overwrite a newer monitor generation with a stale process-local copy.
        Normal monitor commits keep the cache current afterwards.
        """

        requested = sorted({
            str(account_id or "").strip()
            for account_id in account_ids or []
            if str(account_id or "").strip()
        })
        with self.transaction() as connection:
            sql = "SELECT account_id, generated_at, payload_json FROM monitor_snapshots"
            params = ()
            if requested:
                placeholders = ", ".join(["%s"] * len(requested))
                sql += " WHERE account_id IN (" + placeholders + ")"
                params = tuple(requested)
            rows = connection.execute(sql + " FOR UPDATE", params).fetchall()
            count = 0
            stamp = utc_now()
            for row in rows:
                state = _json_loads(row.get("payload_json"), {})
                if not isinstance(state, dict):
                    continue
                self.upsert_reasoning_snapshot_inputs_with_connection(
                    connection,
                    str(row.get("account_id") or ""),
                    state,
                    generated_at=str(row.get("generated_at") or state.get("generatedAt") or stamp),
                    stamp=stamp,
                )
                count += 1
        return count

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        previous_state = self.previous.get(snapshot.account_id)
        state = snapshot_state_for_persistence(snapshot, previous_state)
        self.upsert_snapshot_state(snapshot.account_id, state, previous_state=previous_state)
        self.previous[snapshot.account_id] = state

    def sent_entries(self, events: Iterable[AlertEvent], stamp: str) -> Dict[str, str]:
        entries: Dict[str, str] = {}
        for event in events:
            entries[event.key] = stamp
            entries[event.cadence_key()] = stamp
        return entries

    def mark_sent_with_connection(self, connection, events: Iterable[AlertEvent], stamp: str) -> Dict[str, str]:
        entries = self.sent_entries(events, stamp)
        for key, sent_at in entries.items():
            connection.execute(
                """
                INSERT INTO monitor_sent (sent_key_hash, sent_key, sent_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE sent_at = VALUES(sent_at)
                """,
                (_sent_key_hash(key), key, sent_at),
            )
        return entries

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            entries = self.mark_sent_with_connection(connection, events, stamp)
        self.sent.update(entries)

    def record_cycle(
        self,
        account_ids: List[str],
        snapshots: List[AccountSnapshot],
        alert_events: List[AlertEvent],
        dry_run: bool = False,
        delivery_guard=None,
        source_snapshot_replay: bool = False,
    ):
        return MySQLMonitoringCycleRecorder(
            self.runtime_settings,
            monitor_store=self,
            market_time_series_store=MySQLMarketTimeSeriesStore(self.runtime_settings),
        ).record_cycle(
            account_ids,
            snapshots,
            alert_events,
            dry_run=dry_run,
            delivery_guard=delivery_guard,
            source_snapshot_replay=source_snapshot_replay,
        )

    def write(self) -> None:
        pass


class MySQLOntologyReasoningMonitorStore(MySQLMonitorStore):
    """Read only the target-scoped source cache used by live TypeDB replay.

    ``MySQLMonitorStore`` deliberately retains the complete provider archive
    for research and notification reconstruction.  The reasoning worker must
    not load that archive merely to materialise one selected symbol.  This
    adapter is intentionally read-only and is only valid with
    ``source_snapshot_replay=True``.
    """

    def __init__(self, settings: Dict[str, str] = None):
        # Do not call ``MySQLMonitorStore.__init__``: its normal role is to
        # deserialize every raw monitor snapshot.  A persistent sidecar must
        # start without that cost even before it knows which symbol is next.
        MySQLOperationalConnection.__init__(self, settings)
        self.payload = {"previous": {}, "sent": self.load_sent()}
        self._reasoning_state_cache = {}

    def load_previous(self) -> Dict[str, object]:
        return {}

    @staticmethod
    def _symbols(target_symbols) -> List[str]:
        return sorted({
            str(symbol or "").upper().strip()
            for symbol in target_symbols or []
            if str(symbol or "").strip()
        })

    @staticmethod
    def _merge_external_signals(base: Dict[str, object], incoming: Dict[str, object]) -> Dict[str, object]:
        result = copy.deepcopy(base or {}) if isinstance(base, dict) else {}
        for key, value in (incoming or {}).items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = {**result[key], **copy.deepcopy(value)}
            else:
                result[key] = copy.deepcopy(value)
        return result

    def legacy_snapshot_state(self, account_id: str) -> Dict[str, object]:
        """Use one raw account row only while a rolling upgrade builds cache."""

        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM monitor_snapshots WHERE account_id = %s LIMIT 1",
                    (str(account_id or ""),),
                ).fetchone() or {}
        except Exception:
            return {}
        payload = _json_loads(row.get("payload_json"), {})
        return dict(payload or {}) if isinstance(payload, dict) else {}

    def reasoning_snapshot_state(
        self,
        account_id: str,
        target_symbols=None,
    ) -> Dict[str, object]:
        """Rehydrate only the base row and the selected symbol input rows."""

        normalized_account_id = str(account_id or "").strip()
        selected_symbols = self._symbols(target_symbols)
        cache_key = (normalized_account_id, tuple(selected_symbols))
        if cache_key in self._reasoning_state_cache:
            state = self._reasoning_state_cache[cache_key]
            return copy.deepcopy(state) if isinstance(state, dict) else {}
        if not normalized_account_id:
            return {}
        selection_sql = ""
        params = [normalized_account_id]
        if selected_symbols:
            placeholders = ", ".join(["%s"] * len(selected_symbols))
            selection_sql = " AND (symbol = '' OR symbol IN (" + placeholders + "))"
            params.extend(selected_symbols)
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT generated_at, symbol, payload_json "
                    "FROM monitor_snapshot_reasoning_inputs "
                    "WHERE account_id = %s" + selection_sql,
                    tuple(params),
                ).fetchall()
        except Exception:
            rows = []
        base_row = next((row for row in rows if not str(row.get("symbol") or "")), None)
        if not base_row:
            # Older deployments have the raw source and mailbox event but no
            # cache table yet. Correctness wins for that one rolling-upgrade
            # turn; subsequent monitor commits populate the small rows.
            fallback = self.legacy_snapshot_state(normalized_account_id)
            if fallback:
                self.payload["previous"][normalized_account_id] = fallback
            return copy.deepcopy(fallback)
        base = _json_loads(base_row.get("payload_json"), {})
        if not isinstance(base, dict):
            return {}
        expected_generated_at = str(base_row.get("generated_at") or base.get("generatedAt") or "")
        state = copy.deepcopy(base)
        state["accountId"] = str(state.get("accountId") or normalized_account_id)
        state["generatedAt"] = str(state.get("generatedAt") or expected_generated_at)
        signals = dict(state.get("externalSignals") or {}) if isinstance(state.get("externalSignals"), dict) else {}
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or str(row.get("generated_at") or "") != expected_generated_at:
                continue
            payload = _json_loads(row.get("payload_json"), {})
            source_signals = payload.get("externalSignals") if isinstance(payload, dict) else {}
            if isinstance(source_signals, dict):
                signals = self._merge_external_signals(signals, source_signals)
        if signals:
            state["externalSignals"] = signals
        self._reasoning_state_cache[cache_key] = copy.deepcopy(state)
        self.payload["previous"][normalized_account_id] = copy.deepcopy(state)
        return copy.deepcopy(state)

    def save_snapshot(self, _snapshot: AccountSnapshot) -> None:
        raise RuntimeError("Ontology reasoning snapshot replay store is read-only")

    def upsert_snapshot_state_with_connection(self, *_args, **_kwargs) -> None:
        raise RuntimeError("Ontology reasoning snapshot replay store must not persist source snapshots")

class MySQLMonitoringCycleRecorder(MySQLOperationalConnection):
    def __init__(
        self,
        settings: Dict[str, str] = None,
        monitor_store: MySQLMonitorStore = None,
        market_time_series_store=None,
    ):
        self.monitor_store = monitor_store
        self.market_time_series_store = market_time_series_store
        super().__init__(settings)
        if self.monitor_store is None:
            self.monitor_store = MySQLMonitorStore(settings)
        if self.market_time_series_store is None:
            self.market_time_series_store = MySQLMarketTimeSeriesStore(settings)
        self.market_observation_anchor_store = MySQLMarketObservationReasoningAnchorStore(
            self.runtime_settings
        )

    def account_context_map(self) -> Dict[str, AccountConfig]:
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, label, provider, enabled, watchlist_symbols, quiet_hours_enabled,
                           quiet_hours_start, quiet_hours_end, quiet_hours_timezone,
                           message_delivery_level, investment_strategy_profile
                    FROM service_accounts
                    """
                ).fetchall()
        except Exception:  # noqa: BLE001 - monitoring delivery should continue without strategy lookup.
            rows = []
        accounts: Dict[str, AccountConfig] = {}
        for row in rows:
            account = AccountConfig(
                account_id=row["id"],
                label=row["label"],
                provider=row["provider"],
                base_url=self.runtime_settings.get("tossApiBaseUrl", ""),
                client_id="",
                client_secret="",
                account_seq="",
                watchlist_symbols=split_symbols(row["watchlist_symbols"] or ""),
                enabled=bool(row["enabled"]),
                quiet_hours_enabled=bool(row["quiet_hours_enabled"]),
                quiet_hours_start=row["quiet_hours_start"] or "22:00",
                quiet_hours_end=row["quiet_hours_end"] or "05:00",
                quiet_hours_timezone=row["quiet_hours_timezone"] or "Asia/Seoul",
                message_delivery_level=row["message_delivery_level"] or self.runtime_settings.get("messageDeliveryLevel", "absoluteBeginner"),
                investment_strategy_profile=row["investment_strategy_profile"] or self.runtime_settings.get("investmentStrategyProfile", "balanced"),
            )
            accounts[account.account_id] = account
        return accounts

    def record_cycle(
        self,
        account_ids: List[str],
        snapshots: List[AccountSnapshot],
        alert_events: List[AlertEvent],
        dry_run: bool = False,
        delivery_guard=None,
        source_snapshot_replay: bool = False,
    ):
        if dry_run:
            return MonitoringCycleRecordResult(False, 0, "dry-run")
        previous_states = {
            str(account_id or ""): copy.deepcopy(
                self.monitor_store.previous.get(str(account_id or "")) or {}
            )
            for account_id in account_ids or []
            if str(account_id or "")
        }
        snapshot_states = {}
        live_snapshots = []
        observation_candidates_by_account: Dict[str, List[Dict[str, object]]] = {}
        if not source_snapshot_replay:
            snapshot_states = {
                snapshot.account_id: snapshot_state_for_persistence(
                    snapshot,
                    self.monitor_store.previous.get(snapshot.account_id),
                )
                for snapshot in snapshots
            }
            live_snapshots = [snapshot for snapshot in snapshots if snapshot.has_live_account_data()]
            observation_candidates_by_account = {
                snapshot.account_id: market_observation_reasoning_candidates(
                    snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
                )
                for snapshot in live_snapshots
            }
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        queued = 0
        sent_entries: Dict[str, str] = {}
        notification_store = MySQLNotificationJobStore(self.runtime_settings)
        model_review_store = MySQLModelReviewJobStore(self.runtime_settings)
        account_contexts = self.account_context_map() if alert_events else {}
        guarded_events = list(alert_events or [])
        delivery_guard_result: Dict[str, object] = {"status": "not-applied"}
        with self.transaction() as connection:
            if callable(delivery_guard) and guarded_events:
                try:
                    guard_value = delivery_guard(connection, list(guarded_events))
                    if isinstance(guard_value, tuple) and len(guard_value) == 2:
                        candidate_events, candidate_details = guard_value
                        guarded_events = list(candidate_events or [])
                        delivery_guard_result = dict(candidate_details or {})
                    elif isinstance(guard_value, dict):
                        candidate_events = guard_value.get("events")
                        if candidate_events is not None:
                            guarded_events = list(candidate_events or [])
                        delivery_guard_result = {
                            key: value
                            for key, value in guard_value.items()
                            if key != "events"
                        }
                    elif guard_value is not None:
                        guarded_events = list(guard_value or [])
                        delivery_guard_result = {"status": "applied"}
                    delivery_guard_result.setdefault("status", "applied")
                except Exception as error:  # noqa: BLE001 - retain the source work for a safe retry.
                    # Continuing would persist the new monitor state while
                    # discarding its only delivery candidate. Roll the cycle
                    # back instead: the reasoning worker releases the mailbox
                    # lease and retries the same latest fact revision.
                    raise RuntimeError(
                        "Notification delivery revision guard failed: " + str(error)[:180]
                    ) from error
            delivered = bool(guarded_events)
            alert_source_event = alerts_detected_event(guarded_events) if guarded_events else None
            self.market_time_series_store.record_snapshots_with_connection(connection, live_snapshots)
            for snapshot in live_snapshots:
                insert_domain_event_with_connection(connection, snapshot_collected_event(snapshot))
            outboxed_events: List[AlertEvent] = []
            if alert_source_event:
                insert_domain_event_with_connection(connection, alert_source_event)
                for event in guarded_events:
                    account_context = account_contexts.get(event.account_id)
                    context = merge_strategy_context(alert_context(event), account_context, self.runtime_settings)
                    if str(event.rule or "") == "investmentInsight":
                        context = append_strategy_block(context)
                    message = MySQLNotificationTemplateStore(self.runtime_settings).render(event.rule, context)
                    job = NotificationJob.create(
                        message,
                        account_id=event.account_id,
                        account_label=event.account_label,
                        message_type=event.rule or "alert",
                        source_event_id=alert_source_event.event_id,
                        source_event_name=alert_source_event.name,
                        dedupe_key=":".join(["outbox", alert_source_event.event_id, event.key]),
                        context=context,
                    )
                    if notification_store.enqueue_with_connection(connection, job):
                        queued += 1
                        outboxed_events.append(event)
                model_review_store.enqueue_from_event_with_connection(connection, alert_source_event)
                sent_entries = self.monitor_store.mark_sent_with_connection(connection, guarded_events, stamp)
            if outboxed_events:
                for account_id, state in list(snapshot_states.items()):
                    account_events = [event for event in outboxed_events if event.account_id == account_id]
                    if account_events:
                        snapshot_states[account_id] = apply_market_observation_outbox_baselines(state, account_events)
            # Ordinary price candidates are TypeDB-first. Persist only their
            # pending anchors at the same durable source boundary that creates
            # replay work. The completed anchor advances after TypeDB succeeds.
            for account_id, state in list(snapshot_states.items()):
                candidates = observation_candidates_by_account.get(account_id) or []
                if candidates:
                    snapshot_states[account_id] = apply_market_observation_reasoning_baselines(state, candidates)
            # Persist the source snapshot before publishing its replayable
            # TypeDB work. The two writes share this transaction, so a worker
            # can never see a barrier without the exact snapshot it names.
            for account_id, state in snapshot_states.items():
                self.monitor_store.upsert_snapshot_state_with_connection(
                    connection,
                    account_id,
                    state,
                    stamp,
                    previous_state=previous_states.get(account_id),
                )
            for snapshot in live_snapshots:
                # A material quote candidate is TypeDB-first even when raw
                # delivery is deferred. The old path only forwarded an
                # outboxed raw alert, so a cumulative move could advance its
                # durable baseline without ever receiving the intended graph
                # follow-up. Preserve both candidates and immediately sent
                # observations; de-duplication is per current snapshot.
                followup_symbols = list(dict.fromkeys([
                    *market_observation_reasoning_symbols(
                        snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
                    ),
                    *market_observation_followup_symbols(
                        outboxed_events,
                        snapshot.account_id,
                    ),
                ]))
                reasoning_event = verified_monitor_snapshot_reasoning_event(
                    snapshot,
                    self.monitor_store.previous.get(snapshot.account_id),
                    self.runtime_settings,
                    observation_followup_symbols=followup_symbols,
                )
                if not reasoning_event:
                    continue
                insert_domain_event_with_connection(connection, reasoning_event)
                try:
                    MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(connection, reasoning_event)
                except Exception:
                    # The event remains durable and the bounded repair path
                    # can recreate its mailbox row after an interrupted
                    # operational write.
                    pass
                self.market_observation_anchor_store.mark_pending_with_connection(
                    connection,
                    snapshot.account_id,
                    reasoning_event.event_id,
                    observation_candidates_by_account.get(snapshot.account_id) or [],
                    stamp,
                )
            insert_domain_event_with_connection(
                connection,
                monitoring_cycle_completed_event(list(account_ids or []), len(snapshots), len(guarded_events), False, delivered),
            )
        self.monitor_store.previous.update(snapshot_states)
        self.monitor_store.sent.update(sent_entries)
        return MonitoringCycleRecordResult(
            delivered,
            queued,
            "queued=" + str(queued),
            delivered_events=list(guarded_events),
            details={
                "deliveryGuard": delivery_guard_result,
                "inputEventCount": len(alert_events or []),
                "deliveredEventCount": len(guarded_events),
                "suppressedEventCount": max(0, len(alert_events or []) - len(guarded_events)),
                "sourceSnapshotReplay": bool(source_snapshot_replay),
            },
        )

class MySQLEventLog(MySQLOperationalConnection):
    def handle(self, event: DomainEvent) -> None:
        with self.transaction() as connection:
            insert_domain_event_with_connection(connection, event)
            if str(getattr(event, "name", "") or "") == ONTOLOGY_REASONING_REQUESTED:
                try:
                    MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(connection, event)
                except Exception:
                    # The event remains durable and the runner's bounded
                    # reconciliation query will recover it.  A queue-summary
                    # migration must never reject a source fact.
                    pass

    def research_evidence_events_after(
        self,
        after_occurred_at: str = "",
        after_event_id: str = "",
        limit: int = 100,
    ) -> List[DomainEvent]:
        """Read a stable page for durable news-notification reconciliation."""

        bounded = max(1, min(500, int(limit or 100)))
        after_time = str(after_occurred_at or "1970-01-01T00:00:00Z").strip()
        after_id = str(after_event_id or "").strip()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, name, aggregate_id, occurred_at,
                       correlation_id, payload_json, event_json
                FROM domain_events
                WHERE name = %s
                  AND (
                    occurred_at > %s
                    OR (occurred_at = %s AND event_id > %s)
                  )
                ORDER BY occurred_at, event_id
                LIMIT %s
                """,
                (RESEARCH_EVIDENCE_COLLECTED, after_time, after_time, after_id, bounded),
            ).fetchall()
        return [domain_event_from_row(row) for row in rows]

    def unmaterialized_reasoning_events(self, limit: int = 0) -> List[DomainEvent]:
        """Read only legacy reasoning events missing a mailbox ingress row.

        This is a bounded, low-frequency repair path for an interrupted
        ingress transaction. Direct/non-fungible work has its own indexed
        mailbox-event query below, so normal scheduler and status reads do
        not need to scan the append-only event log.
        """
        bounded = max(1, min(1000, int(limit or 200)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT events.event_id, events.name, events.aggregate_id, events.occurred_at,
                       events.correlation_id, events.payload_json, events.event_json
                FROM domain_events events
                LEFT JOIN ontology_reasoning_mailbox_events mailbox
                  ON mailbox.event_id = events.event_id
                WHERE events.name = %s
                  AND mailbox.event_id IS NULL
                ORDER BY events.occurred_at DESC, events.event_id DESC
                LIMIT %s
                """,
                (ONTOLOGY_REASONING_REQUESTED, bounded),
            ).fetchall()
        return [domain_event_from_row(row) for row in reversed(rows)]

    def reasoning_ingress_repair_page(
        self,
        after_occurred_at: str = "",
        after_event_id: str = "",
        limit: int = 0,
    ) -> Dict[str, object]:
        """Advance a small indexed page of legacy mailbox-ingress recovery.

        Event ingress is atomic in the normal path, so recovery is exceptional.
        The former anti-join scanned the append-only event log on every retry.
        This cursor page scans only event identifiers in index order, then
        loads full payloads solely for rows that genuinely lack a mailbox
        marker. The cursor advances even when every scanned event is already
        materialized.
        """
        bounded = max(1, min(250, int(limit or 100)))
        after_time = str(after_occurred_at or "")
        after_id = str(after_event_id or "")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, occurred_at
                FROM domain_events
                WHERE name = %s
                  AND (
                    occurred_at > %s
                    OR (occurred_at = %s AND event_id > %s)
                  )
                ORDER BY occurred_at, event_id
                LIMIT %s
                """,
                (ONTOLOGY_REASONING_REQUESTED, after_time, after_time, after_id, bounded),
            ).fetchall()
            event_ids = [str(row.get("event_id") or "").strip() for row in rows or []]
            event_ids = [event_id for event_id in event_ids if event_id]
            materialized = set()
            if event_ids:
                placeholders = ", ".join(["%s"] * len(event_ids))
                marker_rows = connection.execute(
                    "SELECT event_id FROM ontology_reasoning_mailbox_events "
                    "WHERE event_id IN (" + placeholders + ")",
                    tuple(event_ids),
                ).fetchall()
                materialized = {
                    str(row.get("event_id") or "").strip()
                    for row in marker_rows or []
                    if str(row.get("event_id") or "").strip()
                }
            missing_ids = [event_id for event_id in event_ids if event_id not in materialized]
            event_rows = []
            if missing_ids:
                placeholders = ", ".join(["%s"] * len(missing_ids))
                event_rows = connection.execute(
                    """
                    SELECT event_id, name, aggregate_id, occurred_at,
                           correlation_id, payload_json, event_json
                    FROM domain_events
                    WHERE event_id IN (""" + placeholders + ")",
                    tuple(missing_ids),
                ).fetchall()
        rows_by_id = {
            str(row.get("event_id") or "").strip(): row
            for row in event_rows or []
            if str(row.get("event_id") or "").strip()
        }
        events = [
            domain_event_from_row(rows_by_id[event_id])
            for event_id in missing_ids
            if event_id in rows_by_id
        ]
        last = (rows or [])[-1] if rows else {}
        return {
            "events": events,
            "cursor": {
                "occurredAt": str(last.get("occurred_at") or after_time),
                "eventId": str(last.get("event_id") or after_id),
            },
            "scannedCount": len(event_ids),
            "recoveredEventCount": len(events),
            "exhausted": len(rows or []) < bounded,
        }

    def direct_pending_reasoning_events(self, limit: int = 0) -> List[DomainEvent]:
        """Read non-fungible reasoning handoffs from their durable marker.

        Direct handoffs store the complete source event in
        ``ontology_reasoning_mailbox_events``. Querying the marker by state
        avoids repeatedly joining and scanning the append-only event log just
        to find a handful of pending research acknowledgements.
        """
        bounded = max(1, min(250, int(limit or 50)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, occurred_at, event_json
                FROM ontology_reasoning_mailbox_events events
                LEFT JOIN ontology_reasoning_work_items work
                  ON work.mailbox_key = CONCAT('direct:', events.event_id)
                 AND work.source_event_id = events.event_id
                WHERE events.state = 'direct-pending'
                  AND (
                    work.mailbox_key IS NULL
                    OR work.work_state = 'queued'
                    OR (work.work_state = 'retrying' AND (work.not_before_at = '' OR work.not_before_at <= %s))
                    OR (work.work_state = 'running' AND (work.lease_until = '' OR work.lease_until <= %s))
                  )
                ORDER BY events.occurred_at, events.event_id
                LIMIT %s
                """,
                (utc_now(), utc_now(), bounded),
            ).fetchall()
        events = []
        for row in rows or []:
            payload = _json_loads(row.get("event_json"), {})
            if not payload:
                continue
            event = DomainEvent.from_dict(payload)
            if str(event.name or "") != ONTOLOGY_REASONING_REQUESTED:
                continue
            events.append(event)
        return events

    def insert_event_dict(self, event: Dict[str, object]) -> None:
        self.handle(DomainEvent.from_dict(event))

    def events(self, name: str = "", aggregate_id: str = "", limit: int = 0) -> List[DomainEvent]:
        clauses = []
        params = []
        if name:
            clauses.append("name = %s")
            params.append(name)
        if aggregate_id:
            clauses.append("aggregate_id = %s")
            params.append(aggregate_id)
        sql = "SELECT event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json FROM domain_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at, event_id"
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [domain_event_from_row(row) for row in rows]

    def recent_events(self, name: str = "", aggregate_id: str = "", limit: int = 0) -> List[DomainEvent]:
        clauses = []
        params = []
        if name:
            clauses.append("name = %s")
            params.append(name)
        if aggregate_id:
            clauses.append("aggregate_id = %s")
            params.append(aggregate_id)
        sql = "SELECT event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json FROM domain_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at DESC, event_id DESC"
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [domain_event_from_row(row) for row in reversed(rows)]

    def latest_events(self, limit: int = 12) -> List[DomainEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json FROM domain_events ORDER BY occurred_at DESC, event_id DESC LIMIT %s",
                (max(1, min(200, int(limit or 12))),),
            ).fetchall()
        return [domain_event_from_row(row) for row in reversed(rows)]

    def latest_events_by_name(self, names: Iterable[str]) -> Dict[str, DomainEvent]:
        result = {}
        with self.connect() as connection:
            for name in [str(item or "").strip() for item in names or [] if str(item or "").strip()]:
                row = connection.execute(
                    "SELECT event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json FROM domain_events WHERE name = %s ORDER BY occurred_at DESC, event_id DESC LIMIT 1",
                    (name,),
                ).fetchone()
                if row:
                    result[name] = domain_event_from_row(row)
        return result

    def event_counts(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT name, COUNT(*) AS count FROM domain_events GROUP BY name").fetchall()
        return {row["name"]: int(row["count"] or 0) for row in rows}
