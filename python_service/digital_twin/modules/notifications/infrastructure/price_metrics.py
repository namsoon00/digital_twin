"""Bound indexed IDs first; never scan notification JSON for diagnostics."""
import json

from ..domain.price_delivery_metrics import summarize_price_delivery


def decoded(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


class PriceMetricsStore:
    def price_observation_metrics(self, limit=100):
        limit = max(1, min(100, int(limit)))
        jobs, attempts, truncated = [], [], False
        with self.connect() as connection:
            ids = [row["job_id"] for row in connection.execute(
                "SELECT job_id FROM notification_jobs WHERE message_type='marketObservation' "
                "ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()]
            for offset in range(0, len(ids), 25):
                batch = ids[offset:offset + 25]
                placeholders = ','.join(['%s'] * len(batch))
                jobs.extend(connection.execute(
                    "SELECT job_id, status, created_at, data_quality, is_mock, "
                    "JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.context.marketObservation.quoteWitness.source_as_of')) AS source_at "
                    "FROM notification_jobs WHERE job_id IN (" + placeholders + ")", tuple(batch)).fetchall())
                rows = connection.execute(
                    "SELECT job_id, status, audience, completed_at, "
                    "JSON_OBJECT('receiptVerified', JSON_EXTRACT(metadata_json, '$.receiptVerified')) AS metadata "
                    "FROM notification_delivery_attempts WHERE job_id IN (" + placeholders + ") "
                    "AND audience='accountNotification' AND status='delivered' ORDER BY completed_at LIMIT 1001",
                    tuple(batch)).fetchall()
                truncated = truncated or len(rows) >= 1001
                attempts.extend(rows)
        for row in attempts:
            row["metadata"] = decoded(row.get("metadata"))
        result = summarize_price_delivery(jobs, attempts)
        result.update(jobSampleLimit=limit, receiptSampleLimitPerBatch=1001, receiptSampleMayBeTruncated=truncated)
        return result
