from typing import Dict, List

from .alert_formatting import compact_number, money, price_money, signed_number, signed_pct
from .market_data import number
from .investment_research import ACTIVE_INVESTMENT_OPINION_VERSION, ACTION_LABELS
from .ontology_rules import AI_PROMPT_REGISTRY_VERSION, strength_label
from .portfolio import AccountSnapshot, AlertEvent


def _ratio_to_threshold(change: float, threshold: float) -> float:
    threshold_value = abs(float(threshold or 0))
    if threshold_value <= 0:
        return 0.0
    return abs(float(change or 0)) / threshold_value


def _threshold_triggered(change: float, threshold: float) -> bool:
    threshold_value = abs(float(threshold or 0))
    if threshold_value <= 0:
        return abs(float(change or 0)) > 0
    return abs(float(change or 0)) >= threshold_value


def _threshold_pct_text(value: float) -> str:
    rounded = round(abs(float(value or 0)), 1)
    if rounded == round(rounded):
        return str(int(round(rounded))) + "%"
    return str(rounded).rstrip("0").rstrip(".") + "%"


def _compact_text(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _crypto_asset_label(symbol: str, name: str, is_bitcoin: bool) -> str:
    if is_bitcoin:
        return "비트코인"
    if str(symbol or "").upper() == "ETH" or "ethereum" in str(name or "").lower():
        return "이더리움"
    return "크립토"


def _crypto_sensitive_position_labels(snapshot: AccountSnapshot, crypto_symbol: str, is_bitcoin: bool) -> List[str]:
    symbol = str(crypto_symbol or "").upper()
    direct_symbols = {"COIN", "MARA", "RIOT", "BITF", "HUT", "CLSK"}
    if is_bitcoin:
        direct_symbols.update({"BTC", "IBIT", "FBTC", "BITB", "MSTR", "STRC"})
    if symbol == "ETH":
        direct_symbols.update({"ETH", "ETHA", "ETHE"})
    text_terms = ["crypto", "크립토", "디지털자산", "blockchain", "블록체인"]
    text_terms.extend(["btc", "bitcoin", "비트코인"] if is_bitcoin else ["eth", "ethereum", "이더리움"])
    labels: List[str] = []
    for position in list(snapshot.positions or []) + list(snapshot.watchlist or []):
        if position.is_cash():
            continue
        position_symbol = str(position.symbol or "").upper()
        blob = " ".join([position_symbol, str(position.name or ""), str(position.sector or "")]).lower()
        if position_symbol not in direct_symbols and not any(term.lower() in blob for term in text_terms):
            continue
        label = str(position.name or position_symbol).strip()
        if position_symbol and position_symbol not in label:
            label += " / " + position_symbol
        if label and label not in labels:
            labels.append(label)
    return labels[:5]


def _evidence_dict(evidence_id: str, symbol: str, kind: str, source: str, title: str, summary: str, polarity: str, impact: float, confidence: float = 0.65) -> Dict[str, object]:
    return {
        "evidenceId": evidence_id,
        "symbol": symbol,
        "kind": kind,
        "source": source,
        "title": title,
        "summary": summary,
        "url": "",
        "observedAt": "",
        "polarity": polarity,
        "impactScore": round(number(impact), 1),
        "confidence": round(number(confidence), 2),
    }


def crypto_active_investment_opinion(
    model: Dict[str, object],
    item: Dict[str, object],
    is_bitcoin: bool,
    sensitive_positions: List[str],
) -> Dict[str, object]:
    symbol = str(model.get("symbol") or item.get("symbol") or "").upper()
    coin_id = str(item.get("id") or item.get("coinId") or symbol.lower()).strip().lower()
    name = str(item.get("name") or symbol or "크립토").strip()
    asset_label = _crypto_asset_label(symbol, name, is_bitcoin)
    score = number(model.get("score"))
    direction = str(model.get("direction") or "")
    dominant_period = str(model.get("dominantPeriodLabel") or model.get("dominantPeriod") or "대표")
    dominant_change = number(model.get("dominantChange"))
    change24h = number(item.get("change24h"))
    change7d = number(item.get("change7d"))
    direct_exposure = bool(sensitive_positions)
    if direct_exposure and direction == "down" and score >= 75:
        action = "TRIM"
    else:
        action = "HOLD"
    action_label = ACTION_LABELS.get(action, action)
    if action == "TRIM":
        primary_label = "민감 종목 분할축소 검토"
    elif direct_exposure:
        primary_label = "보유 유지, 민감 종목 추가매수 보류"
    else:
        primary_label = "보유 영향만 점검"
    exposure_text = "직접 민감 보유: " + ", ".join(sensitive_positions) if sensitive_positions else "직접 민감 보유 없음"
    direction_text = "상승" if direction == "up" else "하락" if direction == "down" else "변동"
    beginner_summary = (
        asset_label
        + " "
        + dominant_period
        + " 변동 "
        + signed_pct(dominant_change)
        + "가 기준을 넘었습니다. "
        + ("연결 보유 종목이 있어 비중 확대보다 리스크 확인이 먼저입니다." if direct_exposure else "직접 연결된 보유 종목이 없으면 매수·매도보다 참고 신호입니다.")
    )
    support_signals = [
        asset_label + " " + dominant_period + " " + signed_pct(dominant_change) + " 기준 초과",
    ]
    risk_signals = [
        "크립토 급변은 보유 종목의 위험 선호와 같이 흔들릴 수 있음",
    ]
    if direct_exposure:
        risk_signals.append(exposure_text)
    counter_signals = []
    if change24h and change7d and change24h * change7d < 0:
        counter_signals.append("24시간 " + signed_pct(change24h) + ", 7일 " + signed_pct(change7d) + "로 단기와 주간 방향이 다름")
    if not direct_exposure:
        counter_signals.append("직접 민감 보유 종목이 없어 단독 매매 근거는 약함")
    execution_plan = {
        "primaryAction": action,
        "primaryActionLabel": primary_label,
        "decisionStage": "external-signal-review",
        "actionGroup": "riskControl" if action == "TRIM" else "marketReference",
        "actionLevel": "review" if direct_exposure else "watch",
        "beginnerSummary": beginner_summary,
        "blockedActions": [
            asset_label + " 변동만 보고 주식 신규 매수·매도",
            "민감 보유 종목 확인 전 추가매수",
        ],
        "riskSignals": risk_signals,
        "supportSignals": support_signals,
        "counterSignals": counter_signals,
        "strengthenConditions": [
            "민감 보유 종목 가격·거래량이 " + asset_label + " 방향과 같이 움직임",
            dominant_period + " 변동이 다음 조회에서도 기준 이상 유지",
        ],
        "weakenConditions": [
            "민감 보유 종목 반응이 없거나 " + asset_label + " 변동이 기준 아래로 둔화",
            "24시간과 7일 방향 충돌이 커짐",
        ],
        "nextChecks": [
            "보유 종목 중 " + asset_label + " 민감 종목이 있는지 확인",
            "민감 종목의 현재가·거래량·수익률이 같은 방향인지 확인",
        ],
        "missingDataImpact": [
            "종목별 " + asset_label + " 민감도 데이터가 없으면 결론 강도를 낮춥니다.",
        ],
    }
    evidence = [
        _evidence_dict(
            "research:" + symbol + ":crypto-move",
            symbol,
            "crypto-market",
            str(item.get("provider") or "CoinGecko"),
            asset_label + " " + dominant_period + " 변동 " + signed_pct(dominant_change),
            "24h " + signed_pct(change24h) + ", 7d " + signed_pct(change7d),
            "support" if direction == "up" else "risk" if direction == "down" else "context",
            min(18.0, max(4.0, score * 0.2)),
            0.72,
        )
    ]
    if direct_exposure:
        evidence.append(_evidence_dict(
            "research:" + symbol + ":crypto-exposure",
            symbol,
            "portfolio-exposure",
            "portfolio",
            exposure_text,
            "크립토 민감 보유 종목은 외부 변동에 가격 반응이 커질 수 있습니다.",
            "risk" if direction == "down" else "context",
            8.0,
            0.7,
        ))
    counter_evidence = [
        _evidence_dict(
            "research:" + symbol + ":crypto-counter:" + str(index),
            symbol,
            "counter-signal",
            "relation-rules",
            text,
            text,
            "context",
            2.0,
            0.62,
        )
        for index, text in enumerate(counter_signals)
    ]
    conviction = min(88.0, max(45.0, 50.0 + score * (0.34 if direct_exposure else 0.25) + (8.0 if action == "TRIM" else 0.0)))
    thesis = (
        asset_label
        + " "
        + direction_text
        + " 변동은 기준을 넘었지만 "
        + ("민감 보유 종목이 있어 " + primary_label + " 의견입니다." if direct_exposure else "직접 민감 보유가 없어 " + primary_label + " 의견입니다.")
    )
    source_url = "https://www.coingecko.com/en/coins/" + coin_id if coin_id and not coin_id.isupper() else ""
    return {
        "engineVersion": ACTIVE_INVESTMENT_OPINION_VERSION,
        "symbol": symbol,
        "action": action,
        "actionLabel": action_label,
        "conviction": round(conviction, 1),
        "timeHorizon": "days",
        "thesis": thesis,
        "evidence": evidence,
        "counterEvidence": counter_evidence,
        "missingData": [],
        "invalidationCondition": " / ".join(execution_plan["weakenConditions"][:2]),
        "nextCheck": " / ".join(execution_plan["nextChecks"][:2]),
        "executionPlan": execution_plan,
        "sourceUrls": [source_url] if source_url else [],
        "scoreBreakdown": {
            "supportScore": round(sum(number(item.get("impactScore")) for item in evidence if item.get("polarity") == "support"), 1),
            "riskScore": round(sum(number(item.get("impactScore")) for item in evidence if item.get("polarity") == "risk"), 1),
            "relationScore": round(score, 1),
            "directExposureCount": len(sensitive_positions),
            "change24h": round(change24h, 1),
            "change7d": round(change7d, 1),
        },
        "promptContract": {
            "requiredDecision": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
            "decisionRole": "investment_opinion_not_order",
            "mustInclude": ["conviction", "evidence", "counterEvidence", "executionPlan", "invalidationCondition"],
            "guardrails": [
                "크립토 가격 하나만으로 주식 자동 주문 지시를 만들지 않습니다.",
                "민감 보유 종목이 없으면 시장 참고 신호로 표현합니다.",
                "초보자가 실행 기준을 이해하도록 보류 행동과 다음 확인을 함께 표시합니다.",
            ],
        },
    }


def crypto_move_model(symbol: str, asset_label: str, change24h: float, change7d: float, day_threshold: float, week_threshold: float) -> Dict[str, object]:
    day_ratio = _ratio_to_threshold(change24h, day_threshold)
    week_ratio = _ratio_to_threshold(change7d, week_threshold)
    day_triggered = _threshold_triggered(change24h, day_threshold)
    week_triggered = _threshold_triggered(change7d, week_threshold)
    candidates: List[Dict[str, object]] = []
    if day_triggered:
        candidates.append({
            "period": "24h",
            "periodLabel": "24시간",
            "change": float(change24h or 0),
            "threshold": float(day_threshold or 0),
            "ratio": day_ratio,
        })
    if week_triggered:
        candidates.append({
            "period": "7d",
            "periodLabel": "7일",
            "change": float(change7d or 0),
            "threshold": float(week_threshold or 0),
            "ratio": week_ratio,
        })
    dominant = max(candidates, key=lambda item: (float(item.get("ratio") or 0), abs(float(item.get("change") or 0)))) if candidates else {
        "period": "",
        "periodLabel": "",
        "change": 0.0,
        "threshold": 0.0,
        "ratio": 0.0,
    }
    dominant_change = float(dominant.get("change") or 0)
    direction = "up" if dominant_change > 0 else "down" if dominant_change < 0 else "flat"
    direction_label = "상승" if direction == "up" else "하락" if direction == "down" else "변동"
    move_label = "급등" if direction == "up" else "급락" if direction == "down" else "급변"
    title_label = asset_label + " 가격 " + move_label
    model_score = round(max(0.0, min(100.0, max(day_ratio, week_ratio) * 60.0)), 1)
    dominant_threshold = float(dominant.get("threshold") or 0)
    threshold_text = "기준 ±" + _threshold_pct_text(dominant_threshold) if dominant_threshold else "기준값 없음"
    reason = (
        str(dominant.get("periodLabel") or "대표")
        + " 변동률 "
        + signed_pct(dominant_change)
        + "가 "
        + threshold_text
        + "를 넘어서 "
        + title_label
        + "으로 판단했습니다."
    ) if candidates else "기준을 넘은 크립토 변동이 없습니다."
    detected_text = (
        "관계 규칙 강도 "
        + ("%g" % model_score)
        + "점, "
        + str(dominant.get("periodLabel") or "대표")
        + " "
        + signed_pct(dominant_change)
        + " ("
        + threshold_text
        + "), 24시간 "
        + signed_pct(change24h)
        + ", 7일 "
        + signed_pct(change7d)
    )
    audit = {
        "key": "cryptoMoveScoreFormula",
        "label": "크립토 변동 공식",
        "expression": "min(100, max(abs(change24h)/dayThreshold, abs(change7d)/weekThreshold) * 60)",
        "result": model_score,
        "selected": True,
        "variables": {
            "change24h": float(change24h or 0),
            "change7d": float(change7d or 0),
            "dayThreshold": float(day_threshold or 0),
            "weekThreshold": float(week_threshold or 0),
            "dayRatio": round(day_ratio, 4),
            "weekRatio": round(week_ratio, 4),
            "dominantChange": dominant_change,
            "dominantRatio": round(float(dominant.get("ratio") or 0), 4),
        },
        "missing": [],
        "note": reason,
    }
    return {
        "triggered": bool(candidates),
        "symbol": str(symbol or "").upper(),
        "score": model_score,
        "direction": direction,
        "directionLabel": direction_label,
        "dominantPeriod": str(dominant.get("period") or ""),
        "dominantPeriodLabel": str(dominant.get("periodLabel") or ""),
        "dominantChange": dominant_change,
        "dominantThreshold": dominant_threshold,
        "dominantRatio": round(float(dominant.get("ratio") or 0), 4),
        "dayTriggered": day_triggered,
        "weekTriggered": week_triggered,
        "titleLabel": title_label,
        "severity": "ALERT" if direction == "down" else "WATCH",
        "reason": reason,
        "detectedText": detected_text,
        "formulaAudit": audit,
    }


def crypto_relation_context(model: Dict[str, object], item: Dict[str, object], is_bitcoin: bool, active_opinion: Dict[str, object] = None) -> Dict[str, object]:
    symbol = str(model.get("symbol") or item.get("symbol") or "").upper()
    score = float(model.get("score") or 0)
    rule_id = "external.crypto.btc_sensitivity.v1" if is_bitcoin else "external.crypto.market_move.v1"
    label = "비트코인 급변 -> 민감 종목 연동 점검" if is_bitcoin else "크립토 급변 -> 시장 위험 선호 점검"
    evidence = [
        "24h " + signed_pct(number(item.get("change24h"))),
        "7d " + signed_pct(number(item.get("change7d"))),
        "거래액 " + money(number(item.get("volume24h")), "USD"),
    ]
    matched_rule = {
        "ruleId": rule_id,
        "rule_id": rule_id,
        "label": label,
        "version": "v1",
        "relationType": "EXTERNAL_MARKET_MOVE",
        "relation_type": "EXTERNAL_MARKET_MOVE",
        "signalType": "cross_asset" if is_bitcoin else "risk_appetite",
        "signal_type": "cross_asset" if is_bitcoin else "risk_appetite",
        "matched": True,
        "strengthScore": round(score, 1),
        "strength_score": round(score, 1),
        "strengthLabel": strength_label(score),
        "strength_label": strength_label(score),
        "confidence": 85.0,
        "evidence": evidence,
        "missing": [],
        "promptHint": "민감 종목 보유 여부와 가격 반응 시차를 확인합니다." if is_bitcoin else "외부 위험 선호 변화가 보유 종목에 직접 연결되는지 확인합니다.",
    }
    prompt_context = {
        "promptVersion": AI_PROMPT_REGISTRY_VERSION,
        "promptRegistryVersion": AI_PROMPT_REGISTRY_VERSION,
        "promptId": "externalCryptoMove",
        "promptPolicy": "providedDataOnly=1\nshowMissingData=1\nseparateInvestmentJudgmentAndDelivery=1",
        "matchedRules": [matched_rule],
        "missingData": [],
        "guardrails": [
            "크립토 가격만으로 주식 매매 결론을 내리지 않습니다.",
            "민감 종목이 없으면 시장 참고 신호로만 표현합니다.",
        ],
    }
    return {
        "engineVersion": "ontology-relation-rules-v1",
        "subject": {"symbol": symbol, "market": "CRYPTO", "asset": str(item.get("name") or symbol)},
        "facts": {
            "symbol": symbol,
            "price": number(item.get("price")),
            "volume24h": number(item.get("volume24h")),
            "change24h": number(item.get("change24h")),
            "change7d": number(item.get("change7d")),
            "provider": str(item.get("provider") or "CoinGecko"),
        },
        "matchedRules": [matched_rule],
        "activeRules": [matched_rule],
        "referenceRules": [],
        "missingData": [],
        "dominantSignals": [label],
        "signalStrength": round(score, 1),
        "signalStrengthLabel": strength_label(score),
        "confidence": 85.0,
        "decision": {
            "label": "비트코인 민감도 점검" if is_bitcoin else "크립토 시장 변동 점검",
            "tone": str(model.get("severity") or "WATCH").lower(),
            "score": round(score, 1),
            "basis": "ontologyRelationRules",
            "selectedRuleId": rule_id,
        },
        "executionPlan": dict((active_opinion or {}).get("executionPlan") or {}),
        "activeInvestmentOpinion": dict(active_opinion or {}),
        "promptContext": prompt_context,
    }


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
        positions = {item.symbol.upper(): item.to_dict() for item in snapshot.positions if item.symbol and not item.is_cash()}
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
            position_context = dict(positions.get(symbol_label) or {})
            if position_context and price:
                position_context["current_price"] = price
                position_context["currency"] = position_context.get("currency") or "USD"
            price_lines = self.holding_price_lines(position_context) if position_context else ["현재가: " + price_money(price, "USD")]
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                "ALERT" if change < 0 else "WATCH",
                "externalEquityMove",
                ":".join([snapshot.account_id, "alpha", symbol_label, signed_pct(change)]),
                symbol_label,
                [
                    "미장 가격 변동 " + signed_pct(change),
                    *price_lines,
                    "거래량 " + compact_number(volume),
                    "기준일 " + (latest_trading_day or "-"),
                    "출처 " + provider,
                ],
                symbol_label,
                criteria=self.criteria(
                    "미장 가격 변동률 ±" + self.threshold_text("externalEquityChangePct", "%") + " 이상",
                    "가격 변동 " + signed_pct(change) + ", 현재가 " + price_money(price, "USD"),
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
        default_day_threshold = float(self.thresholds.get("externalCryptoChange24hPct", 0))
        default_week_threshold = float(self.thresholds.get("externalCryptoChange7dPct", 0))
        bitcoin_day_threshold = float(self.thresholds.get("externalBitcoinChange24hPct", default_day_threshold))
        bitcoin_week_threshold = float(self.thresholds.get("externalBitcoinChange7dPct", default_week_threshold))
        events: List[AlertEvent] = []
        markets = signals.get("cryptoMarkets") or {}
        for coin_id, item in markets.items():
            if not isinstance(item, dict):
                continue
            change24h = number(item.get("change24h"))
            change7d = number(item.get("change7d"))
            symbol = str(item.get("symbol") or coin_id).upper()
            coin_name = str(item.get("name") or "").strip()
            is_bitcoin = symbol == "BTC" or str(coin_id or "").strip().lower() == "bitcoin" or coin_name.lower() == "bitcoin"
            day_threshold = bitcoin_day_threshold if is_bitcoin else default_day_threshold
            week_threshold = bitcoin_week_threshold if is_bitcoin else default_week_threshold
            asset_label = "비트코인" if is_bitcoin else "크립토"
            model = crypto_move_model(symbol, asset_label, change24h, change7d, day_threshold, week_threshold)
            if not model.get("triggered"):
                continue
            item_context = dict(item)
            item_context.setdefault("id", str(coin_id or ""))
            sensitive_positions = _crypto_sensitive_position_labels(snapshot, symbol, is_bitcoin)
            active_opinion = crypto_active_investment_opinion(model, item_context, is_bitcoin, sensitive_positions)
            relation_context = crypto_relation_context(model, item_context, is_bitcoin, active_opinion)
            prompt_context = relation_context.get("promptContext") if isinstance(relation_context.get("promptContext"), dict) else {}
            price = number(item.get("price"))
            volume24h = number(item.get("volume24h"))
            provider = str(item.get("provider") or "CoinGecko")
            change_label = "비트코인 변동" if is_bitcoin else "크립토 변동"
            change_value = "24h " + signed_pct(change24h) + " · 7d " + signed_pct(change7d)
            execution_plan = active_opinion.get("executionPlan") if isinstance(active_opinion.get("executionPlan"), dict) else {}
            beginner_summary = str(execution_plan.get("beginnerSummary") or "").strip()
            exposure_line = "연결 보유 " + (", ".join(sensitive_positions) if sensitive_positions else "없음")
            action_line = str(execution_plan.get("primaryActionLabel") or active_opinion.get("actionLabel") or "").strip()
            severity = str(model.get("severity") or "WATCH")
            threshold_label = (
                "비트코인 24h ±" + self.threshold_text("externalBitcoinChange24hPct", "%") + " 또는 7d ±" + self.threshold_text("externalBitcoinChange7dPct", "%") + " 이상"
                if is_bitcoin
                else "크립토 24h ±" + self.threshold_text("externalCryptoChange24hPct", "%") + " 또는 7d ±" + self.threshold_text("externalCryptoChange7dPct", "%") + " 이상"
            )
            events.append(AlertEvent(
                snapshot.account_id,
                snapshot.account_label,
                severity,
                "externalCryptoMove",
                ":".join([snapshot.account_id, "crypto", symbol, str(model.get("dominantPeriod") or ""), signed_pct(float(model.get("dominantChange") or 0))]),
                "크립토 변동",
                [
                    change_label + " " + change_value,
                    "크립토 가격 " + price_money(price, "USD"),
                    "크립토 거래액 " + money(volume24h, "USD"),
                    "권장 액션: " + action_line if action_line else "",
                    "초보자 요약: " + beginner_summary if beginner_summary else "",
                    exposure_line,
                    "출처 " + provider,
                    ("MSTR/STRC 등 비트코인 민감 종목 점검" if is_bitcoin else "ETH/COIN 등 크립토 민감 종목 점검"),
                ],
                symbol,
                criteria=self.criteria(
                    threshold_label,
                    str(model.get("detectedText") or (("비트코인 " if is_bitcoin else symbol + " ") + "24h " + signed_pct(change24h) + ", 7d " + signed_pct(change7d))),
                ),
                metadata={
                    "market": "CRYPTO",
                    "change24h": change24h,
                    "change7d": change7d,
                    "price": price,
                    "volume24h": volume24h,
                    "provider": provider,
                    "coinId": str(coin_id or ""),
                    "cryptoMoveModel": model,
                    "cryptoMoveScore": model.get("score"),
                    "cryptoMoveDirection": model.get("directionLabel"),
                    "cryptoMoveDominantPeriod": model.get("dominantPeriodLabel"),
                    "cryptoMoveDominantChange": model.get("dominantChange"),
                    "cryptoMoveTitle": model.get("titleLabel"),
                    "cryptoMoveReason": model.get("reason"),
                    "activeInvestmentOpinion": active_opinion,
                    "ontologyRelationContext": relation_context,
                    "ontologyPromptContext": prompt_context,
                    "legacyFormulaAudits": [model.get("formulaAudit")],
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
        positions = {item.symbol.upper(): item.to_dict() for item in snapshot.positions if item.symbol and not item.is_cash()}
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
            provider = str(item.get("provider") or "OpenDART")
            holding_price_lines = self.holding_price_lines(positions.get(symbol_label) or {})
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
                    *holding_price_lines,
                    "접수일 " + str(item.get("receiptDate") or "-"),
                    "최근 공시 " + compact_number(number(item.get("count"))) + "건",
                    "출처 " + provider,
                ],
                symbol_label,
                criteria=self.criteria(
                    "OpenDART 접수번호가 직전 조회와 다를 때",
                    "접수번호 " + receipt + ", 접수일 " + str(item.get("receiptDate") or "-"),
                ),
                metadata={
                    "market": "KR",
                    "provider": provider,
                    "corpCode": str(item.get("corpCode") or ""),
                    "corpName": str(item.get("corpName") or symbol_label),
                    "reportName": str(item.get("reportName") or ""),
                    "receiptNo": receipt,
                    "receiptDate": str(item.get("receiptDate") or ""),
                    "disclosureCount": number(item.get("count")),
                },
            ))
        return events
