import json
from collections import defaultdict
from datetime import timedelta
from typing import Dict, Iterable, List

from ..domain.capital_flow import (
    CapitalFlowObservation,
    canonical_observations,
    merge_capital_flow_rows,
    observation_from_row,
)
from ..domain.market_time_series import (
    MarketTimeSeriesObservation,
    bucket_start,
    completed_daily_rows,
    granularity_preferences,
    iso_utc,
    limit_temporal_rows,
    market_session_date,
    parse_timestamp,
    required_session_count,
    snapshot_safe_granularity_preferences,
    temporal_observation_payload,
    temporal_session_count,
)
from ..domain.portfolio import AccountSnapshot
from ..domain.portfolio_ontology_temporal_concepts import (
    trim_to_recent_sessions,
    window_rows,
)
from ..domain.time_series_storage import (
    TIME_SERIES_CONTRACT_VERSION,
    TimeSeriesBackendDescriptor,
    TimeSeriesCapabilities,
    TimeSeriesWatermark,
)
from .mysql_operational_connection import MySQLOperationalConnection


GLOBAL_MARKET_ACCOUNT_ID = "__market_data__"


OBSERVATION_COLUMNS = [
    "account_id", "symbol", "granularity", "bucket_at", "observed_at", "source_as_of",
    "provider", "source_role", "name", "market", "currency", "sample_count",
    "open_price", "high_price", "low_price", "current_price", "change_rate",
    "quantity", "average_price", "profit_loss_rate", "volume", "trading_value",
    "volume_ratio", "trade_strength", "bid_ask_imbalance", "foreign_net_volume",
    "institution_net_volume", "individual_net_volume", "investor_coverage_json", "ma5", "ma20", "ma60",
    "ma20_slope", "ma60_slope", "ma20_distance", "ma60_distance", "data_quality",
]

# Older local installations created these columns as NOT NULL. Coverage JSON,
# not the numeric placeholder, is the semantic source of truth, so retain a
# storage-only zero sentinel while domain reads expose unsupported values as
# ``None``. This avoids an online table rebuild on a large history table.
LEGACY_OPTIONAL_SIGNAL_COLUMNS = {
    "trade_strength",
    "bid_ask_imbalance",
    "foreign_net_volume",
    "institution_net_volume",
    "individual_net_volume",
}

CAPITAL_FLOW_COLUMNS = [
    "observation_id", "subject_kind", "subject_id", "market", "currency", "sector",
    "trading_date", "observed_at", "source_as_of", "received_at", "provider",
    "measurement_type", "observation_status", "freshness_status", "judgement_eligible",
    "observed_fields_json", "coverage_json", "current_price", "market_volume", "trading_value",
    "foreign_net_volume", "foreign_net_amount", "foreign_buy_volume", "foreign_sell_volume",
    "institution_net_volume", "institution_net_amount", "institution_buy_volume", "institution_sell_volume",
    "individual_net_volume", "individual_net_amount", "individual_buy_volume", "individual_sell_volume",
    "data_quality", "contract_version", "created_at", "updated_at",
]

CAPITAL_FLOW_OPTIONAL_NUMERIC_COLUMNS = [
    "current_price", "market_volume", "trading_value",
    "foreign_net_volume", "foreign_net_amount", "foreign_buy_volume", "foreign_sell_volume",
    "institution_net_volume", "institution_net_amount", "institution_buy_volume", "institution_sell_volume",
    "individual_net_volume", "individual_net_amount", "individual_buy_volume", "individual_sell_volume",
]


def insert_placeholders() -> str:
    return ", ".join(["%s"] * len(OBSERVATION_COLUMNS))


def row_values(row: Dict[str, object]):
    return tuple(
        0.0
        if column in LEGACY_OPTIONAL_SIGNAL_COLUMNS and row.get(column) is None
        else row.get(column)
        for column in OBSERVATION_COLUMNS
    )


def capital_flow_row(observation: CapitalFlowObservation, now_value: str = "") -> Dict[str, object]:
    row = observation.to_row()
    row["observation_status"] = row.pop("status")
    stamp = str(now_value or observation.received_at or observation.observed_at)
    row["created_at"] = stamp
    row["updated_at"] = stamp
    return row


def capital_flow_values(row: Dict[str, object]):
    return tuple(row.get(column) for column in CAPITAL_FLOW_COLUMNS)


def positive_int(value: object, fallback: int, lower: int = 1, upper: int = 10000) -> int:
    try:
        parsed = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


