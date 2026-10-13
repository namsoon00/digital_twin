"""Explain source readiness per authored rule, without evaluating market conditions."""

from copy import deepcopy


VERSION = "hypothesis-data-readiness-v1"
UNUSABLE = {"stale", "expired", "unavailable", "missing", "invalid", "no-tick"}
LABELS = {"quote": "시세", "flow": "체결·수급", "trend": "가격 흐름",
          "price-path": "기간별 가격 흐름", "model-signal": "모델 입력",
          "rule-evidence": "규칙 근거", "news": "뉴스", "disclosure": "공시"}
NEXT_CHECKS = {
    "source-stale": "해당 원천의 허용 시간 안에 관측된 자료를 다시 확인합니다.",
    "source-unusable": "해당 원천 자료의 사용 가능 여부를 다시 확인합니다.",
    "source-clock-missing": "수집 시각이 아닌 원천 관측 시각을 확인합니다.",
    "model-missing": "이 규칙의 현재 입력에 대한 모델 평가 결과를 확인합니다.",
    "model-unverified": "미확인 조건의 입력을 보완한 뒤 같은 규칙을 다시 평가합니다.",
    "model-ineligible": "해당 모델 근거의 사용 제한이 해소됐는지 확인합니다.",
    "source-unchanged": "원천 관측 시각이 이전 확인 이후로 갱신되면 다시 평가합니다.",
    "source-regressed": "이전 판단보다 새로운 원천 자료를 확인합니다.",
    "baseline-missing": "이번 원천 시각을 기준으로 저장하고 다음 새 자료에서 비교합니다.",
    "market-closed": "거래 재개 후 새로운 원천 자료가 들어오면 다시 평가합니다.",
    "required-evidence-unresolved": "이 규칙의 필수 조건을 실제 ABox 관측값에 다시 연결합니다.",
    "domain-unverified": "이 규칙이 요구한 자료의 사용 가능 상태를 확인합니다.",
}


def mapping(value):
    return value if isinstance(value, dict) else {}


def issue(rule_id, domain, reason_code, reason, profile=None, *, rule_label="", condition_id=""):
    profile = mapping(profile)
    return {
        "ruleId": rule_id, "ruleLabel": rule_label or rule_id,
        "domain": domain, "label": LABELS.get(domain, domain or "규칙 근거"),
        "conditionId": condition_id, "field": profile.get("field") or "",
        "reasonCode": reason_code, "reason": reason,
        "nextCheck": NEXT_CHECKS.get(reason_code, NEXT_CHECKS["source-unusable"]),
        "sourceAsOf": profile.get("sourceAsOf") or profile.get("observedAt") or "",
        "sourceFetchedAt": profile.get("sourceFetchedAt") or "",
        "source": profile.get("observationSource") or profile.get("source") or "",
        "freshnessStatus": profile.get("freshnessStatus") or "unknown",
        "ageMinutes": profile.get("freshnessAgeMinutes"), "maxAgeMinutes": profile.get("maxAgeMinutes"),
        "marketSessionStatus": profile.get("marketSessionStatus") or "unknown",
        # A missing model assessment is not proof of a quote/feed outage.
        "sourceProblem": reason_code in {"source-stale", "source-unusable", "source-clock-missing"},
    }


def summary(checks):
    blocked = [row for row in checks if row.get("status") != "ready"]
    unavailable = any(row.get("status") == "unavailable" for row in blocked)
    blocked_ids = {row["ruleId"] for row in blocked}
    return {
        "version": VERSION, "status": "unavailable" if unavailable else "awaiting-source" if blocked else "ready",
        "rules": checks,
        "readyRuleIds": list(dict.fromkeys(row["ruleId"] for row in checks if row["ruleId"] and row.get("status") == "ready" and row["ruleId"] not in blocked_ids)),
        "blockedRuleIds": list(dict.fromkeys(row["ruleId"] for row in blocked if row["ruleId"])),
        "reason": " · ".join(dict.fromkeys(
            (row.get("ruleLabel") or row["ruleId"] or "가설 근거") + ": " + row["reason"] for row in blocked if row.get("reason"))),
    }


