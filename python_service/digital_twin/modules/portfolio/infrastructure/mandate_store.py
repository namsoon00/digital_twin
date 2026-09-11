"""Connection-bound portfolio mandate history and current-state writes."""

import hashlib
from digital_twin.domain.investment_mandate import InvestmentMandate
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.infrastructure.settings import utc_now


def save_mandate_with_connection(connection, mandate: InvestmentMandate, stamp: str = "") -> None:
    stamp = str(stamp or utc_now())
    version_record_id = "mandate-version:" + hashlib.sha256(
        (mandate.mandate_id + "|" + mandate.policy_version).encode("utf-8")
    ).hexdigest()[:32]
    connection.execute(
        """
        INSERT IGNORE INTO investment_mandate_versions (
            mandate_version_id, mandate_id, portfolio_id, account_id,
            policy_version, profile, effective_at, payload_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            version_record_id,
            mandate.mandate_id,
            mandate.portfolio_id,
            mandate.account_id,
            mandate.policy_version,
            mandate.profile,
            mandate.effective_at,
            json_dumps(mandate.to_dict()),
            stamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO investment_mandates (
            mandate_id, portfolio_id, account_id, policy_version, profile,
            status, effective_at, payload_json, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE policy_version = VALUES(policy_version),
            profile = VALUES(profile), status = 'active',
            effective_at = VALUES(effective_at), payload_json = VALUES(payload_json),
            updated_at = VALUES(updated_at)
        """,
        (
            mandate.mandate_id,
            mandate.portfolio_id,
            mandate.account_id,
            mandate.policy_version,
            mandate.profile,
            mandate.effective_at,
            json_dumps(mandate.to_dict()),
            stamp,
            stamp,
        ),
    )
