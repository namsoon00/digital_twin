import html
import re
from html.parser import HTMLParser
from typing import Dict, List

from ..domain.market_data import number
from ..domain.portfolio import Position
from .external_signal_utils import symbol_assignments


DEFAULT_SEC_COMPANY_CIKS = {
    "AAPL": "0000320193",
    "AMD": "0000002488",
    "CPNG": "0001834584",
    "MSTR": "0001050446",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "STRC": "0001050446",
    "TSLA": "0001318605",
}

SEC_CONTACT_EMAIL_PATTERN = re.compile(
    r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63})",
    re.IGNORECASE,
)


class SecDocumentTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if str(tag or "").lower() in {"script", "style", "noscript", "svg", "iframe"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if str(tag or "").lower() in {"script", "style", "noscript", "svg", "iframe"}:
            self.skip_depth = max(0, self.skip_depth - 1)

    def handle_data(self, data):
        if not self.skip_depth:
            text = str(data or "").strip()
            if text:
                self.parts.append(text)


def sec_document_text(raw_html: object, limit: int) -> str:
    parser = SecDocumentTextParser()
    try:
        parser.feed(str(raw_html or ""))
    except Exception:  # noqa: BLE001 - malformed SEC HTML falls back to the readable fragments.
        pass
    text = re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()
    return text[:max(500, min(20000, int(limit or 6000)))]


class ExternalSignalSecMixin:
    def sec_enabled(self) -> bool:
        return self.external_api_enabled("externalSecEnabled")

    def configured_sec_user_agent(self) -> str:
        return str(self.settings.get("externalSecUserAgent") or "").strip()

    def sec_contact_email(self) -> str:
        direct = str(self.settings.get("externalSecContactEmail") or "").strip()
        direct_match = SEC_CONTACT_EMAIL_PATTERN.search(direct)
        if direct_match:
            return direct_match.group(1)
        configured = self.configured_sec_user_agent()
        if "local-contact" in configured.lower():
            return ""
        agent_match = SEC_CONTACT_EMAIL_PATTERN.search(configured)
        return agent_match.group(1) if agent_match else ""

    def sec_user_agent(self) -> str:
        configured = self.configured_sec_user_agent()
        if configured and "local-contact" not in configured.lower() and SEC_CONTACT_EMAIL_PATTERN.search(configured):
            return configured
        contact = self.sec_contact_email()
        if contact:
            application = configured if configured and "local-contact" not in configured.lower() else "OrbitAlpha/1.0"
            return application + " " + contact
        return configured or "OrbitAlpha/1.0 local-contact"

    def sec_headers(self) -> Dict[str, str]:
        return {"Accept": "application/json", "User-Agent": self.sec_user_agent()}

    def sec_document_headers(self) -> Dict[str, str]:
        return {"Accept": "text/html,application/xhtml+xml", "User-Agent": self.sec_user_agent()}

    def sec_document_text_enabled(self) -> bool:
        return str(self.settings.get("externalSecDocumentTextEnabled") or "0").strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def sec_document_text_max_chars(self) -> int:
        return max(500, min(20000, int(number(self.settings.get("externalSecDocumentTextMaxChars")) or 6000)))

    def sec_document_access_configured(self) -> bool:
        return bool(self.sec_contact_email())

    def sec_ticker_lookup_configured(self) -> bool:
        """SEC rejects anonymous generic agents; known CIKs remain usable."""
        return self.sec_document_access_configured()

    def sec_symbol_key(self, symbol: str) -> str:
        return str(symbol or "").upper().replace(".", "-").strip()

    def sec_symbols(self, positions: List[Position]) -> List[str]:
        if not self.sec_enabled():
            return []
        max_symbols = int(number(self.settings.get("externalSecMaxSymbols")) or 3)
        symbols = []
        seen = set()
        for position in positions:
            if position.is_cash():
                continue
            symbol = self.sec_symbol_key(position.symbol)
            if not symbol or symbol in seen or symbol.isdigit():
                continue
            if position.market.upper() == "US" or position.currency.upper() == "USD":
                seen.add(symbol)
                symbols.append(symbol)
        return symbols[:max(1, max_symbols)]

