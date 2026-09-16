import unittest
import socket
from copy import deepcopy
from datetime import datetime, timezone
from threading import Event, Thread
from unittest.mock import Mock, patch

from digital_twin.infrastructure.kis_market_signals import (
    KISMarketSignalProvider,
    merge_fresh_websocket_stages,
)
from digital_twin.infrastructure.kis_realtime_ws import (
    CCNL_COLUMNS, ORDERBOOK_COLUMNS, KIS_TR_CCN_PRICE, KIS_TR_ORDERBOOK,
    KISRealtimeWebSocketClient, MinimalWebSocket, merge_realtime_signal, parse_kis_realtime_text,
)


class MemoryQuotes:
    def __init__(self):
        self.rows = {}
        self.writes = []

    def load(self, provider, account, symbol):
        return deepcopy(self.rows.get((provider, account, symbol), {}))

    def save(self, provider, account, symbol, payload):
        self.rows[(provider, account, symbol)] = deepcopy(payload)
        self.writes.append(deepcopy(payload))


def tick_values(**overrides):
    values = {
        "MKSC_SHRN_ISCD": "000680", "STCK_CNTG_HOUR": "134338",
        "STCK_PRPR": "2975", "PRDY_CTRT": "-0.83",
        "STCK_HGPR": "3000", "STCK_LWPR": "2900",
        "ACML_VOL": "9062", "ACML_TR_PBMN": "26814638",
        "CTTR": "47.83", "SELN_CNTG_SMTN": "4000",
        "SHNU_CNTG_SMTN": "3000", "BSOP_DATE": "20260914",
    }
    values.update(overrides)
    return [values.get(name, "") for name in CCNL_COLUMNS]


def frame(values, count=1, tr_id=KIS_TR_CCN_PRICE):
    return "0|" + tr_id + "|" + str(count) + "|" + "^".join(values)


