"""Publication policy for independent, actionless AI observations."""
from datetime import datetime, timezone
import math


MESSAGE_TYPE = "aiObservation"
RETIRED_REASON = "기존 규칙 기반 투자 알림 경로가 종료되었습니다. 중앙 AI의 독립 관찰로 전환했습니다."
COOLDOWN_MINUTES = 180
DAILY_SUBJECT_LIMIT = 2
DAILY_ACCOUNT_LIMIT = 8


def legacy_route_retired(settings):
    # Runtime composition always supplies this route. Historical component
    # replays can still exercise their old contracts without enabling a worker.
    return (settings or {}).get("investmentNotificationRoute") == "ai-control"


def instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def quote_from(packet):
    for fact in packet.get("facts", []):
        try:
            if math.isfinite(float(fact.get("currentPrice"))) and float(fact["currentPrice"]) > 0:
                return fact
        except (TypeError, ValueError):
            pass
    return {}


def publication_block(result, now=None):
    now = now or datetime.now(timezone.utc)
    if result.get("notification", {}).get("send") is not True:
        return "AI가 지금 알릴 만큼 새로운 해석이 없다고 판단했습니다."
    packet = result.get("input", {})
    captured = instant(packet.get("capturedAt"))
    if captured is None or not 0 <= (now - captured).total_seconds() <= 1800:
        return "분석 근거를 수집한 지 오래되어 다음 관찰에서 다시 확인합니다."
    quote = quote_from(packet)
    if quote.get("id") not in result.get("evidenceIds", []):
        return "표시할 시세가 분석에서 인용한 근거에 포함되지 않아 재확인이 필요합니다."
    clock = instant(quote.get("sourceAsOf") or quote.get("asOf"))
    if not quote or clock is None or not 0 <= (now - clock).total_seconds() <= 86400:
        return "확인 가능한 최근 시세가 없어 관찰 기록으로만 보관합니다."
    if str(quote.get("freshnessStatus", "")).lower() in {"stale", "expired", "invalid", "missing"}:
        return "시세의 유효성이 확인되지 않아 다음 수집 결과를 기다립니다."
    return ""


def repeat_block(result, receipts, account_id, symbol, now=None):
    now = now or datetime.now(timezone.utc)
    subject = [row for row in receipts if row.get("accountId") == account_id and row.get("symbol") == symbol]
    if any(row.get("inputFingerprint") == result.get("inputFingerprint") for row in subject):
        return "이미 전달한 근거와 같아 반복 발송하지 않습니다."
    meaning = result.get("quality", {}).get("insightFingerprint")
    latest = max(subject, key=lambda row: instant(row.get("deliveredAt")) or datetime.min.replace(tzinfo=timezone.utc), default={})
    if meaning and latest.get("insightFingerprint") == meaning:
        return "이미 전달한 설명과 관측 관계가 같아 반복 발송하지 않습니다."
    recent = [row for row in subject if instant(row.get("deliveredAt")) is not None]
    invalidated = any(row.get("transitionVerified") and row.get("effect") == "invalidates"
                      for row in result.get("input", {}).get("followUpEvaluations", []))
    gap = 60 if invalidated else COOLDOWN_MINUTES
    if recent and (now - max(instant(row["deliveredAt"]) for row in recent)).total_seconds() < gap * 60:
        return "반증 조건 전환의 최소 간격 1시간이 지나지 않았습니다." if invalidated else "같은 종목의 알림 간격 3시간이 지나지 않았습니다."
    today = [row for row in receipts if row.get("accountId") == account_id and str(row.get("deliveredAt", ""))[:10] == now.date().isoformat()]
    if len([row for row in today if row.get("symbol") == symbol]) >= DAILY_SUBJECT_LIMIT:
        return "오늘 이 종목의 알림 한도에 도달했습니다."
    if len(today) >= DAILY_ACCOUNT_LIMIT:
        return "오늘 계정의 AI 관찰 알림 한도에 도달했습니다."
    return ""
