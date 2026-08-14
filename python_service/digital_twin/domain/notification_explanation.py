from dataclasses import dataclass
import re
from typing import Dict, Iterable, Tuple


DEFAULT_NOTIFICATION_DETAIL_LEVEL = "concise"
NOTIFICATION_DETAIL_LEVELS = {
    "concise": {
        "label": "간결",
        "description": "행동, 변화, 핵심 근거와 판단 변경 조건만 알림으로 보냅니다.",
        "maxEvidence": 3,
        "maxCounterEvidence": 1,
        "maxNextChecks": 2,
    },
    "standard": {
        "label": "표준",
        "description": "간결 알림에 현재 흐름, 핵심 TypeDB 추론과 가치 정보를 더합니다.",
        "maxEvidence": 4,
        "maxCounterEvidence": 2,
        "maxNextChecks": 3,
    },
    "full": {
        "label": "전체",
        "description": "추론, 근거, 자료 상태와 추적 정보를 알림에도 모두 표시합니다.",
        "maxEvidence": 0,
        "maxCounterEvidence": 0,
        "maxNextChecks": 0,
    },
}


def normalize_notification_detail_level(value: object) -> str:
    text = str(value or "").strip()
    aliases = {
        "간결": "concise",
        "compact": "concise",
        "short": "concise",
        "표준": "standard",
        "normal": "standard",
        "전체": "full",
        "detailed": "full",
        "detail": "full",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in NOTIFICATION_DETAIL_LEVELS else DEFAULT_NOTIFICATION_DETAIL_LEVEL


def notification_detail_profile(value: object = None) -> Dict[str, object]:
    level = normalize_notification_detail_level(value)
    profile = dict(NOTIFICATION_DETAIL_LEVELS[level])
    profile["level"] = level
    return profile


def _normalized_text(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def _bounded_text(value: object, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    boundary = max(
        text.rfind(". ", 0, max_chars),
        text.rfind(" · ", 0, max_chars),
        text.rfind(", ", 0, max_chars),
    )
    if boundary < int(max_chars * 0.6):
        boundary = max_chars
    return text[:boundary].rstrip(" .,·") + "…"


def _dedupe_rows(
    values: Iterable[object],
    limit: int,
    seen: set = None,
    max_chars: int = 0,
) -> Tuple[str, ...]:
    rows = []
    known = seen if seen is not None else set()
    for value in values or []:
        text = _bounded_text(value, max_chars)
        key = _normalized_text(text)
        if not text or not key:
            continue
        if any(key in existing or existing in key for existing in known):
            continue
        known.add(key)
        rows.append(text)
        if limit > 0 and len(rows) >= limit:
            break
    return tuple(rows)


@dataclass(frozen=True)
class NotificationExplanationPacket:
    detail_level: str
    action: str
    change: str
    current_flow: Tuple[str, ...]
    evidence: Tuple[str, ...]
    counter_evidence: Tuple[str, ...]
    inference: Tuple[str, ...]
    company_value: Tuple[str, ...]
    next_checks: Tuple[str, ...]
    data_warnings: Tuple[str, ...]


def build_notification_explanation_packet(
    *,
    detail_level: object,
    action: object,
    change: object = "",
    current_flow: Iterable[object] = (),
    evidence: Iterable[object] = (),
    counter_evidence: Iterable[object] = (),
    inference: Iterable[object] = (),
    company_value: Iterable[object] = (),
    next_checks: Iterable[object] = (),
    data_warnings: Iterable[object] = (),
) -> NotificationExplanationPacket:
    profile = notification_detail_profile(detail_level)
    seen = set()
    action_text = re.sub(r"\s+", " ", str(action or "")).strip()
    change_text = re.sub(r"\s+", " ", str(change or "")).strip()
    if action_text:
        seen.add(_normalized_text(action_text))
    if change_text and not any(
        key and (_normalized_text(change_text) in key or key in _normalized_text(change_text))
        for key in seen
    ):
        seen.add(_normalized_text(change_text))
    elif change_text:
        change_text = ""

    level = str(profile["level"])
    concise = level == "concise"
    current_limit = 3 if level == "concise" else (4 if level == "standard" else 0)
    inference_limit = 1 if level == "concise" else (2 if level == "standard" else 0)
    company_limit = 3 if level == "standard" else 0
    warning_limit = 2 if level == "standard" else 1
    return NotificationExplanationPacket(
        detail_level=level,
        action=_bounded_text(action_text, 240 if concise else 320),
        change=_bounded_text(change_text, 180 if concise else 260),
        current_flow=_dedupe_rows(current_flow, current_limit, seen, 220) if current_limit else (),
        evidence=_dedupe_rows(evidence, int(profile["maxEvidence"]), seen, 190 if concise else 240),
        counter_evidence=_dedupe_rows(counter_evidence, int(profile["maxCounterEvidence"]), seen, 180 if concise else 220),
        inference=_dedupe_rows(inference, inference_limit, seen, 260) if inference_limit else (),
        company_value=_dedupe_rows(company_value, company_limit, seen, 240) if company_limit else (),
        next_checks=_dedupe_rows(next_checks, int(profile["maxNextChecks"]), seen, 220 if concise else 280),
        data_warnings=_dedupe_rows(data_warnings, warning_limit, seen, 200 if concise else 260),
    )
