"""QuestDB adapter for the vendor-neutral temporal storage ports."""

import base64
import json
import math
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping

from ..domain.market_time_series import (
    completed_daily_rows,
    granularity_preferences,
    limit_temporal_rows,
    parse_timestamp,
    required_session_count,
    snapshot_safe_granularity_preferences,
    temporal_observation_payload,
    temporal_session_count,
)
from ..domain.portfolio_ontology_temporal_concepts import trim_to_recent_sessions, window_rows
from ..domain.time_series_storage import (
    TIME_SERIES_CONTRACT_VERSION,
    TimeSeriesBackendDescriptor,
    TimeSeriesCapabilities,
    TimeSeriesWatermark,
)


QUESTDB_ADAPTER_VERSION = "questdb-time-series-adapter-v1"

GRANULARITY_TABLES = {
    "3m": "market_observations_3m",
    "15m": "market_observations_15m",
    "1h": "market_observations_1h",
    "1d": "market_observations_1d",
}

MARKET_COLUMNS = [
    "event_at", "observed_at", "source_as_of", "received_at",
    "account_id", "symbol", "granularity", "provider", "source_role", "name",
    "market", "currency", "sample_count", "open_price", "high_price",
    "low_price", "current_price", "change_rate", "volume", "trading_value",
    "quantity", "average_price", "profit_loss_rate",
    "volume_ratio", "trade_strength", "bid_ask_imbalance",
    "foreign_net_volume", "institution_net_volume", "individual_net_volume",
    "investor_coverage_json", "ma5", "ma20", "ma60", "ma20_slope",
    "ma60_slope", "ma20_distance", "ma60_distance", "data_quality",
]

PORTFOLIO_MARK_COLUMNS = [
    "event_at", "observed_at", "account_id", "symbol", "quantity",
    "average_price", "profit_loss_rate", "current_price", "provider",
]


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: object) -> str:
    return str(value or "").strip()


def sql_text(value: object) -> str:
    return "'" + clean_text(value).replace("'", "''") + "'"


def sql_number(value: object, integer: bool = False) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if integer:
        return str(int(number))
    return format(number, ".15g")


