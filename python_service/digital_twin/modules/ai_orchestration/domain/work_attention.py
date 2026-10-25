"""Explainable work ordering, never investment scoring or model authority."""
from datetime import datetime, timedelta
import math

VERSION = "central-work-attention-v1"
WINDOW = 20


def clock(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def subject_context(position, total, observed_at):
    """Monitor values are scheduling hints only; AI still reads frozen ABox facts."""
    value = number(position.account_value_krw) or number(position.market_value_krw)
    total = number(total)
    weight = 100 * value / total if value is not None and total and total > 0 else None
    return {"version": VERSION, "observedAt": observed_at,
        "held": position.source == "holding" and bool(position.quantity),
        "weightPct": round(weight, 4) if weight is not None and 0 <= weight <= 100 else None,
        "basis": "stored-monitor-account-value-over-portfolio-total",
        "authority": "work-order-only"}


def attention(task, subject, case, now):
    scoped = all(subject.get(k) == task.get(k) for k in ("accountId", "symbol", "worldId"))
    hint = subject.get("workContext") or {} if scoped else {}
    observed = clock(hint.get("observedAt"))
    usable = (hint.get("version") == VERSION and hint.get("authority") == "work-order-only"
              and observed is not None and timedelta(0) <= now - observed <= timedelta(hours=72))
    held = usable and hint.get("held") is True
    weight = number(hint.get("weightPct")) if held else None
    weight = weight if weight is not None and 0 <= weight <= 100 else 0
    owned = (case.get("caseId") == task.get("brainCaseId") and bool(task.get("brainCaseId"))
        and all(case.get(k) == task.get(k) for k in ("accountId", "symbol", "worldId"))
        and case.get("status") in {"open", "waiting", "review-needed", "blocked"})
    answer = case.get("latestSourceAnswer") or {} if owned else {}
    review = case.get("questionAssessment") or {} if owned else {}
    finish = answer.get("status") == "answered" and review.get("status") != "answered"
    partial = answer.get("status") == "partial"
    counter = (bool(review.get("researchReviewInputId")) and
               (review.get("judgmentImpact") or {}).get("effect") == "weakens")
    reasons = (["확보한 답변의 최종 검토"] if finish else []) + (["보유 종목"] if held else [])
    reasons += (["이전 판단의 반대 근거 확인"] if counter else []) + (["기존 부분 답변 이어가기"] if partial else [])
    return {"version": VERSION, "authority": "work-order-only", "held": bool(held),
        "weightPct": weight if held else None, "contextObservedAt": hint.get("observedAt") if usable else None,
        "sourceAnswerReady": finish, "partialAnswer": partial, "counterEvidenceReview": counter,
        "reasons": reasons or ["기존 작업 순서"],
        "rank": [int(finish), int(counter), int(held), weight, int(partial)]}


def select(rows, subjects, cases, cursor, lane, now):
    indexed = {(s["accountId"], s["symbol"], s["worldId"]): s for s in subjects}
    values = [attention(row, indexed.get(tuple(row.get(k) for k in ("accountId", "symbol", "worldId")), {}),
                        cases.get(row.get("brainCaseId"), {}), now) for row in rows]
    # Alternate per lane; a large holding must never starve old/watchlist work.
    fair = int((cursor.get("laneDispatchCounts") or {}).get(lane, 0)) % 2 == 0
    index = 0 if fair else max(range(len(rows)), key=lambda i: (values[i]["rank"], -i))
    return index, {**values[index], "selection": "oldest-fair-turn" if fair else "importance-turn",
                   "candidateCount": len(rows), "candidateLimit": WINDOW}
