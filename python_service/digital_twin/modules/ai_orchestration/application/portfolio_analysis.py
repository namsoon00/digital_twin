"""One bounded model step per central task; checkpoints survive worker waits."""
from copy import deepcopy
from datetime import datetime, timezone

from digital_twin.modules.reasoning.contracts import EvidenceContractError, EvidenceNotReady
from ..domain import portfolio_analysis as contract


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PortfolioAnalysisWorker:
    def __init__(self, runs, control, evidence, subjects, model, publisher, accounts):
        self.runs, self.control, self.evidence, self.subjects = runs, control, evidence, subjects
        self.model, self.publisher, self.accounts = model, publisher, accounts

    def _prepare(self, run):
        start = run["batchIndex"] * contract.BATCH_SIZE
        subjects = run["source"]["subjects"][start:start + contract.BATCH_SIZE]
        active = {(s["accountId"], s["symbol"], s["worldId"]): s for s in self.subjects()}
        records, metadata = {}, {}
        for subject in subjects:
            scope = active.get((run["accountId"], subject["symbol"], subject["worldId"]))
            session, limitation = None, ""
            if scope:
                try:
                    session = self.evidence({**scope, "capability": contract.CAPABILITY})
                except (EvidenceNotReady, EvidenceContractError) as error:
                    limitation = getattr(error, "code", "verified-graph-unavailable")
            else:
                limitation = "subject-no-longer-active"
            packet = session.packet() if session else {}
            captured = now()
            records.update(contract.inventory(subject, session, captured, snapshot_at=run["source"]["snapshotAt"]))
            metadata[subject["symbol"]] = {"capturedAt": captured, "sourceSnapshots": packet.get("sourceSnapshots", {}),
                "quoteAssessment": packet.get("quoteAssessment", {}), "coverage": packet.get("coverage", {}),
                "graphLimitation": limitation, "memoryCoverage": subject["coverage"]}
        return {"subjects": [{k: s[k] for k in ("symbol", "name", "scope")} for s in subjects],
                "records": records, "selected": contract.baseline(records), "metadata": metadata,
                "trace": [], "draft": {}}

    @staticmethod
    def _data(run):
        batch = run["batch"]
        return {"subjects": batch["subjects"], "snapshotAt": run["source"]["snapshotAt"],
                "sourceMetadata": batch["metadata"], "selectedSources": batch["selected"],
                "unreadCount": len(batch["records"]) - len(batch["selected"]), "readTrace": batch["trace"]}

    def _call(self, job, run, phase, data):
        if not run.get("work"):
            envelope = contract.freeze(job, phase, data)
            input_id = self.control.save_execution_input(job, envelope)
            if not input_id:
                raise RuntimeError("portfolio-report-lease-lost")
            run["work"] = {"envelope": envelope, "inputId": input_id, "attempt": job.get("attempts", 0)}
            self.runs.checkpoint(job, run)
        work = run["work"]
        if "response" not in work:
            # Frozen input ownership includes the claim attempt. A timeout must
            # keep its original audit, but the next claim needs its own input
            # identity before begin_call can admit another model execution.
            if (work.get("attempt") != job.get("attempts", 0)
                    or work["envelope"]["current"]["taskId"] != job["taskId"]):
                previous = work["inputId"]
                envelope = contract.freeze(job, phase, work["envelope"]["data"])
                input_id = self.control.save_execution_input(job, envelope)
                if not input_id:
                    raise RuntimeError("portfolio-report-lease-lost")
                work.update(envelope=envelope, inputId=input_id, attempt=job.get("attempts", 0))
                work.setdefault("previousInputIds", []).append(previous)
                self.runs.checkpoint(job, run)
            self.control.execution_wait()
            response = self.model(work["envelope"], work["inputId"])
            # A provider timeout remains uncertain; no fabricated completion.
            work["response"], work["respondedAt"] = response, now()
            self.runs.checkpoint(job, run)
        run["execution"].append({"taskId": work["envelope"]["current"]["taskId"], "inputId": work["inputId"],
            "phase": phase, "promptVersion": work["envelope"]["promptVersion"],
            "promptBytes": len(work["envelope"]["prompt"].encode()), "respondedAt": work["respondedAt"],
            "responseHash": contract.digest(work["response"]), "response": deepcopy(work["response"])})
        return work["response"]

    @staticmethod
    def _batch_done(run, claims):
        batch = run["batch"]
        for subject in batch["subjects"]:
            symbol = subject["symbol"]
            refs = {r["sourceId"] for c in claims[symbol].values() for r in c["refs"]}
            original = next(s for s in run["source"]["subjects"] if s["symbol"] == symbol)
            available = sum(r["symbol"] == symbol for r in batch["records"].values())
            read = sum(r["symbol"] == symbol for r in batch["selected"].values())
            run["results"].append({**subject, "claims": claims[symbol], "analyzedAt": now(),
                "position": original.get("position", {}), "sources": {k: batch["selected"][k] for k in refs},
                "sourceCoverage": {"available": available, "read": read, "unread": available - read,
                    "omittedReads": [v for t in batch["trace"] for v in t.get("omitted", [])
                        if not v.get("sourceId") or batch["records"][v["sourceId"]]["symbol"] == symbol],
                    **batch["metadata"][symbol]}, "openQuestions": original.get("openQuestions", [])})
        run.pop("batch", None)
        run["batchIndex"] += 1
        run["readRound"] = 0
        run["phase"] = "read" if len(run["results"]) < len(run["source"]["subjects"]) else "synthesis"

    def __call__(self, job):
        run = self.runs.get(job["accountId"], job["analysisId"])
        if not run or run["taskId"] != job["taskId"]:
            raise ValueError("portfolio-report-task-mismatch")
        if run.get("completedStep") == job["step"]:
            return self._transition(run, job)
        if run["accountId"] not in {a["accountId"] for a in self.accounts()}:
            raise ValueError("portfolio-report-account-disabled")
        run["status"] = "processing"
        phase = run["phase"]
        if phase in {"read", "write", "review"} and "batch" not in run:
            run["batch"] = self._prepare(run)
            self.runs.checkpoint(job, run)
        if phase == "read":
            data = {**self._data(run), "availableSources": contract.catalog({k: r for k, r in run["batch"]["records"].items() if k not in run["batch"]["selected"]}),
                    "remainingReadRounds": contract.READ_ROUNDS - run["readRound"]}
            response = self._call(job, run, "read", data)
            selected, omitted = contract.select_sources(run["batch"]["records"], run["batch"]["selected"], response)
            run["batch"]["selected"] = selected
            run["batch"]["trace"].append({"inputId": run["work"]["inputId"], "selection": response, "omitted": omitted})
            run["readRound"] += 1
            if not isinstance(response, dict) or not response.get("sourceIds") or run["readRound"] >= contract.READ_ROUNDS:
                run["phase"] = "write"
        elif phase == "write":
            data = self._data(run)
            response = self._call(job, run, "write", data)
            try:
                run["batch"]["draft"] = contract.admit_draft(response, data)
                run["phase"] = "review"
            except (ValueError, TypeError):
                rejected = {s["symbol"]: {d: contract.missing_claim("분석 결과의 인용·범위를 검증하지 못했습니다.") for d in contract.DIMENSIONS} for s in data["subjects"]}
                self._batch_done(run, rejected)
        elif phase == "review":
            data = {**self._data(run), "draft": run["batch"]["draft"]}
            response = self._call(job, run, "review", data)
            self._batch_done(run, contract.reviewed(data["draft"], response, contract.DIMENSIONS))
        elif phase == "synthesis":
            data = contract.synthesis_data(run)
            response = self._call(job, run, "synthesis", data)
            try:
                run["overallDraft"] = contract.admit_draft(response, data, synthesis=True)
                run["phase"] = "synthesis-review"
            except (ValueError, TypeError):
                run["overallDraft"] = {"portfolio": {d: contract.missing_claim("전체 종합의 근거 검증을 통과하지 못했습니다. 종목별 결과를 확인해 주세요.") for d in contract.GLOBAL_DIMENSIONS}}
                self._finalize(run, run["overallDraft"]["portfolio"])
        elif phase == "synthesis-review":
            data = {**contract.synthesis_data(run), "draft": run["overallDraft"]}
            response = self._call(job, run, "review", data)
            overall = contract.reviewed(data["draft"], response, contract.GLOBAL_DIMENSIONS)["portfolio"]
            self._finalize(run, overall)
        elif phase == "deliver":
            report = self.runs.reports.get(run["accountId"], run["reportId"])
            self.publisher(report)
            run["status"], run["phase"] = "completed", "done"
        else:
            raise ValueError("unknown portfolio report phase")
        run.pop("work", None)
        run["completedStep"] = job["step"]
        run["step"] = job["step"] + 1
        self.runs.checkpoint(job, run)
        return self._transition(run, job)

    @staticmethod
    def _finalize(run, overall):
        run["report"] = contract.build_analysis_report(run, overall, now())
        run["reportId"] = run["report"]["reportId"]
        run.pop("overallDraft", None)
        run["status"] = "ready"
        run["phase"] = "deliver" if run["sendWhenReady"] else "done"

    @staticmethod
    def _transition(run, job):
        children = [] if run["phase"] == "done" else [contract.task(run, run["step"])]
        return {"status": "report-step-completed", "analysisId": run["analysisId"],
                "completedSubjects": len(run["results"]), "phase": run["phase"]}, children
