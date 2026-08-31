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

from ..domain.capital_flow import (
    boolean_value,
    merge_capital_flow_rows,
    observation_from_row,
    observed_fields_from_coverage,
)
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


QUESTDB_ADAPTER_VERSION = "questdb-time-series-adapter-v2"

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

CAPITAL_FLOW_TABLE = "capital_flow_observations"
CAPITAL_FLOW_NUMERIC_FIELDS = [
    ("current_price", "currentPrice"), ("market_volume", "marketVolume"),
    ("trading_value", "tradingValue"),
    ("foreign_net_volume", "foreignNetVolume"), ("foreign_net_amount", "foreignNetAmount"),
    ("foreign_buy_volume", "foreignBuyVolume"), ("foreign_sell_volume", "foreignSellVolume"),
    ("institution_net_volume", "institutionNetVolume"), ("institution_net_amount", "institutionNetAmount"),
    ("institution_buy_volume", "institutionBuyVolume"), ("institution_sell_volume", "institutionSellVolume"),
    ("individual_net_volume", "individualNetVolume"), ("individual_net_amount", "individualNetAmount"),
    ("individual_buy_volume", "individualBuyVolume"), ("individual_sell_volume", "individualSellVolume"),
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


def ttl_days_value(value: object, unit: object) -> int:
    """Normalize QuestDB's canonical TTL units before drift comparison."""

    amount = int(numeric_value(value))
    normalized_unit = clean_text(unit).upper().rstrip("S")
    multiplier = {
        "DAY": 1,
        "WEEK": 7,
    }.get(normalized_unit)
    return amount * multiplier if multiplier is not None else -1


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
        except urllib.error.HTTPError as error:
            try:
                response_detail = error.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001 - retain the original HTTP failure.
                response_detail = ""
            detail = (" response=" + response_detail) if response_detail else ""
            raise RuntimeError("QuestDB request failed: " + str(error) + detail) from error
        except (OSError, urllib.error.URLError) as error:
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
            ("ma5", "ma5"), ("ma20", "ma20"),
            ("ma60", "ma60"), ("ma20_slope", "ma20Slope"), ("ma60_slope", "ma60Slope"),
            ("ma20_distance", "ma20Distance"), ("ma60_distance", "ma60Distance"),
        ]:
            fields.append(snake + "=" + format(numeric_value(source_value(raw, snake, camel)), ".17g"))
        coverage = source_value(raw, "investor_coverage_json", "investorCoverage") or "{}"
        if isinstance(coverage, (dict, list)):
            coverage = json.dumps(coverage, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        fields.append("investor_coverage_json=" + ilp_string(coverage))
        observed_fields = set(observed_fields_from_coverage(coverage))
        for snake, camel in [
            ("foreign_net_volume", "foreignNetVolume"),
            ("institution_net_volume", "institutionNetVolume"),
            ("individual_net_volume", "individualNetVolume"),
        ]:
            value = source_value(raw, snake, camel)
            if camel in observed_fields and value not in (None, ""):
                fields.append(snake + "=" + format(numeric_value(value), ".17g"))
        for snake, camel in [("trade_strength", "tradeStrength"), ("bid_ask_imbalance", "bidAskImbalance")]:
            value = source_value(raw, snake, camel)
            if value not in (None, "", 0, 0.0):
                fields.append(snake + "=" + format(numeric_value(value), ".17g"))
        return table_name + tags + " " + ",".join(fields) + " " + str(epoch_value(event_at, 1_000_000_000))

    def capital_flow_line(self, raw: Mapping[str, object]) -> str:
        source_as_of = source_value(raw, "source_as_of", "sourceAsOf") or source_value(raw, "observed_at", "observedAt")
        observed_at = source_value(raw, "observed_at", "observedAt") or source_as_of
        received_at = source_value(raw, "received_at", "receivedAt") or observed_at
        event_at = source_as_of or observed_at
        tags = self.ilp_tags(raw, [
            ("subject_kind", "subjectKind"), ("subject_id", "subjectId"),
            ("market", "market"), ("currency", "currency"), ("sector", "sector"),
            ("provider", "provider"), ("measurement_type", "measurementType"),
            ("status", "status"), ("freshness_status", "freshnessStatus"),
            ("data_quality", "dataQuality"),
        ])
        observed_fields = source_value(raw, "observed_fields_json", "observedFields") or []
        if isinstance(observed_fields, (dict, list, tuple)):
            observed_fields = json.dumps(observed_fields, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        coverage = source_value(raw, "coverage_json", "coverage") or {}
        if isinstance(coverage, (dict, list, tuple)):
            coverage = json.dumps(coverage, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        fields = [
            "observed_at=" + str(epoch_value(observed_at, 1_000_000)) + "t",
            "source_as_of=" + str(epoch_value(source_as_of, 1_000_000)) + "t",
            "received_at=" + str(epoch_value(received_at, 1_000_000)) + "t",
            "observation_id=" + ilp_string(source_value(raw, "observation_id", "observationId")),
            "trading_date=" + ilp_string(source_value(raw, "trading_date", "tradingDate")),
            "judgement_eligible=" + ("true" if boolean_value(source_value(raw, "judgement_eligible", "judgementEligible")) else "false"),
            "observed_fields_json=" + ilp_string(observed_fields or "[]"),
            "coverage_json=" + ilp_string(coverage or "{}"),
            "contract_version=" + ilp_string(source_value(raw, "contract_version", "contractVersion") or "capital-flow-observation-v1"),
        ]
        for snake, camel in CAPITAL_FLOW_NUMERIC_FIELDS:
            value = source_value(raw, snake, camel)
            if value not in (None, ""):
                fields.append(snake + "=" + format(numeric_value(value), ".17g"))
        return CAPITAL_FLOW_TABLE + tags + " " + ",".join(fields) + " " + str(epoch_value(event_at, 1_000_000_000))

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

    def wal_table_statuses(self, table_names: Iterable[str]) -> Dict[str, Dict[str, object]]:
        requested = sorted({clean_text(table_name) for table_name in table_names or [] if clean_text(table_name)})
        if not requested:
            return {}
        rows = self.query_rows(
            "SELECT name, suspended, writerTxn AS writer_txn, sequencerTxn AS sequencer_txn, "
            "errorTag AS error_tag, errorMessage AS error_message FROM wal_tables() WHERE name IN ("
            + ", ".join(sql_text(table_name) for table_name in requested)
            + ")"
        )
        statuses = {
            clean_text(row.get("name")): {
                "suspended": str(row.get("suspended") or "").strip().lower() in {"true", "1"},
                "writerTxn": int(numeric_value(row.get("writer_txn"))),
                "sequencerTxn": int(numeric_value(row.get("sequencer_txn"))),
                "errorTag": clean_text(row.get("error_tag")),
                "error": clean_text(row.get("error_message"))[:240],
            }
            for row in rows
            if clean_text(row.get("name"))
        }
        missing = sorted(set(requested) - set(statuses))
        if missing:
            raise RuntimeError("QuestDB WAL status is missing tables: " + ", ".join(missing))
        return statuses

    @staticmethod
    def assert_wal_tables_writable(statuses: Mapping[str, Mapping[str, object]]) -> None:
        suspended = [
            table_name + ((": " + clean_text(status.get("error"))) if clean_text(status.get("error")) else "")
            for table_name, status in statuses.items()
            if bool(status.get("suspended"))
        ]
        if suspended:
            raise RuntimeError("QuestDB WAL is suspended: " + "; ".join(suspended))

    def wait_for_wal_application(self, table_names: Iterable[str]) -> None:
        """Do not acknowledge an ILP write until its WAL transactions are query-visible."""

        statuses = self.wal_table_statuses(table_names)
        self.assert_wal_tables_writable(statuses)
        targets = {
            table_name: int(status.get("sequencerTxn") or 0)
            for table_name, status in statuses.items()
        }
        for table_name, target_txn in targets.items():
            rows = self.query_rows(
                "SELECT wait_wal_table(" + sql_text(table_name) + ", " + str(target_txn) + ") AS applied"
            )
            applied = str((rows[0] if rows else {}).get("applied") or "").strip().lower()
            if applied not in {"true", "1"}:
                raise RuntimeError("QuestDB WAL application was not confirmed: " + table_name)
        final_statuses = self.wal_table_statuses(targets)
        self.assert_wal_tables_writable(final_statuses)
        lagging = [
            table_name
            for table_name, target_txn in targets.items()
            if int(final_statuses[table_name].get("writerTxn") or 0) < target_txn
        ]
        if lagging:
            raise RuntimeError("QuestDB WAL application is lagging: " + ", ".join(lagging))

    def expected_ttl_days(self) -> Dict[str, int]:
        retention_days = {
            "3m": max(1, int(float(self.settings.get("marketTimeSeriesRawRetentionDays") or 7))),
            "15m": max(1, int(float(self.settings.get("marketTimeSeries15mRetentionDays") or 30))),
            "1h": max(1, int(float(self.settings.get("marketTimeSeries1hRetentionDays") or 365))),
            "1d": max(1, int(float(self.settings.get("marketTimeSeriesDailyRetentionDays") or 1825))),
        }
        return {
            **{
                table_name: retention_days[granularity]
                for granularity, table_name in GRANULARITY_TABLES.items()
            },
            "portfolio_marks": retention_days["3m"],
            CAPITAL_FLOW_TABLE: retention_days["1d"],
        }

    def schema_metadata(self) -> Dict[str, Dict[str, object]]:
        table_names = [*GRANULARITY_TABLES.values(), "portfolio_marks", CAPITAL_FLOW_TABLE]
        rows = self.query_rows(
            "SELECT table_name, ttlValue AS ttl_value, ttlUnit AS ttl_unit FROM tables() "
            "WHERE table_name IN ("
            + ", ".join(sql_text(table_name) for table_name in table_names)
            + ")"
        )
        return {
            clean_text(row.get("table_name")): dict(row)
            for row in rows
            if clean_text(row.get("table_name"))
        }

    def ensure_schema(self) -> None:
        cache_key = (self.base_url, self.backend_id)
        if cache_key in QuestDBTimeSeriesAdapter._schema_ready:
            return
        with QuestDBTimeSeriesAdapter._schema_lock:
            if cache_key in QuestDBTimeSeriesAdapter._schema_ready:
                return
            expected_ttl_days = self.expected_ttl_days()
            metadata = self.schema_metadata()
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
            for table_name in GRANULARITY_TABLES.values():
                if table_name not in metadata:
                    self.execute(market_schema.format(table_name=table_name))
            if "portfolio_marks" not in metadata:
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
            if CAPITAL_FLOW_TABLE not in metadata:
                self.execute(
                    """
                CREATE TABLE IF NOT EXISTS capital_flow_observations (
                    event_at TIMESTAMP,
                    observed_at TIMESTAMP,
                    source_as_of TIMESTAMP,
                    received_at TIMESTAMP,
                    observation_id VARCHAR,
                    subject_kind SYMBOL,
                    subject_id SYMBOL,
                    market SYMBOL,
                    currency SYMBOL,
                    sector SYMBOL,
                    trading_date VARCHAR,
                    provider SYMBOL,
                    measurement_type SYMBOL,
                    status SYMBOL,
                    freshness_status SYMBOL,
                    judgement_eligible BOOLEAN,
                    observed_fields_json VARCHAR,
                    coverage_json VARCHAR,
                    current_price DOUBLE,
                    market_volume DOUBLE,
                    trading_value DOUBLE,
                    foreign_net_volume DOUBLE,
                    foreign_net_amount DOUBLE,
                    foreign_buy_volume DOUBLE,
                    foreign_sell_volume DOUBLE,
                    institution_net_volume DOUBLE,
                    institution_net_amount DOUBLE,
                    institution_buy_volume DOUBLE,
                    institution_sell_volume DOUBLE,
                    individual_net_volume DOUBLE,
                    individual_net_amount DOUBLE,
                    individual_buy_volume DOUBLE,
                    individual_sell_volume DOUBLE,
                    data_quality SYMBOL,
                    contract_version VARCHAR
                ) TIMESTAMP(event_at) PARTITION BY DAY WAL
                DEDUP UPSERT KEYS(event_at, subject_id, provider, measurement_type)
                """
                )
            if set(metadata) != set(expected_ttl_days):
                metadata = self.schema_metadata()
            current_ttl_days = {
                table_name: ttl_days_value(row.get("ttl_value"), row.get("ttl_unit"))
                for table_name, row in metadata.items()
            }
            for table_name, expected_days in expected_ttl_days.items():
                if current_ttl_days.get(table_name) == expected_days:
                    continue
                self.execute("ALTER TABLE " + table_name + " SET TTL " + str(expected_days) + " DAYS")
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
        touched_tables = {
            GRANULARITY_TABLES[clean_text(source_value(row, "granularity", "observationGranularity"))]
            for row in rows
            if clean_text(source_value(row, "granularity", "observationGranularity")) in GRANULARITY_TABLES
        }
        if any(
            clean_text(source_value(row, "account_id", "accountId")) not in {"", "__market_data__"}
            for row in rows
        ):
            touched_tables.add("portfolio_marks")
        if touched_tables:
            self.assert_wal_tables_writable(self.wal_table_statuses(touched_tables))
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
        if touched_tables:
            self.wait_for_wal_application(touched_tables)
        return {
            "backendId": self.backend_id,
            "writtenCount": written,
            "portfolioMarkCount": marks,
            "status": "completed",
        }

    def write_capital_flow_observations(self, observations: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        rows = [dict(item or {}) for item in observations or []]
        if not rows:
            return {"backendId": self.backend_id, "writtenCount": 0, "status": "completed"}
        self.ensure_schema()
        self.assert_wal_tables_writable(self.wal_table_statuses([CAPITAL_FLOW_TABLE]))
        lines = [self.capital_flow_line(row) for row in rows]
        self.write_lines(lines)
        self.wait_for_wal_application([CAPITAL_FLOW_TABLE])
        return {"backendId": self.backend_id, "writtenCount": len(lines), "status": "completed"}

    def load_capital_flow_observations(
        self,
        symbols: Iterable[str] = (),
        market: str = "",
        observed_after: str = "",
        as_of: str = "",
        limit: int = 5000,
    ) -> List[Dict[str, object]]:
        self.ensure_schema()
        clauses = ["judgement_eligible = true", "status = 'available'"]
        clean_symbols = sorted({clean_text(symbol).upper() for symbol in symbols or [] if clean_text(symbol)})
        if clean_symbols:
            clauses.append("subject_id IN (" + ",".join(sql_text(symbol) for symbol in clean_symbols) + ")")
        if clean_text(market):
            clauses.append("market = " + sql_text(clean_text(market).upper()))
        if clean_text(observed_after):
            clauses.append("observed_at >= " + sql_text(observed_after))
        if clean_text(as_of):
            clauses.append("observed_at <= " + sql_text(as_of))
        selected = (
            "observation_id, subject_kind, subject_id, market, currency, sector, trading_date, "
            "observed_at, source_as_of, received_at, provider, measurement_type, status, freshness_status, "
            "judgement_eligible, observed_fields_json, coverage_json, current_price, market_volume, trading_value, "
            "foreign_net_volume, foreign_net_amount, foreign_buy_volume, foreign_sell_volume, "
            "institution_net_volume, institution_net_amount, institution_buy_volume, institution_sell_volume, "
            "individual_net_volume, individual_net_amount, individual_buy_volume, individual_sell_volume, "
            "data_quality, contract_version"
        )
        rows = self.query_rows(
            "SELECT " + selected + " FROM " + CAPITAL_FLOW_TABLE + " WHERE " + " AND ".join(clauses)
            + " ORDER BY subject_id, event_at LIMIT " + str(max(1, min(50000, int(limit or 5000))))
        )
        payloads = []
        for row in rows:
            observation = observation_from_row(row)
            if observation and observation.valid():
                payloads.append(observation.to_payload())
        return payloads

    def capital_flow_quality(self, legacy_days: int = 30) -> Dict[str, object]:
        self.ensure_schema()
        rows = self.query_rows(
            "SELECT count(*) total, count_distinct(subject_id) subjects, max(source_as_of) source_as_of, "
            "sum(case when measurement_type = 'daily-final' then 1 else 0 end) daily_final, "
            "sum(case when measurement_type = 'intraday-estimate' then 1 else 0 end) intraday_estimate "
            "FROM " + CAPITAL_FLOW_TABLE + " WHERE judgement_eligible = true AND status = 'available'"
        )
        row = rows[0] if rows else {}
        return {
            "status": "ready",
            "observationCount": int(numeric_value(row.get("total"))),
            "subjectCount": int(numeric_value(row.get("subjects"))),
            "dailyFinalCount": int(numeric_value(row.get("daily_final"))),
            "intradayEstimateCount": int(numeric_value(row.get("intraday_estimate"))),
            "sourceAsOf": clean_text(row.get("source_as_of")),
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
        capital_flow_rows = self.load_capital_flow_observations(
            symbols=clean_symbols,
            as_of=as_of,
            limit=max(100, len(clean_symbols) * 80),
        )
        capital_flow_by_symbol: Dict[str, List[Dict[str, object]]] = {}
        for row in capital_flow_rows:
            capital_flow_by_symbol.setdefault(clean_text(row.get("subjectId")).upper(), []).append(row)
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
                    ordered = merge_capital_flow_rows(ordered, capital_flow_by_symbol.get(symbol, []))
                windows[key] = ordered
            result[symbol] = windows
        return result

    def watermark(self) -> TimeSeriesWatermark:
        try:
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
        suspended_tables = []
        try:
            expected_tables = set(self.expected_ttl_days())
            missing_tables = sorted(expected_tables - set(self.schema_metadata()))
            if missing_tables:
                raise RuntimeError("QuestDB schema is missing tables: " + ", ".join(missing_tables))
            rows = self.query_rows("SELECT 1 AS ready")
            ready = bool(rows and int(rows[0].get("ready") or 0) == 1)
            wal_rows = self.query_rows(
                "SELECT name, suspended, writerTxn AS writer_txn, "
                "sequencerTxn AS sequencer_txn, errorTag AS error_tag, "
                "errorMessage AS error_message FROM wal_tables()"
            )
            suspended_tables = [
                {
                    "table": clean_text(row.get("name")),
                    "writerTxn": int(numeric_value(row.get("writer_txn"))),
                    "sequencerTxn": int(numeric_value(row.get("sequencer_txn"))),
                    "errorTag": clean_text(row.get("error_tag")),
                    "error": clean_text(row.get("error_message"))[:240],
                }
                for row in wal_rows
                if str(row.get("suspended") or "").strip().lower() in {"true", "1"}
            ]
            status = "degraded" if ready and suspended_tables else ("ready" if ready else "unhealthy")
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
            "suspendedTables": suspended_tables,
            "checkedAt": utc_iso(),
        }

    def summary(self, account_id: str = "") -> Dict[str, object]:
        del account_id
        rows = []
        errors = []
        for granularity, table_name in GRANULARITY_TABLES.items():
            try:
                result = self.query_rows(
                    "SELECT count() AS count, count_distinct(symbol) AS symbol_count, "
                    "min(event_at) AS earliest_at, max(observed_at) AS latest_at FROM " + table_name
                )
                row = dict((result or [{}])[0] or {})
                row["granularity"] = granularity
                row["status"] = "ready"
                rows.append(row)
            except Exception as error:  # noqa: BLE001 - one damaged WAL table must not hide healthy tables.
                detail = str(error)[:240]
                rows.append({"granularity": granularity, "status": "unavailable", "error": detail})
                errors.append({"granularity": granularity, "table": table_name, "error": detail})
        return {
            "backendId": self.backend_id,
            "enabled": True,
            "status": "degraded" if errors and len(errors) < len(rows) else ("unavailable" if errors else "ready"),
            "errors": errors,
            "granularities": [
                {
                    "granularity": clean_text(row.get("granularity")),
                    "status": clean_text(row.get("status")) or "ready",
                    "count": int(row.get("count") or 0),
                    "symbolCount": int(row.get("symbol_count") or 0),
                    "earliestAt": clean_text(row.get("earliest_at")),
                    "latestAt": clean_text(row.get("latest_at")),
                    "error": clean_text(row.get("error")),
                }
                for row in rows
            ],
        }
