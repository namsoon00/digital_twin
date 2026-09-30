from typing import Dict, List
import hashlib

from digital_twin.modules.notifications.domain.message_types import EXTERNAL_DATA_CONNECTION
from digital_twin.modules.portfolio.contracts import AccountSnapshot, AlertEvent


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
        return self.external_data_connection_events(snapshot, signals, _previous)

    def external_data_connection_events(self, snapshot: AccountSnapshot, signals: Dict[str, object], previous=None) -> List[AlertEvent]:
        previous_metadata = (previous or {}).get("metadata") or {}
        incidents = dict(previous_metadata.get("externalConnectionIncidents") or {})
        grouped: Dict[str, List[str]] = {}
        observed = set()
        for item in signals.get("statuses") or []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "외부 API")
            # Missing sources are not proof of recovery.
            if "ok" not in item:
                continue
            observed.add(source)
            if item.get("ok"):
                continue
            message = str(item.get("message") or "연결 확인 필요")
            grouped.setdefault(source, []).append(message)
        events: List[AlertEvent] = []
        for source, messages in grouped.items():
            incident = incidents.get(source) or hashlib.sha256(
                (snapshot.account_id + ":" + source + ":" + snapshot.generated_at).encode("utf-8")
            ).hexdigest()[:24]
            incidents[source] = incident
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
                ":".join([snapshot.account_id, "external", source, incident, "failed"]),
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
                    "connectionIncidentId": incident,
                    "connectionState": "failed",
                    "notificationSignals": ["statusNoise"],
                },
            ))
        for source in sorted(observed - set(grouped)):
            incident = incidents.pop(source, "")
            if not incident:
                continue
            events.append(AlertEvent(
                snapshot.account_id, snapshot.account_label, "INFO", EXTERNAL_DATA_CONNECTION,
                ":".join([snapshot.account_id, "external", source, incident, "recovered"]),
                "외부 데이터 연결 복구",
                ["공급자 " + source, "상태 이번 수집에서 오류 없이 응답함"],
                criteria=self.criteria("이전 장애 이후 해당 공급자의 성공 응답을 확인했을 때", source + " 연결 복구"),
                metadata={"provider": source, "connectionIncidentId": incident, "connectionState": "recovered",
                          "notificationSignals": ["confirmingData"]},
            ))
        snapshot.metadata = {**dict(snapshot.metadata or {}), "externalConnectionIncidents": incidents}
        return events
