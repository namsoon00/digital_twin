from digital_twin.modules.accounts.domain.event_types import ACCOUNT_SAVED, ACCOUNT_REMOVED
from digital_twin.modules.accounts.domain.accounts import AccountConfig
from digital_twin.shared_kernel.events import DomainEvent






def account_saved_event(account: AccountConfig) -> DomainEvent:
    profile = account.domain_profile()
    mandate = account.investment_mandate()
    return DomainEvent(
        name=ACCOUNT_SAVED,
        aggregate_id=account.account_id,
        payload={
            "account": account.masked(),
            "accountDomain": profile.to_dict(),
            "investmentMandate": mandate.to_dict(),
        },
    )


def account_removed_event(account_id: str) -> DomainEvent:
    return DomainEvent(
        name=ACCOUNT_REMOVED,
        aggregate_id=account_id,
        payload={"accountId": account_id},
    )
