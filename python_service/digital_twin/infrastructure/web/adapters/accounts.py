"""Web accounts boundary."""

from digital_twin.infrastructure.service_factory import build_account_service
from digital_twin.infrastructure.web.events import RealtimeEventBridge
from digital_twin.modules.accounts.public import AccountApplicationService
from typing import Dict


def account_service() -> AccountApplicationService:
    return build_account_service(event_publisher=RealtimeEventBridge())


def service_accounts_payload() -> Dict[str, object]:
    return {"accounts": account_service().list_masked()}


def save_account_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return {"account": account_service().save_payload(payload).masked()}


def remove_account_payload(account_id: str) -> Dict[str, object]:
    return {"removed": account_service().remove(account_id), "id": account_id}
