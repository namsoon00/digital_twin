"""Source and measurement updates with no AI or trading authority."""

import html
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit


def _text(value):
    return html.escape(str(value or ""), quote=True)


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M KST")
    except (ValueError, TypeError):
        return str(value or "")


def information_update_message(row, phase):
    source = row["source"]
    title = "공식 발표 결과" if phase == "release" else "공개 이후 가격 확인"
    lines = ["<b>" + title + "</b>", _text(source["title"]), ""]
    if phase == "release":
        for metric in source.get("releaseMetrics") or []:
            value = metric.get("actual")
            if isinstance(value, list):
                value = " ~ ".join(str(item) for item in value)
            line = str(metric.get("label")) + ": " + str(value) + str(metric.get("unit") or "")
            if metric.get("previous") is not None:
                line += " (직전 " + str(metric["previous"]) + str(metric.get("unit") or "") + ")"
            lines.append("• " + _text(line))
    else:
        for item in (row.get("marketReaction") or {}).get("observations") or []:
            if str(item.get("horizonMinutes")) != phase or item.get("status") != "observed":
                continue
            before, after = item["baseline"], item["outcome"]
            lines.extend(["• " + _text(item["symbol"] + " · " + ("24시간" if phase == "1440" else "1시간") + " 후 " + format(item["priceChangePercent"], "+.2f") + "%"),
                "  " + _text(str(before["price"]) + " → " + str(after["price"]) + " " + after["currency"]),
                "  " + _text(_time(before["sourceAsOf"]) + " → " + _time(after["sourceAsOf"]))])
        lines.extend(["", "이 구간의 가격 변화이며, 해당 사건만의 영향으로 단정하지 않습니다."])
    published = _time(source["eventAt"]) if source.get("eventAt") else str(source.get("publicationDate") or "") + " (날짜만 확인)"
    lines.extend(["", "발표·발행 " + _text(published), "확인 " + _text(_time(row["updatedAt"]))])
    if row["status"] == "active":
        lines.append("시스템이 남은 관측 구간을 계속 확인합니다.")
    url = str(source.get("sourceUrl") or "")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "\n".join(lines)
    if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password and not any(ord(char) <= 32 for char in url):
        lines.append('<a href="' + _text(url) + '">근거 원문</a>')
    return "\n".join(lines)
