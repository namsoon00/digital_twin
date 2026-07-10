from typing import Dict, Iterable, List, Optional

from .market_data import clamp, number
from .ontology_prompt_registry import (
    DEFAULT_PROMPT_TEMPLATES,
    default_ai_prompt_policy_text,
    default_ai_prompt_templates_text,
    default_ontology_relation_rules_text,
)
from .ontology_relation_contracts import (
    AI_PROMPT_REGISTRY_VERSION,
    DEFAULT_RELATION_THRESHOLDS,
    ONTOLOGY_RULE_ENGINE_VERSION,
    DecisionStageDefinition,
    OntologyPromptTemplate,
    OntologyRuleMatch,
    RelationRuleDefinition,
    ScoreBandDefinition,
)
from .ontology_relation_facts import (
    _bp_text,
    _fx_context_line_from_facts,
    _has_numeric_fact,
    _number_text,
    _rate_context_line_from_facts,
    moving_average_distance_text,
    position_signal_facts,
    research_evidence_facts,
)
from .ontology_rule_catalog import (
    DECISION_LABEL_ALIASES,
    DECISION_STAGE_DEFINITIONS,
    DEFAULT_RELATION_RULES,
    SCORE_BANDS,
)
from .parsing import parse_assignments
from .portfolio import PortfolioSummary, Position


def parse_relation_rule_definitions_text(text: str) -> List[RelationRuleDefinition]:
    defaults = {item.rule_id: item for item in DEFAULT_RELATION_RULES}
    definitions: List[RelationRuleDefinition] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        rule_id = parts[0] if parts else ""
        if not rule_id:
            continue
        default = defaults.get(rule_id)
        label = parts[1] if len(parts) > 1 and parts[1] else (default.label if default else rule_id)
        condition = parts[2] if len(parts) > 2 and parts[2] else (default.condition_summary if default else "")
        relation_type = parts[3] if len(parts) > 3 and parts[3] else (default.relation_type if default else "CUSTOM_RELATION")
        signal_type = parts[4] if len(parts) > 4 and parts[4] else (default.signal_type if default else "custom")
        prompt_hint = " | ".join(parts[5:]).strip() if len(parts) > 5 else (default.prompt_hint if default else "")
        definitions.append(RelationRuleDefinition(
            rule_id=rule_id,
            label=label,
            version=default.version if default else "custom",
            relation_type=relation_type,
            signal_type=signal_type,
            condition_summary=condition,
            prompt_hint=prompt_hint,
            required_fields=list(default.required_fields if default else []),
        ))
    return definitions or list(DEFAULT_RELATION_RULES)


def relation_rule_definitions_from_settings(settings: Optional[Dict[str, object]] = None) -> List[RelationRuleDefinition]:
    settings = settings or {}
    text = str(settings.get("ontologyRelationRules") or "").strip()
    return parse_relation_rule_definitions_text(text) if text else list(DEFAULT_RELATION_RULES)


def _thresholds(settings: Optional[Dict[str, object]]) -> Dict[str, float]:
    settings = settings or {}
    legacy = parse_assignments(str(settings.get("alertThresholds") or ""), DEFAULT_RELATION_THRESHOLDS)
    configured = str(settings.get("relationRuleThresholds") or "").strip()
    if not configured:
        return legacy
    return parse_assignments(configured, legacy)


def relation_thresholds_from_settings(settings: Optional[Dict[str, object]] = None) -> Dict[str, float]:
    return _thresholds(settings)


def parse_ai_prompt_templates_text(text: str) -> List[OntologyPromptTemplate]:
    defaults = {item.prompt_id: item for item in DEFAULT_PROMPT_TEMPLATES}
    templates: List[OntologyPromptTemplate] = []
    current: Dict[str, str] = {}

    def flush_current() -> None:
        prompt_id = str(current.get("prompt_id") or "").strip()
        if not prompt_id:
            return
        default = defaults.get(prompt_id, DEFAULT_PROMPT_TEMPLATES[0])
        guardrails_text = str(current.get("guardrails") or "").strip()
        guardrails = [
            item.strip()
            for item in guardrails_text.replace("\n", " / ").split(" / ")
            if item.strip()
        ] or list(default.guardrails)
        templates.append(OntologyPromptTemplate(
            prompt_id=prompt_id,
            label=str(current.get("label") or default.label or prompt_id).strip(),
            version=str(current.get("version") or default.version or AI_PROMPT_REGISTRY_VERSION).strip(),
            purpose=str(current.get("purpose") or default.purpose or "").strip(),
            system_prompt=str(current.get("system") or default.system_prompt or "").strip(),
            user_prompt=str(current.get("user") or default.user_prompt or "").strip(),
            output_schema=dict(default.output_schema or {}),
            guardrails=guardrails,
        ))

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush_current()
            current = {"prompt_id": line[1:-1].strip()}
            continue
        if "=" not in line or not current:
            continue
        key, value = line.split("=", 1)
        current[key.strip()] = value.strip()
    flush_current()
    return templates or list(DEFAULT_PROMPT_TEMPLATES)


def prompt_templates_from_settings(settings: Optional[Dict[str, object]] = None) -> List[OntologyPromptTemplate]:
    settings = settings or {}
    text = str(settings.get("aiPromptTemplates") or "").strip()
    if not text:
        return list(DEFAULT_PROMPT_TEMPLATES)
    configured = parse_ai_prompt_templates_text(text)
    merged: Dict[str, OntologyPromptTemplate] = {item.prompt_id: item for item in DEFAULT_PROMPT_TEMPLATES}
    extra_order: List[str] = []
    for item in configured:
        if item.prompt_id not in merged:
            extra_order.append(item.prompt_id)
        merged[item.prompt_id] = item
    return [merged[item.prompt_id] for item in DEFAULT_PROMPT_TEMPLATES if item.prompt_id in merged] + [merged[key] for key in extra_order]


def prompt_template(prompt_id: str, settings: Optional[Dict[str, object]] = None) -> OntologyPromptTemplate:
    templates = prompt_templates_from_settings(settings)
    requested = str(prompt_id or "").strip()
    for item in templates:
        if item.prompt_id == requested:
            return item
    for item in templates:
        if item.prompt_id == "default":
            return item
    return templates[0]


def prompt_template_for_message_type(message_type: str, settings: Optional[Dict[str, object]] = None) -> OntologyPromptTemplate:
    return prompt_template(str(message_type or "").strip() or "default", settings)


def score_band(score: float) -> ScoreBandDefinition:
    value = float(score or 0)
    for band in SCORE_BANDS:
        if band.contains(value):
            return band
    return SCORE_BANDS[-1]


def strength_label(score: float) -> str:
    return score_band(score).label


def relation_score_meaning(score: float) -> str:
    return score_band(score).meaning


def decision_stage_by_key(stage_key: str) -> DecisionStageDefinition:
    return DECISION_STAGE_DEFINITIONS.get(stage_key, DECISION_STAGE_DEFINITIONS["HOLD_KEEP"])


def decision_action_group_for_label(label: object) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    stage_key = DECISION_LABEL_ALIASES.get(text)
    if not stage_key:
        for key, stage in DECISION_STAGE_DEFINITIONS.items():
            if text == stage.label:
                stage_key = key
                break
    if stage_key:
        return decision_stage_by_key(stage_key).action_group
    if "비트코인" in text or "크립토" in text or "민감도" in text:
        return "cryptoSensitivity"
    if any(term in text for term in ["손절", "손실", "분할축소"]):
        return "lossControl"
    if any(term in text for term in ["분할매도", "익절", "수익"]):
        return "profitTake"
    if "리밸런싱" in text:
        return "rebalance"
    if any(term in text for term in ["매수", "진입"]):
        return "entryRisk" if "보류" in text else "entry"
    if "금리" in text:
        return "rateRegime"
    if "환율" in text:
        return "fxRegime"
    if "공시" in text:
        return "disclosure"
    if "뉴스" in text and any(term in text for term in ["리스크", "대응", "부정"]):
        return "eventRisk"
    if "뉴스" in text and any(term in text for term in ["동조", "확인", "우호"]):
        return "eventConfirmation"
    if any(term in text for term in ["섹터", "피어"]):
        return "sectorContext"
    if any(term in text for term in ["보유", "관망", "관찰", "유지"]):
        return "holdWatch"
    return text


def _stage_for_score(review_stage: str, action_stage: str, score: float) -> DecisionStageDefinition:
    return decision_stage_by_key(action_stage if float(score or 0) >= 70 else review_stage)


