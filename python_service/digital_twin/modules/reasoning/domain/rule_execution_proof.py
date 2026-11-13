"""Backend-explicit completion proof shared by reasoning and delivery readers."""

COMPILED_BACKEND = "compiled-rulebox-v1"


def condition_execution_proven(condition):
    values = condition or {}
    return values.get("matchedByTypeDB") is True or (
        values.get("matchedByRuleEngine") is True and values.get("ruleEngineBackend") == COMPILED_BACKEND)


def rule_notice_execution_proven(rule):
    values = rule or {}
    return bool(values.get("nativeProof") or (
        values.get("compiledProof") is True and values.get("ruleExecutionBackend") == COMPILED_BACKEND))


def compiled_execution_identity(inference):
    if (inference or {}).get("graphStore") != "compiled-rulebox":
        return {}
    return {key: inference.get(key) for key in (
        "graphStore", "accountId", "worldId", "compiledExecutionProof", "compiledRuleboxFingerprint",
        "ruleExecutionBackend", "inferenceGenerationId", "sourceAboxSnapshotId", "targetSymbols")}


def compiled_rule_execution_completed(inference):
    values = inference or {}
    if not isinstance(values, dict):
        return False
    proof = values.get("compiledExecutionProof") or {}
    if not isinstance(proof, dict):
        return False
    expected = proof.get("enabledRuleIds")
    evaluated = proof.get("evaluatedRuleIds")
    if any(not isinstance(ids, list) or any(not isinstance(rid, str) or not rid for rid in ids)
           for ids in (expected, evaluated)):
        return False
    return bool(
        values.get("graphStore") == "compiled-rulebox"
        and values.get("ruleExecutionBackend") == COMPILED_BACKEND
        and proof.get("version") == COMPILED_BACKEND
        and proof.get("complete") is True and proof.get("durable") is True
        and proof.get("programFingerprint")
        and proof.get("programFingerprint") == values.get("compiledRuleboxFingerprint")
        and proof.get("sourceFingerprint")
        and proof.get("accountId") and proof.get("accountId") == values.get("accountId")
        and proof.get("worldId") and proof.get("worldId") == values.get("worldId")
        and proof.get("generationId") == values.get("inferenceGenerationId")
        and proof.get("sourceAboxSnapshotId") == values.get("sourceAboxSnapshotId")
        and values.get("sourceAboxSnapshotId") and values.get("inferenceGenerationId")
        and isinstance(expected, list) and isinstance(evaluated, list)
        and len(expected) == len(set(expected))
        and len(evaluated) == len(set(evaluated)) and set(expected) == set(evaluated)
        and isinstance(proof.get("targetSymbols"), list)
        and proof.get("targetSymbols") == values.get("targetSymbols")
    )


def rule_execution_completed(inference):
    values = inference or {}
    if values.get("graphStore") == "compiled-rulebox":
        return compiled_rule_execution_completed(values)
    return bool(values.get("nativeTypeDbReasoningCompleted") or values.get("typedbNativeRuleEvaluationCompleted"))


def rule_execution_used(inference):
    values = inference or {}
    if values.get("graphStore") == "compiled-rulebox":
        return compiled_rule_execution_completed(values) and bool(values.get("relations") or values.get("traces"))
    return bool(values.get("nativeTypeDbReasoningUsed"))
