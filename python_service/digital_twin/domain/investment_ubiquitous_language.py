"""TBox-backed ubiquitous language for user-facing investment communication."""

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

from .ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology, entity_id
from .ontology_tbox import CLASS_DEFS, tbox_class_def


LANGUAGE_REGISTRY_SETTING_KEY = "investmentLanguageRegistryJson"
LANGUAGE_REGISTRY_VERSION = "investment-language-v1"
LANGUAGE_GOVERNANCE_BOX = "LanguageGovernance"
LANGUAGE_LEVELS = {
    "absoluteBeginner": "왕초보",
    "beginner": "초보",
    "intermediate": "중수",
    "advanced": "고수",
}
LANGUAGE_TERM_STATUSES = {"approved", "draft", "deprecated"}

POSITION_INTENT_LABELS = {
    "core": "핵심 보유",
    "growth": "성장 투자",
    "trading": "기회 대응",
    "income": "배당·현금흐름",
    "market-signal": "시장 흐름 확인",
}

POSITION_INTENT_SENTENCES = {
    "core": "계좌에서는 오래 가져갈 핵심 보유 종목으로 관리합니다.",
    "growth": "계좌에서는 성장 가능성을 보고 투자하는 종목으로 관리합니다.",
    "trading": "계좌에서는 가격 변화에 맞춰 비중을 조절하는 종목으로 관리합니다.",
    "income": "계좌에서는 배당과 현금흐름을 기대하는 종목으로 관리합니다.",
    "market-signal": "계좌에서는 직접 매매보다 시장 흐름을 확인하는 지표로 사용합니다.",
}


@dataclass(frozen=True)
class InvestmentLanguageTerm:
    term_id: str
    category: str
    preferred_label: str
    definition: str = ""
    renderings: Dict[str, str] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)
    forbidden_expressions: List[str] = field(default_factory=list)
    status: str = "approved"
    version: str = LANGUAGE_REGISTRY_VERSION
    owner: str = "ontology"
    source: str = "tbox"
    replacement_term_id: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "termId": payload["term_id"],
            "category": payload["category"],
            "preferredLabel": payload["preferred_label"],
            "definition": payload["definition"],
            "renderings": dict(payload["renderings"]),
            "aliases": list(payload["aliases"]),
            "forbiddenExpressions": list(payload["forbidden_expressions"]),
            "status": payload["status"],
            "version": payload["version"],
            "owner": payload["owner"],
            "source": payload["source"],
            "replacementTermId": payload["replacement_term_id"],
        }


def _investment_archetype_definitions():
    definitions = {item.name: item for item in CLASS_DEFS}
    rows = []
    for item in CLASS_DEFS:
        name = item.name
        while name and name in definitions:
            if name == "InvestmentArchetype":
                rows.append(item)
                break
            name = definitions[name].parent
    for extra in ("MarketProxyInstrument",):
        definition = definitions.get(extra)
        if definition and definition not in rows:
            rows.append(definition)
    return rows


ARCHETYPE_DEFINITIONS = _investment_archetype_definitions()


def _contains_hangul(value: str) -> bool:
    return any("가" <= char <= "힣" for char in str(value or ""))


def _unique_strings(values: Iterable[object]) -> List[str]:
    rows: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in rows:
            rows.append(text)
    return rows


def _expression_pattern(value: object) -> str:
    expression = str(value or "")
    escaped = re.escape(expression)
    if re.match(r"^[A-Za-z0-9_.-]+$", expression):
        return r"(?<![A-Za-z0-9_-])" + escaped + r"(?![A-Za-z0-9_-])"
    return escaped


_KOREAN_PARTICLE_PAIRS = {
    "은": ("은", "는"),
    "는": ("은", "는"),
    "이": ("이", "가"),
    "가": ("이", "가"),
    "을": ("을", "를"),
    "를": ("을", "를"),
    "과": ("과", "와"),
    "와": ("과", "와"),
}


def _last_hangul_syllable(value: object) -> str:
    for char in reversed(str(value or "").strip()):
        if "가" <= char <= "힣":
            return char
    return ""


