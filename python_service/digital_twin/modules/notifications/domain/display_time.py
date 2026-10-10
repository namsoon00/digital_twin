"""Korean notification display clocks; source/audit timestamps stay unchanged."""
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
# Protect links and markup: timestamps inside an evidence URL are identifiers.
_SEGMENTS = re.compile(r"(<[^>]*>|https?://[^\s<>]+)")
_STAMP = re.compile(r"(?<![\w:/.-])\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?| \d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2}| UTC))(?![\w:+.-])")


def kst_timestamp(value, pattern="%Y-%m-%d %H:%M KST"):
    text = str(value or "").strip()
    if not text:
        return "시점 미확인"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text + " (시각 미기록)"
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" UTC", "+00:00"))
        # Internal naive clocks historically represent UTC, never host local time.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(KST).strftime(pattern)
    except (ValueError, TypeError):
        return text


def notification_times_kst(text):
    parts = _SEGMENTS.split(str(text or ""))
    for index in range(0, len(parts), 2):
        parts[index] = _STAMP.sub(lambda match: kst_timestamp(match.group()), parts[index])
    return "".join(parts)
