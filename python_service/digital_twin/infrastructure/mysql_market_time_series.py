from collections import defaultdict
from datetime import timedelta
from typing import Dict, Iterable, List

from ..domain.market_time_series import (
    MarketTimeSeriesObservation,
    bucket_start,
    granularity_preferences,
    iso_utc,
    market_session_date,
    parse_timestamp,
    required_session_count,
)
from ..domain.portfolio import AccountSnapshot
from .mysql_operational_connection import MySQLOperationalConnection


GLOBAL_MARKET_ACCOUNT_ID = "__market_data__"


OBSERVATION_COLUMNS = [
    "account_id", "symbol", "granularity", "bucket_at", "observed_at", "source_as_of",
    "provider", "source_role", "name", "market", "currency", "sample_count",
    "open_price", "high_price", "low_price", "current_price", "change_rate",
    "quantity", "average_price", "profit_loss_rate", "volume", "trading_value",
    "volume_ratio", "trade_strength", "bid_ask_imbalance", "foreign_net_volume",
    "institution_net_volume", "individual_net_volume", "ma5", "ma20", "ma60",
    "ma20_slope", "ma60_slope", "ma20_distance", "ma60_distance", "data_quality",
]


def insert_placeholders() -> str:
    return ", ".join(["%s"] * len(OBSERVATION_COLUMNS))


def row_values(row: Dict[str, object]):
    return tuple(row.get(column) for column in OBSERVATION_COLUMNS)


def positive_int(value: object, fallback: int, lower: int = 1, upper: int = 10000) -> int:
    try:
        parsed = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


