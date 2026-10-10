"""Stable, account/world/subject-scoped research conversation identity."""
import hashlib
import json


class ResearchDeliveryDeferred(RuntimeError):
    """Wait for the original question or another sender without spending retries."""


def research_thread_key(job):
    if job.message_type != "researchProgress":
        return ""
    event = (job.context or {}).get("researchProgress") or {}
    if event.get("accountId") != job.account_id or not all(event.get(k) for k in ("caseId", "worldId", "symbol")):
        raise ValueError("연구 알림의 계정·대상 연결 정보를 확인할 수 없습니다.")
    sources = list(dict.fromkeys(event.get("sourceQuestionIds") or []))
    # Only unambiguous question lineage shares a conversation. Multi-question
    # hypotheses own a separate conversation instead of choosing arbitrarily.
    root = sources[0] if len(sources) == 1 else event["caseId"]
    return hashlib.sha256(json.dumps([job.account_id, event["worldId"], event["symbol"], root]).encode()).hexdigest()