def source_check(rule_id, domain, old, new, profile, stamp, prior_stamp, now_clock, old_clock, has_previous):
    """Preserve the existing new-source gate and expose its exact blocking cause."""
    label = new.get("label") or old.get("label") or rule_id
    causes = []
    if not new:
        causes.append(("model-missing", "이 규칙의 현재 모델 평가 결과가 없습니다."))
    elif new.get("status") == "untested" or new.get("unknownConditionIds"):
        causes.append(("model-unverified", "모델 입력에서 확인하지 못한 조건이 있습니다."))
    elif new.get("freshnessCompatible") is False or new.get("eligibilityStatus") in {"ineligible", "reference-only"}:
        causes.append(("model-ineligible", "이 규칙의 모델 근거가 현재 사용 기준을 통과하지 못했습니다."))
    if profile.get("freshnessStatus") in UNUSABLE or profile.get("judgementEvidenceUsable") is False:
        code = "source-stale" if profile.get("freshnessStatus") in {"stale", "expired"} else "source-unusable"
        causes.append((code, profile.get("freshnessGateReason") or "이 규칙에 필요한 원천 자료를 사용할 수 없습니다."))
    if now_clock is None:
        causes.append(("source-clock-missing", "이 규칙에 필요한 원천 관측 시각이 없습니다."))
    closed = profile.get("marketSessionStatus") == "closed"
    status = "awaiting-source" if causes and closed else "unavailable" if causes else "ready"
    if not causes:
        if old_clock is not None and now_clock <= old_clock:
            code = "source-unchanged" if now_clock == old_clock else "source-regressed"
            causes.append((code, "원천 관측 시각이 이전 판단 이후로 갱신되지 않았습니다."))
        elif has_previous and old_clock is None:
            causes.append(("baseline-missing", "이전 원천 관측 시각이 없어 이번 자료부터 비교 기준을 저장합니다."))
        elif not has_previous and closed:
            causes.append(("market-closed", "거래 시간 밖의 최초 자료이므로 다음 새 거래 자료를 기다립니다."))
        if causes:
            status = "awaiting-source"
    details = [issue(rule_id, domain, code, reason, {**profile, "sourceAsOf": stamp}, rule_label=label)
               for code, reason in causes]
    return {
        "ruleId": rule_id, "ruleLabel": label, "domain": domain, "status": status,
        "reasonCode": details[0]["reasonCode"] if details else "ready",
        "reason": " ".join(row["reason"] for row in details), "issues": details,
        "sourceAsOf": stamp or "", "previousSourceAsOf": prior_stamp or "",
        "marketSessionStatus": profile.get("marketSessionStatus") or "unknown",
        "modelStatus": new.get("status") or "", "unknownConditionIds": list(new.get("unknownConditionIds") or []),
        "failedConditionIds": list(new.get("failedConditionIds") or []),
        "conditionState": "unverified" if causes else "not-matched" if new.get("status") == "not-supported" else "",
    }


def domain_issues(rule_id, label, domains, profiles, *, trace_verified=False):
    issues = []
    for domain in domains:
        profile = mapping(profiles.get(domain))
        if not profile and not trace_verified:
            issues.append(issue(rule_id, domain, "domain-unverified", LABELS.get(domain, domain) + " 자료의 사용 가능 상태를 확인하지 못했습니다.", rule_label=label))
        elif profile.get("judgementEvidenceUsable") is False or profile.get("freshnessStatus") in UNUSABLE:
            code = "source-stale" if profile.get("freshnessStatus") in {"stale", "expired"} else "source-unusable"
            issues.append(issue(rule_id, domain, code, profile.get("freshnessGateReason") or "필요한 원천 자료를 사용할 수 없습니다.", profile, rule_label=label))
    return issues