def _korean_particle_for(label: str, particle: str) -> str:
    syllable = _last_hangul_syllable(label)
    if not syllable or not particle:
        return particle
    jongseong = (ord(syllable) - ord("가")) % 28
    if particle in {"으로", "로"}:
        return "으로" if jongseong not in {0, 8} else "로"
    pair = _KOREAN_PARTICLE_PAIRS.get(particle)
    if not pair:
        return particle
    return pair[0] if jongseong else pair[1]


def _replace_expression_with_particle(text: str, source: str, label: str) -> str:
    pattern = _expression_pattern(source) + r"(으로|로|은|는|이|가|을|를|과|와)?"
    return re.sub(
        pattern,
        lambda match: label + _korean_particle_for(label, match.group(1) or ""),
        text,
        flags=re.IGNORECASE,
    )


def _level_renderings(label: str, absolute_beginner: str = "") -> Dict[str, str]:
    return {
        "absoluteBeginner": str(absolute_beginner or label).strip(),
        "beginner": str(label or "").strip(),
        "intermediate": str(label or "").strip(),
        "advanced": str(label or "").strip(),
    }


CORE_LANGUAGE_TERMS = [
    InvestmentLanguageTerm(
        "instrument-archetype",
        "investment-concept",
        "종목 성격",
        "종목의 사업 구조와 가격 움직임 특성에 따라 판단 기준을 다르게 적용하는 분류입니다.",
        _level_renderings("종목 성격"),
        ["종목 타입"],
        ["instrumentArchetype", "instrument type"],
    ),
    InvestmentLanguageTerm(
        "position-intent",
        "investment-concept",
        "계좌에서의 역할",
        "이 종목을 계좌에서 오래 보유할지, 성장 투자나 기회 대응에 사용할지 나타냅니다.",
        _level_renderings("계좌에서의 역할"),
        ["계좌 안 역할"],
        ["positionIntent"],
    ),
    InvestmentLanguageTerm(
        "relation-score",
        "decision-concept",
        "확인 필요 점수",
        "가격 예측 확률이 아니라 여러 투자 근거를 다시 확인해야 하는 정도입니다.",
        _level_renderings("확인 필요 점수", "얼마나 다시 확인해야 하는지"),
        ["관계 점수", "관계 강도"],
        ["relation score", "relationStrength"],
    ),
    InvestmentLanguageTerm(
        "feature-contribution",
        "decision-concept",
        "판단에 미친 영향",
        "각 데이터가 최종 판단을 어느 방향으로 얼마나 움직였는지 설명합니다.",
        _level_renderings("판단에 미친 영향"),
        [],
        ["feature 기여도", "feature contribution"],
    ),
    InvestmentLanguageTerm(
        "investment-thesis",
        "decision-concept",
        "보유 이유",
        "이 종목을 보유하거나 관심 있게 보는 핵심 이유입니다.",
        _level_renderings("보유 이유"),
        ["투자 가설"],
        ["thesis"],
    ),
    InvestmentLanguageTerm(
        "trend-breakdown",
        "market-concept",
        "주요 평균 가격 아래로 내려감",
        "현재가가 확인 기준으로 삼는 이동평균 가격보다 낮아진 상태입니다.",
        _level_renderings("주요 평균 가격 아래로 내려감"),
        ["평균선 이탈"],
        ["추세 훼손", "기준선 이탈", "trend breakdown"],
    ),
    InvestmentLanguageTerm(
        "downside-acceleration",
        "market-concept",
        "하락 속도가 빨라짐",
        "이전 확인 때보다 가격 하락 속도가 더 빨라진 상태입니다.",
        _level_renderings("하락 속도가 빨라짐"),
        [],
        ["하락 가속", "downside acceleration"],
    ),
    InvestmentLanguageTerm(
        "market-beta",
        "risk-concept",
        "시장과 같이 움직이는 정도",
        "시장 전체가 움직일 때 이 종목이 얼마나 같은 방향으로 민감하게 움직이는지 나타냅니다.",
        _level_renderings("시장과 같이 움직이는 정도"),
        ["시장 민감도"],
        ["market beta"],
    ),
]


