"""Bounded SQL field reads for a consumer-declared immutable payload contract."""
import json


def payload_field_paths(fields):
    if fields is not None and (not isinstance(fields, dict) or any(
            not isinstance(group, str) or not isinstance(keys, (tuple, list))
            or any(not isinstance(key, str) for key in keys) for group, keys in fields.items())):
        raise ValueError("Invalid immutable payload field contract")
    paths = tuple(sorted((group, key) for group, keys in (fields or {}).items() for key in keys))
    if len(paths) > 32 or any(not group or not key for group, key in paths):
        raise ValueError("Invalid immutable payload field contract")
    return paths


def revision_page_query(batch, identities, paths):
    """Keep exact revision membership and metadata; never copy the raw archive.

    SQL NULL denotes a missing path; JSON 'null' denotes a present null value.
    JSON paths and normalized subject keys are bound parameters, not SQL text.
    """
    placeholders = ", ".join(["%s"] * len(batch))
    if not paths:
        return ("SELECT /* operation:external-revision-page */ * FROM external_fact_revision "
                "WHERE revision_id IN (" + placeholders + ")", tuple(batch))
    columns = ["revision_id", "dataset_id", "subject_key", "provider_id", "source_revision",
               "payload_hash", "source_schema_version", "availability", "source_as_of",
               "fetched_at", "quality_json", "created_at"]
    subject_case = "CASE revision_id " + " ".join("WHEN %s THEN %s" for _ in batch) + " END"
    params = []
    for index, (group, key) in enumerate(paths):
        columns.append("JSON_EXTRACT(payload_json, CONCAT(%s, JSON_QUOTE(" + subject_case
                       + "), %s)) AS projected_value_" + str(index))
        params.append("$." + json.dumps(group) + ".")
        for revision_id in batch:
            params.extend([revision_id, " ".join(str(identities[revision_id]["subject_key"]).split()).upper()])
        params.append("." + json.dumps(key))
    return ("SELECT /* operation:external-revision-page */ " + ", ".join(columns)
            + " FROM external_fact_revision WHERE revision_id IN (" + placeholders + ")",
            tuple(params + list(batch)))


def restore_projected_payload(row, paths):
    if not paths:
        return row
    payload = {}
    subject = " ".join(str(row["subject_key"]).split()).upper()
    for index, (group, key) in enumerate(paths):
        raw = row["projected_value_" + str(index)]
        if raw is not None:
            payload.setdefault(group, {}).setdefault(subject, {})[key] = json.loads(raw)
    return {**row, "payload_json": json.dumps(payload, ensure_ascii=False)}
