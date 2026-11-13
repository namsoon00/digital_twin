"""Bounded, account-scoped reads. No provider calls, model runs or graph writes."""
from copy import deepcopy
import json

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.snapshot_payload_codec import snapshot_json_loads
from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state
from digital_twin.modules.reasoning.contracts import world_from_snapshot
from ..domain.portfolio_report import MAX_SUBJECTS, known, reviewed_question, safe_url
from ..domain.insight_quality import quality_block
from ..domain.insight_contract import resolve_ref
from .mysql_control import MySQLAIControlStore


class PortfolioReportSourceReader(MySQLOperationalConnection):
    def __init__(self, settings, account_reader, company_reader):
        super().__init__({**settings, "_skipOperationalSchemaBootstrap": "1"})
        self.account_reader, self.company_reader = account_reader, company_reader

    def accounts(self):
        return [{"accountId": a.account_id, "label": a.label or "계정"}
                for a in self.account_reader() if a.enabled]

    def capture(self, account_id, cutoff, *, full=False):
        account = next((a for a in self.account_reader() if a.enabled and a.account_id == account_id), None)
        if account is None:
            raise ValueError("사용 가능한 계정을 선택해 주세요.")
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM monitor_snapshots WHERE account_id=%s", (account_id,)).fetchone()
        state = snapshot_json_loads(row["payload_json"]) if row else {}
        snapshot = account_snapshot_from_monitor_state(state)
        if (not snapshot or not snapshot.has_live_account_data()
                or not known(snapshot.generated_at, cutoff)):
            raise ValueError("실계좌의 보유·관심 종목 스냅샷이 준비되지 않았습니다.")
        world = world_from_snapshot(snapshot, self.runtime_settings).world_id
        universe = {}
        for scope, positions in (("watchlist", snapshot.watchlist), ("holding", snapshot.positions)):
            for p in positions:
                if scope == "watchlist" and p.symbol not in account.watchlist_symbols:
                    continue
                universe[p.symbol] = {"symbol": p.symbol, "name": p.name or p.symbol, "scope": scope}
                if full:
                    universe[p.symbol]["position"] = {key: getattr(p, key) for key in
                        ("currency", "market", "quantity", "current_price", "average_price", "account_value_krw",
                         "account_value_basis", "valuation_fx_state", "source_as_of", "freshness_status")}
        # Configured interests missing from a partial source must stay visible.
        for symbol in account.watchlist_symbols:
            universe.setdefault(symbol, {"symbol": symbol, "name": symbol, "scope": "watchlist"})
        if not universe or len(universe) > MAX_SUBJECTS:
            raise ValueError("보유·관심 종목 1~100개를 확인할 수 있어야 합니다.")
        result = []
        for subject in sorted(universe.values(), key=lambda s: (s["scope"] != "holding", s["name"])):
            symbol = subject["symbol"]
            with self.connect() as connection:
                cases, limited = self._cases(connection, account_id, symbol, cutoff)
                opinion = self._opinion(connection, account_id, symbol, world, cutoff)
            answers = [r for case in cases if (r := reviewed_question(case, account_id, symbol, world, cutoff))]
            answers.sort(key=lambda r: r["at"], reverse=True)
            pending = [{"question": c.get("question", ""), "status": c.get("status", ""),
                        "reason": c.get("reason", ""), "nextCheckAt": c.get("nextCheckAt", "")}
                       for c in cases if c.get("worldId") == world and c.get("status") in
                       {"open", "waiting", "blocked", "review-needed"}]
            company = self.company_reader(state, account_id, symbol)
            if company and (company.get("accountId") != account_id or company.get("symbol") != symbol
                            or not known(company.get("sourceCutoffAt"), cutoff)):
                raise ValueError("기업 자료의 종목·계정·기준 시각이 일치하지 않습니다.")
            limit = 100 if full else 5
            result.append({**subject, "worldId": world, "opinion": opinion, "answers": answers[:limit],
                "openQuestions": pending[:limit], "companyReport": company,
                "coverage": {"caseReadLimitReached": limited, "reviewedAnswersFound": len(answers),
                    "omittedAnswers": max(0, len(answers) - limit), "omittedOpenQuestions": max(0, len(pending) - limit),
                    "opinionReadLimit": 20},
                "opinionStatus": "dated-reviewed" if opinion else "no-reviewed-opinion-in-read-window"})
        return {"snapshotAt": snapshot.generated_at, "subjects": result}

    @staticmethod
    def _cases(connection, account, symbol, cutoff):
        # Index metadata first, then <=16 primary-key payloads per read.
        ids = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ case_id FROM ai_brain_cases FORCE INDEX(brain_subject) "
            "WHERE account_id=%s AND symbol=%s AND kind='question' AND updated_at<=%s "
            "ORDER BY updated_at DESC,case_id DESC LIMIT 101",
            (account, symbol, cutoff)).fetchall()
        values = []
        for start in range(0, min(len(ids), 100), 16):
            batch = [r["case_id"] for r in ids[start:min(start + 16, 100)]]
            rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE case_id IN ("
                + ",".join(["%s"] * len(batch)) + ") AND account_id=%s AND symbol=%s",
                (*batch, account, symbol)).fetchall()
            values.extend(json.loads(r["payload_json"]) for r in rows)
        return values, len(ids) > 100

    @staticmethod
    def _opinion(connection, account, symbol, world, cutoff):
        ids = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ record_id FROM ai_opinion_records FORCE INDEX(opinion_subject) "
            "WHERE account_id=%s AND symbol=%s AND world_id=%s AND known_at<=%s "
            "ORDER BY known_at DESC,record_id DESC LIMIT 20", (account, symbol, world, cutoff)).fetchall()
        for item in ids:
            record = connection.execute("SELECT payload_json FROM ai_opinion_records WHERE record_id=%s",
                                        (item["record_id"],)).fetchone()
            record = json.loads(record["payload_json"])
            if record.get("kind") != "market" or not record.get("reviewInputId"):
                continue
            row = connection.execute("SELECT result_json FROM ai_control_tasks WHERE task_id=%s AND account_id=%s "
                "AND symbol=%s AND status='completed'", (record["taskId"], account, symbol)).fetchone()
            if not row:
                continue
            draft = json.loads(row["result_json"])
            packet = draft.get("input", {})
            if (any(packet.get(k) != v for k, v in (("accountId", account), ("symbol", symbol), ("worldId", world)))
                    or not known(packet.get("capturedAt"), cutoff) or quality_block(draft)):
                continue
            proof = MySQLAIControlStore.review_proof_with_connection(connection, record["reviewInputId"])
            if proof != {"taskId": record["taskId"], "draftHash": draft["quality"]["draftHash"]}:
                continue
            evidence = []
            for section, refs in draft.get("claimEvidence", {}).items():
                for ref in refs:
                    fact, value = resolve_ref(packet, ref)
                    evidence.append({**deepcopy(ref), "section": section, "value": deepcopy(value),
                        "label": fact.get("label") or fact.get("title") or fact.get("name") or ref["field"],
                        "sourceAsOf": fact.get("sourceAsOf") or fact.get("asOf") or fact.get("publishedAt", ""),
                        "sourceUrl": safe_url(fact.get("sourceUrl") or fact.get("url"))})
            return {**{k: deepcopy(draft[k]) for k in ("summary", "hypothesis", "counterEvidence", "comparison",
                    "portfolioImpact", "followUpConditions", "companyValue", "externalInfluences") if k in draft},
                "at": packet["capturedAt"], "taskId": record["taskId"], "reviewInputId": record["reviewInputId"],
                "evidence": evidence, "authority": "dated-reviewed-opinion", "currentValidity": "not-reassessed"}
        return None
