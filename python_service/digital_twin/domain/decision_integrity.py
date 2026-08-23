"""Integrity checks for one immutable investment decision episode."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping


DECISION_INTEGRITY_VERSION = "decision-integrity-v1"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def decision_comparison_state(
    episode: Mapping[str, object],
    type_db_actions: Iterable[object],
    final_action: object,
) -> Dict[str, object]:
    """Describe TypeDB/AI participation without inventing an agreement."""

    facts = _mapping(episode.get("factsAtDecision") or episode.get("facts_at_decision"))
    explicit = _text(facts.get("decisionComparisonState"))
    source = _text(episode.get("source")).lower()
    actions = list(dict.fromkeys(
        _text(value).upper() for value in type_db_actions or [] if _text(value)
    ))
    action = _text(final_action).upper()
    if explicit:
        state = explicit
    elif "typedb" in source and "fallback" in source:
        state = "typedb-only"
    elif not actions and action:
        state = "ai-only"
    elif actions and action in actions:
        state = "agreed"
    elif actions and action:
        state = "adjusted"
    else:
        state = "unavailable"
    labels = {
        "agreed": "TypeDB 후보와 AI 의견 일치",
        "adjusted": "AI가 TypeDB 후보를 조정",
        "typedb-only": "TypeDB 추론만 사용",
        "ai-only": "AI 의견만 기록",
        "unavailable": "비교할 판단 없음",
    }
    comparable = state in {"agreed", "adjusted"}
    return {
        "state": state,
        "label": labels.get(state, labels["unavailable"]),
        "comparable": comparable,
        "different": state == "adjusted" if comparable else None,
        "typeDbCandidateActions": actions,
        "aiFinalAction": action,
        "decisionSource": _text(facts.get("decisionWriter")) or _text(episode.get("source")),
    }


def validate_decision_episode_integrity(episode: Mapping[str, object]) -> Dict[str, object]:
    """Return a user-safe audit state without querying a live graph."""

    facts = _mapping(episode.get("factsAtDecision") or episode.get("facts_at_decision"))
    reasoning = _mapping(facts.get("reasoningDetailSnapshot"))
    issues = []

    def add(code: str, label: str, severity: str, detail: str) -> None:
        issues.append({
            "code": code,
            "label": label,
            "severity": severity,
            "detail": detail,
        })

    if not _text(episode.get("episodeId") or episode.get("episode_id")):
        add("episode-id-missing", "판단 식별자 없음", "blocked", "영구 판단 링크를 만들 수 없습니다.")
    if not _text(episode.get("sourceAboxSnapshotId") or episode.get("source_abox_snapshot_id")):
        add("source-snapshot-missing", "원천 스냅샷 없음", "blocked", "판단 시점의 원천 사실을 재현할 수 없습니다.")
    if not _text(episode.get("inferenceGenerationId") or episode.get("inference_generation_id")):
        add("inference-generation-missing", "추론 세대 없음", "blocked", "TypeDB 추론 결과와 판단을 연결할 수 없습니다.")

    snapshot_state = _text(reasoning.get("snapshotState"))
    if not reasoning:
        add(
            "reasoning-snapshot-missing",
            "추론 상세 미저장",
            "warning",
            "판단 식별자는 유효하지만 사실·관계·규칙의 당시 상세는 부분 복원만 가능합니다.",
        )
    elif snapshot_state != "exact":
        add("reasoning-snapshot-reconstructed", "추론 상세 부분 복원", "warning", "당시 저장된 값만 확정적으로 사용할 수 있습니다.")
    else:
        incomplete_conditions = 0
        for trace in reasoning.get("traces") or []:
            trace_row = _mapping(trace)
            for condition in trace_row.get("conditions") or []:
                row = _mapping(condition)
                if not _text(row.get("field") or row.get("relationType")) or row.get("observedValue") in (None, ""):
                    incomplete_conditions += 1
        if incomplete_conditions:
            add(
                "reasoning-condition-incomplete",
                "규칙 관측값 일부 누락",
                "warning",
                f"성립 조건 {incomplete_conditions}건은 관측 필드 또는 값이 완전하지 않습니다.",
            )

    state = "blocked" if any(item["severity"] == "blocked" for item in issues) else (
        "warning" if issues else "pass"
    )
    return {
        "version": DECISION_INTEGRITY_VERSION,
        "state": state,
        "label": {"pass": "판단 기록 확인 완료", "warning": "판단 기록 일부 확인", "blocked": "판단 기록 연결 실패"}[state],
        "exact": state == "pass",
        "issues": issues,
    }
