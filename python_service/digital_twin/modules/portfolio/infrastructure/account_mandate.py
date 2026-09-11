"""Portfolio-owned account mandate writes within a supplied transaction."""

from digital_twin.infrastructure.mysql_investment_domain import save_mandate_with_connection


def write_account_mandate(connection, account, stamp):
    connection.execute(
        "UPDATE service_accounts SET investment_strategy_profile = %s WHERE id = %s",
        (account.investment_strategy_profile, account.account_id),
    )
    save_mandate_with_connection(connection, account.investment_mandate(stamp), stamp)