    def normalize_cik(self, value: object) -> str:
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits.zfill(10) if digits else ""

    def add_sec_edgar(self, signals: Dict[str, object], positions: List[Position]) -> None:
        symbols = self.limited_targets(signals, "SEC EDGAR", self.sec_symbols(positions), "externalSecMaxSymbols", 3)
        if not symbols:
            return
        document_text_enabled = self.sec_document_text_enabled()
        document_access_configured = self.sec_document_access_configured()
        if document_text_enabled and not document_access_configured:
            self.status(
                signals,
                "SEC EDGAR",
                True,
                "filing 본문 조회 보류 · SEC 연락처 이메일을 설정하면 원문 검증을 재개합니다.",
                dataUsable=True,
                deferred=True,
                operationalAlert=False,
                documentTextDataUsable=False,
                configurationKey="externalSecContactEmail",
            )
        mappings = {
            self.sec_symbol_key(symbol): self.normalize_cik(cik)
            for symbol, cik in DEFAULT_SEC_COMPANY_CIKS.items()
            if self.normalize_cik(cik)
        }
        mappings.update({
            self.sec_symbol_key(symbol): self.normalize_cik(cik)
            for symbol, cik in symbol_assignments(self.settings.get("externalSecCompanyCiks") or "").items()
            if self.normalize_cik(cik)
        })
        ticker_map: Dict[str, str] = {}
        missing_symbols = [symbol for symbol in symbols if symbol not in mappings]
        if missing_symbols and not self.sec_ticker_lookup_configured():
            self.status(
                signals,
                "SEC EDGAR",
                True,
                "CIK 자동 조회 보류 · externalSecUserAgent에 연락처 이메일을 포함해 설정 필요",
                dataUsable=False,
                deferred=True,
                operationalAlert=False,
            )
        elif missing_symbols:
            try:
                def fetch_tickers():
                    return self.sec_ticker_lookup_payload(self.fetch_json("https://www.sec.gov/files/company_tickers.json", self.sec_headers()))