def resolve_decision_stage(rule_id: str, score: float, facts: Dict[str, object]) -> DecisionStageDefinition:
    value = float(score or 0)
    pnl = float(facts.get("profitLossRate") or 0)
    loss_threshold = float(facts.get("lossThreshold") or DEFAULT_RELATION_THRESHOLDS["lossRateLow"])
    if rule_id == "trend.breakdown_acceleration.v1":
        return decision_stage_by_key("BREAKDOWN_ACCELERATION" if value >= 70 else "LOSS_REDUCE")
    if rule_id == "breakout.failure.v1":
        return decision_stage_by_key("BREAKOUT_FAILURE" if value >= 70 else "LOSS_REDUCE")
    if rule_id == "support.retest.failed.v1":
        return decision_stage_by_key("SUPPORT_RETEST_FAILED")
    if rule_id == "support.retest.confirmed.v1":
        return decision_stage_by_key("SUPPORT_RETEST")
    if rule_id == "trend.support_retest.v1":
        return decision_stage_by_key("SUPPORT_RETEST")
    if rule_id == "trend.recovery_attempt.v1":
        return decision_stage_by_key("RECOVERY_CONFIRM")
    if rule_id == "holding.loss_guard.breakdown.v1":
        if value >= 70 and pnl <= loss_threshold:
            return decision_stage_by_key("LOSS_CUT")
        if value >= 55:
            return decision_stage_by_key("LOSS_REDUCE")
        return decision_stage_by_key("LOSS_WATCH")
    if rule_id == "holding.profit_take.trend_weakness.v1":
        return _stage_for_score("PROFIT_PARTIAL", "PROFIT_SPLIT", value)
    if rule_id == "holding.concentration.rebalance.v1":
        return _stage_for_score("REBALANCE_REVIEW", "REBALANCE_ACTION", value)
    if rule_id == "factor.crowding.v1":
        return decision_stage_by_key("FACTOR_CROWDING")
    if rule_id == "liquidity.exit_capacity.v1":
        return _stage_for_score("LIQUIDITY_REVIEW", "LIQUIDITY_ACTION", value)
    if rule_id == "distribution.detected.v1":
        return decision_stage_by_key("DISTRIBUTION_REVIEW")
    if rule_id == "profit.protection.volatility.v1":
        return decision_stage_by_key("PROFIT_PROTECT")
    if rule_id == "data.conflict.v1":
        return decision_stage_by_key("DATA_CONFLICT")
    if rule_id == "macro.regime.shift.v1":
        return decision_stage_by_key("MACRO_REGIME")
    if rule_id == "rates.interest_rate.sensitivity.v1":
        return _stage_for_score("RATE_REVIEW", "RATE_ACTION", value)
    if rule_id == "fx.usd_krw.exposure.v1":
        return _stage_for_score("FX_REVIEW", "FX_ACTION", value)
    if rule_id == "external.crypto.btc_sensitivity.v1":
        return _stage_for_score("BTC_REVIEW", "BTC_REDUCE", value)
    if rule_id == "disclosure.material_event.v1":
        return decision_stage_by_key("DISCLOSURE_REVIEW")
    if rule_id == "news.direct_risk.price_confirmed.v1":
        return decision_stage_by_key("NEWS_RISK")
    if rule_id == "news.direct_support.price_confirmed.v1":
        return decision_stage_by_key("NEWS_CONFIRMATION")
    if rule_id == "news.sector_peer_context.v1":
        return decision_stage_by_key("SECTOR_NEWS")
    if rule_id == "holding.trend_flow.confirmation.v1":
        return decision_stage_by_key("FLOW_DEFENSE" if value >= 55 else "FLOW_WATCH")
    if rule_id == "entry.pullback.supported.v1":
        if value >= 70:
            return decision_stage_by_key("ENTRY_READY")
        if value >= 55:
            return decision_stage_by_key("ENTRY_SPLIT_BUY")
        return decision_stage_by_key("ENTRY_WATCH")
    if rule_id == "entry.momentum.confirmed.v1":
        if value >= 70:
            return decision_stage_by_key("ENTRY_READY")
        return decision_stage_by_key("ENTRY_SPLIT_BUY")
    if rule_id == "entry.wait_for_confirmation.v1":
        return decision_stage_by_key("ENTRY_WAIT")
    if rule_id == "entry.add_buy.blocked.v1":
        return decision_stage_by_key("ADD_BUY_BLOCKED")
    if rule_id == "averaging_down.block.v1":
        return decision_stage_by_key("ADD_BUY_BLOCKED")
    return decision_stage_by_key("HOLD_KEEP")


def relation_score_direction_meaning(delta: float) -> str:
    value = float(delta or 0)
    if abs(value) < 0.05:
        return "이전과 같은 수준의 대응 필요 강도"
    if value > 0:
        return "대응 필요 강도가 커졌다는 뜻이며, 가격 상승 예측 점수가 아닙니다"
    return "대응 필요 강도가 완화됐다는 뜻이며, 매수 신호는 아닙니다"

def _rule(rule_id: str, definitions: Optional[List[RelationRuleDefinition]] = None) -> RelationRuleDefinition:
    for item in definitions or DEFAULT_RELATION_RULES:
        if item.rule_id == rule_id:
            return item
    for item in DEFAULT_RELATION_RULES:
        if item.rule_id == rule_id:
            return item
    return DEFAULT_RELATION_RULES[-1]


def _match(
    rule_id: str,
    score: float,
    confidence: float,
    evidence: Iterable[str],
    missing: Iterable[str] = (),
    matched: bool = True,
    reference_only: bool = False,
    definitions: Optional[List[RelationRuleDefinition]] = None,
) -> OntologyRuleMatch:
    definition = _rule(rule_id, definitions)
    return OntologyRuleMatch(
        definition.rule_id,
        definition.label,
        definition.version,
        definition.relation_type,
        definition.signal_type,
        matched,
        clamp(score, 0.0, 100.0),
        strength_label(score),
        clamp(confidence, 0.0, 100.0),
        [str(item) for item in evidence if str(item or "").strip()],
        [str(item) for item in missing if str(item or "").strip()],
        reference_only,
        definition.prompt_hint,
    )


def decision_from_matches(facts: Dict[str, object], matches: List[OntologyRuleMatch]) -> Dict[str, object]:
    active = [item for item in matches if item.matched and not item.reference_only]
    if not active:
        stage = decision_stage_by_key("RELATION_WATCH")
        band = score_band(35.0)
        return {
            "label": stage.label,
            "tone": stage.tone,
            "score": 35.0,
            "basis": "ontologyRelationRules",
            "selectedRuleId": "",
            "decisionStage": stage.stage_key,
            "actionGroup": stage.action_group,
            "actionLevel": stage.action_level,
            "scoreBand": band.to_dict(),
            "nextStageAt": stage.next_stage_at,
        }
    priority = {
        "breakout.failure.v1": 48,
        "trend.breakdown_acceleration.v1": 45,
        "support.retest.failed.v1": 43,
        "holding.loss_guard.breakdown.v1": 40,
        "entry.wait_for_confirmation.v1": 39,
        "entry.momentum.confirmed.v1": 38,
        "entry.pullback.supported.v1": 38,
        "averaging_down.block.v1": 37,
        "distribution.detected.v1": 36,
        "profit.protection.volatility.v1": 36,
        "holding.profit_take.trend_weakness.v1": 35,
        "liquidity.exit_capacity.v1": 34,
        "news.direct_risk.price_confirmed.v1": 32,
        "disclosure.material_event.v1": 30,
        "news.direct_support.price_confirmed.v1": 28,
        "rates.interest_rate.sensitivity.v1": 27,
        "factor.crowding.v1": 19,
        "macro.regime.shift.v1": 27,
        "fx.usd_krw.exposure.v1": 26,
        "external.crypto.btc_sensitivity.v1": 25,
        "news.sector_peer_context.v1": 24,
        "data.conflict.v1": 23,
        "holding.concentration.rebalance.v1": 20,
        "entry.add_buy.blocked.v1": 18,
        "support.retest.confirmed.v1": 17,
        "trend.support_retest.v1": 16,
        "trend.recovery_attempt.v1": 14,
    }
    selected = max(active, key=lambda item: (priority.get(item.rule_id, 10), item.strength_score, item.confidence))
    stage = resolve_decision_stage(selected.rule_id, selected.strength_score, facts)
    band = score_band(selected.strength_score)
    tone = stage.tone
    if selected.rule_id == "holding.profit_take.trend_weakness.v1" and selected.strength_score >= 80:
        tone = "danger"
    return {
        "label": stage.label,
        "tone": tone,
        "score": round(float(selected.strength_score or 0), 1),
        "basis": "ontologyRelationRules",
        "selectedRuleId": selected.rule_id,
        "decisionStage": stage.stage_key,
        "actionGroup": stage.action_group,
        "actionLevel": stage.action_level,
        "scoreBand": band.to_dict(),
        "nextStageAt": stage.next_stage_at,
    }


def _append_unique(rows: List[str], value: object) -> None:
    text = str(value or "").strip()
    if text and text not in rows:
        rows.append(text)


def _active_rule_labels(matches: List[OntologyRuleMatch]) -> List[str]:
    return [
        item.label
        for item in matches or []
        if item.matched and not item.reference_only and str(item.label or "").strip()
    ]


