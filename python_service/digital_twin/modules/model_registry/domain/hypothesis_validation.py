"""Validation receipts are not investment evidence or an empirical qualification."""

from .hypothesis_development import validation_gate


def preview_states(preview, rule_ids, symbols, world_id):
    status = str(preview.get("status") or "")
    if status != "ok":
        retryable = status in {"missing-abox", "incomplete-abox", "provisioning", "unavailable", "error", "typedb-error"}
        return ("needs-data", "needs-data") if retryable else ("blocked", "blocked")
    native = preview.get("nativeMatchResult") or {}
    proven = (
        preview.get("nativeTypeDbReasoningUsed") is True
        and preview.get("typedbDirectTypeqlUsed") is True
        and preview.get("validationOnly") is True
        and preview.get("mutatedOperationalRuleBox") is False
        and preview.get("wroteInferenceBox") is False
        and not preview.get("nativeCandidateExecutionSkipped")
        and native.get("status") == "ok"
        and int(native.get("executedRuleCount") or 0) == len(rule_ids)
        and int(native.get("skippedRuleCount") or 0) == 0
        and set(preview.get("candidateRuleIds") or []) == set(rule_ids)
        and set(preview.get("targetSymbols") or []) == set(symbols)
        and preview.get("worldId") == world_id
        and type(preview.get("matchedCount")) is int and preview["matchedCount"] >= 0
    )
    if not proven:
        return "blocked", "blocked"
    return "passed", "passed" if preview["matchedCount"] > 0 else "needs-data"


def additional_validation_gate(requirements, type_status, replay_status):
    checks = []
    for item in requirements:
        check = item["check"]
        status = {"typedb-execution": type_status, "current-match": replay_status}.get(check, "not-run")
        checks.append({**item, "status": status,
                       "authority": "independent-forward-outcomes" if check == "paired-forward-outcomes" else "verified-review-required" if check == "review" else "TypeDB-preview"})
    status = "passed"
    if any(row["status"] == "blocked" for row in checks):
        status = "blocked"
    elif any(row["status"] != "passed" for row in checks):
        status = "not-run"
    outstanding = [row["requirement"] for row in checks if row["status"] != "passed"]
    return validation_gate(
        "additional-validation", "후속 검증 요구", status,
        " / ".join(outstanding) or "추가 검증 요구를 확인했습니다.", True,
        {"checks": checks, "snapshotCountsProveCausality": False},
    )
