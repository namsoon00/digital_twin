"""Retry transport failures, never replay a semantic mismatch inline."""

from typing import Callable, Dict

from .ports import CandidateRetryStore


def with_scoped_abox_candidate_verification_retry(store: CandidateRetryStore, operation, timing: Dict[str, object]=None, verification: Dict[str, object]=None, *, error_code: Callable[[object], str]):
    """Run one candidate write and never replay a semantic mismatch inline.

    A candidate verification failure is not a transient transport error.
    Replaying the complete write in the live inference request doubled the
    most expensive ABox stage and could starve unrelated symbols. The next
    projection attempt clears this immutable candidate before writing, so
    keep the active generation and let the control plane retry it later.
    """

    telemetry = timing if isinstance(timing, dict) else {}
    scope_verification = verification if isinstance(verification, dict) else {}
    retry_transient = lambda error: error_code(error) in {  # noqa: E731
        "typedbConnectionError",
        "typedbTimeout",
    }
    try:
        return store.with_typedb_retries(operation, retry_if=retry_transient)
    except Exception as error:
        if error_code(error) != "typedbCandidateVerificationError":
            raise
        telemetry["candidateVerificationRetryAttempted"] = False
        telemetry["candidateVerificationRetryCount"] = 0
        telemetry["candidateVerificationRetryDeferred"] = True
        telemetry["candidateVerificationFirstFailure"] = {
            "reason": str(error)[:220],
            "failedScopeIds": sorted(
                scope_id
                for scope_id, value in scope_verification.items()
                if str((value or {}).get("status") or "") != "ok"
            ),
            "scopeVerification": {
                scope_id: dict(value or {})
                for scope_id, value in scope_verification.items()
                if str((value or {}).get("status") or "") != "ok"
            },
        }
        raise
