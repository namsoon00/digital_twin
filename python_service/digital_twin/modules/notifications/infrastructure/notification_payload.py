"""Lossless storage aliases for duplicate frozen notification context.

Canonical top-level context stays queryable by SQL. Only equal metadata copies
are omitted; reading restores independent values for existing consumers.
"""
from copy import deepcopy
from digital_twin.modules.notifications.domain.sent_article_filter import collect_article_identity_keys_from_context


ENCODING = "shared-metadata-v1"
SHARED_KEYS = (
    "ontologyRelationContext", "ontologyPromptContext", "hypothesisLifecycle",
    "investmentSubjectDecisionCase", "contextObservationDecision",
    "inferenceDispatchDecision", "contextObservationDeliveryDecision",
    "relationLifecycleTransition",
)


def article_history_summary(context):
    fields = ("dispatchInsightType", "signalType", "sourceSignalType", "sourceSignalTypes")
    insight = context.get("ontologyInsight")
    insight = insight if isinstance(insight, dict) else {}
    return {"version": 1,
            "keys": sorted(collect_article_identity_keys_from_context(context, max_depth=7, max_nodes=600, max_keys=800)),
            "context": {**{key: context[key] for key in fields if key in context},
                        "ontologyInsight": {key: insight[key] for key in fields if key in insight}}}


def compact_payload(payload):
    context = payload.get("context") or {}
    if not isinstance(context, dict):
        return payload
    metadata = context.get("metadata") or {}
    if not isinstance(metadata, dict):
        return payload
    keys = [key for key in SHARED_KEYS if isinstance(context.get(key), dict)
            and key in metadata and identical_value(context[key], metadata[key])]
    if not keys:
        return payload
    return {**payload, "contextEncoding": ENCODING, "sharedMetadataKeys": keys,
            "context": {**context, "metadata": {key: value for key, value in metadata.items() if key not in keys}}}


def identical_value(left, right):
    # Python equality considers True == 1 == 1.0. Storage aliases must also
    # preserve JSON value types, including inside arrays and dictionaries.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(identical_value(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(identical_value(a, b) for a, b in zip(left, right))
    return left == right


def expand_payload(payload):
    if "contextEncoding" not in payload:
        return payload
    if payload["contextEncoding"] != ENCODING:
        raise ValueError("unsupported notification context encoding")
    context = dict(payload.get("context") or {})
    metadata = dict(context.get("metadata") or {})
    keys = payload.get("sharedMetadataKeys")
    if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("invalid notification context aliases")
    for key in keys:
        if key not in SHARED_KEYS or not isinstance(context.get(key), dict) or key in metadata:
            raise ValueError("invalid notification context alias")
        metadata[key] = deepcopy(context[key])
    context["metadata"] = metadata
    result = {**payload, "context": context}
    result.pop("contextEncoding")
    result.pop("sharedMetadataKeys")
    return result
