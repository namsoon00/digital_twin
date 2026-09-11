"""Account command composition, separate from read-only account dependencies."""


def build_account_service(settings=None, event_publisher=None):
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.modules.accounts.public import AccountApplicationService

    repository = stores.account_registry(settings)
    publisher = event_publisher if event_publisher is not None else default_event_bus()
    return AccountApplicationService(repository, repository.settings, event_publisher=publisher)
