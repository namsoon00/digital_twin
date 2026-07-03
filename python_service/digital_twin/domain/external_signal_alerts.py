from typing import Dict, List

from .alert_formatting import compact_number, money, signed_number, signed_pct
from .market_data import number
from .portfolio import AccountSnapshot, AlertEvent


class ExternalSignalAlertMixin:
    def external_signal_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        signals = snapshot.external_signals or {}
        if not signals:
            return []
        previous_signals = previous.get("externalSignals") or {}
        events: List[AlertEvent] = []
        events.extend(self.external_data_connection_events(snapshot, signals))
        events.extend(self.external_equity_events(snapshot, signals))
        events.extend(self.external_crypto_events(snapshot, signals))
        events.extend(self.external_macro_events(snapshot, signals, previous_signals))
        events.extend(self.external_dart_events(snapshot, signals, previous_signals))
        return events

    def external_data_connection_events(self, snapshot: AccountSnapshot, signals: Dict[str, object]) -> List[AlertEvent]:
        events: List[AlertEvent] = []
        for item in signals.get("statuses") or []:
            if not isinstance(item, dict) or item.get("ok", True):
                continue
            source = str(item.get("source") or "외부 API")
            message = str(item.get("message") or "연결 확인 필요")
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "WATCH",
                "externalDataConnection",
                ":".join([snapshot.account_id, "external", source, message[:32]]),
                "외부 데이터 연결",
                [source, message, "키/호출 제한/응답 형식 확인"],
                criteria=self.criteria(
                    "외부 데이터 API 응답 오류, 호출 제한, 또는 응답 형식 문제가 감지될 때",
                    source + " - " + message,
                ),
            ))
        return events

    def external_equity_events(self, snapshot: AccountSnapshot, signals: Dict[str, object]) -> List[AlertEvent]:
        threshold = float(self.thresholds.get("externalEquityChangePct", 0))
        events: List[AlertEvent] = []
        quotes = signals.get("equityQuotes") or {}
        for symbol, quote in quotes.items():
            if not isinstance(quote, dict):
                continue
            change = number(quote.get("changePercent"))
            if threshold and abs(change) < threshold:
                continue
            symbol_label = str(symbol or "").upper()
            price = number(quote.get("price"))
            volume = number(quote.get("volume"))
            provider = str(quote.get("provider") or "Alpha Vantage")
            latest_trading_day = str(quote.get("latestTradingDay") or "")
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT" if change < 0 else "WATCH",
                "externalEquityMove",
                ":".join([snapshot.account_id, "alpha", symbol_label, signed_pct(change)]),
                symbol_label,
                [
                    "미장 가격 변동 " + signed_pct(change),
                    "가격 " + money(price, "USD"),
                    "거래량 " + compact_number(volume),
                    "기준일 " + (latest_trading_day or "-"),
                    "출처 " + provider,
                ],
                symbol_label,
                criteria=self.criteria(
                    "미장 가격 변동률 ±" + self.threshold_text("externalEquityChangePct", "%") + " 이상",
                    "가격 변동 " + signed_pct(change) + ", 가격 " + money(price, "USD"),
                ),
                metadata={
                    "market": "US",
                    "changePercent": change,
                    "price": price,
                    "volume": volume,
                    "latestTradingDay": latest_trading_day,
                    "provider": provider,
                },
            ))
        return events

    def external_crypto_events(self, snapshot: AccountSnapshot, signals: Dict[str, object]) -> List[AlertEvent]:
        day_threshold = float(self.thresholds.get("externalCryptoChange24hPct", 0))
        week_threshold = float(self.thresholds.get("externalCryptoChange7dPct", 0))
        events: List[AlertEvent] = []
        markets = signals.get("cryptoMarkets") or {}
        for coin_id, item in markets.items():
            if not isinstance(item, dict):
                continue
            change24h = number(item.get("change24h"))
            change7d = number(item.get("change7d"))
            if day_threshold and abs(change24h) < day_threshold and week_threshold and abs(change7d) < week_threshold:
                continue
            symbol = str(item.get("symbol") or coin_id).upper()
            coin_name = str(item.get("name") or "").strip()
            price = number(item.get("price"))
            volume24h = number(item.get("volume24h"))
            provider = str(item.get("provider") or "CoinGecko")
            is_bitcoin = symbol == "BTC" or str(coin_id or "").strip().lower() == "bitcoin" or coin_name.lower() == "bitcoin"
            change_label = "비트코인 변동" if is_bitcoin else "크립토 변동"
            change_value = "24h " + signed_pct(change24h) + " · 7d " + signed_pct(change7d)
            severity = "ALERT" if change24h < 0 or change7d < 0 else "WATCH"
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                severity,
                "externalCryptoMove",
                ":".join([snapshot.account_id, "crypto", symbol, signed_pct(change24h)]),
                "크립토 변동",
                [
                    change_label + " " + change_value,
                    "크립토 가격 " + money(price, "USD"),
                    "크립토 거래액 " + money(volume24h, "USD"),
                    "출처 " + provider,
                    "MSTR/STRC 등 비트코인 민감 종목 점검",
                ],
                symbol,
                criteria=self.criteria(
                    "크립토 24h ±" + self.threshold_text("externalCryptoChange24hPct", "%") + " 또는 7d ±" + self.threshold_text("externalCryptoChange7dPct", "%") + " 이상",
                    ("비트코인 " if is_bitcoin else symbol + " ") + "24h " + signed_pct(change24h) + ", 7d " + signed_pct(change7d),
                ),
                metadata={
                    "market": "CRYPTO",
                    "change24h": change24h,
                    "change7d": change7d,
                    "price": price,
                    "volume24h": volume24h,
                    "provider": provider,
                    "coinId": str(coin_id or ""),
                },
            ))
        return events

    def external_macro_events(self, snapshot: AccountSnapshot, signals: Dict[str, object], previous_signals: Dict[str, object]) -> List[AlertEvent]:
        threshold_bp = float(self.thresholds.get("externalMacroRateDeltaBp", 0))
        macro = signals.get("macro") if isinstance(signals.get("macro"), dict) else {}
        previous_macro = previous_signals.get("macro") if isinstance(previous_signals.get("macro"), dict) else {}
        series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
        previous_series = previous_macro.get("series") if isinstance(previous_macro.get("series"), dict) else {}
        events: List[AlertEvent] = []
        lines: List[str] = []
        for series_id, item in series.items():
            if not isinstance(item, dict):
                continue
            previous_item = previous_series.get(series_id) if isinstance(previous_series.get(series_id), dict) else {}
            if not previous_item:
                continue
            current_value = number(item.get("value"))
            previous_value = number(previous_item.get("value"))
            delta_bp = (current_value - previous_value) * 100
            if threshold_bp and abs(delta_bp) < threshold_bp:
                continue
            lines.append(str(series_id) + " " + compact_number(current_value) + "% (" + signed_number(delta_bp) + "bp)")
        if "yieldSpread10y2y" in macro and "yieldSpread10y2y" in previous_macro:
            spread = number(macro.get("yieldSpread10y2y"))
            previous_spread = number(previous_macro.get("yieldSpread10y2y"))
            spread_delta_bp = (spread - previous_spread) * 100
            if not threshold_bp or abs(spread_delta_bp) >= threshold_bp:
                lines.append("10Y-2Y " + compact_number(spread) + "% (" + signed_number(spread_delta_bp) + "bp)")
        if not lines:
            return events
        severity = "ALERT" if any("(-" in line for line in lines) else "WATCH"
        events.append(AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            severity,
            "externalMacroShift",
            ":".join([snapshot.account_id, "macro", ",".join(lines[:2])]),
            "거시 지표 변화",
            ["FRED 금리/스프레드 변화"] + lines + ["모델 위험 선호 점수 재확인"],
            criteria=self.criteria(
                "FRED 금리 또는 10Y-2Y 스프레드 변화 ±" + self.threshold_text("externalMacroRateDeltaBp", "bp") + " 이상",
                ", ".join(lines[:3]),
            ),
        ))
        return events

    def external_dart_events(self, snapshot: AccountSnapshot, signals: Dict[str, object], previous_signals: Dict[str, object]) -> List[AlertEvent]:
        disclosures = signals.get("dartDisclosures") if isinstance(signals.get("dartDisclosures"), dict) else {}
        previous_disclosures = previous_signals.get("dartDisclosures") if isinstance(previous_signals.get("dartDisclosures"), dict) else {}
        events: List[AlertEvent] = []
        for symbol, item in disclosures.items():
            if not isinstance(item, dict):
                continue
            previous_item = previous_disclosures.get(symbol) if isinstance(previous_disclosures.get(symbol), dict) else {}
            previous_receipt = str(previous_item.get("receiptNo") or "")
            receipt = str(item.get("receiptNo") or "")
            if not previous_receipt or not receipt or previous_receipt == receipt:
                continue
            symbol_label = str(symbol or "").upper()
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "WATCH",
                "externalDartDisclosure",
                ":".join([snapshot.account_id, "dart", symbol_label, receipt]),
                str(item.get("corpName") or symbol_label),
                [
                    "신규 공시 감지",
                    str(item.get("reportName") or "-"),
                    "접수일 " + str(item.get("receiptDate") or "-"),
                    "최근 공시 " + compact_number(number(item.get("count"))) + "건",
                    "출처 " + str(item.get("provider") or "OpenDART"),
                ],
                symbol_label,
                criteria=self.criteria(
                    "OpenDART 접수번호가 직전 조회와 다를 때",
                    "접수번호 " + receipt + ", 접수일 " + str(item.get("receiptDate") or "-"),
                ),
            ))
        return events
