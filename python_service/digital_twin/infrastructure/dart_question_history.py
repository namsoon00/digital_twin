"""Bounded DART listing reads through the existing provider call guard."""
from urllib.parse import urlencode

from digital_twin.modules.market_data.contracts import ExternalCallDeferred
from digital_twin.modules.news_intelligence.contracts import build_dart_filing_history


def collect_history(provider, symbol, corp, key, begin, now, periods, include_history):
    rows, pages, statuses = [], [], []
    params = {"crtfc_key": key, "corp_code": corp, "bgn_de": begin,
              "end_de": now[:10].replace("-", ""), "pblntf_ty": "A", "last_reprt_at": "N",
              "page_count": 100, "sort": "date", "sort_mth": "desc"}
    try:
        for page in range(1, (2 if include_history else 1) + 1):
            url = "https://opendart.fss.or.kr/api/list.json?" + urlencode({**params, "page_no": page})
            payload = provider.guarded_call("OpenDART",
                "question-history:"+symbol+":"+begin+":"+params["end_de"]+":"+str(page),
                lambda: provider.fetch_json(url))
            if payload.get("status") not in {"000", "013"}:
                statuses.append({"ok": False, "status": "metadata-unavailable",
                                 "reasonCode": "dart-list-"+str(payload.get("status", "unknown"))})
                break
            pages.append(payload)
            rows.extend(row for row in (payload.get("list") or [])[:100]
                        if isinstance(row, dict) and row.get("corp_code") == corp)
            if payload.get("status") == "013" or int(payload.get("total_page") or 1) <= page:
                break
        history = build_dart_filing_history(pages, symbol, corp,
            begin[:4]+"-"+begin[4:6]+"-"+begin[6:8], now, periods) if pages else {}
    except ExternalCallDeferred:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        history = {}
        statuses.append({"ok": False, "status": "filing-history-unverified",
                         "reasonCode": "filing-history-invalid-metadata"})
    except Exception as error:  # Do not copy authenticated URLs from provider errors.
        history = {}
        statuses.append({"ok": False, "status": "metadata-unavailable", "errorKind": type(error).__name__})
    if history:
        statuses.append({"ok": True, "status": "filing-history-collected",
                         "complete": history["complete"], "pageCount": history["pageCount"],
                         "coverageGaps": history["coverageGaps"]})
    return rows, history, statuses
