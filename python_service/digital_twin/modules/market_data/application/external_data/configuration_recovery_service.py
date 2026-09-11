"""Recover deferred external-data work after configuration becomes usable."""

from __future__ import annotations

import re
from typing import Dict, Mapping


SEC_CONFIGURATION_RECOVERY_VERSION = "sec-configuration-recovery-v1"
_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def _enabled(value: object, default: bool = True) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off", "disabled"}


def _sec_contact(settings: Mapping[str, object]) -> str:
    direct = str(settings.get("externalSecContactEmail") or "").strip()
    match = _EMAIL_PATTERN.search(direct)
    if match:
        return match.group(0)
    user_agent = str(settings.get("externalSecUserAgent") or "").strip()
    if "local-contact" in user_agent.lower():
        return ""
    match = _EMAIL_PATTERN.search(user_agent)
    return match.group(0) if match else ""


def sec_metadata_access_ready(settings: Mapping[str, object]) -> bool:
    return bool(_sec_contact(dict(settings or {})))


def sec_document_access_ready(settings: Mapping[str, object]) -> bool:
    values = dict(settings or {})
    return sec_metadata_access_ready(values) and _enabled(
        values.get("externalSecDocumentTextEnabled"),
    )


class ExternalDataConfigurationRecoveryService:
    """Move configuration-blocked collection work back to the durable queue."""

    def __init__(self, store):
        self.store = store

    def recover(
        self,
        previous_settings: Mapping[str, object],
        current_settings: Mapping[str, object],
    ) -> Dict[str, object]:
        before = dict(previous_settings or {})
        after = dict(current_settings or {})
        before_metadata_ready = sec_metadata_access_ready(before)
        after_metadata_ready = sec_metadata_access_ready(after)
        before_document_ready = sec_document_access_ready(before)
        after_document_ready = sec_document_access_ready(after)
        dataset_ids = []
        if not before_metadata_ready and after_metadata_ready:
            dataset_ids.append("sec.submissions")
        if not before_document_ready and after_document_ready:
            dataset_ids.append("sec.document")
        if not dataset_ids:
            return {
                "version": SEC_CONFIGURATION_RECOVERY_VERSION,
                "status": "not-required",
                "secMetadataAccessReady": after_metadata_ready,
                "secDocumentAccessReady": after_document_ready,
                "datasetIds": [],
                "madeDueCount": 0,
            }
        try:
            made_due = int(self.store.make_due(tuple(dataset_ids)) or 0)
        except Exception as error:  # noqa: BLE001 - settings remain saved; status is actionable.
            return {
                "version": SEC_CONFIGURATION_RECOVERY_VERSION,
                "status": "failed",
                "secMetadataAccessReady": after_metadata_ready,
                "secDocumentAccessReady": after_document_ready,
                "datasetIds": dataset_ids,
                "madeDueCount": 0,
                "reason": str(error)[:240],
            }
        return {
            "version": SEC_CONFIGURATION_RECOVERY_VERSION,
            "status": "scheduled",
            "secMetadataAccessReady": after_metadata_ready,
            "secDocumentAccessReady": after_document_ready,
            "datasetIds": dataset_ids,
            "madeDueCount": made_due,
        }
