from typing import Dict, List

from .alert_formatting import money, price_money, signed_pct
from .data_freshness import freshness_record
from .market_data import number
from .message_types import EXTERNAL_CRYPTO_MOVE, EXTERNAL_DATA_CONNECTION
from .portfolio import AccountSnapshot, AlertEvent


def _compact_text(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _crypto_asset_label(coin_id: str, item: Dict[str, object]) -> str:
    key = str(coin_id or "").lower().strip()
    symbol = str(item.get("symbol") or "").upper().strip()
    if key == "bitcoin" or symbol == "BTC":
        return "비트코인"
    if key == "ethereum" or symbol == "ETH":
        return "이더리움"
    return str(item.get("name") or symbol or coin_id or "크립토").strip()


def _crypto_title(asset_label: str, direction: str) -> str:
    if direction == "상승":
        return asset_label + " 가격 급등"
    if direction == "하락":
        return asset_label + " 가격 급락"
    return asset_label + " 가격 급변"


def _threshold_pct(value: object) -> str:
    rounded = round(number(value), 1)
    text = str(rounded).rstrip("0").rstrip(".")
    return text or "0"


class ExternalSignalAlertMixin:
    def external_signal_freshness(self, signals: Dict[str, object], message_type: str, source: str, source_as_of: str = "") -> Dict[str, object]:
        freshness = signals.get("freshness") if isinstance(signals.get("freshness"), dict) else {}
        quality = signals.get("quality") if isinstance(signals.get("quality"), dict) else {}
        return freshness_record(
            source,
            message_type,
            settings=getattr(self, "settings", {}),
            source_fetched_at=freshness.get("fetchedAt") or signals.get("fetchedAt"),
            source_as_of=source_as_of,
            data_quality=quality.get("dataState"),
        )

    def external_signal_events(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        signals = snapshot.external_signals or {}
        if not signals:
            return []
        events: List[AlertEvent] = []
        events.extend(self.external_crypto_move_events(snapshot, signals))
        events.extend(self.external_data_connection_events(snapshot, signals))
        return events

    def external_crypto_thresholds(self, coin_id: str, item: Dict[str, object]) -> Dict[str, float]:
        symbol = str(item.get("symbol") or "").upper().strip()
        key = str(coin_id or "").lower().strip()
        relation_thresholds = getattr(self, "relation_thresholds", {}) if isinstance(getattr(self, "relation_thresholds", {}), dict) else {}
        settings = getattr(self, "settings", {}) if isinstance(getattr(self, "settings", {}), dict) else {}
        if key == "bitcoin" or symbol == "BTC":
            change24h = number(settings.get("externalBitcoinChange24hPct") or relation_thresholds.get("externalBitcoinChange24hPct") or 3)
            change7d = number(settings.get("externalBitcoinChange7dPct") or relation_thresholds.get("externalBitcoinChange7dPct") or 4)
        else:
            change24h = number(settings.get("externalCryptoChange24hPct") or 4)
            change7d = number(settings.get("externalCryptoChange7dPct") or 10)
        return {
            "change24h": max(0.1, abs(change24h or 0)),
            "change7d": max(0.1, abs(change7d or 0)),
        }

    def external_crypto_move_model(self, coin_id: str, item: Dict[str, object]) -> Dict[str, object]:
        thresholds = self.external_crypto_thresholds(coin_id, item)
        change24h = number(item.get("change24h"))
        change7d = number(item.get("change7d"))
        candidates = [
            {
                "key": "24h",
                "label": "24시간",
                "change": change24h,
                "threshold": thresholds["change24h"],
                "ratio": abs(change24h) / thresholds["change24h"],
            },
            {
                "key": "7d",
                "label": "7일",
                "change": change7d,
                "threshold": thresholds["change7d"],
                "ratio": abs(change7d) / thresholds["change7d"],
            },
        ]
        dominant = max(candidates, key=lambda item: item["ratio"])
        if dominant["ratio"] < 1:
            return {"triggered": False, "thresholds": thresholds}
        direction = "상승" if dominant["change"] > 0 else "하락" if dominant["change"] < 0 else "변동"
        asset_label = _crypto_asset_label(coin_id, item)
        title = _crypto_title(asset_label, direction)
        threshold_multiple = float(dominant["ratio"] or 0)
        if threshold_multiple >= 2.0:
            review_level = "immediate"
        elif threshold_multiple >= 1.5:
            review_level = "act"
        else:
            review_level = "check"
        reason = (
            dominant["label"]
            + " 변화율 "
            + signed_pct(dominant["change"])
            + "가 기준 ±"
            + _threshold_pct(dominant["threshold"])
            + "%"
            + "를 넘어서 "
            + title
            + "으로 분류했습니다."
        )
        return {
            "triggered": True,
            "assetLabel": asset_label,
            "direction": direction,
            "titleLabel": title,
            "reviewLevel": review_level,
            "dataState": "sufficient",
            "changeState": "new-condition",
            "conflictState": "context-only",
            "validationState": "conditional",
            "dominantPeriod": dominant["key"],
            "dominantPeriodLabel": dominant["label"],
            "dominantChange": round(dominant["change"], 2),
            "dominantThreshold": round(dominant["threshold"], 2),
            "change24h": round(change24h, 2),
            "change7d": round(change7d, 2),
            "thresholds": thresholds,
            "reason": reason,
        }

    def external_crypto_move_events(self, snapshot: AccountSnapshot, signals: Dict[str, object]) -> List[AlertEvent]:
        markets = signals.get("cryptoMarkets") if isinstance(signals.get("cryptoMarkets"), dict) else {}
        events: List[AlertEvent] = []
        for coin_id, item in sorted(markets.items()):
            if not isinstance(item, dict):
                continue
            model = self.external_crypto_move_model(str(coin_id), item)
            if not model.get("triggered"):
                continue
            symbol = str(item.get("symbol") or coin_id).upper().strip()
            asset_label = str(model.get("assetLabel") or symbol or "크립토")
            provider = str(item.get("provider") or "CoinGecko").strip() or "CoinGecko"
            change24h = number(item.get("change24h"))
            change7d = number(item.get("change7d"))
            review_level = str(model.get("reviewLevel") or "check")
            severity = "ALERT" if review_level in {"act", "immediate"} else "WATCH"
            thresholds = model.get("thresholds") if isinstance(model.get("thresholds"), dict) else {}
            threshold_line = (
                "설정: "
                + ("비트코인" if symbol == "BTC" else "크립토")
                + " 24시간 ±"
                + _threshold_pct(thresholds.get("change24h"))
                + "% 또는 7일 ±"
                + _threshold_pct(thresholds.get("change7d"))
                + "% 이상"
            )
            direction_key = "up" if model.get("direction") == "상승" else "down" if model.get("direction") == "하락" else "move"
            period_key = str(model.get("dominantPeriod") or "move")
            state_key = ":".join([snapshot.account_id, "crypto", symbol or str(coin_id), period_key, direction_key])
            lines = [
                asset_label + " 변동 24h " + signed_pct(change24h) + " · 7d " + signed_pct(change7d),
                "크립토 가격 " + price_money(number(item.get("price")), "USD"),
                "크립토 거래액 " + money(number(item.get("volume24h")), "USD"),
                "출처 " + provider,
                "확인 행동 비트코인 민감 종목과 보유 종목의 가격 반응을 함께 확인",
                "판단 의미 매수·매도 지시가 아니라 외부시장 등락 관찰",
            ]
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                severity,
                EXTERNAL_CRYPTO_MOVE,
                state_key,
                "크립토 변동",
                lines,
                symbol,
                criteria=self.criteria(
                    threshold_line,
                    asset_label + " 24h " + signed_pct(change24h) + ", 7d " + signed_pct(change7d),
                ),
                metadata={
                    "market": "CRYPTO",
                    "provider": provider,
                    "cryptoId": str(coin_id),
                    "change24h": round(change24h, 2),
                    "change7d": round(change7d, 2),
                    "price": number(item.get("price")),
                    "volume24h": number(item.get("volume24h")),
                    "lastUpdated": str(item.get("lastUpdated") or ""),
                    "cryptoMoveModel": model,
                    "cryptoMoveState": review_level,
                    "reviewLevel": review_level,
                    "dataState": str(model.get("dataState") or "sufficient"),
                    "changeState": str(model.get("changeState") or "new-condition"),
                    "conflictState": str(model.get("conflictState") or "context-only"),
                    "validationState": str(model.get("validationState") or "conditional"),
                    "cryptoMoveDirection": str(model.get("direction") or ""),
                    "cryptoMoveDominantPeriod": str(model.get("dominantPeriodLabel") or ""),
                    "cryptoMoveDominantChange": model.get("dominantChange"),
                    "cryptoMoveTitle": str(model.get("titleLabel") or ""),
                    "cryptoMoveReason": str(model.get("reason") or ""),
                    "notificationSignals": ["important", "confirmingData", "actionable"],
                    "dataFreshness": self.external_signal_freshness(
                        signals,
                        EXTERNAL_CRYPTO_MOVE,
                        provider,
                        str(item.get("lastUpdated") or ""),
                    ),
                    "dataFreshnessRequired": True,
                    "sourceSignalRole": "external-market-observation",
                    "investmentJudgement": False,
                },
            ))
        return events

    def external_data_connection_events(self, snapshot: AccountSnapshot, signals: Dict[str, object]) -> List[AlertEvent]:
        grouped: Dict[str, List[str]] = {}
        for item in signals.get("statuses") or []:
            if not isinstance(item, dict) or item.get("ok", True):
                continue
            source = str(item.get("source") or "외부 API")
            message = str(item.get("message") or "연결 확인 필요")
            grouped.setdefault(source, []).append(message)
        events: List[AlertEvent] = []
        for source, messages in grouped.items():
            issue_count = len(messages)
            sample_messages = [_compact_text(message, 110) for message in messages[:3]]
            summary = source + " 오류 " + str(issue_count) + "건"
            if sample_messages:
                summary += " · " + " / ".join(sample_messages)
            lines = [
                "공급자 " + source,
                "상태 오류 " + str(issue_count) + "건",
                *["예시 " + message for message in sample_messages],
                "확인 행동 API 키, 호출 제한, 응답 형식, 마지막 성공 시각 점검",
            ]
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "WATCH",
                EXTERNAL_DATA_CONNECTION,
                ":".join([snapshot.account_id, "external", source, str(issue_count)]),
                "외부 데이터 연결",
                lines,
                criteria=self.criteria(
                    "외부 데이터 API 응답 오류, 호출 제한, 또는 응답 형식 문제가 감지될 때",
                    summary,
                ),
                metadata={
                    "connectionIssueCount": issue_count,
                    "connectionIssues": messages[:8],
                    "provider": source,
                    "notificationSignals": ["statusNoise"],
                },
            ))
        return events
