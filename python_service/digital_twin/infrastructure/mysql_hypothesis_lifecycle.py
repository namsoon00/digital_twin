from typing import Dict, Iterable, List

from ..domain.hypothesis_lifecycle import (
    HypothesisLifecycleRecord,
    HypothesisLifecycleTransition,
    utc_now_iso,
)
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps


class MySQLHypothesisLifecycleStore(MySQLOperationalConnection):
    """Persist TypeDB-hypothesis audit state without influencing inference."""

    def current_for_keys(self, lifecycle_keys: Iterable[str]) -> Dict[str, HypothesisLifecycleRecord]:
        keys = list(dict.fromkeys(
            str(item or "").strip()
            for item in lifecycle_keys or []
            if str(item or "").strip()
        ))
        if not keys:
            return {}
        placeholders = ",".join(["%s"] * len(keys))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM investment_hypothesis_lifecycle_states "
                "WHERE lifecycle_key IN (" + placeholders + ")",
                tuple(keys),
            ).fetchall()
        records = [self.record_from_row(row) for row in rows or []]
        return {item.lifecycle_key: item for item in records if item.lifecycle_key}

    def list_current(
        self,
        account_id: str = "",
        symbol: str = "",
        market_id: str = "",
        scope: str = "",
        limit: int = 100,
    ) -> List[HypothesisLifecycleRecord]:
        where: List[str] = []
        params: List[object] = []
        if account_id:
            # Shared market records remain useful to this account, but have no
            # private account id by design.
            where.append("(account_id = %s OR scope = 'market')")
            params.append(str(account_id))
        if symbol:
            where.append("symbol = %s")
            params.append(str(symbol).upper())
        if market_id:
            where.append("market_id = %s")
            params.append(str(market_id))
        if scope:
            where.append("scope = %s")
            params.append(str(scope))
        params.append(max(1, min(1000, int(limit or 100))))
        sql = "SELECT * FROM investment_hypothesis_lifecycle_states"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC, lifecycle_key ASC LIMIT %s"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self.record_from_row(row) for row in rows or []]

    def current_for_subjects(
        self,
        account_id: str,
        symbols: Iterable[str],
    ) -> Dict[str, HypothesisLifecycleRecord]:
        clean_symbols = list(dict.fromkeys(
            str(item or "").upper().strip()
            for item in symbols or []
            if str(item or "").strip()
        ))
        if not clean_symbols:
            return {}
        placeholders = ",".join(["%s"] * len(clean_symbols))
        sql = (
            "SELECT * FROM investment_hypothesis_lifecycle_states "
            "WHERE symbol IN (" + placeholders + ") "
            "AND (account_id = %s OR scope = 'market')"
        )
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(clean_symbols) + (str(account_id or ""),)).fetchall()
        records = [self.record_from_row(row) for row in rows or []]
        return {item.lifecycle_key: item for item in records if item.lifecycle_key}

    def list_events(
        self,
        account_id: str = "",
        symbol: str = "",
        lifecycle_key: str = "",
        market_id: str = "",
        scope: str = "",
        limit: int = 100,
    ) -> List[HypothesisLifecycleTransition]:
        where: List[str] = []
        params: List[object] = []
        if account_id:
            where.append("(account_id = %s OR scope = 'market')")
            params.append(str(account_id))
        if symbol:
            where.append("symbol = %s")
            params.append(str(symbol).upper())
        if lifecycle_key:
            where.append("lifecycle_key = %s")
            params.append(str(lifecycle_key))
        if market_id:
            where.append("market_id = %s")
            params.append(str(market_id))
        if scope:
            where.append("scope = %s")
            params.append(str(scope))
        params.append(max(1, min(1000, int(limit or 100))))
        sql = "SELECT * FROM investment_hypothesis_lifecycle_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY occurred_at DESC, transition_id DESC LIMIT %s"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self.transition_from_row(row) for row in rows or []]

    def save(
        self,
        record: HypothesisLifecycleRecord,
        transition: HypothesisLifecycleTransition = None,
    ) -> HypothesisLifecycleRecord:
        stamp = utc_now_iso()
        payload = record.to_dict()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO investment_hypothesis_lifecycle_states (
                    lifecycle_key, lifecycle_id, scope, account_id,
                    portfolio_world_id, market_world_id, market_id, symbol,
                    family_id, state, first_observed_at, last_observed_at,
                    last_transition_at, inference_generation_id,
                    inference_generation_at, previous_generation_id,
                    semantic_fingerprint, transition_reason, material_change,
                    payload_json, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) ON DUPLICATE KEY UPDATE
                    lifecycle_id = VALUES(lifecycle_id),
                    scope = VALUES(scope),
                    account_id = VALUES(account_id),
                    portfolio_world_id = VALUES(portfolio_world_id),
                    market_world_id = VALUES(market_world_id),
                    market_id = VALUES(market_id),
                    symbol = VALUES(symbol),
                    family_id = VALUES(family_id),
                    state = VALUES(state),
                    last_observed_at = VALUES(last_observed_at),
                    last_transition_at = VALUES(last_transition_at),
                    inference_generation_id = VALUES(inference_generation_id),
                    inference_generation_at = VALUES(inference_generation_at),
                    previous_generation_id = VALUES(previous_generation_id),
                    semantic_fingerprint = VALUES(semantic_fingerprint),
                    transition_reason = VALUES(transition_reason),
                    material_change = VALUES(material_change),
                    payload_json = VALUES(payload_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    record.lifecycle_key,
                    record.lifecycle_id,
                    record.scope,
                    record.account_id,
                    record.portfolio_world_id,
                    record.market_world_id,
                    record.market_id,
                    record.symbol,
                    record.family_id,
                    record.state,
                    record.first_observed_at,
                    record.last_observed_at,
                    record.last_transition_at,
                    record.inference_generation_id,
                    record.inference_generation_at,
                    record.previous_generation_id,
                    record.semantic_fingerprint,
                    record.transition_reason,
                    1 if record.material_change else 0,
                    json_dumps(payload),
                    stamp,
                    stamp,
                ),
            )
            if transition:
                transition_payload = transition.to_dict()
                connection.execute(
                    """
                    INSERT IGNORE INTO investment_hypothesis_lifecycle_events (
                        transition_id, lifecycle_key, lifecycle_id, scope,
                        account_id, market_id, symbol, previous_state,
                        current_state, inference_generation_id,
                        previous_generation_id, occurred_at, material_change,
                        payload_json, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        transition.transition_id,
                        transition.lifecycle_key,
                        transition.lifecycle_id,
                        transition.scope,
                        record.account_id,
                        record.market_id,
                        record.symbol,
                        transition.previous_state,
                        transition.current_state,
                        transition.inference_generation_id,
                        transition.previous_generation_id,
                        transition.occurred_at,
                        1 if transition.material_change else 0,
                        json_dumps(transition_payload),
                        stamp,
                    ),
                )
        return record

    def record_from_row(self, row: Dict[str, object]) -> HypothesisLifecycleRecord:
        payload = _json_loads(row.get("payload_json"), {})
        payload = dict(payload or {})
        payload.update({
            "lifecycleKey": payload.get("lifecycleKey") or row.get("lifecycle_key"),
            "lifecycleId": payload.get("lifecycleId") or row.get("lifecycle_id"),
            "scope": payload.get("scope") or row.get("scope"),
            "accountId": payload.get("accountId") or row.get("account_id"),
            "portfolioWorldId": payload.get("portfolioWorldId") or row.get("portfolio_world_id"),
            "marketWorldId": payload.get("marketWorldId") or row.get("market_world_id"),
            "marketId": payload.get("marketId") or row.get("market_id"),
            "symbol": payload.get("symbol") or row.get("symbol"),
            "familyId": payload.get("familyId") or row.get("family_id"),
            "state": payload.get("state") or row.get("state"),
            "firstObservedAt": payload.get("firstObservedAt") or row.get("first_observed_at"),
            "lastObservedAt": payload.get("lastObservedAt") or row.get("last_observed_at"),
            "lastTransitionAt": payload.get("lastTransitionAt") or row.get("last_transition_at"),
            "inferenceGenerationId": payload.get("inferenceGenerationId") or row.get("inference_generation_id"),
            "inferenceGenerationAt": payload.get("inferenceGenerationAt") or row.get("inference_generation_at"),
            "previousGenerationId": payload.get("previousGenerationId") or row.get("previous_generation_id"),
            "semanticFingerprint": payload.get("semanticFingerprint") or row.get("semantic_fingerprint"),
            "transitionReason": payload.get("transitionReason") or row.get("transition_reason"),
            "materialChange": payload.get("materialChange") if "materialChange" in payload else bool(row.get("material_change")),
        })
        return HypothesisLifecycleRecord.from_dict(payload)

    def transition_from_row(self, row: Dict[str, object]) -> HypothesisLifecycleTransition:
        payload = _json_loads(row.get("payload_json"), {})
        payload = dict(payload or {})
        payload.update({
            "transitionId": payload.get("transitionId") or row.get("transition_id"),
            "lifecycleKey": payload.get("lifecycleKey") or row.get("lifecycle_key"),
            "lifecycleId": payload.get("lifecycleId") or row.get("lifecycle_id"),
            "scope": payload.get("scope") or row.get("scope"),
            "previousState": payload.get("previousState") or row.get("previous_state"),
            "currentState": payload.get("currentState") or row.get("current_state"),
            "occurredAt": payload.get("occurredAt") or row.get("occurred_at"),
            "inferenceGenerationId": payload.get("inferenceGenerationId") or row.get("inference_generation_id"),
            "previousGenerationId": payload.get("previousGenerationId") or row.get("previous_generation_id"),
            "materialChange": payload.get("materialChange") if "materialChange" in payload else bool(row.get("material_change")),
        })
        return HypothesisLifecycleTransition.from_dict(payload)
