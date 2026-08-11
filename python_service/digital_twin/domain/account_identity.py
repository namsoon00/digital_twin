"""Account identity contracts separated from provider and delivery secrets."""

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List


ACCOUNT_IDENTITY_VERSION = "account-identity-v1"


def _unique_symbols(values: Iterable[object]) -> List[str]:
    return list(dict.fromkeys(
        str(value or "").upper().strip()
        for value in values or []
        if str(value or "").strip()
    ))


@dataclass(frozen=True)
class CredentialRef:
    provider: str
    reference: str
    configured: bool = False

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerageAccount:
    account_id: str
    label: str
    provider: str
    account_sequence: str = ""
    credential_ref: CredentialRef = field(default_factory=lambda: CredentialRef("", ""))
    enabled: bool = True

    @property
    def portfolio_id(self) -> str:
        return "portfolio:" + (self.account_id or "default")

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["portfolioId"] = self.portfolio_id
        payload["credentialRef"] = self.credential_ref.to_dict()
        payload.pop("credential_ref", None)
        return payload


@dataclass(frozen=True)
class AccountUniverse:
    account_id: str
    symbols: List[str] = field(default_factory=list)
    version: str = ACCOUNT_IDENTITY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", _unique_symbols(self.symbols))

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DeliveryProfile:
    account_id: str
    provider: str = ""
    destination_ref: str = ""
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "05:00"
    quiet_hours_timezone: str = "Asia/Seoul"
    message_level: str = "absoluteBeginner"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AccountDomainProfile:
    brokerage_account: BrokerageAccount
    universe: AccountUniverse
    delivery_profile: DeliveryProfile

    @classmethod
    def from_legacy(cls, account: object):
        account_id = str(getattr(account, "account_id", "") or "default")
        provider = str(getattr(account, "provider", "") or "toss")
        credential_ref = CredentialRef(
            provider=provider,
            reference="credential:" + provider + ":" + account_id,
            configured=bool(
                str(getattr(account, "client_id", "") or "").strip()
                and str(getattr(account, "client_secret", "") or "").strip()
            ),
        )
        brokerage_account = BrokerageAccount(
            account_id=account_id,
            label=str(getattr(account, "label", "") or account_id),
            provider=provider,
            account_sequence=str(getattr(account, "account_seq", "") or ""),
            credential_ref=credential_ref,
            enabled=bool(getattr(account, "enabled", True)),
        )
        universe = AccountUniverse(
            account_id=account_id,
            symbols=list(getattr(account, "watchlist_symbols", []) or []),
        )
        destination_configured = bool(
            str(getattr(account, "telegram_chat_id", "") or "").strip()
            or str(getattr(account, "notify_link_url", "") or "").strip()
        )
        delivery_profile = DeliveryProfile(
            account_id=account_id,
            provider=str(getattr(account, "notify_provider", "") or ""),
            destination_ref=("delivery:" + account_id) if destination_configured else "",
            quiet_hours_enabled=bool(getattr(account, "quiet_hours_enabled", True)),
            quiet_hours_start=str(getattr(account, "quiet_hours_start", "") or "22:00"),
            quiet_hours_end=str(getattr(account, "quiet_hours_end", "") or "05:00"),
            quiet_hours_timezone=str(getattr(account, "quiet_hours_timezone", "") or "Asia/Seoul"),
            message_level=str(getattr(account, "message_delivery_level", "") or "absoluteBeginner"),
        )
        return cls(brokerage_account, universe, delivery_profile)

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": ACCOUNT_IDENTITY_VERSION,
            "brokerageAccount": self.brokerage_account.to_dict(),
            "universe": self.universe.to_dict(),
            "deliveryProfile": self.delivery_profile.to_dict(),
        }
