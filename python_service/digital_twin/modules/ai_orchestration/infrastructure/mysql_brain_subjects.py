"""Recover display names from a case's own task lineage, without new evidence."""


def fill_case_names(connection, cases, task_id=""):
    missing = [case for case in cases if not str(case.get("name") or "").strip()
               or str(case["name"]).strip() == case["symbol"]]
    refs = {case["caseId"]: list(dict.fromkeys(value for value in [task_id, *[
        (case.get(key) or {}).get("taskId") for key in
        ("origin", "lastResearch", "lastAssessment", "lastReview", "researchPlan")]] if value)) for case in missing}
    ids = sorted({value for values in refs.values() for value in values})
    if not ids:
        return
    rows = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ task_id,account_id,symbol,"
        "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.name')) AS name,"
        "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId')) AS world_id "
        "FROM ai_control_tasks WHERE task_id IN (" + ",".join(["%s"] * len(ids)) + ")", tuple(ids)).fetchall()
    tasks = {row["task_id"]: row for row in rows}
    for case in missing:
        for key in refs[case["caseId"]]:
            task = tasks.get(key)
            if not task or task["account_id"] != case["accountId"] or task["symbol"] != case["symbol"]:
                continue
            if case.get("worldId") and task["world_id"] != case["worldId"]:
                continue
            name = str(task["name"] or "").strip()
            if name and name != case["symbol"]:
                case["name"] = name
                break
