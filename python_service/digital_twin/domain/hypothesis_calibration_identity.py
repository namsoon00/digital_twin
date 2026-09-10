"""Content-addressed qualification identities, independent of family labels."""

import hashlib
import json
from typing import Mapping

from .rule_claim_contract import RuleClaimContract


def claim_validation_fingerprint(value: Mapping[str, object]) -> str:
    source = dict(value or {})
    claim = RuleClaimContract.from_dict(source)
    if not claim.claim_contract_id or not claim.rule_id or not claim.statement:
        return ""
    # Claim semantics include the observation criteria and qualification policy.
    # Optional model identities are preserved when authored by the release.
    semantics = {
        **claim.to_dict(),
        **{key: source[key] for key in ("modelReleaseId", "hypothesisContractId", "ruleFingerprint") if key in source},
    }
    body = json.dumps(semantics, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def claim_revision_identity(claim_id: str, fingerprint: str) -> str:
    if not claim_id or not fingerprint:
        return ""
    body = str(claim_id) + "|" + str(fingerprint)
    return "claim-revision:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