def default_investment_language_terms() -> List[InvestmentLanguageTerm]:
    terms = list(CORE_LANGUAGE_TERMS)
    for definition in ARCHETYPE_DEFINITIONS:
        label = str(definition.label or "사용자 정의 종목 성격").strip()
        terms.append(InvestmentLanguageTerm(
            definition.name,
            "instrument-archetype",
            label,
            str(definition.description or (label + "에 맞는 투자 판단 기준을 적용합니다.")).strip(),
            _level_renderings(label),
            [],
            [],
            source="tbox-class",
        ))
    for identifier, label in POSITION_INTENT_LABELS.items():
        terms.append(InvestmentLanguageTerm(
            identifier,
            "position-intent",
            label,
            POSITION_INTENT_SENTENCES[identifier],
            _level_renderings(label),
            [],
            [],
            source="position-policy",
        ))
    return terms


def default_investment_language_registry() -> Dict[str, object]:
    return {
        "version": LANGUAGE_REGISTRY_VERSION,
        "updatedAt": "",
        "source": "tbox-defaults",
        "levels": dict(LANGUAGE_LEVELS),
        "terms": [item.to_dict() for item in default_investment_language_terms()],
    }


def _term_from_dict(payload: Dict[str, object], fallback: Optional[InvestmentLanguageTerm] = None) -> InvestmentLanguageTerm:
    source = payload if isinstance(payload, dict) else {}
    fallback = fallback or InvestmentLanguageTerm("", "", "")
    term_id = str(source.get("termId") or source.get("id") or fallback.term_id).strip()
    preferred = str(source.get("preferredLabel") or source.get("label") or fallback.preferred_label).strip()
    renderings = source.get("renderings") if isinstance(source.get("renderings"), dict) else fallback.renderings
    normalized_renderings = {
        level: str((renderings or {}).get(level) or preferred).strip()
        for level in LANGUAGE_LEVELS
    }
    return InvestmentLanguageTerm(
        term_id=term_id,
        category=str(source.get("category") or fallback.category or "investment-concept").strip(),
        preferred_label=preferred,
        definition=str(source.get("definition") or fallback.definition).strip(),
        renderings=normalized_renderings,
        aliases=_unique_strings(source.get("aliases") if isinstance(source.get("aliases"), list) else fallback.aliases),
        forbidden_expressions=_unique_strings(
            source.get("forbiddenExpressions")
            if isinstance(source.get("forbiddenExpressions"), list)
            else fallback.forbidden_expressions
        ),
        status=str(source.get("status") or fallback.status or "approved").strip().lower(),
        version=str(source.get("version") or fallback.version or LANGUAGE_REGISTRY_VERSION).strip(),
        owner=str(source.get("owner") or fallback.owner or "ontology").strip(),
        source=str(source.get("source") or fallback.source or "admin").strip(),
        replacement_term_id=str(source.get("replacementTermId") or fallback.replacement_term_id).strip(),
    )


def normalize_investment_language_registry(payload: Dict[str, object] = None) -> Dict[str, object]:
    defaults = default_investment_language_terms()
    default_by_id = {item.term_id: item for item in defaults}
    source = payload if isinstance(payload, dict) else {}
    supplied = source.get("terms") if isinstance(source.get("terms"), list) else []
    supplied_by_id = {
        str(item.get("termId") or item.get("id") or "").strip(): item
        for item in supplied
        if isinstance(item, dict) and str(item.get("termId") or item.get("id") or "").strip()
    }
    terms: List[InvestmentLanguageTerm] = []
    for fallback in defaults:
        terms.append(_term_from_dict(supplied_by_id.pop(fallback.term_id, {}), fallback))
    for term_id, item in supplied_by_id.items():
        term = _term_from_dict(item)
        if term_id and term.preferred_label:
            terms.append(term)
    terms.sort(key=lambda item: (item.category, item.preferred_label, item.term_id))
    return {
        "version": str(source.get("version") or LANGUAGE_REGISTRY_VERSION).strip(),
        "updatedAt": str(source.get("updatedAt") or "").strip(),
        "source": str(source.get("source") or ("settings" if supplied else "tbox-defaults")).strip(),
        "levels": dict(LANGUAGE_LEVELS),
        "terms": [item.to_dict() for item in terms],
    }