def trace_checks(traces, profiles, required_domains):
    """Bind source restrictions to their own rule, never another rule's trace."""
    checks = []
    if not traces and required_domains:
        traces = [{"ruleId": "", "hypothesisLifecycle": {"requiredFreshnessDomains": required_domains}}]
    assigned_domains = set()
    for trace in traces:
        rule_id = str(trace.get("ruleId") or "")
        label = str(trace.get("label") or trace.get("ruleLabel") or rule_id)
        issues = []
        unusable = trace.get("evidenceUsableForJudgement") is False or trace.get("freshnessStatus") in UNUSABLE
        for condition_id in trace.get("ungroundedRequiredConditionIds") or []:
            issues.append(issue(rule_id, "rule-evidence", "required-evidence-unresolved",
                "필수 조건 ‘" + str(condition_id) + "’의 실제 관측값을 연결하지 못했습니다.",
                trace, rule_label=label, condition_id=condition_id))
        for condition in trace.get("matchedConditions") or []:
            if not isinstance(condition, dict) or not unusable:
                continue
            if condition.get("judgementEvidenceUsable") is not False and condition.get("freshnessStatus") not in UNUSABLE:
                continue
            props = mapping(condition.get("matchedTargetProperties"))
            domain = condition.get("observationDomain") or props.get("observationDomain") or (
                "model-signal" if condition.get("relationType") == "HAS_MODEL_SIGNAL" else "rule-evidence")
            code = "source-stale" if condition.get("freshnessStatus") in {"stale", "expired"} else "source-unusable"
            issues.append(issue(rule_id, domain, code,
                condition.get("freshnessGateReason") or trace.get("freshnessGateReason") or "이 조건의 관측 근거를 사용할 수 없습니다.",
                condition, rule_label=label, condition_id=condition.get("conditionId") or ""))
        if unusable and not issues:
            issues.append(issue(rule_id, "rule-evidence", "required-evidence-unresolved",
                trace.get("freshnessGateReason") or "규칙의 필수 관측 근거를 확인하지 못했습니다.", trace, rule_label=label))
        policy = mapping(trace.get("hypothesisLifecycle") or trace.get("hypothesis_lifecycle"))
        # Older single-rule packets may only carry the aggregate requirement.
        # A multi-rule union must never become every rule's dependency list.
        domains = policy.get("requiredFreshnessDomains") or (required_domains if len(traces) == 1 else [])
        assigned_domains.update(domains)
        issues.extend(domain_issues(rule_id, label, domains, profiles,
                                   trace_verified=trace.get("freshnessStatus") == "fresh" and not unusable))
        checks.append({"ruleId": rule_id, "ruleLabel": label, "status": "unavailable" if issues else "ready",
                       "reason": " ".join(dict.fromkeys(row["reason"] for row in issues)), "issues": issues})
    # Legacy multi-rule packets may have only a union of required domains.
    # Keep unresolved requirements at hypothesis scope instead of assigning
    # them to every rule or borrowing a different rule's successful trace.
    remaining = set(required_domains) - assigned_domains
    issues = domain_issues("", "가설 공통 자료", sorted(remaining), profiles)
    if issues:
        checks.append({"ruleId": "", "ruleLabel": "가설 공통 자료", "status": "unavailable",
                       "reason": " ".join(row["reason"] for row in issues), "issues": issues})
    return checks


def merge_availability(refresh, availability):
    """Add native trace failures without replacing them with a generic wait reason."""
    checks = deepcopy(refresh.get("rules") or [])
    native_checks = availability.get("ruleChecks") or []
    checks.extend(deepcopy(native_checks))
    if not any(row.get("status") != "ready" for row in native_checks) and availability.get("state") == "unavailable":
        # Legacy snapshots lack per-rule proof. Keep the restriction explicit;
        # never silently treat an unexplained block as a successful assessment.
        checks.append({"ruleId": "", "ruleLabel": "가설 근거", "status": "unavailable",
                       "reason": " · ".join(availability.get("reasons") or ["보류 원인의 상세 근거가 저장되지 않았습니다."]),
                       "issues": deepcopy(availability.get("issues") or [])})
    source_checks = refresh.get("rules") or []
    for row in checks:
        sources = [source for source in source_checks if not row["ruleId"] or source["ruleId"] == row["ruleId"]]
        trading_issues = all(item.get("domain") in {"quote", "flow", "trend", "price-path", "model-signal", "rule-evidence"}
                             for item in row.get("issues") or [])
        if (row.get("status") == "unavailable" and sources and trading_issues
                and all(source.get("marketSessionStatus") == "closed" for source in sources)):
            row["status"] = "awaiting-source"
    return summary(checks)


def held_availability(refresh):
    return {"version": VERSION, "state": "unavailable", "reasons": [refresh["reason"]],
            "ruleChecks": deepcopy(refresh.get("rules") or []),
            "issues": [deepcopy(issue) for row in refresh.get("rules") or [] for issue in row.get("issues") or []],
            "readyRuleIds": list(refresh.get("readyRuleIds") or []), "blockedRuleIds": list(refresh.get("blockedRuleIds") or [])}