class KISRealtimeIntegrityTests(unittest.TestCase):
    def test_idle_receive_keeps_connection_and_accepts_next_tick(self):
        for timeout in (TimeoutError, socket.timeout):
            with self.subTest(timeout=timeout):
                ws = Mock()
                ws.recv_text.side_effect = [timeout("idle"), frame(tick_values())]
                client = self.client()
                client.websocket_factory = lambda *args: ws
                client.approval_key = "test-only"
                with patch("digital_twin.infrastructure.kis_realtime_ws.time.monotonic", side_effect=[0, 0, 0, 2, 2]), patch("digital_twin.infrastructure.kis_realtime_ws.time.sleep"):
                    result = client.collect(["000680"], 1)
                self.assertEqual("ok", result["status"])
                self.assertEqual(1, result["savedCount"])
                self.assertEqual(2, ws.recv_text.call_count)
                ws.connect.assert_called_once()
                ws.close.assert_called_once()

    def test_actual_disconnect_remains_a_transport_failure(self):
        ws = Mock()
        ws.recv_text.side_effect = ConnectionError("closed")
        client = self.client()
        client.websocket_factory = lambda *args: ws
        client.approval_key = "test-only"
        with patch("digital_twin.infrastructure.kis_realtime_ws.time.sleep"):
            result = client.collect(["000680"], 1)
        self.assertEqual("connection-error", result["status"])
        self.assertEqual("receive", result["errorStage"])
        self.assertTrue(result["reconnectRecommended"])
        ws.close.assert_called_once()

    def test_fragmented_socket_message_survives_timeout_and_control_frame(self):
        from websockets.sync.server import serve

        first_fragment = Event()
        release = Event()
        raw = frame(tick_values())

        def handle(connection):
            def fragments():
                yield raw[:23]
                first_fragment.set()
                release.wait(3)
                yield raw[23:]
            connection.ping(b"health")
            connection.send(fragments())

        with serve(handle, "127.0.0.1", 0) as server:
            thread = Thread(target=server.serve_forever)
            thread.start()
            client = MinimalWebSocket("ws://127.0.0.1:" + str(server.socket.getsockname()[1]))
            try:
                client.connect()
                self.assertTrue(first_fragment.wait(3))
                with self.assertRaises(TimeoutError):
                    client.recv_text(timeout=0.02)
                release.set()
                self.assertEqual(raw, client.recv_text(timeout=3))
            finally:
                release.set()
                client.close()
                server.shutdown()
                thread.join(3)
                self.assertFalse(thread.is_alive())

    def client(self, cache=None):
        return KISRealtimeWebSocketClient(
            {"kisAppKey": "test", "kisAppSecret": "test"},
            quote_cache=cache or MemoryQuotes(),
            now_provider=lambda: datetime.now(timezone.utc).isoformat(),
        )

    def test_decoder_rejects_wrong_record_count_width_and_encryption_atomically(self):
        valid = tick_values()
        for raw in (
            frame(valid[:-1]), frame(valid + ["unexpected", "unexpected"]),
            frame(valid, count=2), frame(valid, count=0),
            frame(valid, count="invalid"), frame(valid).replace("0|", "1|", 1),
            frame(valid + tick_values()[:-1], count=2),
        ):
            with self.subTest(raw=raw[:30]):
                cache = MemoryQuotes()
                self.assertEqual([], self.client(cache).apply_message(raw))
                self.assertEqual([], cache.writes)

    def test_decoder_never_turns_a_quantity_into_a_stock_code(self):
        for symbol in ("680", "0006800", "A000680", "12.34", ""):
            with self.subTest(symbol=symbol):
                self.assertIsNone(parse_kis_realtime_text(frame(tick_values(MKSC_SHRN_ISCD=symbol))))

    def test_corrupt_quote_and_flow_values_never_reach_cache(self):
        for changes in (
            {"STCK_CNTG_HOUR": "26814638"},
            {"STCK_PRPR": "24382", "ACML_TR_PBMN": "-101000", "CTTR": "1740000"},
            {"STCK_PRPR": "24382"},
            {"STCK_PRPR": "nan"}, {"STCK_PRPR": "inf"},
            {"STCK_PRPR": "0"}, {"ACML_VOL": "-1"},
            {"SHNU_CNTG_SMTN": "not-a-number"},
        ):
            with self.subTest(changes=changes):
                cache = MemoryQuotes()
                self.assertEqual([], self.client(cache).apply_message(frame(tick_values(**changes))))
                self.assertEqual([], cache.writes)

    def test_valid_multi_record_and_large_genuine_move_remain_available(self):
        second = tick_values(MKSC_SHRN_ISCD="005930", STCK_PRPR="72000", STCK_HGPR="72000", STCK_LWPR="50000", PRDY_CTRT="40")
        for extension in ([], ["2"]):
            with self.subTest(width=46 + len(extension)):
                cache = MemoryQuotes()
                updates = self.client(cache).apply_message(frame(tick_values() + extension + second + extension, count=2))
                self.assertEqual(["000680", "005930"], [item["symbol"] for item in updates])
                self.assertEqual([2975, 72000], [item["currentPrice"] for item in cache.writes])

    def test_orderbook_refresh_does_not_relabel_inherited_quote_as_a_new_tick(self):
        cache = MemoryQuotes()
        client = self.client(cache)
        tick = client.apply_message(frame(tick_values()))[0]["payload"]
        values = {"MKSC_SHRN_ISCD": "000680", "BSOP_HOUR": "134410", "ACML_VOL": "9100", "TOTAL_BIDP_RSQN": "12440", "TOTAL_ASKP_RSQN": "17524"}
        book = client.apply_message(frame([values.get(k, "") for k in ORDERBOOK_COLUMNS] + ["2975", "0", "0", "2"], tr_id=KIS_TR_ORDERBOOK))[0]["payload"]
        self.assertEqual(63, book["marketSignalCoverage"]["orderbook"]["wireFieldCount"])
        # A flattened REST write may replace fields between two socket frames.
        mixed = {**book, "currentPrice": 24382, "tradingValue": -101000}
        result = merge_fresh_websocket_stages({"currentPrice": 2970}, mixed, 120)
        self.assertEqual(2975, result["currentPrice"])
        self.assertEqual(26814638, result["tradingValue"])
        self.assertEqual(tick["marketSignalCoverage"]["ccnl"], book["marketSignalCoverage"]["ccnl"])

    def test_unvalidated_legacy_socket_cache_cannot_override_rest(self):
        legacy = {
            "symbol": "000680", "currentPrice": 24382, "tradingValue": -101000,
            "marketSignalCoverage": {"ccnl": {
                "status": "available", "transport": "websocket", "cadence": "websocket",
                "fields": ["currentPrice", "tradingValue"], "freshnessStatus": "realtime",
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
            }},
        }
        rest = {"symbol": "000680", "currentPrice": 2975, "tradingValue": 26814638}
        result = merge_fresh_websocket_stages(rest, legacy, 120)
        self.assertEqual(2975, result["currentPrice"])
        self.assertEqual(26814638, result["tradingValue"])
        cache = MemoryQuotes()
        cache.save("kis", "__market_signals__", "000680", legacy)
        provider = KISMarketSignalProvider({"kisEnabled": "1"}, quote_cache=cache)
        self.assertEqual({}, provider.cached_signal("000680"))
        book = merge_realtime_signal(legacy, {"symbol": "000680", "orderbookBidVolume": 1}, "orderbook", KIS_TR_ORDERBOOK, datetime.now(timezone.utc).isoformat())
        self.assertNotIn("currentPrice", book)


if __name__ == "__main__":
    unittest.main()