def execution_plan_from_relation_context(
    facts: Dict[str, object],
    decision: Dict[str, object],
    matches: List[OntologyRuleMatch],
) -> Dict[str, object]:
    facts = facts or {}
    decision = decision or {}
    action_group = str(decision.get("actionGroup") or "")
    action_level = str(decision.get("actionLevel") or "")
    label = str(decision.get("label") or "")
    pnl = float(facts.get("profitLossRate") or 0)
    ma20_distance = float(facts.get("ma20Distance") or 0)
    ma60_distance = float(facts.get("ma60Distance") or 0)
    volume_ratio = float(facts.get("volumeRatio") or 0)
    trade_strength = float(facts.get("tradeStrength") or 0)
    bid_ask_imbalance = float(facts.get("bidAskImbalance") or 0)
    primary_action = "HOLD"
    primary_label = "보유 유지, 다음 데이터 확인"
    blocked_actions: List[str] = []
    risk_signals: List[str] = []
    support_signals: List[str] = []
    counter_signals: List[str] = []
    strengthen_conditions: List[str] = []
    weaken_conditions: List[str] = []
    next_checks: List[str] = []

    if action_group == "lossControl":
        primary_action = "TRIM_OR_SELL_REVIEW" if action_level in {"action", "urgent"} else "LOSS_CONTROL_REVIEW"
        primary_label = "추가매수 보류, 분할축소/매도 기준 검토"
        blocked_actions.append("20일선 회복 전 추가매수")
        _append_unique(risk_signals, "수익률 " + ("%.1f" % pnl) + "%")
        if ma20_distance < 0:
            _append_unique(risk_signals, "20일선보다 " + ("%.1f" % abs(ma20_distance)) + "% 낮음")
        if ma60_distance < 0:
            _append_unique(risk_signals, "60일선보다 " + ("%.1f" % abs(ma60_distance)) + "% 낮음")
            _append_unique(strengthen_conditions, "60일선 아래 상태가 유지되면 손절·축소 강도를 높임")
        else:
            _append_unique(counter_signals, "60일선보다 " + ("%.1f" % abs(ma60_distance)) + "% 높아 중기 지지는 남아 있음")
            _append_unique(strengthen_conditions, "60일선 아래로 내려가면 손절·축소 강도 상향")
        if volume_ratio and volume_ratio < 1:
            _append_unique(counter_signals, "거래량 " + ("%.1f" % volume_ratio) + "x로 평균 이하라 투매 확정은 아님")
        elif volume_ratio >= 1:
            _append_unique(risk_signals, "거래량 " + ("%.1f" % volume_ratio) + "x로 하락 확인 강도 상승")
        if trade_strength and trade_strength < 100:
            _append_unique(risk_signals, "체결강도 " + ("%.1f" % trade_strength) + "로 매수 체결 우위 부족")
        elif trade_strength >= 100:
            _append_unique(counter_signals, "체결강도 " + ("%.1f" % trade_strength) + "로 단기 매수 체결은 확인됨")
        if bid_ask_imbalance > 0:
            _append_unique(counter_signals, "호가잔량 매수 우위 " + ("%.1f" % bid_ask_imbalance) + "%")
        elif bid_ask_imbalance < 0:
            _append_unique(risk_signals, "호가잔량 매도 우위 " + ("%.1f" % abs(bid_ask_imbalance)) + "%")
        _append_unique(weaken_conditions, "20일선 회복과 거래량 동반 반등이 확인되면 축소 강도 완화")
        _append_unique(next_checks, "매도 가능 수량과 손절/분할축소 기준 확인")
        _append_unique(next_checks, "다음 조회에서도 손실 관리 규칙이 유지되는지 확인")
    elif action_group == "profitTake":
        primary_action = "TRIM_REVIEW"
        primary_label = "분할매도/수익 보호 기준 검토"
        _append_unique(risk_signals, "수익 구간에서 추세 약화")
        _append_unique(blocked_actions, "목표·추세 확인 없는 일괄 매도")
        _append_unique(strengthen_conditions, "20일선 회복 실패와 거래량 증가가 이어지면 분할매도 강도 상향")
        _append_unique(weaken_conditions, "20일선 회복과 수급 개선이 확인되면 보유 유지로 완화")
        _append_unique(next_checks, "목표 수익률, 분할매도 수량, 재진입 조건 확인")
    elif action_group == "rebalance":
        primary_action = "REBALANCE_REVIEW"
        primary_label = "초과 비중 축소와 리밸런싱 검토"
        _append_unique(blocked_actions, "같은 섹터 추가매수")
        _append_unique(risk_signals, "포트폴리오 집중도 확대")
        _append_unique(next_checks, "섹터 비중과 단일 종목 비중 한도 확인")
    elif action_group == "entry":
        primary_action = "SPLIT_BUY_REVIEW"
        primary_label = "소액 분할매수 조건 검토"
        _append_unique(blocked_actions, "확인 없는 일괄 매수")
        _append_unique(support_signals, "5일선 타이밍, 20/60일선, 거래량, 금리·환율 조건이 함께 통과")
        _append_unique(strengthen_conditions, "5일선 위 유지, 거래량 증가, 20/60일선 회복이 함께 유지되면 진입 강도 상향")
        _append_unique(weaken_conditions, "5일선 재이탈, 60일선 이탈, 금리·환율 부담 확대 또는 부정 공시가 나오면 매수 후보 해제")
        _append_unique(next_checks, "첫 진입 가격, 손절 기준, 추가매수 조건, 환율 기준 확인")
    elif action_group == "entryWait":
        primary_action = "WAIT_FOR_ENTRY_CONFIRMATION"
        primary_label = "신규 진입 대기, 조건 재확인"
        _append_unique(blocked_actions, "5일선·60일선·거래량·금리·환율 확인 전 신규 매수")
        for reason in list(facts.get("entryBlockReasons") or [])[:4]:
            _append_unique(risk_signals, reason)
        if ma20_distance >= 0:
            _append_unique(support_signals, "20일선 위라 단기 반등 관찰 근거는 있음")
        _append_unique(strengthen_conditions, "5일선 위 유지, 60일선 회복, 거래량 증가, 금리·환율 부담 완화가 같이 나오면 소액 진입 검토")
        _append_unique(weaken_conditions, "20일선 아래로 다시 내려가거나 거래량이 붙지 않으면 대기 유지")
        _append_unique(next_checks, "5일선·20일선·60일선 위치, 거래량 배율, 미국 10년 금리, USD/KRW를 다음 조회에서 확인")
    elif action_group == "entryRisk":
        primary_action = "AVOID_OR_WAIT"
        primary_label = "추가매수 보류, 회복 조건 대기"
        _append_unique(blocked_actions, "추세 회복 전 추가매수")
        _append_unique(risk_signals, "보유 종목의 추세 훼손 또는 이벤트 리스크")
        _append_unique(weaken_conditions, "20일선 회복과 부정 이벤트 해소 시 보류 강도 완화")
        _append_unique(next_checks, "회복 조건과 비중 한도 확인")
    elif action_group == "disclosure":
        primary_action = "EVENT_RISK_REVIEW"
        primary_label = "공시 원문과 가격 반응 확인"
        _append_unique(blocked_actions, "공시 성격 확인 전 비중 확대")
        _append_unique(risk_signals, "신규 공시가 보유 판단에 연결됨")
        _append_unique(strengthen_conditions, "공시 이후 거래량 증가와 20일선 이탈이 겹치면 방어 강도 상향")
        _append_unique(weaken_conditions, "공시 영향이 제한적이고 가격·수급이 회복되면 경계 강도 완화")
        _append_unique(next_checks, "공시 원문, 접수번호, 후속 정정 여부 확인")
    elif action_group == "eventRisk":
        primary_action = "EVENT_RISK_REVIEW"
        primary_label = "뉴스 리스크와 가격·수급 동조 확인"
        _append_unique(blocked_actions, "뉴스 영향 확인 전 추가매수")
        _append_unique(risk_signals, "직접 부정 뉴스가 가격·수급 반응과 연결됨")
        _append_unique(strengthen_conditions, "후속 보도와 거래량 증가, 20일선 이탈이 이어지면 방어 강도 상향")
        _append_unique(weaken_conditions, "뉴스 반박 또는 가격·수급 회복이 확인되면 경계 강도 완화")
        _append_unique(next_checks, "기사 원문, 출처 신뢰도, 후속 보도, 가격·거래량 동조 여부 확인")
    elif action_group == "eventConfirmation":
        primary_action = "CONFIRMATION_REVIEW"
        primary_label = "우호 뉴스의 가격·수급 확인"
        _append_unique(blocked_actions, "확인 없는 추격 매수")
        _append_unique(support_signals, "직접 우호 뉴스와 가격·수급 확인 신호가 연결됨")
        _append_unique(counter_signals, "우호 뉴스도 추세 재이탈 시 무효화될 수 있음")
        _append_unique(strengthen_conditions, "거래량 증가와 20일선 회복이 유지되면 우호 강도 상향")
        _append_unique(weaken_conditions, "가격 반응이 사라지거나 부정 뉴스가 추가되면 우호 강도 완화")
        _append_unique(next_checks, "뉴스 원문, 거래량 지속성, 체결강도, 다음 지지선 유지 확인")
    elif action_group == "sectorContext":
        primary_action = "SECTOR_CONTEXT_REVIEW"
        primary_label = "섹터·피어 뉴스의 전파 가능성 확인"
        _append_unique(blocked_actions, "직접 뉴스처럼 과대 해석")
        _append_unique(counter_signals, "직접 종목 뉴스가 아니라 섹터·피어 맥락")
        _append_unique(next_checks, "피어/섹터 뉴스가 대상 종목 가격·수급에 전파되는지 확인")
    elif action_group == "cryptoSensitivity":
        primary_action = "EXPOSURE_REVIEW"
        primary_label = "비트코인 민감 비중 점검"
        _append_unique(blocked_actions, "크립토 변동 안정 전 민감 종목 비중 확대")
        _append_unique(next_checks, "BTC 변화와 보유 종목 가격 반응의 시차 확인")
    elif action_group == "distributionRisk":
        primary_action = "TRIM_REVIEW"
        primary_label = "분산매도 가능성 점검"
        _append_unique(blocked_actions, "수급 확인 없는 추가매수")
        _append_unique(risk_signals, "가격 버팀과 매도 수급이 충돌")
        _append_unique(strengthen_conditions, "거래량 증가와 20일선 이탈이 겹치면 축소 강도 상향")
        _append_unique(weaken_conditions, "외국인·기관 순매수와 20일선 회복이 확인되면 방어 강도 완화")
        _append_unique(next_checks, "거래량 증가가 매집인지 분산인지 다음 체결/호가에서 확인")
    elif action_group == "executionRisk":
        primary_action = "SPLIT_EXECUTION_REVIEW"
        primary_label = "시장가보다 분할 실행 기준 검토"
        _append_unique(blocked_actions, "유동성 확인 없는 일괄 매도/매수")
        _append_unique(risk_signals, "거래대금 대비 포지션 또는 매도 가능 수량 제약")
        _append_unique(next_checks, "거래대금, 호가잔량, 매도 가능 수량으로 분할 수량을 계산")
    elif action_group == "factorRisk":
        primary_action = "EXPOSURE_REVIEW"
        primary_label = "섹터·팩터 과밀 노출 점검"
        _append_unique(blocked_actions, "같은 팩터 종목 동시 추가매수")
        _append_unique(risk_signals, "개별 종목 리스크가 포트폴리오 팩터 리스크로 전파")
        _append_unique(next_checks, "동일 섹터/팩터 보유 종목의 동방향 신호를 함께 확인")
    elif action_group == "dataQuality":
        primary_action = "DATA_REPAIR_REVIEW"
        primary_label = "데이터 충돌 해소 전 판단 강도 제한"
        _append_unique(blocked_actions, "누락 데이터 추정 기반 매매 판단")
        _append_unique(risk_signals, "출처 품질 또는 신선도 부족")
        _append_unique(next_checks, "가격·수급·외부 피드의 출처와 최신성을 먼저 복구")
    elif action_group == "macroRegime":
        primary_action = "REGIME_EXPOSURE_REVIEW"
        primary_label = "거시 레짐 민감도 점검"
        _append_unique(blocked_actions, "레짐 악화 중 민감 팩터 비중 확대")
        _append_unique(risk_signals, "금리·크립토·통화 레짐이 민감 종목에 전파")
        _append_unique(next_checks, "금리, 환율, BTC, 지수 반응을 다음 데이터에서 함께 확인")
    elif action_group == "rateRegime":
        primary_action = "RATE_EXPOSURE_REVIEW"
        primary_label = "금리 변화가 밸류에이션에 주는 영향 점검"
        _append_unique(blocked_actions, "금리 부담 확인 없이 성장/테마 비중 확대")
        _append_unique(risk_signals, "금리 상승 또는 수익률곡선 변화가 밸류에이션과 위험 선호에 전파")
        _append_unique(next_checks, "미국 10년 금리, 2년 금리, 10Y-2Y 스프레드와 보유 종목 반응을 함께 확인")
        _append_unique(weaken_conditions, "금리가 기준 아래로 내려가거나 종목 가격·수급이 금리 부담을 이겨내면 신호 약화")
    elif action_group == "fxRegime":
        primary_action = "FX_EXPOSURE_REVIEW"
        primary_label = "환율 효과와 실제 주가 흐름 분리"
        _append_unique(blocked_actions, "환율로 오른 평가액을 종목 자체 상승으로 단정")
        _append_unique(risk_signals, "원화 기준 평가액이 환율 변화에 흔들릴 수 있음")
        _append_unique(next_checks, "USD/KRW, 외화 노출 비중, 현지 통화 기준 주가 변화를 나눠 확인")
        _append_unique(weaken_conditions, "환율이 기준 구간으로 돌아오거나 외화 노출이 줄면 신호 약화")

    for item in _active_rule_labels(matches):
        if any(token in item for token in ["손실", "리스크", "하락", "공시", "집중", "부정"]):
            _append_unique(risk_signals, item)
        elif any(token in item for token in ["지지", "회복", "수급", "매수", "우호", "동조"]):
            _append_unique(support_signals, item)

    missing_impact = []
    for item in facts.get("missingData") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("label") or item.get("key") or "").strip()
        effect = str(item.get("effect") or "").strip()
        if name:
            missing_impact.append(name + (": " + effect if effect else "는 판단 강도를 낮춥니다."))

    return {
        "engineVersion": "ontology-execution-plan-v1",
        "tboxClass": "ExecutionPlan",
        "subject": {
            "symbol": facts.get("symbol"),
            "name": facts.get("name"),
            "market": facts.get("market"),
            "source": facts.get("source"),
        },
        "decisionStage": decision.get("decisionStage"),
        "actionGroup": action_group,
        "actionLevel": action_level,
        "decisionLabel": label,
        "primaryAction": primary_action,
        "primaryActionLabel": primary_label,
        "blockedActions": blocked_actions[:5],
        "riskSignals": risk_signals[:7],
        "supportSignals": support_signals[:5],
        "counterSignals": counter_signals[:5],
        "strengthenConditions": strengthen_conditions[:5],
        "weakenConditions": weaken_conditions[:5],
        "nextChecks": next_checks[:5],
        "missingDataImpact": missing_impact[:5],
        "sourceFacts": {
            "currentPrice": facts.get("currentPrice"),
            "averagePrice": facts.get("averagePrice"),
            "profitLossRate": facts.get("profitLossRate"),
            "ma5Distance": round(float(facts.get("ma5Distance") or 0), 2),
            "ma20Distance": round(float(facts.get("ma20Distance") or 0), 2),
            "ma60Distance": round(float(facts.get("ma60Distance") or 0), 2),
            "volumeRatio": facts.get("volumeRatio"),
            "tradeStrength": facts.get("tradeStrength"),
            "bidAskImbalance": facts.get("bidAskImbalance"),
            "sellableQuantity": facts.get("sellableQuantity"),
            "foreignBuyVolume": facts.get("foreignBuyVolume"),
            "foreignSellVolume": facts.get("foreignSellVolume"),
            "foreignNetVolume": facts.get("foreignNetVolume"),
            "institutionBuyVolume": facts.get("institutionBuyVolume"),
            "institutionSellVolume": facts.get("institutionSellVolume"),
            "institutionNetVolume": facts.get("institutionNetVolume"),
            "individualBuyVolume": facts.get("individualBuyVolume"),
            "individualSellVolume": facts.get("individualSellVolume"),
            "individualNetVolume": facts.get("individualNetVolume"),
            "positionToTradingValuePct": round(float(facts.get("positionToTradingValuePct") or 0), 2),
            "exitDaysAtTenPctADV": round(float(facts.get("exitDaysAtTenPctADV") or 0), 2),
            "liquidityRiskScore": round(float(facts.get("liquidityRiskScore") or 0), 1),
            "priceDeltaFromPreviousPct": round(float(facts.get("priceDeltaFromPreviousPct") or 0), 2),
            "profitLossRateDeltaPct": round(float(facts.get("profitLossRateDeltaPct") or 0), 2),
            "researchEvidenceCount": facts.get("researchEvidenceCount"),
            "directNewsCount": facts.get("directNewsCount"),
            "directRiskNewsCount": facts.get("directRiskNewsCount"),
            "directSupportNewsCount": facts.get("directSupportNewsCount"),
            "peerNewsCount": facts.get("peerNewsCount"),
            "sectorNewsCount": facts.get("sectorNewsCount"),
            "marketNewsCount": facts.get("marketNewsCount"),
            "averageNewsRelevanceScore": facts.get("averageNewsRelevanceScore"),
            "averageNewsSourceReliability": facts.get("averageNewsSourceReliability"),
            "topNewsTitles": list(facts.get("topNewsTitles") or [])[:5],
            "macroDgs10": facts.get("macroDgs10"),
            "macroDgs2": facts.get("macroDgs2"),
            "macroDff": facts.get("macroDff"),
            "macroYieldSpread10y2y": facts.get("macroYieldSpread10y2y"),
            "rateRegime": facts.get("rateRegime"),
            "yieldCurveRegime": facts.get("yieldCurveRegime"),
            "fxRatePair": facts.get("fxRatePair"),
            "fxRateToKrw": facts.get("fxRateToKrw"),
            "usdKrwRate": facts.get("usdKrwRate"),
            "fxExposureRatio": round(float(facts.get("fxExposureRatio") or 0), 2),
            "entryMa5TimingOk": facts.get("entryMa5TimingOk"),
            "entryMomentumTrendReady": facts.get("entryMomentumTrendReady"),
            "entrySupportCount": facts.get("entrySupportCount"),
            "entryMacroBlocked": facts.get("entryMacroBlocked"),
            "entryFxBlocked": facts.get("entryFxBlocked"),
            "entryRequiredDataMissing": facts.get("entryRequiredDataMissing"),
            "entryBlockReasons": list(facts.get("entryBlockReasons") or [])[:6],
            "fxRegime": facts.get("fxRegime"),
        },
    }


