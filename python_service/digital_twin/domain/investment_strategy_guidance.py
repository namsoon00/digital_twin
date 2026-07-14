from typing import Dict, Iterable, List

from .accounts import investment_strategy_profile


def clean_text(value: object, limit: int = 700) -> str:
    return " ".join(str(value or "").split()).strip()[:limit].rstrip()


def unique_texts(values: Iterable[object], limit: int = 8) -> List[str]:
    result = []
    for value in values or []:
        text = clean_text(value, 180)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _profile_key_from(account=None, context: Dict[str, object] = None, settings: Dict[str, object] = None) -> str:
    context = context if isinstance(context, dict) else {}
    settings = settings if isinstance(settings, dict) else {}
    if account is not None:
        value = getattr(account, "investment_strategy_profile", "")
        if value:
            return str(value)
    strategy_payload = context.get("investmentStrategy") if isinstance(context.get("investmentStrategy"), dict) else {}
    return str(
        strategy_payload.get("profile")
        or context.get("investmentStrategyProfile")
        or context.get("investment_strategy_profile")
        or settings.get("investmentStrategyProfile")
        or ""
    )


def investment_strategy_context(account=None, context: Dict[str, object] = None, settings: Dict[str, object] = None) -> Dict[str, object]:
    profile = investment_strategy_profile(_profile_key_from(account, context, settings))
    if account is not None:
        profile["accountId"] = getattr(account, "account_id", "") or ""
        profile["accountLabel"] = getattr(account, "label", "") or ""
    elif isinstance(context, dict):
        profile["accountId"] = context.get("accountId") or ""
        profile["accountLabel"] = context.get("accountLabel") or ""
    return {
        "investmentStrategyProfile": profile["profile"],
        "investmentStrategyProfileLabel": profile["label"],
        "investmentStrategy": profile,
    }


def message_delivery_context(account=None) -> Dict[str, object]:
    if account is None or not hasattr(account, "message_delivery_context"):
        return {}
    return account.message_delivery_context()


def account_guidance(account=None, context: Dict[str, object] = None, settings: Dict[str, object] = None) -> Dict[str, object]:
    strategy_context = investment_strategy_context(account, context, settings)
    profile = strategy_context["investmentStrategy"]
    profile_key = profile.get("profile")
    label = profile.get("label")
    if profile_key == "capitalPreservation":
        stance = "원금 방어와 현금 여력을 먼저 확인합니다."
        response = "좋은 뉴스나 이벤트도 바로 비중을 늘리기보다 주요 가격 회복과 손실 제한 기준을 먼저 봅니다."
        risk_checks = ["손실 허용폭 " + str(profile.get("lossTolerancePct")) + "%", "현금 비중", "종목/섹터 과집중"]
        action_boundaries = ["추가매수는 회복 확인 전 보류", "불리한 지표는 비중 축소 기준부터 점검"]
    elif profile_key == "growth":
        stance = "성장 근거와 추세 유지 여부를 우선 확인합니다."
        response = "변동성은 어느 정도 허용하되 성장 서사, 매출/가이던스, 수급 약화가 동시에 흔들리면 분할 대응합니다."
        risk_checks = ["성장 가정 훼손", "주요 평균선 이탈", "뉴스/가이던스 악화"]
        action_boundaries = ["성장 근거 유지 시 보유 우선", "추세와 수급이 같이 깨지면 축소 조건 점검"]
    elif profile_key == "aggressive":
        stance = "기회 포착을 적극 보되 집중도와 급락 리스크를 강하게 봅니다."
        response = "강한 추세·수급·뉴스가 맞으면 진입 후보를 검토하지만, 과도한 비중과 급락 신호는 즉시 경고합니다."
        risk_checks = ["최대 종목 비중 " + str(profile.get("maxPositionWeightPct")) + "%", "급락/거래량 폭증", "반대 뉴스"]
        action_boundaries = ["소액 분할 진입 우선", "집중도 초과 시 추가 진입 제한"]
    else:
        stance = "손실 관리와 수익 유지의 균형을 봅니다."
        response = "바로 실행보다 가격·수급·뉴스를 함께 확인하고, 보유·분할축소·소액 진입을 나눠 판단합니다."
        risk_checks = ["손실 허용폭 " + str(profile.get("lossTolerancePct")) + "%", "주요 평균선", "뉴스/공시 반대 근거"]
        action_boundaries = ["확인 후 분할 대응", "좋은 신호와 반대 신호를 같이 비교"]
    return {
        "profile": profile_key,
        "label": label,
        "stance": stance,
        "response": response,
        "riskChecks": unique_texts(risk_checks, 5),
        "actionBoundaries": unique_texts(action_boundaries, 5),
        "promptInstruction": clean_text(profile.get("promptInstruction"), 1000),
    }