def investment_language_registry(settings: Dict[str, object] = None) -> Dict[str, object]:
    settings = settings if isinstance(settings, dict) else {}
    raw = settings.get(LANGUAGE_REGISTRY_SETTING_KEY)
    if isinstance(raw, dict):
        return normalize_investment_language_registry(raw)
    if str(raw or "").strip():
        try:
            parsed = json.loads(str(raw))
            if isinstance(parsed, dict):
                return normalize_investment_language_registry(parsed)
        except (TypeError, ValueError):
            pass
    return normalize_investment_language_registry()


def investment_language_term(identifier: object, settings: Dict[str, object] = None) -> Optional[Dict[str, object]]:
    key = str(identifier or "").strip()
    if not key:
        return None
    for term in investment_language_registry(settings).get("terms") or []:
        if str(term.get("termId") or "") == key:
            return term
    return None


def investment_language_label(identifier: object, level: str = "beginner", settings: Dict[str, object] = None) -> str:
    term = investment_language_term(identifier, settings)
    if not term or str(term.get("status") or "approved") != "approved":
        return ""
    renderings = term.get("renderings") if isinstance(term.get("renderings"), dict) else {}
    return str(renderings.get(level) or term.get("preferredLabel") or "").strip()


def investment_archetype_label(identifier: object, settings: Dict[str, object] = None, level: str = "beginner") -> str:
    """Return the approved domain label without exposing an internal class identifier."""
    key = str(identifier or "").strip()
    if not key:
        return ""
    managed = investment_language_label(key, level, settings)
    if managed:
        return managed
    definition = tbox_class_def(key)
    if definition and str(definition.label or "").strip():
        return str(definition.label).strip()
    if _contains_hangul(key):
        return key
    return "사용자 정의 종목 성격"


def investment_archetype_labels(
    identifiers: Iterable[object],
    settings: Dict[str, object] = None,
    level: str = "beginner",
) -> List[str]:
    rows: List[str] = []
    for identifier in identifiers or []:
        label = investment_archetype_label(identifier, settings, level)
        if label and label not in rows:
            rows.append(label)
    return rows


def position_intent_label(identifier: object, settings: Dict[str, object] = None, level: str = "beginner") -> str:
    key = str(identifier or "").strip()
    if not key:
        return ""
    managed = investment_language_label(key, level, settings)
    if managed:
        return managed
    if key in POSITION_INTENT_LABELS:
        return POSITION_INTENT_LABELS[key]
    if _contains_hangul(key):
        return key
    return "사용자 정의 역할"


def position_intent_sentence(identifier: object, settings: Dict[str, object] = None) -> str:
    key = str(identifier or "").strip()
    if not key:
        return ""
    term = investment_language_term(key, settings)
    if term and str(term.get("status") or "approved") == "approved" and str(term.get("definition") or "").strip():
        return str(term.get("definition")).strip()
    if key in POSITION_INTENT_SENTENCES:
        return POSITION_INTENT_SENTENCES[key]
    return "계좌에서는 " + position_intent_label(key, settings) + " 종목으로 관리합니다."


def user_facing_investment_language(
    value: object,
    settings: Dict[str, object] = None,
    level: str = "beginner",
) -> str:
    """Replace approved IDs, aliases, and forbidden expressions before delivery."""
    text = str(value or "")
    registry = investment_language_registry(settings)
    default_terms = {item.term_id: item.to_dict() for item in default_investment_language_terms()}
    approved = [item for item in registry.get("terms") or [] if str(item.get("status") or "approved") == "approved"]
    replacements = []
    for term in approved:
        label = str((term.get("renderings") or {}).get(level) or term.get("preferredLabel") or "").strip()
        if not label:
            continue
        default_term = default_terms.get(str(term.get("termId") or "")) or {}
        values = [
            term.get("termId"),
            *(term.get("aliases") or []),
            *(term.get("forbiddenExpressions") or []),
            default_term.get("preferredLabel"),
            *((default_term.get("renderings") or {}).values()),
        ]
        for source in _unique_strings(values):
            if source != label:
                replacements.append((source, label))
    for source, label in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        text = _replace_expression_with_particle(text, source, label)
    for identifier, label in POSITION_INTENT_LABELS.items():
        replacement = position_intent_label(identifier, settings, level) or label
        patterns = [
            r"(계좌에서의 역할\s*[:=]\s*)" + re.escape(identifier) + r"\b",
            r"(positionIntent\s*[:=]\s*)" + re.escape(identifier) + r"\b",
        ]
        for pattern in patterns:
            text = re.sub(pattern, lambda match: match.group(1) + replacement, text, flags=re.IGNORECASE)
    return text


