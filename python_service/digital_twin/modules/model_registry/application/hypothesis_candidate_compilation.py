"""Durable, disabled authoring receipts; no investment or deployment authority."""

import copy
import json

from digital_twin.modules.model_registry.domain.hypothesis_compilation import (
    RULE_DESIGN_VERSION, authoring_input_fingerprint, compilation_blockers, compilation_fingerprint,
)
from digital_twin.modules.portfolio.contracts import utc_now_iso


def capture_compilation(case, result, world):
    candidates = copy.deepcopy(result.get("candidates") or [])
    if len(candidates) > 3 or len(json.dumps(candidates, ensure_ascii=False).encode("utf-8")) > 128000:
        raise ValueError("Hypothesis compilation receipt exceeds bounded candidate contract")
    content = {"candidates": candidates, "contextSummary": dict(result.get("contextSummary") or {}),
               "world": dict(world)}
    case.compilation_draft = {
        "contract": "hypothesis-compilation-receipt-v1", "capturedAt": utc_now_iso(),
        "designVersion": RULE_DESIGN_VERSION, "inputFingerprint": authoring_input_fingerprint(case),
        "contentFingerprint": compilation_fingerprint(content), **content,
    }


def reusable_compilation(case, rulebox, world):
    draft = case.compilation_draft
    if not draft or draft.get("designVersion") != RULE_DESIGN_VERSION or draft.get("rejectedReason"):
        return None
    if draft.get("inputFingerprint") != authoring_input_fingerprint(case) or draft.get("world") != world:
        return None
    content = {key: draft.get(key) for key in ("candidates", "contextSummary", "world")}
    if draft.get("contentFingerprint") != compilation_fingerprint(content):
        raise ValueError("Saved compilation candidate fingerprint mismatch")
    context = draft.get("contextSummary") or {}
    current_hash = rulebox.get("rulesHash") or rulebox.get("ruleboxRulesHash")
    if rulebox.get("status") != "ok" or not current_hash or current_hash != context.get("ruleboxRulesHash"):
        return None
    if not context.get("ruleboxSnapshotId") or context["ruleboxSnapshotId"] != rulebox.get("ruleboxSnapshotId"):
        return None
    candidates = draft.get("candidates") or []
    if not any(isinstance(row.get("proposedRule"), dict) and not compilation_blockers([row]) for row in candidates):
        return None
    return {"status": "reused", "reused": True, "candidates": copy.deepcopy(candidates),
            "contextSummary": copy.deepcopy(context)}