def strategy_guidance_context(account=None, context: Dict[str, object] = None, settings: Dict[str, object] = None) -> Dict[str, object]:
    strategy_context = investment_strategy_context(account, context, settings)
    guidance = account_guidance(account, context, settings)
    result = dict(strategy_context)
    result.update({
        "investmentStrategyGuidance": guidance,
        "strategyTone": guidance["stance"],
        "strategyActionBoundaries": list(guidance["actionBoundaries"]),
        "strategyRiskChecks": list(guidance["riskChecks"]),
    })
    result.update(message_delivery_context(account))
    return result


def merge_strategy_context(context: Dict[str, object], account=None, settings: Dict[str, object] = None) -> Dict[str, object]:
    merged = dict(context or {})
    strategy_context = strategy_guidance_context(account, merged, settings)
    merged.update(strategy_context)
    return merged


def strategy_message_lines(context: Dict[str, object], prefix: str = "• ") -> List[str]:
    context = context if isinstance(context, dict) else {}
    guidance = context.get("investmentStrategyGuidance") if isinstance(context.get("investmentStrategyGuidance"), dict) else {}
    if not guidance:
        guidance = account_guidance(context=context)
    label = clean_text(guidance.get("label"))
    lines = []
    if label:
        lines.append(prefix + "계정 성향: " + label)
    if guidance.get("stance"):
        lines.append(prefix + "성향 기준: " + clean_text(guidance.get("stance")))
    if guidance.get("response"):
        lines.append(prefix + "대응 원칙: " + clean_text(guidance.get("response")))
    checks = unique_texts(guidance.get("riskChecks") or [], 4)
    if checks:
        lines.append(prefix + "추가 확인: " + " / ".join(checks))
    boundaries = unique_texts(guidance.get("actionBoundaries") or [], 4)
    if boundaries:
        lines.append(prefix + "행동 경계: " + " / ".join(boundaries))
    return lines


def append_strategy_block(context: Dict[str, object], title: str = "계정 성향 기준") -> Dict[str, object]:
    merged = merge_strategy_context(context)
    lines = strategy_message_lines(merged)
    if not lines:
        return merged
    block = title + "\n" + "\n".join(lines)
    html_block = "<b>" + title + "</b>\n" + "\n".join(lines)
    for key, value in {
        "readableMessage": block,
        "body": html_block,
        "telegramMessage": html_block,
    }.items():
        existing = str(merged.get(key) or "").strip()[:10000].rstrip()
        if existing and title not in existing:
            merged[key] = existing + "\n\n" + value
        elif not existing:
            merged[key] = value
    return merged


def target_text(symbols: Iterable[object] = None, markets: Iterable[object] = None) -> str:
    selected = unique_texts(list(symbols or []) + list(markets or []), 8)
    return ", ".join(selected or ["전체 포트폴리오"])


