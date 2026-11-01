"""Separate useful evidence progress from another paid description of a gap."""
import math

from digital_twin.modules.reasoning.contracts import content_hash, observation_progress_facts_identity, OBSERVATION_QUOTE_KINDS
from .observation_abstention import source_delivery
from .research_evidence_use import usable_fact

VERSION = "insight-source-progress-v1"


def enabled(packet):
    return packet.get("insightProgressPolicy") == VERSION and packet.get("executionLane") == "market-observe"


def memory(packet):
    facts = packet.get("facts", [])
    return {"version": VERSION, "factsKey": observation_progress_facts_identity(packet, facts),
        "quoteKey": observation_progress_facts_identity(packet, [f for f in facts if f["kind"] in OBSERVATION_QUOTE_KINDS]),
        "deliveredKey": packet.get("deliveredNotificationContext", {}).get("fingerprint"),
        "questionsKey": content_hash(packet.get("reviewedQuestionContext", {}))}


def source_needs(packet):
    groups = source_delivery(packet)["groups"]
    facts = {f["id"]: f for f in packet.get("facts", [])}
    event = groups["events"]
    needs = []
    if event["status"] != "delivered" or not event["originalTextFactIds"]:
        candidates = [{"title": str(facts[key].get("title", ""))[:160],
                       "publishedAt": str(facts[key].get("publishedAt", ""))[:40]}
                      for key in event["selectedFactIds"][:3]]
        targets = "; ".join(row["title"] + " (" + row["publishedAt"] + ")" for row in candidates if row["title"])
        needs.append({"kind": "event-original", "factIds": event["selectedFactIds"],
            "status": event["status"], "route": "existing-documentary-research",
            "requirement": "시세 변동과 관련된 사건의 실제 원문·원천 공개 시각·기업 관련성을 기존 조사 질문에서 확인해야 합니다. 메타데이터나 기존 AI 요약은 원문 확보가 아닙니다."
                           + (" 확인 대상 자료: " + targets if targets else "")})
    market_ids = groups["market"]["selectedFactIds"]
    # Numeric presence is merely a minimum, not proof of comparable periods.
    # Never replace an absent benchmark with an unrelated asset or invent a return.
    numeric = any(usable_fact(facts[key]) and type(facts[key].get(field)) in (int, float) and math.isfinite(facts[key][field])
                  for key in market_ids for field in ("currentPrice", "changeRate", "returnPct", "relativeReturn", "value"))
    if not numeric:
        needs.append({"kind": "market-comparison", "factIds": market_ids,
            "status": "numeric-observation-missing", "route": "market-data-source-required",
            "requirement": "종목 등락률과 같은 비교 시작·종료 시각의 시장 지수 원천 관측값이 필요합니다. 지수 이름만 있는 기록은 비교 자료가 아닙니다."})
    return needs


def assessment(packet, previous, research=()):
    if not enabled(packet):
        return {}
    needs = source_needs(packet)
    current = memory(packet)
    prior = previous.get("marketProgressMemory") or (memory(previous["input"]) if previous.get("input") else {})
    same_context = (prior.get("version") == VERSION and bool(current["deliveredKey"])
                    and all(prior.get(k) == current[k] for k in ("quoteKey", "deliveredKey", "questionsKey")))
    repeat = (same_context and prior.get("factsKey") == current["factsKey"]
              and previous.get("quality", {}).get("status") == "observation-only"
              and previous.get("notification", {}).get("send") is False
              and previous.get("priceExplanation", {}).get("status") == "cause-unconfirmed"
              and not any(row.get("transitionVerified") for row in packet.get("followUpEvaluations", []))
              and not any(row.get("reviewDue") for row in research if row.get("kind") == "business-thesis"))
    return {"version": VERSION, "needs": needs, "unchangedSelectedEvidence": bool(repeat),
        "skipAuthor": bool(repeat and needs), "authority": "operational-source-progress-only",
        "gapKey": content_hash({"version": VERSION, **{k: packet.get(k) for k in ("accountId", "symbol", "worldId")},
                               "needs": needs}),
        "researchRequirements": [n["requirement"] for n in needs if n["kind"] == "event-original"]}


def research_job(subject, progress, available_at):
    if not progress.get("researchRequirements"):
        return None
    from .planning import identity
    from .research_work import PRIORITY
    needs = [n for n in progress["needs"] if n["kind"] == "event-original"]
    key = research_key(subject, needs)
    return {**subject, "capability": "research-plan", "priority": PRIORITY,
        "taskId": identity("insight-source-gap-v1", key), "sourceProgressPolicy": VERSION,
        "researchGaps": progress["researchRequirements"], "sourceNeeds": needs,
        "availableAt": available_at}


def research_key(subject, needs):
    return content_hash({"version": VERSION, **{k: subject.get(k) for k in ("accountId", "symbol", "worldId")},
                         "needs": needs})


def source_gap_request(job):
    """One captured gap may seek a plan; parked case limits still govern execution.

    The durable task ID deduplicates the gap across observation cycles. Market
    refreshes and timer changes cannot mint another documentary planning pass.
    """
    from .planning import identity
    needs = job.get("sourceNeeds", [])
    return (job.get("sourceProgressPolicy") == VERSION and bool(needs)
            and all(n.get("kind") == "event-original" and n.get("requirement") for n in needs)
            and job.get("researchGaps") == [n["requirement"] for n in needs]
            and job.get("taskId") == identity("insight-source-gap-v1", research_key(job, needs)))
