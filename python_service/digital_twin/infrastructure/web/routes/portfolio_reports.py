"""Owner-only report snapshots and explicit one-time delivery requests."""
import re

from digital_twin.infrastructure.composition.portfolio_reports import build_portfolio_report_service
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.router import NOT_HANDLED


def portfolio_report_response(request, path, query, service_factory=build_portfolio_report_service):
    if not (path == "/api/ai-reports" or path.startswith("/api/ai-reports/")):
        return NOT_HANDLED
    if request.share_access().role not in {"owner", "local-owner"}:
        return request.send_payload(403, {"error": "AI 보고서는 소유자만 확인할 수 있습니다."})
    match = re.fullmatch(r"/api/ai-reports/([0-9a-f]{64})(/send|/delivery)?", path)
    create = path == "/api/ai-reports" and request.command == "POST"
    send = bool(match and match[2] == "/send" and request.command == "POST")
    if request.command != "GET" and not (create or send):
        return request.send_payload(405, {"error": "지원하지 않는 보고서 요청입니다."})
    if (create or send) and not request.ensure_writable("보고서 생성·발송은 소유자만 요청할 수 있습니다."):
        return None
    try:
        body = request.read_json_body() if create or send else {}
        if not isinstance(body, dict):
            raise ValueError("보고서 요청 형식을 확인해 주세요.")
        account = body.get("accountId") if create or send else first_query(query, "accountId")
        service = service_factory(writable=create)
        if path == "/api/ai-reports/accounts" and request.command == "GET":
            return request.send_payload(200, {"accounts": service.accounts()})
        if create:
            report = service.create(account, body.get("requestId"))
            return request.send_payload(201, {"report": report, "delivery": service.publisher.status(report)})
        if send:
            return request.send_payload(200, {"delivery": service.send(account, match[1])})
        if path == "/api/ai-reports":
            return request.send_payload(200, {"reports": service.list(account)})
        if match and match[2] in {None, "/delivery"}:
            report = service.get(account, match[1])
            if report:
                if match[2] == "/delivery":
                    return request.send_payload(200, {"delivery": service.publisher.status(report)})
                return request.send_payload(200, {"report": report, "delivery": service.publisher.status(report)})
        return request.send_payload(404, {"error": "보고서를 찾지 못했습니다."})
    except LookupError:
        return request.send_payload(404, {"error": "보고서를 찾지 못했습니다."})
    except (ValueError, TypeError):
        return request.send_payload(400, {"error": "계정·요청 식별자 또는 저장된 근거를 확인하지 못했습니다. 새로고침 후 다시 시도해 주세요."})
