from typing import Dict, List

from .message_types import EXTERNAL_DATA_CONNECTION
from .portfolio import AccountSnapshot, AlertEvent


def _compact_text(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


class ExternalSignalAlertMixin:
    """Emit only operational external-source alerts.

    Investment-relevant external observations now enter the ABox and are
    evaluated by TypeDB RuleBox rules. Keeping a parallel crypto threshold
    alert here would bypass the graph, duplicate notifications, and re-create
    a second Python decision policy.
    """

    def external_signal_events(self, snapshot: AccountSnapshot, _previous: Dict[str, object]) -> List[AlertEvent]:
        signals = snapshot.external_signals or {}
        if not signals:
            return []
        return self.external_data_connection_events(snapshot, signals)

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