def ilp_tag(value: object) -> str:
    return clean_text(value).replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def ilp_string(value: object) -> str:
    return '"' + clean_text(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def numeric_value(value: object) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def epoch_value(value: object, multiplier: int) -> int:
    parsed = parse_timestamp(value)
    if not parsed:
        raise ValueError("QuestDB observation timestamp is invalid: " + clean_text(value))
    return int(round(parsed.timestamp() * multiplier))


def source_value(row: Mapping[str, object], snake: str, camel: str = ""):
    if snake in row:
        return row.get(snake)
    return row.get(camel) if camel else None


class QuestDBTimeSeriesAdapter:
    _schema_lock = threading.Lock()
    _schema_ready = set()

    def __init__(self, settings: Mapping[str, object] = None, backend_id: str = "questdb-shadow"):
        self.settings = dict(settings or {})
        self.backend_id = str(backend_id or "questdb-shadow")
        self.base_url = clean_text(self.settings.get("questDbHttpUrl") or "http://127.0.0.1:9000").rstrip("/")
        try:
            self.timeout_seconds = max(1, min(120, int(float(self.settings.get("questDbTimeoutSeconds") or 10))))
        except (TypeError, ValueError):
            self.timeout_seconds = 10
        self.username = clean_text(self.settings.get("questDbUsername"))
        self.password = clean_text(self.settings.get("questDbPassword"))

    def descriptor(self) -> TimeSeriesBackendDescriptor:
        return TimeSeriesBackendDescriptor(
            backend_id=self.backend_id,
            adapter_name="questdb",
            adapter_version=QUESTDB_ADAPTER_VERSION,
            status="shadow",
            contract_version=TIME_SERIES_CONTRACT_VERSION,
            capabilities=TimeSeriesCapabilities(
                out_of_order_write=True,
                idempotent_upsert=True,
                time_partitioning=True,
                automatic_retention=True,
                incremental_aggregation=True,
                as_of_join=True,
                window_functions=True,
                batch_ingestion=True,
                point_in_time_read=True,
            ),
            settings={"httpUrl": self.base_url},
        )

    def request(self, path: str, values: Mapping[str, object] = None) -> Dict[str, object]:
        query = urllib.parse.urlencode({str(key): str(value) for key, value in dict(values or {}).items()})
        url = self.base_url + path + (("?" + query) if query else "")
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        if self.username:
            token = base64.b64encode((self.username + ":" + self.password).encode("utf-8")).decode("ascii")
            request.add_header("Authorization", "Basic " + token)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
            raise RuntimeError("QuestDB request failed: " + str(error)) from error
        if not body.strip():
            return {}
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise RuntimeError("QuestDB returned invalid JSON") from error
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError("QuestDB query failed: " + str(payload.get("error")))
        return dict(payload or {})

    def execute(self, sql: str) -> Dict[str, object]:
        return self.request("/exec", {"query": sql})

    def write_lines(self, lines: Iterable[str]) -> None:
        payload = "\n".join(str(line) for line in lines if str(line).strip())
        if not payload:
            return
        request = urllib.request.Request(
            self.base_url + "/write?precision=n",
            data=(payload + "\n").encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8", "Accept": "application/json"},
            method="POST",
        )
        if self.username:
            token = base64.b64encode((self.username + ":" + self.password).encode("utf-8")).decode("ascii")
            request.add_header("Authorization", "Basic " + token)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response.read()
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
            raise RuntimeError("QuestDB ILP write failed: " + str(error)) from error

    @staticmethod
    def ilp_tags(raw: Mapping[str, object], columns: Iterable[tuple]) -> str:
        tags = []
        for snake, camel in columns:
            value = clean_text(source_value(raw, snake, camel))
            if value:
                tags.append(snake + "=" + ilp_tag(value))
        return ("," + ",".join(tags)) if tags else ""

    def market_line(self, raw: Mapping[str, object]) -> str:
        event_at = source_value(raw, "bucket_at", "bucketAt") or source_value(raw, "observed_at", "observedAt")
        observed_at = source_value(raw, "observed_at", "observedAt") or event_at
        source_as_of = source_value(raw, "source_as_of", "sourceAsOf") or observed_at
        received_at = utc_iso()
        granularity = clean_text(source_value(raw, "granularity", "observationGranularity"))
        table_name = GRANULARITY_TABLES.get(granularity)
        if not table_name:
            return ""
        tags = self.ilp_tags(raw, [
            ("account_id", "accountId"), ("symbol", "symbol"), ("granularity", "observationGranularity"),
            ("provider", "provider"), ("source_role", "source"), ("market", "market"),
            ("currency", "currency"), ("data_quality", "dataQuality"),
        ])
        fields = [
            "observed_at=" + str(epoch_value(observed_at, 1_000_000)) + "t",
            "source_as_of=" + str(epoch_value(source_as_of, 1_000_000)) + "t",
            "received_at=" + str(epoch_value(received_at, 1_000_000)) + "t",
            "name=" + ilp_string(source_value(raw, "name", "name")),
            "sample_count=" + str(int(numeric_value(source_value(raw, "sample_count", "sampleCountInBucket")))) + "i",
        ]
        for snake, camel in [
            ("open_price", "openPrice"), ("high_price", "highPrice"), ("low_price", "lowPrice"),
            ("current_price", "currentPrice"), ("change_rate", "changeRate"), ("volume", "volume"),
            ("trading_value", "tradingValue"), ("quantity", "quantity"), ("average_price", "averagePrice"),
            ("profit_loss_rate", "profitLossRate"), ("volume_ratio", "volumeRatio"),
            ("trade_strength", "tradeStrength"), ("bid_ask_imbalance", "bidAskImbalance"),
            ("foreign_net_volume", "foreignNetVolume"), ("institution_net_volume", "institutionNetVolume"),
            ("individual_net_volume", "individualNetVolume"), ("ma5", "ma5"), ("ma20", "ma20"),
            ("ma60", "ma60"), ("ma20_slope", "ma20Slope"), ("ma60_slope", "ma60Slope"),
            ("ma20_distance", "ma20Distance"), ("ma60_distance", "ma60Distance"),
        ]:
            fields.append(snake + "=" + format(numeric_value(source_value(raw, snake, camel)), ".17g"))
        coverage = source_value(raw, "investor_coverage_json", "investorCoverage") or "{}"
        if isinstance(coverage, (dict, list)):
            coverage = json.dumps(coverage, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        fields.append("investor_coverage_json=" + ilp_string(coverage))
        return table_name + tags + " " + ",".join(fields) + " " + str(epoch_value(event_at, 1_000_000_000))

    def portfolio_mark_line(self, raw: Mapping[str, object]) -> str:
        event_at = source_value(raw, "bucket_at", "bucketAt") or source_value(raw, "observed_at", "observedAt")
        observed_at = source_value(raw, "observed_at", "observedAt") or event_at
        tags = self.ilp_tags(raw, [
            ("account_id", "accountId"), ("symbol", "symbol"), ("provider", "provider"),
        ])
        fields = ["observed_at=" + str(epoch_value(observed_at, 1_000_000)) + "t"]
        for snake, camel in [
            ("quantity", "quantity"), ("average_price", "averagePrice"),
            ("profit_loss_rate", "profitLossRate"), ("current_price", "currentPrice"),
        ]:
            fields.append(snake + "=" + format(numeric_value(source_value(raw, snake, camel)), ".17g"))
        return "portfolio_marks" + tags + " " + ",".join(fields) + " " + str(epoch_value(event_at, 1_000_000_000))

    def query_rows(self, sql: str) -> List[Dict[str, object]]:
        payload = self.execute(sql)
        columns = [str(item.get("name") or "") for item in payload.get("columns") or []]
        return [dict(zip(columns, values)) for values in payload.get("dataset") or []]

    def ensure_schema(self) -> None:
        cache_key = (self.base_url, self.backend_id)
        if cache_key in QuestDBTimeSeriesAdapter._schema_ready:
            return
        with QuestDBTimeSeriesAdapter._schema_lock:
            if cache_key in QuestDBTimeSeriesAdapter._schema_ready:
                return
            retention_days = {
                "3m": max(1, int(float(self.settings.get("marketTimeSeriesRawRetentionDays") or 2))),
                "15m": max(1, int(float(self.settings.get("marketTimeSeries15mRetentionDays") or 10))),
                "1h": max(1, int(float(self.settings.get("marketTimeSeries1hRetentionDays") or 90))),
                "1d": max(1, int(float(self.settings.get("marketTimeSeriesDailyRetentionDays") or 180))),
            }
            market_schema = """
                CREATE TABLE IF NOT EXISTS {table_name} (
                    event_at TIMESTAMP,
                    observed_at TIMESTAMP,
                    source_as_of TIMESTAMP,
                    received_at TIMESTAMP,
                    account_id SYMBOL,
                    symbol SYMBOL,
                    granularity SYMBOL,
                    provider SYMBOL,
                    source_role SYMBOL,
                    name VARCHAR,
                    market SYMBOL,
                    currency SYMBOL,
                    sample_count LONG,
                    open_price DOUBLE,
                    high_price DOUBLE,
                    low_price DOUBLE,
                    current_price DOUBLE,
                    change_rate DOUBLE,
                    volume DOUBLE,
                    trading_value DOUBLE,
                    quantity DOUBLE,
                    average_price DOUBLE,
                    profit_loss_rate DOUBLE,
                    volume_ratio DOUBLE,
                    trade_strength DOUBLE,
                    bid_ask_imbalance DOUBLE,
                    foreign_net_volume DOUBLE,
                    institution_net_volume DOUBLE,
                    individual_net_volume DOUBLE,
                    investor_coverage_json VARCHAR,
                    ma5 DOUBLE,
                    ma20 DOUBLE,
                    ma60 DOUBLE,
                    ma20_slope DOUBLE,
                    ma60_slope DOUBLE,
                    ma20_distance DOUBLE,
                    ma60_distance DOUBLE,
                    data_quality SYMBOL
                ) TIMESTAMP(event_at) PARTITION BY DAY WAL
                DEDUP UPSERT KEYS(event_at, account_id, symbol, granularity, provider, source_role)
                """
            for granularity, table_name in GRANULARITY_TABLES.items():
                self.execute(market_schema.format(table_name=table_name))
                self.execute("ALTER TABLE " + table_name + " SET TTL " + str(retention_days[granularity]) + " DAYS")
            self.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_marks (
                    event_at TIMESTAMP,
                    observed_at TIMESTAMP,
                    account_id SYMBOL,
                    symbol SYMBOL,
                    quantity DOUBLE,
                    average_price DOUBLE,
                    profit_loss_rate DOUBLE,
                    current_price DOUBLE,
                    provider SYMBOL
                ) TIMESTAMP(event_at) PARTITION BY DAY WAL
                DEDUP UPSERT KEYS(event_at, account_id, symbol, provider)
                """
            )
            self.execute("ALTER TABLE portfolio_marks SET TTL " + str(retention_days["3m"]) + " DAYS")
            QuestDBTimeSeriesAdapter._schema_ready.add(cache_key)

    def market_values(self, raw: Mapping[str, object]) -> List[str]:
        event_at = source_value(raw, "bucket_at", "bucketAt") or source_value(raw, "observed_at", "observedAt")
        observed_at = source_value(raw, "observed_at", "observedAt") or event_at
        source_as_of = source_value(raw, "source_as_of", "sourceAsOf") or observed_at
        values = [sql_text(event_at), sql_text(observed_at), sql_text(source_as_of), sql_text(utc_iso())]
        values.append(sql_text(source_value(raw, "account_id", "accountId") or "__market_data__"))
        values.extend(sql_text(source_value(raw, key, camel)) for key, camel in [
            ("symbol", "symbol"), ("granularity", "observationGranularity"),
            ("provider", "provider"), ("source_role", "source"), ("name", "name"),
            ("market", "market"), ("currency", "currency"),
        ])
        values.append(sql_number(source_value(raw, "sample_count", "sampleCountInBucket"), integer=True))
        for key, camel in [
            ("open_price", "openPrice"), ("high_price", "highPrice"), ("low_price", "lowPrice"),
            ("current_price", "currentPrice"), ("change_rate", "changeRate"), ("volume", "volume"),
            ("trading_value", "tradingValue"), ("quantity", "quantity"), ("average_price", "averagePrice"),
            ("profit_loss_rate", "profitLossRate"),
            ("volume_ratio", "volumeRatio"),
            ("trade_strength", "tradeStrength"), ("bid_ask_imbalance", "bidAskImbalance"),
            ("foreign_net_volume", "foreignNetVolume"),
            ("institution_net_volume", "institutionNetVolume"),
            ("individual_net_volume", "individualNetVolume"),
        ]:
            values.append(sql_number(source_value(raw, key, camel)))
        coverage = source_value(raw, "investor_coverage_json", "investorCoverage") or "{}"
        if isinstance(coverage, (dict, list)):
            coverage = json.dumps(coverage, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        values.append(sql_text(coverage))
        for key, camel in [
            ("ma5", "ma5"), ("ma20", "ma20"), ("ma60", "ma60"),
            ("ma20_slope", "ma20Slope"), ("ma60_slope", "ma60Slope"),
            ("ma20_distance", "ma20Distance"), ("ma60_distance", "ma60Distance"),
        ]:
            values.append(sql_number(source_value(raw, key, camel)))
        values.append(sql_text(source_value(raw, "data_quality", "dataQuality")))
        return values

    def portfolio_mark_values(self, raw: Mapping[str, object]) -> List[str]:
        event_at = source_value(raw, "bucket_at", "bucketAt") or source_value(raw, "observed_at", "observedAt")
        observed_at = source_value(raw, "observed_at", "observedAt") or event_at
        return [
            sql_text(event_at),
            sql_text(observed_at),
            sql_text(source_value(raw, "account_id", "accountId")),
            sql_text(source_value(raw, "symbol", "symbol")),
            sql_number(source_value(raw, "quantity", "quantity")),
            sql_number(source_value(raw, "average_price", "averagePrice")),
            sql_number(source_value(raw, "profit_loss_rate", "profitLossRate")),
            sql_number(source_value(raw, "current_price", "currentPrice")),
            sql_text(source_value(raw, "provider", "provider")),
        ]

    def write_observations(self, observations: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        rows = [dict(item or {}) for item in observations or []]
        if not rows:
            return {"backendId": self.backend_id, "writtenCount": 0, "portfolioMarkCount": 0}
        self.ensure_schema()
        # ILP is QuestDB's ingestion path. SQL /exec remains read/schema only.
        batch_size = max(1, min(1000, int(float(self.settings.get("questDbWriteBatchSize") or 200))))
        written = 0
        marks = 0
        for offset in range(0, len(rows), batch_size):
            batch = rows[offset:offset + batch_size]
            market_lines = [self.market_line(row) for row in batch]
            market_lines = [line for line in market_lines if line]
            self.write_lines(market_lines)
            written += len(market_lines)
            mark_rows = [row for row in batch if clean_text(source_value(row, "account_id", "accountId")) not in {"", "__market_data__"}]
            if mark_rows:
                self.write_lines([self.portfolio_mark_line(row) for row in mark_rows])
                marks += len(mark_rows)
        return {
            "backendId": self.backend_id,
            "writtenCount": written,
            "portfolioMarkCount": marks,
            "status": "completed",
        }

    def rows_for(self, account_id: str, symbol: str, granularity: str, as_of: str, limit: int) -> List[Dict[str, object]]:
        table_name = GRANULARITY_TABLES.get(str(granularity or ""))
        if not table_name:
            return []
        selected_columns = (
            "event_at AS bucket_at, observed_at, source_as_of, account_id, symbol, granularity, provider, "
            "source_role, name, market, currency, sample_count, open_price, high_price, low_price, "
            "current_price, change_rate, volume, trading_value, quantity, average_price, profit_loss_rate, "
            "volume_ratio, trade_strength, bid_ask_imbalance, foreign_net_volume, institution_net_volume, "
            "individual_net_volume, investor_coverage_json, ma5, ma20, ma60, ma20_slope, ma60_slope, "
            "ma20_distance, ma60_distance, data_quality"
        )
        rows = []
        for requested_account in dict.fromkeys([clean_text(account_id), "__market_data__"]):
            clauses = [
                "account_id = " + sql_text(requested_account),
                "symbol = " + sql_text(symbol),
                "granularity = " + sql_text(granularity),
            ]
            if as_of:
                clauses.append("observed_at <= " + sql_text(as_of))
            rows.extend(self.query_rows(
                "SELECT " + selected_columns + " FROM " + table_name + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY event_at DESC LIMIT " + str(max(1, min(5000, int(limit or 500))))
            ))
        payloads = []
        for row in rows:
            payload = temporal_observation_payload(row, "questdb-market-time-series")
            payload["_accountId"] = clean_text(row.get("account_id"))
            payloads.append(payload)
        return payloads

    def load_temporal_windows(
        self,
        account_id: str,
        symbols: Iterable[str],
        definitions: Iterable[object],
        as_of: str = "",
    ) -> Dict[str, Dict[str, List[Dict[str, object]]]]:
        self.ensure_schema()
        clean_symbols = sorted({clean_text(symbol).upper() for symbol in symbols or [] if clean_text(symbol)})
        definition_rows = list(definitions or [])
        preference_by_window = {
            clean_text(getattr(definition, "key", "")).upper(): (
                snapshot_safe_granularity_preferences(definition)
                if as_of
                else list(granularity_preferences(getattr(definition, "key", "")))
            )
            for definition in definition_rows
        }
        maximum = max(20, min(2000, int(float(self.settings.get("marketTimeSeriesMaxPointsPerWindow") or 500))))
        result: Dict[str, Dict[str, List[Dict[str, object]]]] = {}
        for symbol in clean_symbols:
            windows: Dict[str, List[Dict[str, object]]] = {}
            row_cache: Dict[str, List[Dict[str, object]]] = {}
            for definition in definition_rows:
                key = clean_text(getattr(definition, "key", "")).upper()
                required = int(getattr(definition, "required_sessions", 0) or required_session_count(getattr(definition, "lookback_days", 1)))
                selected: List[Dict[str, object]] = []
                best: List[Dict[str, object]] = []
                for granularity in preference_by_window.get(key) or []:
                    candidate = row_cache.setdefault(
                        granularity,
                        self.rows_for(account_id, symbol, granularity, as_of, maximum),
                    )
                    account_rows = [row for row in candidate if row.get("_accountId") == clean_text(account_id)]
                    global_rows = [row for row in candidate if row.get("_accountId") == "__market_data__"]
                    if as_of:
                        if granularity == "3m":
                            candidate = account_rows
                        else:
                            candidate = completed_daily_rows(global_rows, as_of)
                    else:
                        candidate = account_rows if temporal_session_count(account_rows) >= temporal_session_count(global_rows) and account_rows else (global_rows or account_rows)
                    candidate = limit_temporal_rows(candidate, granularity, required, maximum)
                    if temporal_session_count(candidate) > temporal_session_count(best) or (
                        temporal_session_count(candidate) == temporal_session_count(best) and len(candidate) > len(best)
                    ):
                        best = candidate
                    if temporal_session_count(candidate) >= required:
                        selected = candidate
                        break
                # Window selection reuses the backend row cache. Strip the
                # internal account marker from copies so one short window
                # cannot change the candidates available to later windows.
                ordered = [dict(row) for row in reversed(selected or best)]
                for row in ordered:
                    row.pop("_accountId", None)
                if bool(getattr(definition, "is_intraday", False)):
                    ordered = window_rows(ordered, definition, parse_timestamp(as_of))
                else:
                    ordered = trim_to_recent_sessions(ordered, required)
                windows[key] = ordered
            result[symbol] = windows
        return result

    def watermark(self) -> TimeSeriesWatermark:
        try:
            self.ensure_schema()
            union_sql = " UNION ALL ".join(
                "SELECT max(observed_at) AS observed_through FROM " + table_name
                for table_name in GRANULARITY_TABLES.values()
            )
            rows = self.query_rows("SELECT max(observed_through) AS observed_through FROM (" + union_sql + ")")
            observed_through = clean_text((rows[0] if rows else {}).get("observed_through"))
            return TimeSeriesWatermark(self.backend_id, observed_through, status="ready")
        except Exception as error:  # noqa: BLE001 - health reports the unavailable candidate without affecting active reads.
            return TimeSeriesWatermark(self.backend_id, "", status="unavailable:" + str(error)[:120])

    def health(self) -> Dict[str, object]:
        started = datetime.now(timezone.utc)
        try:
            rows = self.query_rows("SELECT 1 AS ready")
            ready = bool(rows and int(rows[0].get("ready") or 0) == 1)
            status = "ready" if ready else "unhealthy"
            error = ""
        except Exception as caught:  # noqa: BLE001 - candidate health is data, not a caller failure.
            status = "unavailable"
            error = str(caught)[:240]
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {
            "backendId": self.backend_id,
            "adapter": "questdb",
            "status": status,
            "latencyMs": duration_ms,
            "error": error,
            "checkedAt": utc_iso(),
        }

    def summary(self, account_id: str = "") -> Dict[str, object]:
        del account_id
        try:
            self.ensure_schema()
            rows = []
            for granularity, table_name in GRANULARITY_TABLES.items():
                result = self.query_rows(
                    "SELECT count() AS count, count_distinct(symbol) AS symbol_count, "
                    "min(event_at) AS earliest_at, max(observed_at) AS latest_at FROM " + table_name
                )
                row = dict((result or [{}])[0] or {})
                row["granularity"] = granularity
                rows.append(row)
        except Exception as error:  # noqa: BLE001 - shadow summary must not break the active service.
            return {"backendId": self.backend_id, "enabled": True, "status": "unavailable", "error": str(error)[:240]}
        return {
            "backendId": self.backend_id,
            "enabled": True,
            "status": "ready",
            "granularities": [
                {
                    "granularity": clean_text(row.get("granularity")),
                    "count": int(row.get("count") or 0),
                    "symbolCount": int(row.get("symbol_count") or 0),
                    "earliestAt": clean_text(row.get("earliest_at")),
                    "latestAt": clean_text(row.get("latest_at")),
                }
                for row in rows
            ],
        }
