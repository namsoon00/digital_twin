from datetime import datetime, timedelta, timezone

from ..domain.information_observation import exact_time, price_observation


class InformationObservationService:
    """Read existing time series only; no orders, notifications or vendor calls."""

    def __init__(self, reader, now=None):
        self.reader = reader
        self.now = now or (lambda: datetime.now(timezone.utc))

    def observe(self, event_at, symbols):
        event = exact_time(event_at)
        now = self.now()
        result = {"version": "information-price-observation-v1", "eventAt": event_at, "checkedAt": now.isoformat(),
                  "status": "unavailable", "label": "공개 전후 가격 관측", "observations": [],
                  "note": "시간상 전후 비교이며 이 사건이 가격 변화를 일으켰다는 뜻은 아닙니다. 거래량은 집계 기준이 달라 비교하지 않습니다.",
                  "monitoringMode": "on-read", "notificationRegistered": False, "decisionAuthority": False}
        if not event or event > now:
            return {**result, "label": "공식 발표·발행 시각 미확인" if not event else "발표 전"}
        symbols = sorted({str(value or "").upper().strip() for value in symbols if str(value or "").strip()})[:3]
        bases = [{"requestId": symbol, "symbol": symbol, "targetAt": event.isoformat()} for symbol in symbols]
        targets = [{"requestId": symbol + ":" + str(minutes), "symbol": symbol, "targetAt": (event + timedelta(minutes=minutes)).isoformat()}
                   for symbol in symbols for minutes in [60, 1440] if event + timedelta(minutes=minutes) <= now]
        if not symbols:
            return {**result, "label": "등록된 관측 종목 없음"}
        try:
            baseline = self.reader.load_baseline_observations("__market_data__", bases, max_age_minutes=1440)
            outcomes = self.reader.load_outcome_observations("__market_data__", targets, max_delay_minutes=180) if targets else {}
        except Exception:
            return {**result, "status": "error", "label": "가격 관측 기록 조회 오류"}
        result["observations"] = [price_observation(symbol, event_at, minutes, baseline.get(symbol), outcomes.get(symbol + ":" + str(minutes)), now)
                                  for symbol in symbols for minutes in [60, 1440]]
        result["status"] = "observed" if any(row["status"] == "observed" for row in result["observations"]) else "insufficient"
        return result
