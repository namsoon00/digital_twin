"""Read at most two discovered filings through the existing provider guard."""
import hashlib
import re
import urllib.parse

from .opendart_xbrl import parse_opendart_xbrl_archive, opendart_xbrl_error_response


def collect_xbrl_reports(provider, api_key, symbol, reports):
    documents, unavailable = [], []
    for report in reports[:2]:
        receipt = str(report.get("receiptNo") or "")
        code = str(report.get("reportCode") or "11011")
        if not re.fullmatch(r"\d{14}", receipt) or code not in {"11011", "11012", "11013", "11014"}:
            raise ValueError("Invalid discovered XBRL report identity")
        url = "https://opendart.fss.or.kr/api/fnlttXbrl.xml?" + urllib.parse.urlencode({
            "crtfc_key": api_key, "rcept_no": receipt,
        })
        raw = provider.guarded_call("OpenDART", "xbrl:" + symbol + ":" + receipt,
            lambda: provider.fetch_bytes(url, {"Accept": "application/zip,application/xml"}))
        error = opendart_xbrl_error_response(raw)
        if error:
            if error.get("status") in {"013", "014"}:
                unavailable.append({"receiptNo": receipt, "reason": "official-xbrl-not-found"})
                continue
            raise RuntimeError("OpenDART XBRL " + str(error.get("status") or "error"))
        document = parse_opendart_xbrl_archive(raw, receipt_no=receipt, symbol=symbol,
            business_year=str(report.get("businessYear") or ""), report_code=code)
        document["receiptDate"] = receipt[:4] + "-" + receipt[4:6] + "-" + receipt[6:8]
        documents.append(document)
    revision = hashlib.sha256("|".join(
        d["receiptNo"] + ":" + d["archiveHash"] for d in documents
    ).encode()).hexdigest()
    return {"schemaVersion": "opendart-xbrl-reports-v2", "symbol": symbol, "provider": "OpenDART XBRL",
            "documents": documents, "unavailableReports": unavailable,
            "candidateFactCount": sum(d["candidateFactCount"] for d in documents),
            "archiveHash": revision}