class MySQLMarketTimeSeriesStore(MySQLOperationalConnection):
    backend_id = "mysql-primary"

    def descriptor(self) -> TimeSeriesBackendDescriptor:
        return TimeSeriesBackendDescriptor(
            backend_id=self.backend_id,
            adapter_name="mysql",
            adapter_version="mysql-market-time-series-v1",
            status="active",
            contract_version=TIME_SERIES_CONTRACT_VERSION,
            capabilities=TimeSeriesCapabilities(
                idempotent_upsert=True,
                window_functions=True,
                batch_ingestion=True,
                point_in_time_read=True,
            ),
        )

    def health(self) -> Dict[str, object]:
        try:
            with self.connect() as connection:
                row = connection.execute("SELECT 1 AS ready").fetchone() or {}
            status = "ready" if int(row.get("ready") or 0) == 1 else "unhealthy"
            error = ""
        except Exception as caught:  # noqa: BLE001 - backend health is returned as data.
            status = "unavailable"
            error = str(caught)[:240]
        return {"backendId": self.backend_id, "adapter": "mysql", "status": status, "error": error}

    def watermark(self) -> TimeSeriesWatermark:
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT MAX(observed_at) AS observed_through FROM market_time_series_observations"
                ).fetchone() or {}
            return TimeSeriesWatermark(
                backend_id=self.backend_id,
                observed_through=str(row.get("observed_through") or ""),
                status="ready",
            )
        except Exception as error:  # noqa: BLE001 - callers compare unavailable candidates safely.
            return TimeSeriesWatermark(self.backend_id, "", status="unavailable:" + str(error)[:120])

    def enabled(self) -> bool:
        return str(self.runtime_settings.get("marketTimeSeriesEnabled", "1")).strip().lower() not in {
            "0", "false", "no", "off", "disabled",
        }

    def max_points_per_window(self) -> int:
        return positive_int(self.runtime_settings.get("marketTimeSeriesMaxPointsPerWindow"), 500, 20, 2000)

    def record_snapshots_with_connection(self, connection, snapshots: Iterable[AccountSnapshot]) -> Dict[str, object]:
        if not self.enabled():
            return {"enabled": False, "savedCount": 0, "aggregateCount": 0}
        saved = 0
        aggregate_count = 0
        capital_flow_count = 0
        capital_flow_rows = []
        symbols = set()
        for snapshot in snapshots or []:
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or []):
                if not position or position.is_cash():
                    continue
                observation = MarketTimeSeriesObservation.from_position(
                    snapshot.account_id,
                    position,
                    snapshot.generated_at,
                    provider=snapshot.provider,
                )
                flow_observation = CapitalFlowObservation.from_position(
                    position,
                    snapshot.generated_at,
                    provider=snapshot.provider,
                )
                if flow_observation and self.insert_capital_flow_with_connection(connection, flow_observation):
                    capital_flow_count += 1
                    capital_flow_rows.append(flow_observation.to_row())
                if not observation.valid():
                    continue
                inserted = self.insert_observation_with_connection(connection, observation, replace=False)
                if not inserted:
                    continue
                saved += 1
                symbols.add(observation.symbol)
                for granularity in ["15m", "1h", "1d"]:
                    aggregate_count += self.upsert_aggregate_with_connection(connection, observation, granularity)
        return {
            "enabled": True,
            "savedCount": saved,
            "aggregateCount": aggregate_count,
            "capitalFlowCount": capital_flow_count,
            "symbolCount": len(symbols),
            "_capitalFlowRows": capital_flow_rows,
        }

    def record_snapshots(self, snapshots: Iterable[AccountSnapshot]) -> Dict[str, object]:
        with self.transaction() as connection:
            return self.record_snapshots_with_connection(connection, snapshots)

    def record_positions(
        self,
        account_id: str,
        positions: Iterable[object],
        observed_at: str,
        provider: str = "",
        replace: bool = True,
    ) -> Dict[str, object]:
        """Persist non-account quote observations for later outcome matching."""
        if not self.enabled():
            return {"enabled": False, "savedCount": 0, "aggregateCount": 0}
        saved = 0
        aggregate_count = 0
        capital_flow_count = 0
        capital_flow_rows = []
        symbols = set()
        with self.transaction() as connection:
            for position in positions or []:
                if not position or position.is_cash():
                    continue
                observation = MarketTimeSeriesObservation.from_position(
                    str(account_id or GLOBAL_MARKET_ACCOUNT_ID),
                    position,
                    observed_at,
                    provider=provider,
                )
                flow_observation = CapitalFlowObservation.from_position(position, observed_at, provider=provider)
                if flow_observation and self.insert_capital_flow_with_connection(connection, flow_observation):
                    capital_flow_count += 1
                    capital_flow_rows.append(flow_observation.to_row())
                if not observation.valid():
                    continue
                inserted = self.insert_observation_with_connection(
                    connection,
                    observation,
                    replace=replace,
                )
                if inserted:
                    saved += 1
                    symbols.add(observation.symbol)
                elif not replace:
                    continue
                for granularity in ["15m", "1h", "1d"]:
                    aggregate_count += self.upsert_aggregate_with_connection(connection, observation, granularity)
        return {
            "enabled": True,
            "savedCount": saved,
            "aggregateCount": aggregate_count,
            "capitalFlowCount": capital_flow_count,
            "symbolCount": len(symbols),
            "_capitalFlowRows": capital_flow_rows,
        }

    def record_daily_candles(
        self,
        candles_by_symbol: Dict[str, List[Dict[str, object]]],
        metadata_by_symbol: Dict[str, Dict[str, object]] = None,
        provider: str = "toss-candles",
    ) -> Dict[str, object]:
        if not self.enabled():
            return {"enabled": False, "savedCount": 0, "symbolCount": 0}
        metadata_by_symbol = metadata_by_symbol or {}
        saved = 0
        skipped = 0
        symbols = set()
        projected_rows = []
        with self.transaction() as connection:
            latest_buckets = self.latest_daily_buckets_with_connection(connection, candles_by_symbol.keys())
            for symbol, candles in dict(candles_by_symbol or {}).items():
                metadata = metadata_by_symbol.get(str(symbol or "").upper()) or {}
                observations = [
                    MarketTimeSeriesObservation.from_daily_candle(
                        GLOBAL_MARKET_ACCOUNT_ID,
                        symbol,
                        candle,
                        market=str(metadata.get("market") or ""),
                        currency=str(metadata.get("currency") or ""),
                        provider=provider,
                        name=str(metadata.get("name") or symbol),
                    )
                    for candle in candles or []
                ]
                for observation in sorted(observations, key=lambda item: item.bucket_at):
                    if not observation.valid():
                        continue
                    latest_bucket = str(latest_buckets.get(observation.symbol) or "")
                    if latest_bucket and observation.bucket_at < latest_bucket:
                        skipped += 1
                        continue
                    if self.insert_observation_with_connection(connection, observation, replace=True):
                        saved += 1
                        symbols.add(observation.symbol)
                        projected_rows.append(observation.to_row())
                    latest_buckets[observation.symbol] = max(latest_bucket, observation.bucket_at)
        return {
            "enabled": True,
            "savedCount": saved,
            "symbolCount": len(symbols),
            "unchangedHistoricalCount": skipped,
            "_projectedRows": projected_rows,
        }

    def projectable_rows_with_connection(
        self,
        connection,
        account_ids: Iterable[str] = (),
        symbols: Iterable[str] = (),
        observed_ats: Iterable[str] = (),
        granularities: Iterable[str] = (),
        providers: Iterable[str] = (),
        observed_after: str = "",
        limit: int = 10000,
        offset: int = 0,
        after_key: Dict[str, object] = None,
    ) -> List[Dict[str, object]]:
        clauses = []
        params: List[object] = []
        for column, values in [
            ("account_id", account_ids),
            ("symbol", symbols),
            ("observed_at", observed_ats),
            ("granularity", granularities),
            ("provider", providers),
        ]:
            clean_values = sorted({str(value or "").strip() for value in values or [] if str(value or "").strip()})
            if not clean_values:
                continue
            clauses.append(column + " IN (" + ",".join(["%s"] * len(clean_values)) + ")")
            params.extend(clean_values)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        if str(observed_after or "").strip():
            where += (" AND " if where else " WHERE ") + "observed_at > %s"
            params.append(iso_utc(observed_after))
        cursor_key = dict(after_key or {})
        if all(str(cursor_key.get(key) or "") for key in ["account_id", "symbol", "granularity", "bucket_at"]):
            cursor_clause = "(account_id, symbol, granularity, bucket_at) > (%s, %s, %s, %s)"
            where += (" AND " if where else " WHERE ") + cursor_clause
            params.extend([
                str(cursor_key.get("account_id") or ""),
                str(cursor_key.get("symbol") or ""),
                str(cursor_key.get("granularity") or ""),
                str(cursor_key.get("bucket_at") or ""),
            ])
        bounded_limit = max(1, min(50000, int(limit or 10000)))
        bounded_offset = max(0, int(offset or 0))
        params.extend([bounded_limit, bounded_offset])
        rows = connection.execute(
            "SELECT " + ",".join(OBSERVATION_COLUMNS)
            + " FROM market_time_series_observations" + where
            + " ORDER BY account_id, symbol, granularity, bucket_at LIMIT %s OFFSET %s",
            params,
        ).fetchall()
        return [dict(row or {}) for row in rows or []]

    def projectable_rows(self, **kwargs) -> List[Dict[str, object]]:
        with self.connect() as connection:
            return self.projectable_rows_with_connection(connection, **kwargs)

    def load_portfolio_analysis_series(
        self,
        account_id: str,
        symbols: Iterable[str],
        as_of: str = "",
        limit_per_symbol: int = 260,
    ) -> Dict[str, List[Dict[str, object]]]:
        """Load one bounded daily-history packet for portfolio analytics.

        Global provider candles are preferred because they are consistent
        across accounts. Account daily rollups are used only when a global
        series is unavailable. The observed-at cutoff prevents look-ahead on
        historical snapshot replay.
        """
        clean_symbols = sorted({
            str(symbol or "").upper().strip()
            for symbol in symbols or []
            if str(symbol or "").strip()
        })
        if not self.enabled() or not clean_symbols:
            return {}
        limit_value = positive_int(limit_per_symbol, 260, 20, 1000)
        cutoff = iso_utc(as_of)
        placeholders = ",".join(["%s"] * len(clean_symbols))
        cutoff_clause = " AND observations.observed_at <= %s" if cutoff else ""
        params: List[object] = [
            str(account_id or ""),
            GLOBAL_MARKET_ACCOUNT_ID,
            *clean_symbols,
        ]
        if cutoff:
            params.append(cutoff)
        params.append(limit_value)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT observations.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY account_id, symbol
                               ORDER BY bucket_at DESC
                           ) AS row_number_value
                    FROM market_time_series_observations observations
                    WHERE account_id IN (%s, %s)
                      AND granularity = '1d'
                      AND symbol IN (""" + placeholders + ")" + cutoff_clause + """
                ) ranked
                WHERE ranked.row_number_value <= %s
                ORDER BY symbol, bucket_at ASC
                """,
                params,
            ).fetchall()
        grouped: Dict[tuple, List[Dict[str, object]]] = defaultdict(list)
        for row in rows or []:
            grouped[(str(row.get("account_id") or ""), str(row.get("symbol") or "").upper())].append(
                self.observation_payload(row)
            )
        result: Dict[str, List[Dict[str, object]]] = {}
        for symbol in clean_symbols:
            global_rows = grouped.get((GLOBAL_MARKET_ACCOUNT_ID, symbol), [])
            account_rows = grouped.get((str(account_id or ""), symbol), [])
            selected = global_rows or account_rows
            if selected:
                result[symbol] = list(selected[-limit_value:])
        return result

    def load_instrument_series(
        self,
        account_id: str,
        symbol: str,
        granularity: str = "1d",
        limit: int = 260,
        as_of: str = "",
    ) -> List[Dict[str, object]]:
        """Load one bounded chart series, preferring global provider candles."""

        clean_symbol = str(symbol or "").upper().strip()
        clean_granularity = str(granularity or "1d").lower().strip()
        if clean_granularity not in {"3m", "15m", "1h", "1d"}:
            clean_granularity = "1d"
        if not self.enabled() or not clean_symbol:
            return []
        row_limit = positive_int(limit, 260, 20, 2000)
        cutoff = iso_utc(as_of)
        cutoff_clause = " AND observations.observed_at <= %s" if cutoff else ""
        params: List[object] = [
            str(account_id or ""),
            GLOBAL_MARKET_ACCOUNT_ID,
            clean_symbol,
            clean_granularity,
        ]
        if cutoff:
            params.append(cutoff)
        params.append(row_limit)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT observations.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY account_id
                               ORDER BY bucket_at DESC
                           ) AS row_number_value
                    FROM market_time_series_observations observations
                    WHERE account_id IN (%s, %s)
                      AND symbol = %s
                      AND granularity = %s
                """ + cutoff_clause + """
                ) ranked
                WHERE ranked.row_number_value <= %s
                ORDER BY account_id, bucket_at ASC
                """,
                params,
            ).fetchall()
        grouped: Dict[str, List[Dict[str, object]]] = defaultdict(list)
        for row in rows or []:
            grouped[str(row.get("account_id") or "")].append(self.observation_payload(row))
        global_rows = grouped.get(GLOBAL_MARKET_ACCOUNT_ID, [])
        account_rows = grouped.get(str(account_id or ""), [])
        return list((global_rows or account_rows)[-row_limit:])

    def latest_daily_buckets_with_connection(self, connection, symbols: Iterable[str]) -> Dict[str, str]:
        clean_symbols = sorted({str(symbol or "").upper().strip() for symbol in symbols or [] if str(symbol or "").strip()})
        if not clean_symbols:
            return {}
        placeholders = ",".join(["%s"] * len(clean_symbols))
        rows = connection.execute(
            "SELECT symbol, MAX(bucket_at) AS latest_bucket "
            "FROM market_time_series_observations "
            "WHERE account_id = %s AND granularity = '1d' AND symbol IN ("
            + placeholders
            + ") GROUP BY symbol",
            [GLOBAL_MARKET_ACCOUNT_ID, *clean_symbols],
        ).fetchall()
        return {
            str(row.get("symbol") or "").upper(): str(row.get("latest_bucket") or "")
            for row in rows
        }

    def insert_observation_with_connection(
        self,
        connection,
        observation: MarketTimeSeriesObservation,
        replace: bool = False,
    ) -> bool:
        row = observation.to_row()
        insert_mode = "INSERT" if replace else "INSERT IGNORE"
        update_clause = ""
        if replace:
            update_clause = " ON DUPLICATE KEY UPDATE " + ", ".join(
                column + " = VALUES(" + column + ")"
                for column in OBSERVATION_COLUMNS
                if column not in {"account_id", "symbol", "granularity", "bucket_at"}
            )
        cursor = connection.execute(
            insert_mode
            + " INTO market_time_series_observations ("
            + ", ".join(OBSERVATION_COLUMNS)
            + ") VALUES ("
            + insert_placeholders()
            + ")"
            + update_clause,
            row_values(row),
        )
        return bool(int(getattr(cursor, "rowcount", 0) or 0))

    def insert_capital_flow_with_connection(
        self,
        connection,
        observation: CapitalFlowObservation,
    ) -> bool:
        if not observation or not observation.valid():
            return False
        row = capital_flow_row(observation, iso_utc(observation.received_at or observation.observed_at))
        update_columns = [
            column for column in CAPITAL_FLOW_COLUMNS
            if column not in {"observation_id", "created_at"}
        ]
        assignments = []
        for column in update_columns:
            if column in CAPITAL_FLOW_OPTIONAL_NUMERIC_COLUMNS:
                assignments.append(column + " = COALESCE(VALUES(" + column + "), " + column + ")")
            else:
                assignments.append(column + " = VALUES(" + column + ")")
        cursor = connection.execute(
            "INSERT INTO capital_flow_observations ("
            + ", ".join(CAPITAL_FLOW_COLUMNS)
            + ") VALUES ("
            + ", ".join(["%s"] * len(CAPITAL_FLOW_COLUMNS))
            + ") ON DUPLICATE KEY UPDATE "
            + ", ".join(assignments),
            capital_flow_values(row),
        )
        return bool(int(getattr(cursor, "rowcount", 0) or 0))

    def write_capital_flow_observations(self, observations: Iterable[Dict[str, object]]) -> Dict[str, object]:
        inserted = 0
        accepted = 0
        with self.transaction() as connection:
            for raw in observations or []:
                observation = observation_from_row(raw) if isinstance(raw, dict) else None
                if not observation or not observation.valid():
                    continue
                accepted += 1
                inserted += int(self.insert_capital_flow_with_connection(connection, observation))
        return {
            "backendId": self.backend_id,
            "acceptedCount": accepted,
            "writtenCount": inserted,
            "status": "completed",
        }

    def load_capital_flow_observations(
        self,
        symbols: Iterable[str] = (),
        market: str = "",
        observed_after: str = "",
        as_of: str = "",
        limit: int = 5000,
    ) -> List[Dict[str, object]]:
        clauses = ["judgement_eligible = 1", "observation_status = 'available'"]
        params: List[object] = []
        clean_symbols = sorted({str(symbol or "").upper().strip() for symbol in symbols or [] if str(symbol or "").strip()})
        if clean_symbols:
            clauses.append("subject_id IN (" + ",".join(["%s"] * len(clean_symbols)) + ")")
            params.extend(clean_symbols)
        if str(market or "").strip():
            clauses.append("market = %s")
            params.append(str(market or "").upper().strip())
        if str(observed_after or "").strip():
            clauses.append("observed_at >= %s")
            params.append(iso_utc(observed_after))
        if str(as_of or "").strip():
            clauses.append("observed_at <= %s")
            params.append(iso_utc(as_of))
        bounded_limit = max(1, min(50000, int(limit or 5000)))
        params.append(bounded_limit)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT "
                + ", ".join(CAPITAL_FLOW_COLUMNS[:-2])
                + " FROM capital_flow_observations WHERE "
                + " AND ".join(clauses)
                + " ORDER BY subject_id, trading_date, observed_at LIMIT %s",
                params,
            ).fetchall()
        payloads = []
        for raw in rows or []:
            row = dict(raw or {})
            row["status"] = row.pop("observation_status", "")
            observation = observation_from_row(row)
            if observation and observation.valid():
                payloads.append(observation.to_payload())
        return payloads

    def capital_flow_quality(self, legacy_days: int = 30) -> Dict[str, object]:
        bounded_days = max(1, min(3650, int(legacy_days or 30)))
        with self.connect() as connection:
            current = connection.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN measurement_type = 'daily-final' THEN 1 ELSE 0 END) AS daily_final, "
                "SUM(CASE WHEN measurement_type = 'intraday-estimate' THEN 1 ELSE 0 END) AS intraday_estimate, "
                "COUNT(DISTINCT subject_id) AS subjects, MAX(source_as_of) AS source_as_of "
                "FROM capital_flow_observations WHERE judgement_eligible = 1 AND observation_status = 'available'"
            ).fetchone() or {}
            legacy = connection.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN investor_coverage_json IS NULL OR investor_coverage_json = '{}' THEN 1 ELSE 0 END) AS empty_coverage, "
                "SUM(CASE WHEN foreign_net_volume = 0 AND institution_net_volume = 0 THEN 1 ELSE 0 END) AS zero_smart_money "
                "FROM market_time_series_observations WHERE market = 'KR' "
                "AND bucket_at >= DATE_FORMAT(DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY), '%%Y-%%m-%%dT%%H:%%i:%%sZ')",
                [bounded_days],
            ).fetchone() or {}
        return {
            "status": "ready",
            "capitalFlow": {
                "observationCount": int(current.get("total") or 0),
                "dailyFinalCount": int(current.get("daily_final") or 0),
                "intradayEstimateCount": int(current.get("intraday_estimate") or 0),
                "subjectCount": int(current.get("subjects") or 0),
                "sourceAsOf": str(current.get("source_as_of") or ""),
            },
            "legacyMixedSeries": {
                "lookbackDays": bounded_days,
                "observationCount": int(legacy.get("total") or 0),
                "emptyCoverageCount": int(legacy.get("empty_coverage") or 0),
                "zeroSmartMoneyCount": int(legacy.get("zero_smart_money") or 0),
            },
            "missingConvertedToZeroCount": 0,
        }

    def rebuild_capital_flow_from_legacy(self, limit: int = 50000) -> Dict[str, object]:
        bounded_limit = max(1, min(500000, int(limit or 50000)))
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM market_time_series_observations "
                "WHERE market = 'KR' AND investor_coverage_json IS NOT NULL "
                "AND investor_coverage_json <> '{}' "
                "ORDER BY observed_at, account_id, symbol, granularity LIMIT %s",
                [bounded_limit],
            ).fetchall()
            accepted = 0
            projectable = []
            for raw in rows or []:
                observation = CapitalFlowObservation.from_legacy_row(dict(raw or {}))
                if not observation or not observation.valid():
                    continue
                accepted += 1
                projectable.append(observation)
            # Account writers and rollups repeat the same public source slot.
            # Collapse that duplication, but retain every sourceAsOf and
            # measurement version so an intraday point-in-time replay does
            # not see a later daily-final observation.
            by_observation_id = {}
            for observation in projectable:
                previous = by_observation_id.get(observation.observation_id)
                if not previous:
                    by_observation_id[observation.observation_id] = observation
                    continue
                observation_rank = (len(observation.observed_fields()), observation.observed_at)
                previous_rank = (len(previous.observed_fields()), previous.observed_at)
                if observation_rank[0] > previous_rank[0] or (
                    observation_rank[0] == previous_rank[0]
                    and observation_rank[1] < previous_rank[1]
                ):
                    by_observation_id[observation.observation_id] = observation
            source_observations = sorted(
                by_observation_id.values(),
                key=lambda item: (item.subject_id, item.trading_date, item.observed_at, item.observation_id),
            )
            written = sum(
                int(self.insert_capital_flow_with_connection(connection, observation))
                for observation in source_observations
            )
        canonical = canonical_observations(source_observations)
        return {
            "status": "completed",
            "scannedCount": len(rows or []),
            "acceptedCount": accepted,
            "sourceObservationCount": len(source_observations),
            "writtenCount": written,
            "canonicalCount": len(canonical),
            "rejectedCount": max(0, len(rows or []) - accepted),
            "observations": [item.to_row() for item in source_observations],
        }

    def upsert_aggregate_with_connection(
        self,
        connection,
        raw: MarketTimeSeriesObservation,
        granularity: str,
    ) -> int:
        row = raw.to_row()
        row.update({
            "granularity": granularity,
            "bucket_at": bucket_start(raw.observed_at, granularity, raw.market, raw.currency),
            "provider": "rollup:" + str(raw.provider or "monitor"),
            "sample_count": 1,
        })
        update_latest = [
            "observed_at", "source_as_of", "provider", "source_role", "name", "market", "currency",
            "current_price", "change_rate", "quantity", "average_price", "profit_loss_rate", "volume",
            "trading_value", "volume_ratio", "trade_strength", "bid_ask_imbalance", "foreign_net_volume",
            "institution_net_volume", "individual_net_volume", "investor_coverage_json", "ma5", "ma20", "ma60", "ma20_slope",
            "ma60_slope", "ma20_distance", "ma60_distance", "data_quality",
        ]
        sql = (
            "INSERT INTO market_time_series_observations ("
            + ", ".join(OBSERVATION_COLUMNS)
            + ") VALUES ("
            + insert_placeholders()
            + ") ON DUPLICATE KEY UPDATE "
            + "sample_count = sample_count + 1, "
            + "high_price = GREATEST(high_price, VALUES(high_price)), "
            + "low_price = CASE WHEN low_price <= 0 THEN VALUES(low_price) ELSE LEAST(low_price, VALUES(low_price)) END, "
            + ", ".join(column + " = VALUES(" + column + ")" for column in update_latest)
        )
        cursor = connection.execute(sql, row_values(row))
        return 1 if int(getattr(cursor, "rowcount", 0) or 0) else 0

    def load_temporal_windows(
        self,
        account_id: str,
        symbols: Iterable[str],
        definitions: Iterable[object],
        as_of: str = "",
    ) -> Dict[str, Dict[str, List[Dict[str, object]]]]:
        """Load only observations that were available at one snapshot time.

        ABox projection can be retried after newer monitor cycles have already
        written price history.  Reading those later rows for an older account
        snapshot would make the same source snapshot produce a different
        investment world and, worse, introduce look-ahead data into a replay.
        ``observed_at`` is the availability boundary for stored observations,
        so use it when the caller provides a valid snapshot timestamp.
        """
        clean_symbols = sorted({str(symbol or "").upper().strip() for symbol in symbols or [] if str(symbol or "").strip()})
        definition_rows = list(definitions or [])
        if not self.enabled() or not clean_symbols or not definition_rows:
            return {}
        observation_cutoff = iso_utc(as_of)
        preference_by_window = {
            str(getattr(definition, "key", "") or "").upper(): (
                snapshot_safe_granularity_preferences(definition)
                if observation_cutoff
                else list(granularity_preferences(getattr(definition, "key", "")))
            )
            for definition in definition_rows
        }
        granularities = sorted({
            granularity
            for values in preference_by_window.values()
            for granularity in values
        })
        grouped: Dict[tuple, List[Dict[str, object]]] = defaultdict(list)
        per_group = self.max_points_per_window()
        placeholders = ",".join(["%s"] * len(clean_symbols))
        with self.connect() as connection:
            for granularity in granularities:
                cutoff_clause = " AND observations.observed_at <= %s" if observation_cutoff else ""
                query_params = [str(account_id or ""), GLOBAL_MARKET_ACCOUNT_ID, granularity, *clean_symbols]
                if observation_cutoff:
                    query_params.append(observation_cutoff)
                query_params.append(per_group)
                rows = connection.execute(
                    """
                    SELECT * FROM (
                        SELECT observations.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY account_id, symbol
                                   ORDER BY bucket_at DESC
                               ) AS row_number_value
                        FROM market_time_series_observations observations
                        WHERE account_id IN (%s, %s)
                          AND granularity = %s
                          AND symbol IN (""" + placeholders + ")" + cutoff_clause + """
                    ) ranked
                    WHERE ranked.row_number_value <= %s
                    ORDER BY bucket_at DESC
                    """,
                    query_params,
                ).fetchall()
                for row in rows:
                    key = (str(row.get("account_id") or ""), str(row.get("symbol") or "").upper(), granularity)
                    if len(grouped[key]) < per_group:
                        grouped[key].append(self.observation_payload(row))
        capital_flow_rows = self.load_capital_flow_observations(
            symbols=clean_symbols,
            as_of=observation_cutoff,
            limit=max(100, len(clean_symbols) * 80),
        )
        capital_flow_by_symbol = defaultdict(list)
        for row in capital_flow_rows:
            capital_flow_by_symbol[str(row.get("subjectId") or "").upper()].append(row)
        result: Dict[str, Dict[str, List[Dict[str, object]]]] = {}
        for symbol in clean_symbols:
            windows: Dict[str, List[Dict[str, object]]] = {}
            for definition in definition_rows:
                window_key = str(getattr(definition, "key", "") or "").upper()
                required_sessions = int(
                    getattr(
                        definition,
                        "required_sessions",
                        required_session_count(getattr(definition, "lookback_days", 1)),
                    )
                    or 1
                )
                selected: List[Dict[str, object]] = []
                best: List[Dict[str, object]] = []
                for granularity in preference_by_window.get(window_key) or []:
                    account_rows = list(grouped.get((str(account_id or ""), symbol, granularity), []))
                    global_rows = list(grouped.get((GLOBAL_MARKET_ACCOUNT_ID, symbol, granularity), []))
                    if observation_cutoff:
                        # Account 3m rows are insert-only. Aggregates may have
                        # been recalculated after the snapshot and global raw
                        # rows can be overwritten by a provider refresh.
                        if granularity == "3m":
                            candidate = account_rows
                        else:
                            candidate = self.completed_global_daily_rows(global_rows, observation_cutoff)
                    else:
                        candidate = self.preferred_rows(account_rows, global_rows)
                    candidate = self.limit_for_window(candidate, granularity, required_sessions, per_group)
                    if self.session_count(candidate) > self.session_count(best) or (
                        self.session_count(candidate) == self.session_count(best) and len(candidate) > len(best)
                    ):
                        best = candidate
                    if self.session_count(candidate) >= required_sessions:
                        selected = candidate
                        break
                ordered = list(reversed(selected or best))
                if bool(getattr(definition, "is_intraday", False)):
                    ordered = window_rows(ordered, definition, parse_timestamp(observation_cutoff))
                else:
                    ordered = trim_to_recent_sessions(ordered, required_sessions)
                    ordered = merge_capital_flow_rows(ordered, capital_flow_by_symbol.get(symbol, []))
                windows[window_key] = ordered
            result[symbol] = windows
        return result

    @staticmethod
    def completed_global_daily_rows(rows: Iterable[Dict[str, object]], snapshot_at: str) -> List[Dict[str, object]]:
        """Exclude the still-open market session from historical daily candles.

        Daily providers can revise the current session in place.  The account
        snapshot itself supplies the current observation, so only prior,
        completed sessions may provide replay history.
        """
        return completed_daily_rows(rows, snapshot_at)

    def preferred_rows(self, account_rows: List[Dict[str, object]], global_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
        account_sessions = self.session_count(account_rows)
        global_sessions = self.session_count(global_rows)
        if account_sessions >= global_sessions and account_rows:
            return account_rows
        return global_rows or account_rows

    def load_outcome_observations(
        self,
        account_id: str,
        targets: Iterable[Dict[str, object]],
        max_delay_minutes: int = 180,
    ) -> Dict[str, Dict[str, object]]:
        """Return the first usable stored market observation after each target.

        An outcome must be tied to the decision's configured horizon. Selecting
        the latest quote here would turn a multi-day-late quote into a false
        60-minute result. The query keeps the nearest account and global time
        series observations separately, then prefers the account observation.
        """
        try:
            delay_minutes = int(float(max_delay_minutes or 180))
        except (TypeError, ValueError):
            delay_minutes = 180
        delay_minutes = max(1, min(60 * 24 * 14, delay_minutes))
        clean_targets = []
        for raw in targets or []:
            target = dict(raw or {}) if isinstance(raw, dict) else {}
            request_id = str(target.get("requestId") or "").strip()
            symbol = str(target.get("symbol") or "").upper().strip()
            target_at = iso_utc(target.get("targetAt"))
            parsed_target = parse_timestamp(target_at)
            if not request_id or not symbol or not parsed_target:
                continue
            try:
                target_delay_minutes = int(float(target.get("maximumObservationDelayMinutes") or delay_minutes))
            except (TypeError, ValueError):
                target_delay_minutes = delay_minutes
            target_delay_minutes = max(1, min(60 * 24 * 14, target_delay_minutes))
            clean_targets.append({
                "requestId": request_id,
                "symbol": symbol,
                "targetAt": target_at,
                "deadlineAt": (parsed_target + timedelta(minutes=target_delay_minutes)).isoformat().replace("+00:00", "Z"),
            })
            if len(clean_targets) >= 1000:
                break
        if not self.enabled() or not clean_targets:
            return {}
        target_sql = " UNION ALL ".join(
            "SELECT %s AS request_key, %s AS symbol, %s AS target_at, %s AS deadline_at"
            for _ in clean_targets
        )
        params: List[object] = []
        for target in clean_targets:
            params.extend([target["requestId"], target["symbol"], target["targetAt"], target["deadlineAt"]])
        params.extend([str(account_id or ""), GLOBAL_MARKET_ACCOUNT_ID])
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT target_requests.request_key,
                           observations.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY target_requests.request_key, observations.account_id
                               ORDER BY observations.observed_at ASC,
                                        CASE observations.granularity
                                            WHEN '3m' THEN 1
                                            WHEN '15m' THEN 2
                                            WHEN '1h' THEN 3
                                            WHEN '1d' THEN 4
                                            ELSE 5
                                        END ASC,
                                        observations.bucket_at ASC
                           ) AS row_number_value
                    FROM (""" + target_sql + """) AS target_requests
                    JOIN market_time_series_observations observations
                      ON observations.symbol = target_requests.symbol
                     AND observations.account_id IN (%s, %s)
                     AND observations.current_price > 0
                     AND observations.observed_at >= target_requests.target_at
                     AND observations.observed_at <= target_requests.deadline_at
                ) ranked
                WHERE ranked.row_number_value = 1
                """,
                params,
            ).fetchall()
        preferred: Dict[str, Dict[str, object]] = {}
        for row in rows or []:
            request_id = str(row.get("request_key") or "")
            if not request_id:
                continue
            current = preferred.get(request_id)
            is_account_row = str(row.get("account_id") or "") == str(account_id or "")
            current_is_account_row = str((current or {}).get("account_id") or "") == str(account_id or "")
            if current and current_is_account_row and not is_account_row:
                continue
            preferred[request_id] = row
        results: Dict[str, Dict[str, object]] = {}
        for request_id, row in preferred.items():
            payload = self.observation_payload(row)
            payload["outcomeRequestId"] = request_id
            payload["observationBasis"] = "historical-market-time-series"
            results[request_id] = payload
        return results

    def limit_for_window(
        self,
        rows: List[Dict[str, object]],
        granularity: str,
        required_sessions: int,
        maximum: int,
    ) -> List[Dict[str, object]]:
        return limit_temporal_rows(rows, granularity, required_sessions, maximum)

    def session_count(self, rows: Iterable[Dict[str, object]]) -> int:
        return temporal_session_count(rows)

    def observation_payload(self, row: Dict[str, object]) -> Dict[str, object]:
        return temporal_observation_payload(row, "mysql-market-time-series")

    @staticmethod
    def parse_investor_coverage(value: object) -> Dict[str, object]:
        if isinstance(value, dict):
            return dict(value)
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}

    def summary(self, account_id: str = "") -> Dict[str, object]:
        clauses = []
        params: List[object] = []
        if account_id:
            clauses.append("account_id = %s")
            params.append(str(account_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT granularity, COUNT(*) AS count, COUNT(DISTINCT symbol) AS symbol_count,
                       MIN(bucket_at) AS earliest_at, MAX(observed_at) AS latest_at
                FROM market_time_series_observations
                """ + where + " GROUP BY granularity ORDER BY granularity",
                params,
            ).fetchall()
        return {
            "enabled": self.enabled(),
            "accountId": str(account_id or ""),
            "granularities": [
                {
                    "granularity": str(row.get("granularity") or ""),
                    "count": int(row.get("count") or 0),
                    "symbolCount": int(row.get("symbol_count") or 0),
                    "earliestAt": str(row.get("earliest_at") or ""),
                    "latestAt": str(row.get("latest_at") or ""),
                }
                for row in rows
            ],
        }