def validate_investment_language_registry(payload: Dict[str, object] = None) -> Dict[str, object]:
    source = payload if isinstance(payload, dict) else {}
    supplied = source.get("terms") if isinstance(source.get("terms"), list) else []
    supplied_term_ids: Dict[str, int] = {}
    for item in supplied:
        if not isinstance(item, dict):
            continue
        term_id = str(item.get("termId") or item.get("id") or "").strip()
        if term_id:
            supplied_term_ids[term_id] = supplied_term_ids.get(term_id, 0) + 1
    registry = normalize_investment_language_registry(payload)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    for term_id, count in supplied_term_ids.items():
        if count > 1:
            errors.append({"termId": term_id, "field": "termId", "message": "같은 내부 식별자가 두 번 등록됐습니다."})
    term_ids = set()
    approved_labels = {}
    for term in registry.get("terms") or []:
        term_id = str(term.get("termId") or "").strip()
        label = str(term.get("preferredLabel") or "").strip()
        status = str(term.get("status") or "approved").strip()
        if not term_id:
            errors.append({"termId": "", "field": "termId", "message": "내부 식별자가 필요합니다."})
            continue
        if term_id in term_ids and supplied_term_ids.get(term_id, 0) <= 1:
            errors.append({"termId": term_id, "field": "termId", "message": "같은 내부 식별자가 두 번 등록됐습니다."})
        term_ids.add(term_id)
        if not label:
            errors.append({"termId": term_id, "field": "preferredLabel", "message": "대표 표현이 필요합니다."})
        if status not in LANGUAGE_TERM_STATUSES:
            errors.append({"termId": term_id, "field": "status", "message": "승인, 검토 중, 사용 중지 상태만 사용할 수 있습니다."})
        if status == "approved" and label:
            duplicate = approved_labels.get(label)
            if duplicate and duplicate != term_id:
                warnings.append({"termId": term_id, "field": "preferredLabel", "message": "다른 용어와 대표 표현이 같습니다: " + duplicate})
            approved_labels[label] = term_id
        renderings = term.get("renderings") if isinstance(term.get("renderings"), dict) else {}
        for level in LANGUAGE_LEVELS:
            if not str(renderings.get(level) or "").strip():
                warnings.append({"termId": term_id, "field": "renderings." + level, "message": LANGUAGE_LEVELS[level] + " 표현이 없어 대표 표현을 사용합니다."})
    required_ids = {item.name for item in ARCHETYPE_DEFINITIONS} | set(POSITION_INTENT_LABELS)
    missing_ids = sorted(required_ids - term_ids)
    for term_id in missing_ids:
        errors.append({"termId": term_id, "field": "coverage", "message": "TBox 또는 계좌 역할에 있는 용어가 레지스트리에 없습니다."})
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "coverage": {
            "requiredCount": len(required_ids),
            "coveredCount": len(required_ids - set(missing_ids)),
            "missingTermIds": missing_ids,
        },
        "registry": registry,
    }


def audit_user_facing_investment_text(
    value: object,
    settings: Dict[str, object] = None,
    level: str = "beginner",
) -> Dict[str, object]:
    text = str(value or "")
    registry = investment_language_registry(settings)
    findings: List[Dict[str, str]] = []
    for term in registry.get("terms") or []:
        if str(term.get("status") or "approved") != "approved":
            continue
        term_id = str(term.get("termId") or "")
        replacement = str((term.get("renderings") or {}).get(level) or term.get("preferredLabel") or "")
        expressions = [term_id, *(term.get("forbiddenExpressions") or [])]
        for expression in _unique_strings(expressions):
            if expression and re.search(_expression_pattern(expression), text, flags=re.IGNORECASE):
                findings.append({
                    "termId": term_id,
                    "expression": expression,
                    "replacement": replacement,
                    "message": "내부 용어나 어려운 표현을 사용자 표현으로 바꿔야 합니다.",
                })
    return {
        "valid": not findings,
        "level": level if level in LANGUAGE_LEVELS else "beginner",
        "findings": findings,
        "renderedText": user_facing_investment_language(text, settings, level),
    }