def build_ai_prompt_context(
    prompt_id: str,
    facts: Dict[str, object],
    matches: List[OntologyRuleMatch],
    settings: Optional[Dict[str, object]] = None,
    execution_plan: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    settings = settings or {}
    template = prompt_template(prompt_id, settings)
    policy = str(settings.get("aiPromptPolicy") or default_ai_prompt_policy_text()).strip()
    return {
        "promptVersion": template.version,
        "promptRegistryVersion": AI_PROMPT_REGISTRY_VERSION,
        "promptId": template.prompt_id,
        "promptTemplate": template.to_dict(),
        "promptPolicy": policy,
        "inputContract": {
            "subject": {
                "symbol": facts.get("symbol"),
                "name": facts.get("name"),
                "market": facts.get("market"),
                "sector": facts.get("sector"),
            },
            "requiredBlocks": ["facts", "trendDynamics", "researchEvidence", "matchedRules", "missingData", "deliveryContext"],
            "forbidden": ["inventing_missing_market_data", "mixing_delivery_priority_with_investment_judgment"],
        },
        "outputSchema": {
            "activeInvestmentOpinion": {
                "action": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
                "conviction": "number 0-100",
                "thesis": "string",
                "evidence": ["ResearchEvidence"],
                "counterEvidence": ["ResearchEvidence"],
                "invalidationCondition": "string",
                "sourceUrls": ["string"],
                "executionPlan": "ExecutionPlan",
            }
        },
        "facts": dict(facts or {}),
        "trendDynamics": dict(facts.get("trendDynamics") or {}),
        "executionPlan": dict(execution_plan or {}),
        "matchedRules": [item.to_dict() for item in matches if item.matched],
        "missingData": list(facts.get("missingData") or []),
        "guardrails": list(template.guardrails),
    }


def evaluate_position_relation_rules(
    position: Position,
    portfolio: PortfolioSummary,
    external_signals: Optional[Dict[str, object]] = None,
    settings: Optional[Dict[str, object]] = None,
    legacy_model: Optional[Dict[str, object]] = None,
    prompt_id: str = "holdingTiming",
    previous_state: Optional[Dict[str, object]] = None,
    previous_decision: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    settings = settings or {}
    relation_definitions = relation_rule_definitions_from_settings(settings)
    thresholds = _thresholds(settings)
    facts = position_signal_facts(position, portfolio, external_signals, previous_state, previous_decision)
    missing_labels = [str(item.get("label") or item.get("key") or "") for item in facts.get("missingData") or []]
    matches: List[OntologyRuleMatch] = []
    data_quality = float(facts.get("dataQualityScore") or 0)
    pnl = float(facts.get("profitLossRate") or 0)
    ma5_distance = float(facts.get("ma5Distance") or 0)
    ma20_distance = float(facts.get("ma20Distance") or 0)
    ma60_distance = float(facts.get("ma60Distance") or 0)
    sector_ratio = float(facts.get("sectorRatio") or 0)
    position_weight = float(facts.get("positionWeight") or 0)
    trend_score = float(facts.get("trendScore") or 0)
    flow_score = float(facts.get("investorFlowScore") or 0)
    btc_change24h = float(facts.get("btcChange24h") or 0)
    btc_change7d = float(facts.get("btcChange7d") or 0)
    source = str(facts.get("source") or "holding").strip()
    is_holding = bool(facts.get("isHolding"))
    volume_ratio = float(facts.get("volumeRatio") or 0)
    trade_strength = float(facts.get("tradeStrength") or 0)
    bid_ask_imbalance = float(facts.get("bidAskImbalance") or 0)
    ma20_slope = float(facts.get("ma20Slope") or 0)
    ma60_slope = float(facts.get("ma60Slope") or 0)
    price_change = float(facts.get("priceChangeRate") or 0)
    trend_curve = float(facts.get("trendCurve") or 0)
    trend_dynamic_risk = float(facts.get("trendDynamicRiskScore") or 0)
    support_retest = bool(facts.get("supportRetest"))
    recovery_attempt = bool(facts.get("recoveryAttempt"))
    breakdown_acceleration = bool(facts.get("breakdownAcceleration"))
    disclosure = facts.get("dartDisclosure")
    has_disclosure = isinstance(disclosure, dict) and bool(disclosure)
    news = facts.get("newsHeadlines")
    direct_news_count = int(number(facts.get("directNewsCount")))
    direct_risk_news_count = int(number(facts.get("directRiskNewsCount")))
    direct_support_news_count = int(number(facts.get("directSupportNewsCount")))
    peer_news_count = int(number(facts.get("peerNewsCount")))
    sector_news_count = int(number(facts.get("sectorNewsCount")))
    market_news_count = int(number(facts.get("marketNewsCount")))
    material_news_count = int(number(facts.get("materialNewsCount")))
    has_news = bool(
        (isinstance(news, dict) and bool(news.get("items") or news.get("count")))
        or material_news_count
        or direct_news_count
    )
    previous_ma20_distance = float(facts.get("previousMa20Distance") or 0)
    previous_ma60_distance = float(facts.get("previousMa60Distance") or 0)
    has_previous_state = bool(facts.get("hasPreviousState"))
    price_delta_previous = float(facts.get("priceDeltaFromPreviousPct") or 0)
    ma20_delta_previous = float(facts.get("ma20DistanceDeltaPct") or 0)
    ma60_delta_previous = float(facts.get("ma60DistanceDeltaPct") or 0)
    position_to_trading_value = float(facts.get("positionToTradingValuePct") or 0)
    liquidity_risk = float(facts.get("liquidityRiskScore") or 0)
    external_quality = float(facts.get("externalSignalQualityScore") or 0)
    external_errors = float(facts.get("externalSignalErrorCount") or 0)
    macro_spread = float(facts.get("macroYieldSpread10y2y") or 0)
    macro_dgs10 = float(facts.get("macroDgs10") or 0)
    macro_dgs2 = float(facts.get("macroDgs2") or 0)
    macro_dgs10_delta_bp = float(facts.get("macroDgs10DeltaBp") or 0)
    macro_dgs2_delta_bp = float(facts.get("macroDgs2DeltaBp") or 0)
    macro_dff_delta_bp = float(facts.get("macroDffDeltaBp") or 0)
    macro_spread_delta_bp = float(facts.get("macroYieldSpreadDeltaBp") or 0)
    currency = str(facts.get("currency") or "").upper().strip()
    usd_krw_rate = float(facts.get("usdKrwRate") or 0)
    usd_krw_delta_krw = float(facts.get("usdKrwDeltaKrw") or 0)
    usd_krw_delta_pct = float(facts.get("usdKrwDeltaPct") or 0)
    usd_krw_7d_delta_krw = float(facts.get("usdKrw7dDeltaKrw") or 0)
    usd_krw_7d_delta_pct = float(facts.get("usdKrw7dDeltaPct") or 0)
    fx_rate_to_krw = float(facts.get("fxRateToKrw") or 0)
    fx_exposure_ratio = float(facts.get("fxExposureRatio") or 0)
    has_rate_signals = bool(facts.get("hasInterestRateSignals"))
    has_rate_delta_signal = bool(facts.get("hasInterestRateDeltaSignal"))
    has_fx_rate_signal = bool(facts.get("hasFxRateSignal"))
    has_fx_delta_signal = bool(facts.get("hasFxDeltaSignal"))
    rate_high_threshold = float(thresholds.get("macroRateHighPct", 4.5) or 4.5)
    rate_low_threshold = float(thresholds.get("macroRateLowPct", 3.0) or 3.0)
    curve_inversion_threshold = float(thresholds.get("macroCurveInversionPct", 0.0) or 0.0)
    rate_delta_threshold = float(
        thresholds.get("macroRateDeltaBp", thresholds.get("externalMacroRateDeltaBp", 15.0)) or 0.0
    )
    usd_krw_high = float(thresholds.get("usdKrwHigh", 1450.0) or 1450.0)
    usd_krw_low = float(thresholds.get("usdKrwLow", 1300.0) or 1300.0)
    usd_krw_delta_krw_threshold = float(thresholds.get("usdKrwDeltaKrw", 15.0) or 0.0)
    usd_krw_delta_pct_threshold = float(thresholds.get("usdKrwDeltaPct", 1.0) or 0.0)
    usd_krw_7d_delta_krw_threshold = float(thresholds.get("usdKrw7dDeltaKrw", 30.0) or 0.0)
    usd_krw_7d_delta_pct_threshold = float(thresholds.get("usdKrw7dDeltaPct", 2.0) or 0.0)
    fx_exposure_review = float(thresholds.get("fxExposureReview", 5.0) or 5.0)
    fx_exposure_high = float(thresholds.get("fxExposureHigh", 10.0) or 10.0)
    macro_sensitive = any(token in str(facts.get("sector") or "") for token in ["반도체", "AI", "플랫폼", "디지털자산"]) or currency == "USD" or facts.get("isBtcSensitive")
    rate_sensitive = macro_sensitive or any(token in str(facts.get("sector") or "") for token in ["성장", "소프트웨어", "테크", "바이오"])
    high_rate_active = bool(has_rate_signals and macro_dgs10 and macro_dgs10 >= rate_high_threshold)
    low_rate_active = bool(has_rate_signals and macro_dgs10 and macro_dgs10 <= rate_low_threshold)
    inverted_curve_active = bool(has_rate_signals and macro_spread < curve_inversion_threshold)
    rate_delta_magnitude = max(
        abs(macro_dgs10_delta_bp),
        abs(macro_dgs2_delta_bp),
        abs(macro_dff_delta_bp),
        abs(macro_spread_delta_bp),
    )
    rate_delta_active = bool(
        has_rate_delta_signal
        and (not rate_delta_threshold or rate_delta_magnitude >= rate_delta_threshold)
    )
    fx_extreme_regime = bool(
        currency == "USD"
        and usd_krw_rate
        and (usd_krw_rate >= usd_krw_high or usd_krw_rate <= usd_krw_low)
    )
    fx_delta_active = bool(
        has_fx_delta_signal
        and (
            (not usd_krw_delta_krw_threshold or abs(usd_krw_delta_krw) >= usd_krw_delta_krw_threshold)
            or (not usd_krw_delta_pct_threshold or abs(usd_krw_delta_pct) >= usd_krw_delta_pct_threshold)
            or (not usd_krw_7d_delta_krw_threshold or abs(usd_krw_7d_delta_krw) >= usd_krw_7d_delta_krw_threshold)
            or (not usd_krw_7d_delta_pct_threshold or abs(usd_krw_7d_delta_pct) >= usd_krw_7d_delta_pct_threshold)
        )
    )
    fx_extreme_active = bool(fx_delta_active and fx_extreme_regime)
    fx_exposure_active = bool(fx_delta_active and has_fx_rate_signal and currency != "KRW" and fx_exposure_ratio >= fx_exposure_high)
    entry_macro_blocked = bool(rate_sensitive and (high_rate_active or inverted_curve_active or rate_delta_active))
    entry_fx_blocked = bool(currency == "USD" and has_fx_rate_signal and (fx_extreme_regime or fx_delta_active or fx_exposure_active))
    entry_macro_missing = bool(rate_sensitive and not has_rate_signals)
    entry_fx_missing = bool(currency == "USD" and not has_fx_rate_signal)
    facts["entryMacroBlocked"] = entry_macro_blocked
    facts["entryFxBlocked"] = entry_fx_blocked
    facts["entryMacroMissing"] = entry_macro_missing
    facts["entryFxMissing"] = entry_fx_missing

    if pnl >= 10 and (ma20_distance <= -2 or ma60_distance <= -5 or trend_score < -3):
        score = 55 + min(25, max(0, pnl - 10) * 1.2) + min(20, abs(min(ma20_distance, ma60_distance, trend_score)))
        matches.append(_match(
            "holding.profit_take.trend_weakness.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    news_relevance = float(facts.get("averageNewsRelevanceScore") or 0)
    news_reliability = float(facts.get("averageNewsSourceReliability") or 0)
    news_materiality = float(facts.get("averageNewsMaterialityScore") or 0)
    top_news_event_types = [str(item) for item in list(facts.get("topNewsEventTypes") or [])[:3] if str(item or "").strip()]
    news_confidence = min(data_quality, 55 + min(40, news_reliability * 45)) if news_reliability else data_quality
    risk_news_confirmation_count = sum(
        1
        for value in [
            price_change <= -1.0,
            ma20_distance < 0,
            volume_ratio >= 1.2,
            trend_score < -4,
            flow_score <= -10 or bid_ask_imbalance <= -10,
        ]
        if value
    )
    if direct_risk_news_count and risk_news_confirmation_count >= 1:
        score = (
            54
            + min(14, direct_risk_news_count * 4)
            + min(12, news_relevance * 0.12)
            + min(8, news_materiality * 0.08)
            + min(12, risk_news_confirmation_count * 4)
            + (6 if price_change <= -2 else 0)
            + (5 if ma20_distance <= -5 else 0)
        )
        matches.append(_match(
            "news.direct_risk.price_confirmed.v1",
            score,
            news_confidence,
            [
                "직접 부정 뉴스 " + str(direct_risk_news_count) + "건",
                "평균 관련성 " + ("%.1f" % news_relevance) + "점",
                "평균 중요도 " + ("%.1f" % news_materiality) + "점" if news_materiality else "",
                "사건 유형 " + " · ".join(top_news_event_types) if top_news_event_types else "",
                "최신 직접 뉴스 " + ("%.1f" % float(facts.get("latestDirectNewsAgeMinutes") or 0)) + "분 전" if facts.get("latestDirectNewsAgeMinutes") else "",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "",
                moving_average_distance_text("20일선", ma20_distance),
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("directRiskNewsTitles") or [])[:2]),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    support_news_confirmation_count = sum(
        1
        for value in [
            price_change >= 1.0,
            volume_ratio >= 1.2,
            trade_strength >= 100,
            ma20_distance >= -2,
            flow_score >= 10 or bid_ask_imbalance >= 5,
        ]
        if value
    )
    if direct_support_news_count and support_news_confirmation_count >= 1:
        score = (
            50
            + min(14, direct_support_news_count * 4)
            + min(12, news_relevance * 0.12)
            + min(8, news_materiality * 0.08)
            + min(12, support_news_confirmation_count * 4)
            + (6 if price_change >= 2 else 0)
        )
        matches.append(_match(
            "news.direct_support.price_confirmed.v1",
            score,
            news_confidence,
            [
                "직접 우호 뉴스 " + str(direct_support_news_count) + "건",
                "평균 관련성 " + ("%.1f" % news_relevance) + "점",
                "평균 중요도 " + ("%.1f" % news_materiality) + "점" if news_materiality else "",
                "사건 유형 " + " · ".join(top_news_event_types) if top_news_event_types else "",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "",
                "체결강도 " + ("%.1f" % trade_strength) if trade_strength else "",
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("directSupportNewsTitles") or [])[:2]),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    sector_context_news_count = peer_news_count + sector_news_count + market_news_count
    sector_context_confirmed = sector_context_news_count > 0 and (
        abs(price_change) >= 1.5 or volume_ratio >= 1.2 or abs(trend_score) >= 4
    )
    if sector_context_news_count:
        score = (
            36
            + min(10, sector_context_news_count * 2.5)
            + min(10, news_relevance * 0.1)
            + min(6, news_materiality * 0.06)
            + (8 if sector_context_confirmed else 0)
        )
        matches.append(_match(
            "news.sector_peer_context.v1",
            score,
            news_confidence,
            [
                "피어 뉴스 " + str(peer_news_count) + "건",
                "섹터 뉴스 " + str(sector_news_count) + "건",
                "시장 뉴스 " + str(market_news_count) + "건",
                "사건 유형 " + " · ".join(top_news_event_types) if top_news_event_types else "",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("sectorNewsTitles") or [])[:2]),
            ],
            missing_labels,
            reference_only=not sector_context_confirmed,
            definitions=relation_definitions,
        ))

    entry_ma20_below = float(thresholds.get("entryPullbackMa20BelowPct", -2.0) or -2.0)
    entry_ma20_deep = float(thresholds.get("entryPullbackMa20DeepPct", -8.0) or -8.0)
    entry_ma5_min = float(thresholds.get("entryMa5TimingMinPct", -0.5) or -0.5)
    entry_momentum_ma20_min = float(thresholds.get("entryMomentumMa20MinPct", -0.5) or -0.5)
    entry_momentum_ma60_min = float(thresholds.get("entryMomentumMa60MinPct", 0.0) or 0.0)
    entry_ma60_support = float(thresholds.get("entryMa60SupportPct", -1.0) or -1.0)
    entry_volume_min = float(thresholds.get("entryVolumeMinRatio", 0.6) or 0.0)
    entry_volume_max = float(thresholds.get("entryVolumeMaxRatio", 1.8) or 0.0)
    entry_smart_money_min = float(thresholds.get("entrySmartMoneyMin", 10.0) or 0.0)
    entry_trade_strength_min = float(thresholds.get("entryTradeStrengthMin", 100.0) or 0.0)
    entry_orderbook_min = float(thresholds.get("entryOrderbookImbalanceMin", 5.0) or 0.0)
    entry_position_max = float(thresholds.get("entryMaxPositionWeight", 20.0) or 0.0)
    entry_sector_max = float(thresholds.get("entryMaxSectorWeight", 45.0) or 0.0)
    pullback_zone = entry_ma20_deep <= ma20_distance <= entry_ma20_below
    ma5_supports_entry = bool(facts.get("ma5")) and ma5_distance >= entry_ma5_min
    ma60_supports_entry = bool(facts.get("ma60")) and ma60_distance >= entry_ma60_support
    ma20_momentum_ready = bool(facts.get("ma20")) and ma20_distance >= entry_momentum_ma20_min
    ma60_momentum_ready = bool(facts.get("ma60")) and ma60_distance >= entry_momentum_ma60_min
    volume_is_usable = bool(volume_ratio) and volume_ratio >= entry_volume_min and (not entry_volume_max or volume_ratio <= entry_volume_max)
    smart_money_supports = bool(flow_score) and flow_score >= entry_smart_money_min
    execution_supports = bool(trade_strength) and trade_strength >= entry_trade_strength_min
    orderbook_supports = bool(bid_ask_imbalance) and bid_ask_imbalance >= entry_orderbook_min
    allocation_room = (not position_weight or position_weight <= entry_position_max) and (not sector_ratio or sector_ratio <= entry_sector_max)
    entry_support_count = sum(
        1
        for value in [
            ma5_supports_entry,
            volume_is_usable,
            smart_money_supports,
            execution_supports,
            orderbook_supports,
            direct_support_news_count > 0,
        ]
        if value
    )
    entry_data_blocked = bool((external_quality and external_quality < 60) or external_errors >= 2)
    entry_external_risk_blocked = bool(has_disclosure or direct_risk_news_count or entry_macro_blocked or entry_fx_blocked or entry_data_blocked)
    entry_required_data_missing = bool(not facts.get("ma5") or entry_macro_missing or entry_fx_missing)
    entry_block_reasons = [
        reason
        for reason in [
            "5일선 타이밍 미확인" if not facts.get("ma5") else "",
            "5일선보다 낮아 짧은 진입 타이밍 부족" if facts.get("ma5") and not ma5_supports_entry else "",
            "60일선 지지 부족" if not ma60_supports_entry else "",
            "거래량 확인 부족" if not volume_is_usable else "",
            "금리 부담 또는 금리 변화 확인 필요" if entry_macro_blocked else "",
            "금리 데이터 없음" if entry_macro_missing else "",
            "환율 부담 또는 환율 변화 확인 필요" if entry_fx_blocked else "",
            "환율 데이터 없음" if entry_fx_missing else "",
            "공시/부정 뉴스 리스크" if has_disclosure or direct_risk_news_count else "",
            "외부 데이터 품질 확인 필요" if entry_data_blocked else "",
            "보유 손실 구간이라 추가매수보다 손실 관리 우선" if is_holding and pnl < 0 else "",
        ]
        if reason
    ]
    facts["entryPullbackZone"] = pullback_zone
    facts["entryMa5TimingOk"] = ma5_supports_entry
    facts["entryMomentumTrendReady"] = ma20_momentum_ready and ma60_momentum_ready
    facts["entrySupportCount"] = entry_support_count
    facts["entryAllocationRoom"] = allocation_room
    facts["entryExternalRiskBlocked"] = entry_external_risk_blocked
    facts["entryRequiredDataMissing"] = entry_required_data_missing
    facts["entryBlockReasons"] = entry_block_reasons
    pullback_entry_ready = (
        pullback_zone
        and ma5_supports_entry
        and ma60_supports_entry
        and allocation_room
        and entry_support_count >= 3
        and not entry_external_risk_blocked
        and not entry_required_data_missing
        and (source == "watchlist" or pnl >= 0)
    )
    momentum_entry_ready = (
        source == "watchlist"
        and ma5_supports_entry
        and ma20_momentum_ready
        and ma60_momentum_ready
        and volume_is_usable
        and allocation_room
        and entry_support_count >= 3
        and not entry_external_risk_blocked
        and not entry_required_data_missing
    )
    if (
        pullback_entry_ready
    ):
        score = (
            52
            + min(14, entry_support_count * 4)
            + (8 if smart_money_supports else 0)
            + (6 if execution_supports or orderbook_supports else 0)
            + (6 if source == "watchlist" else 0)
        )
        matches.append(_match(
            "entry.pullback.supported.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("5일선", ma5_distance),
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "거래량 배율 미확인",
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "체결강도 " + ("%.1f" % trade_strength) if trade_strength else "",
                "호가 불균형 " + ("%.1f" % bid_ask_imbalance) + "%" if bid_ask_imbalance else "",
                "보유 비중 " + ("%.1f" % position_weight) + "%",
                "업종 비중 " + ("%.1f" % sector_ratio) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if momentum_entry_ready:
        score = (
            58
            + min(16, entry_support_count * 4)
            + (8 if ma20_distance >= 0 else 0)
            + (8 if ma60_distance >= 0 else 0)
            + (6 if direct_support_news_count else 0)
        )
        matches.append(_match(
            "entry.momentum.confirmed.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("5일선", ma5_distance),
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "확인 신호 " + str(entry_support_count) + "/6",
                "금리·환율 진입 차단 없음",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    wait_for_entry_confirmation = (
        source == "watchlist"
        and not pullback_entry_ready
        and not momentum_entry_ready
        and (
            ma20_distance >= -2
            or direct_support_news_count > 0
            or has_news
            or entry_macro_blocked
            or entry_fx_blocked
            or entry_required_data_missing
            or volume_ratio < entry_volume_min
        )
    )
    if wait_for_entry_confirmation:
        score = 50 + min(12, len(entry_block_reasons) * 3) + (8 if direct_support_news_count else 0) + (6 if ma20_distance >= 0 else 0)
        matches.append(_match(
            "entry.wait_for_confirmation.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("5일선", ma5_distance) if facts.get("ma5") else "5일선 미확인",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "거래량 배율 미확인",
                "대기 사유 " + " · ".join(entry_block_reasons[:4]) if entry_block_reasons else "확인 조건 부족",
                "매수보다 5일선·60일선·거래량·금리·환율 재확인 우선",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    add_buy_risk = (
        is_holding
        and (
            (pnl < 0 and ma20_distance <= entry_ma20_below)
            or ma60_distance < entry_ma60_support
            or has_disclosure
            or has_news and pnl < 0 and ma20_distance < 0
            or entry_macro_blocked
            or entry_fx_blocked
        )
    )
    if add_buy_risk:
        score = 52
        if pnl < 0:
            score += min(12, abs(pnl) * 1.2)
        if ma20_distance <= entry_ma20_deep:
            score += 12
        elif ma20_distance <= entry_ma20_below:
            score += 7
        if ma60_distance < entry_ma60_support:
            score += 8
        if has_disclosure:
            score += 10
        if has_news:
            score += 4
        matches.append(_match(
            "entry.add_buy.blocked.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "신규 공시 있음" if has_disclosure else "",
                "관련 뉴스 있음" if has_news else "",
                "금리 부담 있음" if entry_macro_blocked else "",
                "환율 부담 있음" if entry_fx_blocked else "",
                "추가매수보다 회복 조건 확인 우선",
            ],
            missing_labels,
                definitions=relation_definitions,
        ))

    averaging_support_count = sum(
        1
        for value in [
            volume_ratio >= 1.0,
            trade_strength >= 100,
            bid_ask_imbalance >= 5,
            flow_score >= 10,
            recovery_attempt,
        ]
        if value
    )
    avg_loss_threshold = float(thresholds.get("lossRateLow", -8.0) or -8.0)
    avg_loss_buffer = abs(float(thresholds.get("lossRateBufferPct", 1.0) or 0.0))
    weak_near_loss_for_averaging = (
        pnl <= avg_loss_threshold
        and avg_loss_threshold - pnl <= avg_loss_buffer
        and ma60_distance > 0
        and volume_ratio < float(thresholds.get("lossGuardVolumeConfirmRatio", 0.8) or 0.8)
        and flow_score > -15
    )
    if is_holding and pnl < 0 and ma20_distance < 0 and averaging_support_count < 2 and not weak_near_loss_for_averaging:
        score = 55 + min(18, abs(pnl) * 1.2) + min(12, abs(ma20_distance) * 1.1) + (8 if ma60_distance < 0 else 0)
        matches.append(_match(
            "averaging_down.block.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("20일선", ma20_distance),
                "확인 신호 " + str(averaging_support_count) + "/5",
                "추가매수보다 보유 이유 재확인 우선",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    loss_threshold = float(thresholds.get("lossRateLow", -8.0) or -8.0)
    loss_buffer = abs(float(thresholds.get("lossRateBufferPct", 1.0) or 0.0))
    volume_confirm_ratio = float(thresholds.get("lossGuardVolumeConfirmRatio", 0.8))
    ma60_support_threshold = float(thresholds.get("lossGuardMa60SupportPct", 0.0) or 0.0)
    weak_evidence_penalty = float(thresholds.get("lossGuardWeakEvidencePenalty", 30.0) or 0.0)
    facts["lossThreshold"] = loss_threshold
    facts["lossRateBufferPct"] = loss_buffer
    if pnl < 0 and (pnl <= loss_threshold or ma20_distance <= -5):
        volume_ratio = float(facts.get("volumeRatio") or 0)
        loss_depth = max(0.0, loss_threshold - pnl) if pnl <= loss_threshold else 0.0
        near_loss_threshold = pnl <= loss_threshold and loss_depth <= loss_buffer
        ma60_holds = bool(facts.get("ma60")) and ma60_distance > ma60_support_threshold
        volume_confirms = volume_ratio >= volume_confirm_ratio
        sell_flow_confirms = float(facts.get("sellShare") or 0) >= 56
        flow_confirms = flow_score <= -15
        slope_confirms = float(facts.get("ma20Slope") or 0) <= -1 or float(facts.get("ma60Slope") or 0) <= -0.5
        ma60_breaks = bool(facts.get("ma60")) and ma60_distance <= ma60_support_threshold
        confirmation_count = sum(1 for value in [ma60_breaks, volume_confirms, sell_flow_confirms, flow_confirms, slope_confirms] if value)
        weak_near_threshold = (
            near_loss_threshold
            and ma20_distance <= -5
            and ma60_holds
            and not volume_confirms
            and not sell_flow_confirms
            and not flow_confirms
        )
        score = 58 + min(24, abs(min(pnl, loss_threshold)) * 1.5) + (10 if ma20_distance <= -5 else 0)
        if weak_near_threshold:
            score -= weak_evidence_penalty
        matches.append(_match(
            "holding.loss_guard.breakdown.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                "손실 기준 " + ("%.1f" % loss_threshold) + "%",
                "손실 완충 " + ("%.1f" % loss_buffer) + "%p",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "확인 신호 " + str(confirmation_count) + "/5",
                ("약한 확인 신호 감점 -" + ("%.1f" % weak_evidence_penalty) + "점") if weak_near_threshold else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if support_retest:
        score = (
            50
            + min(16, abs(ma20_distance) * 1.1)
            + (7 if ma60_distance >= 0 else 3)
            + (4 if price_change >= 0 else 0)
            - (8 if breakdown_acceleration else 0)
        )
        matches.append(_match(
            "trend.support_retest.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "가격 모멘텀 " + str(facts.get("priceMomentumLabel") or "-") + " (" + ("%.1f" % price_change) + "%)",
                "기울기 " + str(facts.get("trendSlopeLabel") or "-"),
                "추세 커브 " + str(facts.get("trendCurveLabel") or "-") + " (" + ("%.1f" % trend_curve) + ")",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    support_confirmation_count = sum(
        1
        for value in [
            support_retest,
            price_change >= 0,
            trade_strength >= 100,
            bid_ask_imbalance >= 5,
            flow_score >= 10,
        ]
        if value
    )
    if support_retest and support_confirmation_count >= 3 and not breakdown_acceleration:
        score = 50 + min(20, support_confirmation_count * 5) + (5 if ma60_distance >= 0 else 0)
        matches.append(_match(
            "support.retest.confirmed.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("60일선", ma60_distance),
                "확인 신호 " + str(support_confirmation_count) + "/5",
                "체결강도 " + ("%.1f" % trade_strength) if trade_strength else "",
                "호가 불균형 " + ("%.1f" % bid_ask_imbalance) + "%" if bid_ask_imbalance else "",
            ],
            missing_labels,
            reference_only=True,
            definitions=relation_definitions,
        ))
    if recovery_attempt:
        score = (
            48
            + (8 if price_change >= 1.0 else 0)
            + min(8, max(0.0, ma20_slope) * 8.0)
            + min(8, max(0.0, trend_curve) * 5.0)
            + (5 if ma60_distance >= 0 else 0)
        )
        matches.append(_match(
            "trend.recovery_attempt.v1",
            score,
            data_quality,
            [
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "20일선 기울기 " + ("%.1f" % ma20_slope) + "%",
                "60일선 기울기 " + ("%.1f" % ma60_slope) + "%",
                "추세 커브 " + ("%.1f" % trend_curve),
                moving_average_distance_text("60일선", ma60_distance),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if has_previous_state and previous_ma20_distance >= 0 and ma20_distance < 0 and (price_change <= -1.0 or volume_ratio >= 1.2 or float(facts.get("sellShare") or 0) >= 56):
        score = (
            60
            + min(16, abs(ma20_distance) * 1.5)
            + min(12, abs(min(0.0, price_change)) * 3.0)
            + (8 if volume_ratio >= 1.2 else 0)
            + (6 if float(facts.get("sellShare") or 0) >= 56 else 0)
        )
        matches.append(_match(
            "breakout.failure.v1",
            score,
            data_quality,
            [
                "이전 20일선 괴리 " + ("%.1f" % previous_ma20_distance) + "% -> 현재 " + ("%.1f" % ma20_distance) + "%",
                "가격 변화 " + ("%.1f" % price_delta_previous) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "매도 체결 비중 " + ("%.1f" % float(facts.get("sellShare") or 0)) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if has_previous_state and previous_ma60_distance >= -1.0 and ma60_distance < -1.0 and (price_change < 0 or trend_curve < 0 or ma60_delta_previous < -1.5):
        score = 56 + min(18, abs(ma60_distance) * 2.2) + min(12, abs(min(0.0, trend_curve)) * 4.0) + min(8, abs(min(0.0, ma60_delta_previous)) * 2.0)
        matches.append(_match(
            "support.retest.failed.v1",
            score,
            data_quality,
            [
                "이전 60일선 괴리 " + ("%.1f" % previous_ma60_distance) + "% -> 현재 " + ("%.1f" % ma60_distance) + "%",
                "60일선 괴리 변화 " + ("%.1f" % ma60_delta_previous) + "%p",
                "추세 커브 " + ("%.1f" % trend_curve),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if breakdown_acceleration:
        score = (
            60
            + min(20, trend_dynamic_risk * 0.35)
            + min(10, abs(min(0.0, price_change)) * 2.0)
            + min(10, abs(min(0.0, ma20_slope)) * 5.0)
            + (6 if ma60_distance < 0 else 0)
        )
        matches.append(_match(
            "trend.breakdown_acceleration.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "20일선 기울기 " + ("%.1f" % ma20_slope) + "%",
                "60일선 기울기 " + ("%.1f" % ma60_slope) + "%",
                "추세 커브 " + ("%.1f" % trend_curve),
                "추세 동역학 리스크 " + ("%.1f" % trend_dynamic_risk) + "점",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if volume_ratio >= 1.2 and price_change > -1.5 and (flow_score <= -15 or float(facts.get("sellShare") or 0) >= 56 or bid_ask_imbalance <= -10):
        score = 52 + min(14, max(0.0, volume_ratio - 1.0) * 10) + min(16, abs(min(0.0, flow_score)) * 0.35) + min(10, abs(min(0.0, bid_ask_imbalance)) * 0.35)
        matches.append(_match(
            "distribution.detected.v1",
            score,
            data_quality,
            [
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "매도 체결 비중 " + ("%.1f" % float(facts.get("sellShare") or 0)) + "%",
                "호가 불균형 " + ("%.1f" % bid_ask_imbalance) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if pnl >= 10 and (volume_ratio >= 1.5 or abs(price_change) >= 2.0) and (ma20_slope <= 0.2 or trend_curve <= -0.4 or ma20_delta_previous <= -2.0):
        score = 54 + min(18, max(0.0, pnl - 10) * 1.0) + min(12, max(0.0, volume_ratio - 1.0) * 8.0) + min(12, abs(min(0.0, trend_curve)) * 5.0)
        matches.append(_match(
            "profit.protection.volatility.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "20일선 기울기 " + ("%.1f" % ma20_slope) + "%",
                "추세 커브 " + ("%.1f" % trend_curve),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if sector_ratio >= float(thresholds.get("sectorWeightHigh", 50.0) or 50.0) or position_weight >= float(thresholds.get("positionWeightHigh", 30.0) or 30.0):
        score = 50 + min(25, max(0, sector_ratio - 35) * 0.9) + min(25, max(0, position_weight - 20) * 1.1)
        matches.append(_match(
            "holding.concentration.rebalance.v1",
            score,
            data_quality,
            [
                "업종 비중 " + ("%.1f" % sector_ratio) + "%",
                "종목 비중 " + ("%.1f" % position_weight) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if is_holding and (liquidity_risk >= 45 or position_to_trading_value >= 5 or facts.get("sellableBlocked")):
        score = 48 + min(25, liquidity_risk * 0.35) + min(18, position_to_trading_value * 1.2) + (8 if facts.get("sellableBlocked") else 0)
        matches.append(_match(
            "liquidity.exit_capacity.v1",
            score,
            data_quality,
            [
                "포지션/거래대금 " + ("%.1f" % position_to_trading_value) + "%",
                "10% 거래대금 기준 청산 일수 " + ("%.1f" % float(facts.get("exitDaysAtTenPctADV") or 0)),
                "유동성 리스크 " + ("%.1f" % liquidity_risk) + "점",
                "매도 가능 수량 제한" if facts.get("sellableBlocked") else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if sector_ratio >= 45 and (position_weight >= 10 or trend_score < -4 or pnl < 0):
        score = 48 + min(26, max(0.0, sector_ratio - 35) * 0.8) + min(16, max(0.0, position_weight - 8) * 0.9) + (8 if trend_score < -4 else 0)
        matches.append(_match(
            "factor.crowding.v1",
            score,
            data_quality,
            [
                "업종 비중 " + ("%.1f" % sector_ratio) + "%",
                "종목 비중 " + ("%.1f" % position_weight) + "%",
                "시장/통화 " + str(facts.get("market") or "-") + "/" + str(facts.get("currency") or "-"),
                "추세 점수 " + ("%.1f" % trend_score),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if trend_score and flow_score and (trend_score > 4 and flow_score > 10 or trend_score < -4 and flow_score < -10):
        direction = "우호" if trend_score > 0 else "위험"
        score = 48 + min(24, abs(trend_score) * 1.2) + min(20, abs(flow_score) * 0.3)
        matches.append(_match(
            "holding.trend_flow.confirmation.v1",
            score,
            data_quality,
            [
                "추세 점수 " + ("%.1f" % trend_score),
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "공통 방향 " + direction,
            ],
            missing_labels,
            reference_only=trend_score > 0 and flow_score > 0,
            definitions=relation_definitions,
        ))
    data_conflict_active = (
        (external_quality and external_quality < 60)
        or external_errors >= 2
        or str(facts.get("externalSignalFreshnessStatus") or "") == "stale"
    )
    if data_conflict_active:
        score = 45 + min(22, max(0.0, 60 - external_quality) * 0.5 if external_quality else 8) + min(12, external_errors * 4) + min(12, len(missing_labels) * 3)
        matches.append(_match(
            "data.conflict.v1",
            score,
            data_quality,
            [
                "외부 신호 품질 " + ("%.1f" % external_quality) if external_quality else "외부 신호 품질 미확인",
                "외부 신호 오류 " + str(int(external_errors)) + "건",
                "신선도 " + str(facts.get("externalSignalFreshnessStatus") or "-"),
                "부족 데이터 " + ", ".join(missing_labels[:4]) if missing_labels else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if rate_sensitive and rate_delta_active:
        score = (
            49
            + min(22, rate_delta_magnitude / max(1.0, rate_delta_threshold or 1.0) * 10.0)
            + (10 if high_rate_active else 0)
            + (6 if low_rate_active else 0)
            + (9 if inverted_curve_active else 0)
            + min(10, max(0.0, macro_dgs10 - rate_high_threshold) * 5.0 if high_rate_active else 0.0)
            + min(8, max(0.0, fx_exposure_ratio - fx_exposure_review) * 0.4)
        )
        matches.append(_match(
            "rates.interest_rate.sensitivity.v1",
            score,
            data_quality,
            [
                "10년 금리 " + ("%.2f" % macro_dgs10) + "%" if macro_dgs10 else "",
                "10년 금리 변화 " + _bp_text(macro_dgs10_delta_bp) if facts.get("hasMacroDgs10Delta") else "",
                "2년 금리 " + ("%.2f" % macro_dgs2) + "%" if macro_dgs2 else "",
                "2년 금리 변화 " + _bp_text(macro_dgs2_delta_bp) if facts.get("hasMacroDgs2Delta") else "",
                "10Y-2Y 스프레드 " + ("%.2f" % macro_spread) + "%p" if _has_numeric_fact(facts.get("macroYieldSpread10y2y")) else "",
                "10Y-2Y 스프레드 변화 " + _bp_text(macro_spread_delta_bp) if facts.get("hasMacroYieldSpreadDelta") else "",
                "금리 레짐 " + str(facts.get("rateRegime") or "-"),
                "민감 섹터/통화 " + str(facts.get("sector") or "-") + "/" + (currency or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if fx_extreme_active or fx_exposure_active:
        score = (
            48
            + (12 if fx_extreme_active else 0)
            + min(18, abs(usd_krw_delta_pct) / max(0.1, usd_krw_delta_pct_threshold or 1.0) * 8.0)
            + min(12, abs(usd_krw_delta_krw) / max(1.0, usd_krw_delta_krw_threshold or 1.0) * 5.0)
            + min(16, abs(usd_krw_rate - (usd_krw_high if usd_krw_rate >= usd_krw_high else usd_krw_low)) / 10.0 if fx_extreme_active else 0.0)
            + min(18, max(0.0, fx_exposure_ratio - fx_exposure_review) * 1.1)
        )
        matches.append(_match(
            "fx.usd_krw.exposure.v1",
            score,
            data_quality,
            [
                "환율 " + str(facts.get("fxRatePair") or "USDKRW") + " " + ("%.2f" % (usd_krw_rate or fx_rate_to_krw)),
                "환율 변화 " + _number_text(usd_krw_delta_krw, 2, signed=True) + "원"
                + (" (" + _number_text(usd_krw_delta_pct, 2, signed=True) + "%)" if _has_numeric_fact(facts.get("usdKrwDeltaPct")) else ""),
                "환율 레짐 " + str(facts.get("fxRegime") or "-"),
                "외화 노출 " + ("%.1f" % fx_exposure_ratio) + "%",
                "보유 통화 " + (currency or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    macro_risk_active = macro_sensitive and (
        rate_delta_active
        or abs(btc_change24h) >= float(thresholds.get("externalBitcoinChange24hPct", 3.0) or 3.0)
        or fx_extreme_active
    )
    if macro_risk_active:
        score = 50 + (8 if high_rate_active else 0) + (8 if inverted_curve_active else 0) + min(12, rate_delta_magnitude / max(1.0, rate_delta_threshold or 1.0) * 5.0) + min(18, abs(btc_change24h) * 2.0) + (6 if fx_extreme_active else 0)
        matches.append(_match(
            "macro.regime.shift.v1",
            score,
            data_quality,
            [
                "10년 금리 " + ("%.2f" % macro_dgs10) if macro_dgs10 else "",
                "10년 금리 변화 " + _bp_text(macro_dgs10_delta_bp) if facts.get("hasMacroDgs10Delta") else "",
                "10Y-2Y 스프레드 " + ("%.2f" % macro_spread) if macro_spread else "",
                "10Y-2Y 스프레드 변화 " + _bp_text(macro_spread_delta_bp) if facts.get("hasMacroYieldSpreadDelta") else "",
                "BTC 24h " + ("%.1f" % btc_change24h) + "%" if btc_change24h else "",
                "환율 " + ("%.2f" % usd_krw_rate) + " · 변화 " + _number_text(usd_krw_delta_krw, 2, signed=True) + "원" if fx_extreme_active else "",
                "민감 섹터/통화 " + str(facts.get("sector") or "-") + "/" + (currency or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    btc_threshold24h = float(thresholds.get("externalBitcoinChange24hPct", 3.0) or 3.0)
    btc_threshold7d = float(thresholds.get("externalBitcoinChange7dPct", 4.0) or 4.0)
    if facts.get("isBtcSensitive") and (abs(btc_change24h) >= btc_threshold24h or abs(btc_change7d) >= btc_threshold7d):
        score = 50 + min(25, abs(btc_change24h) / max(1, btc_threshold24h) * 10) + min(25, abs(btc_change7d) / max(1, btc_threshold7d) * 10)
        matches.append(_match(
            "external.crypto.btc_sensitivity.v1",
            score,
            data_quality,
            [
                "BTC 24h " + ("%.1f" % btc_change24h) + "%",
                "BTC 7d " + ("%.1f" % btc_change7d) + "%",
                "민감 종목 " + str(facts.get("symbol") or ""),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    disclosure = facts.get("dartDisclosure")
    if isinstance(disclosure, dict) and disclosure:
        matches.append(_match(
            "disclosure.material_event.v1",
            62,
            data_quality,
            [
                "공시 " + str(disclosure.get("reportName") or "-"),
                "접수일 " + str(disclosure.get("receiptDate") or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if missing_labels:
        matches.append(_match(
            "data.quality.guard.v1",
            max(35, 100 - data_quality),
            data_quality,
            ["부족 데이터 " + ", ".join(missing_labels[:5])],
            missing_labels,
            reference_only=True,
            definitions=relation_definitions,
        ))

    decision = decision_from_matches(facts, matches)
    execution_plan = execution_plan_from_relation_context(facts, decision, matches)
    prompt_context = build_ai_prompt_context(prompt_id, facts, matches, settings, execution_plan)
    active_matches = [item for item in matches if item.matched and not item.reference_only]
    max_strength = max([item.strength_score for item in active_matches], default=decision["score"])
    return {
        "engineVersion": ONTOLOGY_RULE_ENGINE_VERSION,
        "subject": {
            "symbol": facts.get("symbol"),
            "name": facts.get("name"),
            "market": facts.get("market"),
            "sector": facts.get("sector"),
        },
        "facts": facts,
        "matchedRules": [item.to_dict() for item in matches if item.matched],
        "activeRules": [item.to_dict() for item in active_matches],
        "referenceRules": [item.to_dict() for item in matches if item.reference_only],
        "missingData": list(facts.get("missingData") or []),
        "dominantSignals": [item.label for item in active_matches[:3]],
        "signalStrength": round(float(max_strength or 0), 1),
        "signalStrengthLabel": strength_label(max_strength),
        "confidence": round(data_quality, 1),
        "decision": decision,
        "executionPlan": execution_plan,
        "promptContext": prompt_context,
        "legacyModel": dict(legacy_model or {}),
    }


def relation_rule_context_summary_lines(context: Dict[str, object]) -> List[str]:
    if not isinstance(context, dict) or not context:
        return []
    lines: List[str] = []
    strength = context.get("signalStrength")
    strength_label_value = str(context.get("signalStrengthLabel") or "").strip()
    if strength not in (None, ""):
        lines.append("관계 신호 " + strength_label_value + " (" + ("%.1f" % float(strength)) + "점)")
    fact_rows: List[Dict[str, object]] = []
    for candidate in [
        context.get("facts"),
        (context.get("executionPlan") or {}).get("sourceFacts") if isinstance(context.get("executionPlan"), dict) else {},
        (context.get("promptContext") or {}).get("facts") if isinstance(context.get("promptContext"), dict) else {},
    ]:
        if isinstance(candidate, dict) and candidate:
            fact_rows.append(candidate)
    for facts in fact_rows:
        for fact_line in [_rate_context_line_from_facts(facts), _fx_context_line_from_facts(facts)]:
            if fact_line and fact_line not in lines:
                lines.append(fact_line)
    active_rules = context.get("activeRules") or context.get("matchedRules") or []
    names = []
    for item in active_rules:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = str(item.get("label") or item.get("rule_id") or item.get("ruleId") or "").strip()
        if label:
            names.append(label)
    if names:
        lines.append("성립 규칙 " + " · ".join(names[:3]))
    missing = context.get("missingData") or []
    missing_names = []
    for item in missing:
        if isinstance(item, dict):
            text = str(item.get("label") or item.get("key") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            missing_names.append(text)
    if missing_names:
        lines.append("부족 데이터 " + ", ".join(missing_names[:5]))
    return lines
