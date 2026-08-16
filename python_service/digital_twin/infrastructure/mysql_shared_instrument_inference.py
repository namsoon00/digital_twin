"""MySQL read models for shared instrument inference and account fan-out."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Dict, Iterable, List, Mapping

from .mysql_operational_connection import MySQLOperationalConnection


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: object, fallback):
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed


def _text(value: object) -> str:
    return str(value or "").strip()


def _symbol(value: object) -> str:
    return _text(value).upper()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MySQLSharedInstrumentInferenceStore(MySQLOperationalConnection):
    """Persist reusable market output without making it a decision authority."""

    def reconcile_subscriptions(
        self,
        account_id: str,
        holdings: Iterable[str],
        watchlist: Iterable[str],
        *,
        source_revision: str = "",
        source_as_of: str = "",
    ) -> Dict[str, object]:
        account_id = _text(account_id)
        if not account_id:
            return {"status": "ignored", "reason": "missing-account-id", "activeCount": 0}
        holding_symbols = {_symbol(value) for value in holdings or [] if _symbol(value)}
        watchlist_symbols = {_symbol(value) for value in watchlist or [] if _symbol(value)}
        roles = {
            symbol: "holding" if symbol in holding_symbols else "watchlist"
            for symbol in sorted(holding_symbols | watchlist_symbols)
        }
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE account_instrument_subscriptions SET active = 0, updated_at = %s "
                "WHERE account_id = %s AND active = 1",
                (stamp, account_id),
            )
            for symbol, role in roles.items():
                connection.execute(
                    """
                    INSERT INTO account_instrument_subscriptions (
                        account_id, symbol, position_role, source_revision,
                        source_as_of, active, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        position_role = VALUES(position_role),
                        source_revision = VALUES(source_revision),
                        source_as_of = VALUES(source_as_of),
                        active = 1,
                        updated_at = VALUES(updated_at)
                    """,
                    (
                        account_id,
                        symbol,
                        role,
                        _text(source_revision)[:191],
                        _text(source_as_of)[:40],
                        stamp,
                        stamp,
                    ),
                )
        return {
            "status": "updated",
            "accountId": account_id,
            "activeCount": len(roles),
            "holdingCount": len(holding_symbols),
            "watchlistCount": len(roles) - len(holding_symbols),
        }

    def account_ids_for_symbols(self, symbols: Iterable[str]) -> List[str]:
        selected = sorted({_symbol(value) for value in symbols or [] if _symbol(value)})
        if not selected:
            return []
        placeholders = ",".join(["%s"] * len(selected))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT s.account_id FROM account_instrument_subscriptions s "
                "LEFT JOIN service_accounts a ON a.id = s.account_id "
                "WHERE s.active = 1 AND (a.id IS NULL OR a.enabled = 1) "
                "AND s.symbol IN (" + placeholders + ") "
                "ORDER BY s.account_id",
                tuple(selected),
            ).fetchall()
        return [_text(row.get("account_id")) for row in rows or [] if _text(row.get("account_id"))]

    def publish(self, report: Mapping[str, object]) -> Dict[str, object]:
        snapshots = list((report or {}).get("snapshots") or [])
        overlays = list((report or {}).get("overlays") or [])
        stamp = utc_now()
        head_updates = 0
        with self.transaction() as connection:
            for item in snapshots:
                values = item.to_dict() if hasattr(item, "to_dict") else dict(item or {})
                payload = dict(values.get("payload") or {})
                connection.execute(
                    """
                    INSERT IGNORE INTO shared_instrument_inference_snapshots (
                        snapshot_id, deployment_id, symbol, market_id,
                        semantic_fingerprint, source_fingerprint, release_fingerprint,
                        rulebox_hash, inference_generation_id, source_abox_snapshot_id,
                        source_as_of, consistency_status, source_account_count,
                        payload_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        _text(values.get("snapshot_id"))[:191],
                        _text(values.get("deployment_id"))[:191],
                        _symbol(values.get("symbol"))[:64],
                        _text(values.get("market_id"))[:64],
                        _text(values.get("semantic_fingerprint"))[:64],
                        _text(values.get("source_fingerprint"))[:64],
                        _text(values.get("release_fingerprint"))[:191],
                        _text(values.get("rulebox_hash"))[:191],
                        _text(values.get("inference_generation_id"))[:191],
                        _text(values.get("source_abox_snapshot_id"))[:191],
                        _text(values.get("source_as_of"))[:40],
                        _text(values.get("consistency_status"))[:32],
                        max(1, int(values.get("source_account_count") or 1)),
                        _json(payload),
                        _text(values.get("created_at"))[:40] or stamp,
                    ),
                )
                if _text(values.get("consistency_status")) != "equivalent":
                    continue
                cursor = connection.execute(
                    """
                    INSERT INTO shared_instrument_inference_heads (
                        deployment_id, symbol, snapshot_id, semantic_fingerprint,
                        source_as_of, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        snapshot_id = IF(source_as_of <= VALUES(source_as_of), VALUES(snapshot_id), snapshot_id),
                        semantic_fingerprint = IF(source_as_of <= VALUES(source_as_of), VALUES(semantic_fingerprint), semantic_fingerprint),
                        updated_at = IF(source_as_of <= VALUES(source_as_of), VALUES(updated_at), updated_at),
                        source_as_of = GREATEST(source_as_of, VALUES(source_as_of))
                    """,
                    (
                        _text(values.get("deployment_id"))[:191],
                        _symbol(values.get("symbol"))[:64],
                        _text(values.get("snapshot_id"))[:191],
                        _text(values.get("semantic_fingerprint"))[:64],
                        _text(values.get("source_as_of"))[:40],
                        stamp,
                    ),
                )
                head_updates += int(getattr(cursor, "rowcount", 0) or 0) > 0
            for item in overlays:
                values = item.to_dict() if hasattr(item, "to_dict") else dict(item or {})
                connection.execute(
                    """
                    INSERT INTO portfolio_inference_overlays (
                        overlay_id, deployment_id, account_id, symbol,
                        shared_snapshot_ids_json, inference_generation_id,
                        source_abox_snapshot_id, account_fingerprint,
                        overlay_status, payload_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        shared_snapshot_ids_json = VALUES(shared_snapshot_ids_json),
                        account_fingerprint = VALUES(account_fingerprint),
                        overlay_status = VALUES(overlay_status),
                        payload_json = VALUES(payload_json),
                        created_at = VALUES(created_at)
                    """,
                    (
                        _text(values.get("overlay_id"))[:191],
                        _text(values.get("deployment_id"))[:191],
                        _text(values.get("account_id"))[:191],
                        _symbol(values.get("symbol"))[:64],
                        _json(values.get("shared_snapshot_ids") or []),
                        _text(values.get("inference_generation_id"))[:191],
                        _text(values.get("source_abox_snapshot_id"))[:191],
                        _text(values.get("account_fingerprint"))[:64],
                        _text(values.get("status"))[:32],
                        _json(values.get("payload") or {}),
                        _text(values.get("created_at"))[:40] or stamp,
                    ),
                )
        return {
            "status": _text((report or {}).get("status")) or "ready",
            "snapshotCount": len(snapshots),
            "overlayCount": len(overlays),
            "headUpdateCount": head_updates,
            "sharedSymbolCount": int((report or {}).get("sharedSymbolCount") or 0),
            "verifiedAccountCount": int((report or {}).get("verifiedAccountCount") or 0),
            "consistencyBySymbol": dict((report or {}).get("consistencyBySymbol") or {}),
        }

    def latest(self, deployment_id: str, symbol: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT s.* FROM shared_instrument_inference_heads h
                JOIN shared_instrument_inference_snapshots s ON s.snapshot_id = h.snapshot_id
                WHERE h.deployment_id = %s AND h.symbol = %s
                """,
                (_text(deployment_id), _symbol(symbol)),
            ).fetchone()
        if not row:
            return {}
        values = dict(row)
        values["payload"] = _loads(values.pop("payload_json", "{}"), {})
        return values

    def latest_overlay(self, deployment_id: str, account_id: str, symbol: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_inference_overlays
                WHERE deployment_id = %s AND account_id = %s AND symbol = %s
                ORDER BY created_at DESC, overlay_id DESC LIMIT 1
                """,
                (_text(deployment_id), _text(account_id), _symbol(symbol)),
            ).fetchone()
        if not row:
            return {}
        values = dict(row)
        values["sharedSnapshotIds"] = _loads(values.pop("shared_snapshot_ids_json", "[]"), [])
        values["payload"] = _loads(values.pop("payload_json", "{}"), {})
        return values

    def metrics(self, deployment_id: str = "") -> Dict[str, object]:
        where = " WHERE deployment_id = %s" if _text(deployment_id) else ""
        params = (_text(deployment_id),) if where else ()
        with self.connect() as connection:
            heads = connection.execute(
                "SELECT COUNT(*) AS count FROM shared_instrument_inference_heads" + where,
                params,
            ).fetchone() or {}
            overlays = connection.execute(
                "SELECT COUNT(*) AS count FROM portfolio_inference_overlays" + where,
                params,
            ).fetchone() or {}
            subscriptions = connection.execute(
                "SELECT COUNT(*) AS count, COUNT(DISTINCT account_id) AS accounts, "
                "COUNT(DISTINCT symbol) AS symbols FROM account_instrument_subscriptions WHERE active = 1"
            ).fetchone() or {}
        return {
            "activeSharedHeadCount": int(heads.get("count") or 0),
            "overlayCount": int(overlays.get("count") or 0),
            "activeSubscriptionCount": int(subscriptions.get("count") or 0),
            "subscribedAccountCount": int(subscriptions.get("accounts") or 0),
            "subscribedSymbolCount": int(subscriptions.get("symbols") or 0),
        }

    def prune(self, retention_hours: int = 24, batch_size: int = 200) -> Dict[str, object]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max(1, int(retention_hours or 24)))
        ).isoformat().replace("+00:00", "Z")
        bounded = max(1, min(1000, int(batch_size or 200)))
        with self.transaction() as connection:
            overlay_cursor = connection.execute(
                "DELETE FROM portfolio_inference_overlays WHERE overlay_id IN ("
                "SELECT overlay_id FROM (SELECT overlay_id FROM portfolio_inference_overlays "
                "WHERE created_at < %s ORDER BY created_at LIMIT %s) expired_overlays)",
                (cutoff, bounded),
            )
            snapshot_cursor = connection.execute(
                "DELETE FROM shared_instrument_inference_snapshots WHERE snapshot_id IN ("
                "SELECT snapshot_id FROM (SELECT s.snapshot_id FROM shared_instrument_inference_snapshots s "
                "LEFT JOIN shared_instrument_inference_heads h ON h.snapshot_id = s.snapshot_id "
                "WHERE s.created_at < %s AND h.snapshot_id IS NULL ORDER BY s.created_at LIMIT %s) expired_snapshots)",
                (cutoff, bounded),
            )
        return {
            "status": "ok",
            "cutoff": cutoff,
            "deletedOverlayCount": int(getattr(overlay_cursor, "rowcount", 0) or 0),
            "deletedSnapshotCount": int(getattr(snapshot_cursor, "rowcount", 0) or 0),
        }