def propose_investment_language_changes(
    value: object,
    settings: Dict[str, object] = None,
    level: str = "beginner",
) -> Dict[str, object]:
    audit = audit_user_facing_investment_text(value, settings, level)
    proposals = [
        {
            "proposalId": "replace:" + str(item.get("termId") or "") + ":" + str(index + 1),
            "termId": item.get("termId"),
            "currentExpression": item.get("expression"),
            "proposedExpression": item.get("replacement"),
            "status": "review-required",
            "autoApplied": False,
        }
        for index, item in enumerate(audit.get("findings") or [])
    ]
    return {**audit, "proposals": proposals}


def add_investment_language_governance_concepts(
    graph: PortfolioOntology,
    registry_payload: Dict[str, object] = None,
) -> PortfolioOntology:
    registry = normalize_investment_language_registry(registry_payload)
    registry_id = entity_id("language-registry", str(registry.get("version") or LANGUAGE_REGISTRY_VERSION))
    common = {
        "ontologyBox": LANGUAGE_GOVERNANCE_BOX,
        "box": LANGUAGE_GOVERNANCE_BOX,
        "boundedContext": "operations-dispatch",
        "registryVersion": registry.get("version"),
    }
    graph.entities.append(OntologyEntity(registry_id, "투자 보편언어 " + str(registry.get("version") or ""), "language-registry-version", {
        **common,
        "tboxClass": "LanguageRegistryVersion",
        "source": registry.get("source"),
        "updatedAt": registry.get("updatedAt"),
        "termCount": len(registry.get("terms") or []),
    }))
    for term in registry.get("terms") or []:
        term_id = str(term.get("termId") or "")
        term_entity_id = entity_id("domain-term", term_id)
        graph.entities.append(OntologyEntity(term_entity_id, str(term.get("preferredLabel") or term_id), "domain-term", {
            **common,
            "tboxClass": "DomainTerm",
            "termId": term_id,
            "termCategory": term.get("category"),
            "termStatus": term.get("status"),
            "termVersion": term.get("version"),
            "preferredLabel": term.get("preferredLabel"),
            "definition": term.get("definition"),
            "owner": term.get("owner"),
            "source": term.get("source"),
            "replacementTermId": term.get("replacementTermId"),
            "tboxClassReference": term_id if tbox_class_def(term_id) else "",
        }))
        graph.relations.append(OntologyRelation(registry_id, term_entity_id, "GOVERNS_TERM", properties={**common}))
        for level, label in (term.get("renderings") or {}).items():
            rendering_id = entity_id("term-rendering", term_id + ":" + str(level))
            graph.entities.append(OntologyEntity(rendering_id, str(label or ""), "term-rendering", {
                **common,
                "tboxClass": "TermRendering",
                "termId": term_id,
                "deliveryLevel": level,
                "deliveryLevelLabel": LANGUAGE_LEVELS.get(level, level),
                "renderedLabel": label,
            }))
            graph.relations.append(OntologyRelation(term_entity_id, rendering_id, "HAS_TERM_RENDERING", properties={**common}))
        for index, alias in enumerate(term.get("aliases") or []):
            alias_id = entity_id("term-alias", term_id + ":" + str(index))
            graph.entities.append(OntologyEntity(alias_id, str(alias), "term-alias", {
                **common,
                "tboxClass": "TermAlias",
                "termId": term_id,
                "alias": alias,
            }))
            graph.relations.append(OntologyRelation(term_entity_id, alias_id, "HAS_TERM_ALIAS", properties={**common}))
        for index, expression in enumerate(term.get("forbiddenExpressions") or []):
            expression_id = entity_id("forbidden-expression", term_id + ":" + str(index))
            graph.entities.append(OntologyEntity(expression_id, str(expression), "forbidden-expression", {
                **common,
                "tboxClass": "ForbiddenExpression",
                "termId": term_id,
                "expression": expression,
            }))
            graph.relations.append(OntologyRelation(term_entity_id, expression_id, "FORBIDS_EXPRESSION", properties={**common}))
    return graph