class MySQLMarketTimeSeriesStore(MySQLOperationalConnection):
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
            "symbolCount": len(symbols),
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
    ) -> Dict[str, object]:
        """Persist non-account quote observations for later outcome matching."""
        if not self.enabled():
            return {"enabled": False, "savedCount": 0, "aggregateCount": 0}
        saved = 0
        aggregate_count = 0
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
                if not observation.valid():
                    continue
                if self.insert_observation_with_connection(connection, observation, replace=True):
                    saved += 1
                    symbols.add(observation.symbol)
                for granularity in ["15m", "1h", "1d"]:
                    aggregate_count += self.upsert_aggregate_with_connection(connection, observation, granularity)
        return {
            "enabled": True,
            "savedCount": saved,
            "aggregateCount": aggregate_count,
            "symbolCount": len(symbols),
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
                    latest_buckets[observation.symbol] = max(latest_bucket, observation.bucket_at)
        return {
            "enabled": True,
            "savedCount": saved,
            "symbolCount": len(symbols),
            "unchangedHistoricalCount": skipped,
        }

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
            "institution_net_volume", "individual_net_volume", "ma5", "ma20", "ma60", "ma20_slope",
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
    ) -> Dict[str, Dict[str, List[Dict[str, object]]]]:
        clean_symbols = sorted({str(symbol or "").upper().strip() for symbol in symbols or [] if str(symbol or "").strip()})
        definition_rows = list(definitions or [])
        if not self.enabled() or not clean_symbols or not definition_rows:
            return {}
        granularities = sorted({
            granularity
            for definition in definition_rows
            for granularity in granularity_preferences(getattr(definition, "key", ""))
        })
        grouped: Dict[tuple, List[Dict[str, object]]] = defaultdict(list)
        per_group = self.max_points_per_window()
        placeholders = ",".join(["%s"] * len(clean_symbols))
        with self.connect() as connection:
            for granularity in granularities:
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
                          AND symbol IN (""" + placeholders + """)
                    ) ranked
                    WHERE ranked.row_number_value <= %s
                    ORDER BY bucket_at DESC
                    """,
                    [str(account_id or ""), GLOBAL_MARKET_ACCOUNT_ID, granularity, *clean_symbols, per_group],
                ).fetchall()
                for row in rows:
                    key = (str(row.get("account_id") or ""), str(row.get("symbol") or "").upper(), granularity)
                    if len(grouped[key]) < per_group:
                        grouped[key].append(self.observation_payload(row))
        result: Dict[str, Dict[str, List[Dict[str, object]]]] = {}
        for symbol in clean_symbols:
            windows: Dict[str, List[Dict[str, object]]] = {}
            for definition in definition_rows:
                window_key = str(getattr(definition, "key", "") or "").upper()
                required_sessions = required_session_count(getattr(definition, "lookback_days", 1))
                selected: List[Dict[str, object]] = []
                best: List[Dict[str, object]] = []
                for granularity in granularity_preferences(window_key):
                    account_rows = list(grouped.get((str(account_id or ""), symbol, granularity), []))
                    global_rows = list(grouped.get((GLOBAL_MARKET_ACCOUNT_ID, symbol, granularity), []))
                    candidate = self.preferred_rows(account_rows, global_rows)
                    candidate = self.limit_for_window(candidate, granularity, required_sessions, per_group)
                    if self.session_count(candidate) > self.session_count(best) or (
                        self.session_count(candidate) == self.session_count(best) and len(candidate) > len(best)
                    ):
                        best = candidate
                    if self.session_count(candidate) >= required_sessions:
                        selected = candidate
                        break
                windows[window_key] = list(reversed(selected or best))
            result[symbol] = windows
        return result

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
        if granularity == "1d":
            target = min(maximum, required_sessions)
        elif granularity == "1h":
            target = min(maximum, max(24, required_sessions * 10))
        elif granularity == "15m":
            target = min(maximum, max(80, required_sessions * 32))
        else:
            target = maximum
        return list(rows[:target])

    def session_count(self, rows: Iterable[Dict[str, object]]) -> int:
        return len({str(row.get("marketSessionDate") or row.get("bucketAt") or "")[:10] for row in rows or [] if row})

    def observation_payload(self, row: Dict[str, object]) -> Dict[str, object]:
        return {
            "generatedAt": str(row.get("observed_at") or row.get("bucket_at") or ""),
            "updatedAt": str(row.get("observed_at") or row.get("bucket_at") or ""),
            "sourceAsOf": str(row.get("source_as_of") or ""),
            "bucketAt": str(row.get("bucket_at") or ""),
            "marketSessionDate": market_session_date(
                row.get("bucket_at"),
                row.get("market"),
                row.get("currency"),
            ),
            "symbol": str(row.get("symbol") or ""),
            "name": str(row.get("name") or ""),
            "market": str(row.get("market") or ""),
            "currency": str(row.get("currency") or ""),
            "source": str(row.get("source_role") or ""),
            "provider": str(row.get("provider") or ""),
            "observationGranularity": str(row.get("granularity") or ""),
            "observationSource": "mysql-market-time-series",
            "sampleCountInBucket": int(row.get("sample_count") or 0),
            "openPrice": float(row.get("open_price") or 0),
            "highPrice": float(row.get("high_price") or 0),
            "lowPrice": float(row.get("low_price") or 0),
            "currentPrice": float(row.get("current_price") or 0),
            "changeRate": float(row.get("change_rate") or 0),
            "quantity": float(row.get("quantity") or 0),
            "averagePrice": float(row.get("average_price") or 0),
            "profitLossRate": float(row.get("profit_loss_rate") or 0),
            "volume": float(row.get("volume") or 0),
            "tradingValue": float(row.get("trading_value") or 0),
            "volumeRatio": float(row.get("volume_ratio") or 0),
            "tradeStrength": float(row.get("trade_strength") or 0),
            "bidAskImbalance": float(row.get("bid_ask_imbalance") or 0),
            "foreignNetVolume": float(row.get("foreign_net_volume") or 0),
            "institutionNetVolume": float(row.get("institution_net_volume") or 0),
            "individualNetVolume": float(row.get("individual_net_volume") or 0),
            "ma5": float(row.get("ma5") or 0),
            "ma20": float(row.get("ma20") or 0),
            "ma60": float(row.get("ma60") or 0),
            "ma20Slope": float(row.get("ma20_slope") or 0),
            "ma60Slope": float(row.get("ma60_slope") or 0),
            "ma20Distance": float(row.get("ma20_distance") or 0),
            "ma60Distance": float(row.get("ma60_distance") or 0),
            "dataQuality": str(row.get("data_quality") or ""),
        }

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