                ticker_map = self.guarded_call("SEC EDGAR", "company_tickers", fetch_tickers)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "SEC EDGAR", "company_tickers ", error)

        for symbol in symbols:
            cik = mappings.get(symbol) or ticker_map.get(symbol) or ""
            if not cik:
                self.status(signals, "SEC EDGAR", True, symbol + " CIK mapping 없음")
                continue
            try:
                def fetch_submissions():
                    return self.fetch_json("https://data.sec.gov/submissions/CIK" + cik + ".json", self.sec_headers())

                submissions = self.guarded_call("SEC EDGAR", "submissions:" + symbol, fetch_submissions)
                filing = self.latest_sec_filing(submissions, cik)
                if filing and document_text_enabled and filing.get("url"):
                    if not document_access_configured:
                        filing.update({
                            "documentText": "",
                            "documentTextPreview": "",
                            "documentTextQuality": "deferred-contact",
                            "documentTextStatus": "deferred-contact",
                            "documentTextReason": "SEC 연락처 이메일 설정 후 원문을 수집합니다.",
                        })
                    else:
                        try:
                            def fetch_filing_document():
                                return self.fetch_text(str(filing["url"]), self.sec_document_headers())

                            raw_document = self.guarded_call("SEC EDGAR", "filing-document:" + symbol, fetch_filing_document)
                            document_text = sec_document_text(raw_document, self.sec_document_text_max_chars())
                            filing.update({
                                "documentText": document_text,
                                "documentTextPreview": document_text[:700],
                                "documentTextQuality": "body" if len(document_text) >= 120 else "insufficient",
                            })
                        except Exception as error:  # noqa: BLE001 - retain filing metadata and surface document fallback.
                            filing.update({"documentText": "", "documentTextPreview": "", "documentTextQuality": "unavailable"})
                            self.status_for_error(signals, "SEC EDGAR", symbol + " filing document ", error)

                def fetch_facts():
                    return self.fetch_json("https://data.sec.gov/api/xbrl/companyfacts/CIK" + cik + ".json", self.sec_headers())

                facts = self.guarded_call("SEC EDGAR", "companyfacts:" + symbol, fetch_facts)
                signals["secFilings"][symbol] = {
                    "provider": "SEC EDGAR",
                    "symbol": symbol,
                    "cik": cik,
                    "companyName": str(submissions.get("name") or facts.get("entityName") or symbol),
                    "latestFiling": filing,
                    "facts": self.sec_company_facts_summary(facts),
                }
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "SEC EDGAR", symbol + " ", error)

    def sec_ticker_lookup_payload(self, payload: object) -> Dict[str, str]:
        if not isinstance(payload, dict):
            return {}
        values = payload.values()
        return {
            self.sec_symbol_key(item.get("ticker")): self.normalize_cik(item.get("cik_str"))
            for item in values
            if isinstance(item, dict) and item.get("ticker") and self.normalize_cik(item.get("cik_str"))
        }

    def latest_sec_filing(self, payload: Dict[str, object], cik: str) -> Dict[str, object]:
        recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), dict) else {}
        forms = recent.get("form") if isinstance(recent.get("form"), list) else []
        preferred_forms = {"10-K", "10-Q", "8-K", "20-F", "40-F", "6-K"}
        selected_index = next((index for index, form in enumerate(forms) if str(form or "").upper() in preferred_forms), None)
        if selected_index is None and forms:
            selected_index = 0
        if selected_index is None:
            return {}

        def recent_value(key: str) -> str:
            values = recent.get(key) if isinstance(recent.get(key), list) else []
            return str(values[selected_index] or "") if selected_index < len(values) else ""

        accession = recent_value("accessionNumber")
        primary_document = recent_value("primaryDocument")
        cik_path = str(int(cik)) if cik and cik.isdigit() else cik.lstrip("0")
        accession_path = accession.replace("-", "")
        filing_url = (
            "https://www.sec.gov/Archives/edgar/data/" + cik_path + "/" + accession_path + "/" + primary_document
            if cik_path and accession_path and primary_document
            else ""
        )
        return {
            "form": str(forms[selected_index] or ""),
            "filingDate": recent_value("filingDate"),
            "reportDate": recent_value("reportDate"),
            "accessionNumber": accession,
            "primaryDocument": primary_document,
            "url": filing_url,
        }

    def sec_company_facts_summary(self, payload: Dict[str, object]) -> Dict[str, object]:
        facts = payload.get("facts", {}).get("us-gaap", {}) if isinstance(payload.get("facts"), dict) else {}
        if not isinstance(facts, dict):
            facts = {}
        return {
            "entityName": str(payload.get("entityName") or ""),
            "revenue": self.latest_sec_fact(facts, [
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "SalesRevenueNet",
            ]),
            "netIncome": self.latest_sec_fact(facts, ["NetIncomeLoss", "ProfitLoss"]),
            "assets": self.latest_sec_fact(facts, ["Assets"]),
            "liabilities": self.latest_sec_fact(facts, ["Liabilities"]),
            "equity": self.latest_sec_fact(facts, ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
        }

    def latest_sec_fact(self, facts: Dict[str, object], tags: List[str]) -> Dict[str, object]:
        financial_forms = {"10-K", "10-Q", "20-F", "40-F"}
        for tag in tags:
            concept = facts.get(tag)
            units = concept.get("units") if isinstance(concept, dict) else {}
            values = units.get("USD") if isinstance(units, dict) else []
            if not isinstance(values, list):
                continue
            candidates = [
                item for item in values
                if isinstance(item, dict)
                and str(item.get("form") or "").upper() in financial_forms
                and item.get("val") not in (None, "")
            ]
            if not candidates:
                continue
            latest = sorted(
                candidates,
                key=lambda item: (str(item.get("filed") or ""), str(item.get("end") or "")),
                reverse=True,
            )[0]
            return {
                "tag": tag,
                "value": number(latest.get("val")),
                "end": str(latest.get("end") or ""),
                "filed": str(latest.get("filed") or ""),
                "fy": str(latest.get("fy") or ""),
                "fp": str(latest.get("fp") or ""),
                "form": str(latest.get("form") or ""),
            }
        return {}
