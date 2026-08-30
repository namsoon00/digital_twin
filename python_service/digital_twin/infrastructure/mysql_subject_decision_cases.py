"""MySQL persistence for per-subject decision cases and publications."""

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from typing import List, Optional

from ..domain.investment_reasoning import SubjectDecisionCase
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps


class MySQLSubjectDecisionCaseStore(MySQLOperationalConnection):
    def save(self, subject_case: SubjectDecisionCase) -> SubjectDecisionCase:
        with self.transaction() as connection:
            self.save_with_connection(connection, subject_case)
        return subject_case

    def save_final_with_episode(
        self,
        subject_case,
        episode,
        decision_episode_store,
        connection=None,
    ):
        """Commit the canonical subject publication and its decision atomically."""

        if not subject_case.publication or not subject_case.publication.decision_episode_id:
            raise ValueError("Final subject publication requires a decision episode reference.")
        if subject_case.publication.decision_episode_id != episode.episode_id:
            raise ValueError("Subject publication and decision episode identifiers do not match.")
        transaction = self.transaction() if connection is None else nullcontext(connection)
        with transaction as connection:
            decision_episode_store.save(episode, connection=connection)
            self.save_with_connection(connection, subject_case)
        return subject_case

    @staticmethod
    def save_with_connection(connection, subject_case: SubjectDecisionCase) -> None:
        candidate = subject_case.candidate_set
        existing_candidate = connection.execute(
            "SELECT fingerprint FROM decision_candidate_snapshots WHERE candidate_set_id = %s",
            (candidate.candidate_set_id,),
        ).fetchone()
        if existing_candidate and str(existing_candidate.get("fingerprint") or "") != candidate.fingerprint:
            raise ValueError("Immutable candidate snapshot fingerprint mismatch: " + candidate.candidate_set_id)
        connection.execute(
            """
            INSERT INTO decision_candidate_snapshots (
                candidate_set_id, subject_case_id, account_id, symbol,
                source_abox_snapshot_id, inference_generation_id, synthesis_id,
                fingerprint, payload_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE candidate_set_id = VALUES(candidate_set_id)
            """,
            (
                candidate.candidate_set_id,
                subject_case.subject_case_id,
                candidate.account_id,
                candidate.symbol,
                candidate.source_abox_snapshot_id,
                candidate.inference_generation_id,
                candidate.synthesis_id,
                candidate.fingerprint,
                json_dumps(candidate.to_dict()),
                candidate.created_at or subject_case.created_at,
            ),
        )
        publication = subject_case.publication
        outcome_kind = publication.outcome_kind if publication else ""
        connection.execute(
            """
            INSERT INTO investment_subject_decision_cases (
                subject_case_id, batch_case_id, request_id, deployment_id,
                release_fingerprint, account_id, symbol, source_abox_snapshot_id,
                inference_generation_id, synthesis_id, candidate_set_id,
                candidate_fingerprint, stage, outcome_kind, ai_request_id,
                notification_job_id, payload_json, case_version, created_at,
                updated_at, completed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                stage = VALUES(stage), outcome_kind = VALUES(outcome_kind),
                ai_request_id = VALUES(ai_request_id),
                notification_job_id = VALUES(notification_job_id),
                payload_json = VALUES(payload_json), case_version = VALUES(case_version),
                updated_at = VALUES(updated_at), completed_at = VALUES(completed_at)
            """,
            (
                subject_case.subject_case_id,
                subject_case.batch_case_id,
                subject_case.request_id,
                subject_case.deployment_id,
                subject_case.release_fingerprint,
                subject_case.account_id,
                subject_case.symbol,
                subject_case.source_abox_snapshot_id,
                subject_case.inference_generation_id,
                subject_case.synthesis.synthesis_id,
                candidate.candidate_set_id,
                candidate.fingerprint,
                subject_case.stage,
                outcome_kind,
                subject_case.ai_request_id,
                subject_case.notification_job_id,
                json_dumps(subject_case.to_dict()),
                subject_case.version,
                subject_case.created_at,
                subject_case.updated_at or subject_case.created_at,
                subject_case.completed_at,
            ),
        )
        if publication:
            existing_publication = connection.execute(
                "SELECT publication_fingerprint FROM decision_publications WHERE subject_case_id = %s",
                (subject_case.subject_case_id,),
            ).fetchone()
            if (
                existing_publication
                and str(existing_publication.get("publication_fingerprint") or "") != publication.fingerprint
            ):
                raise ValueError(
                    "Subject decision already has a different publication: "
                    + subject_case.subject_case_id
                )
            connection.execute(
                """
                INSERT INTO decision_publications (
                    publication_id, subject_case_id, outcome_kind,
                    publication_fingerprint, decision_episode_id,
                    notification_job_id, payload_json, created_at, delivered_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    payload_json = IF(
                        delivered_at = '' AND VALUES(delivered_at) <> '',
                        VALUES(payload_json), payload_json
                    ),
                    delivered_at = IF(
                        delivered_at = '' AND VALUES(delivered_at) <> '',
                        VALUES(delivered_at), delivered_at
                    )
                """,
                (
                    publication.publication_id,
                    publication.subject_case_id,
                    publication.outcome_kind,
                    publication.fingerprint,
                    publication.decision_episode_id,
                    publication.notification_job_id,
                    json_dumps(publication.to_dict()),
                    publication.created_at,
                    publication.delivered_at,
                ),
            )
        MySQLSubjectDecisionCaseStore.save_audit_entry_with_connection(
            connection,
            subject_case,
        )

    @staticmethod
    def save_audit_entry_with_connection(connection, subject_case: SubjectDecisionCase) -> None:
        """Append one compact immutable receipt for every lifecycle version."""

        publication = subject_case.publication
        judgment = subject_case.ai_judgment
        decision = subject_case.final_decision
        abstention = subject_case.abstention
        if judgment:
            ai_status = "completed"
        elif subject_case.stage == "AI_PENDING":
            ai_status = "pending"
        elif abstention and str(abstention.reason_code or "").startswith("ai-"):
            ai_status = "failed"
        else:
            ai_status = "not-requested"
        payload = {
            "subjectCaseId": subject_case.subject_case_id,
            "batchCaseId": subject_case.batch_case_id,
            "requestId": subject_case.request_id,
            "deploymentId": subject_case.deployment_id,
            "releaseFingerprint": subject_case.release_fingerprint,
            "accountId": subject_case.account_id,
            "symbol": subject_case.symbol,
            "sourceAboxSnapshotId": subject_case.source_abox_snapshot_id,
            "inferenceGenerationId": subject_case.inference_generation_id,
            "synthesisId": subject_case.synthesis.synthesis_id,
            "candidateSetId": subject_case.candidate_set.candidate_set_id,
            "candidateFingerprint": subject_case.candidate_set.fingerprint,
            "eligibleHypothesisIds": list(subject_case.candidate_set.eligible_hypothesis_ids),
            "graphCandidateAction": subject_case.synthesis.graph_candidate_action,
            "stage": subject_case.stage,
            "caseVersion": subject_case.version,
            "aiRequestId": subject_case.ai_request_id,
            "aiStatus": ai_status,
            "aiJudgment": judgment.to_dict() if judgment else {},
            "finalDecision": decision.to_dict() if decision else {},
            "abstention": abstention.to_dict() if abstention else {},
            "publication": publication.to_dict() if publication else {},
            "notificationJobId": subject_case.notification_job_id,
            "createdAt": subject_case.updated_at or subject_case.created_at,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        entry_fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        audit_id = hashlib.sha256(
            (
                subject_case.subject_case_id
                + "|" + str(subject_case.version)
                + "|" + subject_case.stage
            ).encode("utf-8")
        ).hexdigest()
        existing = connection.execute(
            "SELECT entry_fingerprint FROM investment_decision_audit_entries WHERE audit_id = %s",
            (audit_id,),
        ).fetchone()
        if existing:
            if str(existing.get("entry_fingerprint") or "") != entry_fingerprint:
                raise ValueError("Immutable decision audit fingerprint mismatch: " + audit_id)
            return
        connection.execute(
            """
            INSERT INTO investment_decision_audit_entries (
                audit_id, subject_case_id, case_version, stage, outcome_kind,
                ai_status, final_action, candidate_fingerprint,
                publication_fingerprint, entry_fingerprint, payload_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                audit_id,
                subject_case.subject_case_id,
                subject_case.version,
                subject_case.stage,
                publication.outcome_kind if publication else "",
                ai_status,
                decision.action if decision else "",
                subject_case.candidate_set.fingerprint,
                publication.fingerprint if publication else "",
                entry_fingerprint,
                canonical,
                subject_case.updated_at or subject_case.created_at,
            ),
        )

    def get(self, subject_case_id: str) -> Optional[SubjectDecisionCase]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_subject_decision_cases WHERE subject_case_id = %s",
                (str(subject_case_id or ""),),
            ).fetchone()
        return self.case_from_row(row)

    def for_batch(self, batch_case_id: str) -> List[SubjectDecisionCase]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM investment_subject_decision_cases "
                "WHERE batch_case_id = %s ORDER BY account_id, symbol, subject_case_id",
                (str(batch_case_id or ""),),
            ).fetchall()
        return [item for item in (self.case_from_row(row) for row in rows or []) if item]

    def stale_ready(self, max_age_minutes: int = 30, limit: int = 100) -> List[SubjectDecisionCase]:
        """Return decision candidates that never reached the AI handoff.

        READY is a transient orchestration state. Keeping an old READY row
        makes the console report work that can no longer be resumed safely
        because its point-in-time facts may have expired.
        """

        age_minutes = max(1, min(24 * 60, int(max_age_minutes or 30)))
        row_limit = max(1, min(1000, int(limit or 100)))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM investment_subject_decision_cases "
                "WHERE stage = 'READY' "
                "AND updated_at < DATE_SUB(UTC_TIMESTAMP(6), INTERVAL %s MINUTE) "
                "ORDER BY updated_at ASC LIMIT " + str(row_limit),
                (age_minutes,),
            ).fetchall()
        return [item for item in (self.case_from_row(row) for row in rows or []) if item]

    def get_by_scope(
        self,
        batch_case_id: str,
        account_id: str,
        symbol: str,
        inference_generation_id: str,
        synthesis_id: str = "",
    ) -> Optional[SubjectDecisionCase]:
        conditions = [
            "batch_case_id = %s", "account_id = %s", "symbol = %s",
            "inference_generation_id = %s",
        ]
        params = [
            str(batch_case_id or ""), str(account_id or ""),
            str(symbol or "").upper(), str(inference_generation_id or ""),
        ]
        if str(synthesis_id or ""):
            conditions.append("synthesis_id = %s")
            params.append(str(synthesis_id or ""))
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_subject_decision_cases WHERE "
                + " AND ".join(conditions)
                + " ORDER BY updated_at DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        return self.case_from_row(row)

    def latest(self, account_id: str = "", symbol: str = "", limit: int = 20):
        conditions = []
        params = []
        if str(account_id or ""):
            conditions.append("account_id = %s")
            params.append(str(account_id or ""))
        if str(symbol or ""):
            conditions.append("symbol = %s")
            params.append(str(symbol or "").upper())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM investment_subject_decision_cases"
                + where
                + " ORDER BY updated_at DESC, subject_case_id DESC LIMIT %s",
                (*params, max(1, min(200, int(limit or 20)))),
            ).fetchall()
        return [item for item in (self.case_from_row(row) for row in rows or []) if item]

    def latest_portfolio(self, account_id: str = "") -> Optional[SubjectDecisionCase]:
        """Return the latest account-wide case without scanning instrument cases."""

        conditions = ["symbol = ''"]
        params = []
        if str(account_id or ""):
            conditions.append("account_id = %s")
            params.append(str(account_id or ""))
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_subject_decision_cases WHERE "
                + " AND ".join(conditions)
                + " ORDER BY updated_at DESC, subject_case_id DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        return self.case_from_row(row)

    def audit_trail(self, subject_case_id: str) -> List[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM investment_decision_audit_entries "
                "WHERE subject_case_id = %s ORDER BY case_version, created_at, audit_id",
                (str(subject_case_id or ""),),
            ).fetchall()
        return [
            _json_loads(row.get("payload_json"), {})
            for row in rows or []
            if row.get("payload_json")
        ]

    @staticmethod
    def case_from_row(row) -> Optional[SubjectDecisionCase]:
        if not row:
            return None
        payload = _json_loads(row.get("payload_json"), {})
        return SubjectDecisionCase.from_dict(payload) if payload else None