def event_type_guidance(event_type: object, target: str, symbols: Iterable[object] = None) -> Dict[str, object]:
    event_type = str(event_type or "")
    impact = "이벤트 결과가 " + target + "의 변동성, 뉴스 흐름, 포트폴리오 리스크 점검 우선순위에 영향을 줄 수 있습니다."
    watch_items = ["예상치 대비 실제 결과", "발표 직후 가격·거래량 반응", "기존 투자 가정과 달라진 점"]
    if event_type == "macro":
        impact = "물가·성장·고용 지표는 금리 기대와 달러, 미국 장기금리를 통해 성장주 밸류에이션과 " + target + "의 단기 변동성에 영향을 줄 수 있습니다."
        watch_items = ["컨센서스 대비 헤드라인/핵심 지표", "미국 2년·10년물 금리와 달러 반응", "프리마켓 및 장초반 거래량 변화"]
    elif event_type == "centralBank":
        impact = "중앙은행 결정은 할인율과 위험선호를 바꾸며 " + target + "의 멀티플, 기술주 수급, 환율 민감도에 직접 영향을 줄 수 있습니다."
        watch_items = ["성명서의 완화/긴축 톤 변화", "기자회견의 다음 회의 힌트", "2년물 금리·나스닥 선물·달러 동시 반응"]
    elif event_type == "earnings":
        impact = "실적 이벤트는 " + target + "의 이익 추정치, 밸류에이션 프리미엄, 다음 분기 가이던스 재평가로 이어질 수 있습니다."
        watch_items = ["매출/EPS의 시장 기대 대비 차이", "마진과 비용 구조", "다음 분기 또는 연간 가이던스"]
        watch_items.extend(symbol_specific_watch_items(symbols))
    elif event_type == "dividend":
        impact = "배당·권리 일정은 현금흐름, 배당락 가격 조정, 세후 수익률 점검에 영향을 줄 수 있습니다."
        watch_items = ["배당락일과 지급일", "예상 배당수익률", "배당락 이후 가격 회복 여부"]
    elif event_type == "disclosure":
        impact = "공시 이벤트는 기존 투자 가정, 리스크 요인, 단기 수급 판단에 영향을 줄 수 있습니다."
        watch_items = ["공시의 재무 영향", "일회성/반복성 여부", "시장 반응과 후속 정정 공시"]
    elif event_type == "shareholderMeeting":
        impact = "주주총회는 지배구조, 자본정책, 경영진 메시지 변화가 투자 심리에 영향을 줄 수 있습니다."
        watch_items = ["자본정책 변화", "사업전략 발언", "주주환원 및 이사회 안건"]
    elif event_type == "lockup":
        impact = "락업 해제는 잠재 매도 물량과 단기 수급 부담을 키울 수 있습니다."
        watch_items = ["해제 주식 수와 유통주식 대비 비율", "주요 보유자 매각 가능성", "거래량 급증 여부"]
    elif event_type == "portfolioReview":
        impact = "포트폴리오 점검 일정은 보유 비중, 리스크 노출, 현금 여력을 재확인하는 운영 기준점입니다."
        watch_items = ["종목별 비중 변화", "상관관계가 높은 노출", "현금·손절·추가매수 기준의 최신성"]
    return {"impact": clean_text(impact), "watchItems": unique_texts(watch_items, 6)}


def symbol_specific_watch_items(symbols: Iterable[object]) -> List[str]:
    items = []
    for symbol in [str(item or "").upper() for item in symbols or []]:
        if symbol == "TSLA":
            items.extend(["자동차 총마진/ASP", "에너지 저장 성장", "FSD·로보택시 일정 코멘트"])
        elif symbol == "AAPL":
            items.extend(["iPhone 및 서비스 매출", "중국 매출 흐름", "AI/칩 비용과 자사주 매입"])
        elif symbol == "NVDA":
            items.extend(["데이터센터 매출과 수주", "차세대 GPU 공급 제약", "총마진과 다음 분기 가이던스"])
    return unique_texts(items, 6)


def event_strategy_guidance(event_type: object, symbols: Iterable[object] = None, markets: Iterable[object] = None, account=None, context: Dict[str, object] = None) -> Dict[str, object]:
    target = target_text(symbols, markets)
    event_guidance = event_type_guidance(event_type, target, symbols)
    strategy = account_guidance(account, context)
    watch_items = unique_texts(list(event_guidance["watchItems"]) + list(strategy["riskChecks"]), 6)
    impact = event_guidance["impact"] + " " + str(strategy["label"] or "계정 성향") + " 기준으로는 " + strategy["response"]
    return {
        "impact": clean_text(impact, 900),
        "watchItems": watch_items,
        "strategy": strategy,
        "target": target,
    }
