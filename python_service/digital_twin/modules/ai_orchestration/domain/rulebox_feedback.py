"""Historical feedback guides governed research, never grants investment authority."""

from datetime import datetime, timezone


VERSION = "rulebox-improvement-feedback-v1"


def at_or_before(value, cutoff):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
        return bool(parsed.tzinfo and end.tzinfo and parsed.astimezone(timezone.utc) <= end.astimezone(timezone.utc))
    except (ValueError, TypeError):
        return False


def improvement_feedback(packet, notifications, episodes, unavailable=(), cases=()):
    scope = {k: packet[k] for k in ("accountId", "symbol", "worldId")}
    cutoff = packet["capturedAt"]
    notices, results, publications, seen = [], [], [], set()
    for row in cases:
        if any(row.get(k) != v for k, v in scope.items()) or not at_or_before(row.get("observedAt"), cutoff):
            continue
        dispatch = row.get("dispatch") or {}
        publications.append({"subjectCaseId": row["subjectCaseId"], "observedAt": row["observedAt"],
                             "deploymentId": row.get("deploymentId") or "",
                             "route": dispatch.get("route") or "unrecorded", "reasonCode": dispatch.get("reasonCode") or "",
                             "publicationKind": row.get("publicationKind") or "",
                             "managementResponseCount": row.get("managementResponseCount") or 0,
                             "deliveryState": "publication-record-only; execution mode and production delivery are unverified"})
    for row in notifications:
        if any(row.get(k) != v for k, v in scope.items()) or not at_or_before(row.get("observedAt"), cutoff):
            continue
        comparison, validity = row.get("comparison") or {}, row.get("validity") or {}
        reason = str(row.get("reasonCode") or "")
        category = ("delivered" if row.get("deliveredAt") and at_or_before(row["deliveredAt"], cutoff) else
                    "evidence-recheck" if reason.startswith("rule_evidence_") or validity.get("allowed") is False else
                    "repeat-control" if reason in {"same_rule_notification_meaning", "unchanged_rulebox_relation", "state_cooldown", "similar_repeat"} else
                    "delivery-failure" if row.get("status") == "failed" else "publication-check")
        notices.append({"jobId": row["jobId"], "observedAt": row["observedAt"], "category": category,
                        "reasonCode": reason, "baselineJobId": comparison.get("baselineJobId") or "",
                        "sourceRuleIds": sorted(comparison.get("sourceRuleIds") or []),
                        'policyTransitions': list(comparison.get('policyTransitions') or [])[:6],
                        'remainingPolicyRuleIds': list(comparison.get('remainingPolicyRuleIds') or [])[:12],
                        'unverifiedPreviousPolicyRuleIds': list(comparison.get('unverifiedPreviousPolicyRuleIds') or [])[:12],
                        "recheckRequested": (row.get("recheck") or {}).get("requested") is True})
    for episode in episodes:
        # Older outcome ledgers can lack a world identity. These are explicitly
        # historical account/subject records, never current-world evidence.
        if (episode.get("accountId") != scope["accountId"] or episode.get("symbol") != scope["symbol"]
                or not at_or_before(episode.get("decidedAt"), cutoff)):
            continue
        for outcome in episode.get("outcomes") or []:
            if not outcome.get("outcomeId") or outcome["outcomeId"] in seen or not at_or_before(outcome.get("observedAt"), cutoff):
                continue
            seen.add(outcome["outcomeId"])
            payload = outcome.get("payload") or {}
            eligible = (payload.get("calibrationEligibility") == "eligible" and payload.get("mode") == "contract-criteria"
                        and bool(payload.get("criterionAssessments")) and bool(payload.get("contractFingerprint")))
            results.append({"episodeId": episode["episodeId"], "outcomeId": outcome["outcomeId"],
                            "observedAt": outcome["observedAt"], "status": outcome.get("selectedHypothesisStatus") or "pending",
                            "eligibility": "eligible" if eligible else "excluded-or-unverified",
                            "contractFingerprint": payload.get("contractFingerprint") or "",
                            "horizonMinutes": payload.get("horizonMinutes"),
                            "sourceRuleIds": list((payload.get("hypothesisOutcomeContract") or {}).get("sourceRuleIds") or []),
                            "scope": "historical-account-symbol"})
    if not notices and not results and not publications and not unavailable:
        return []
    return [{"kind": "rulebox-feedback", "version": VERSION, **scope,
             "authority": "historical-system-feedback-only", "notifications": notices[:6], "outcomes": results[:8],
             "cases": publications[:6],
             "unavailableSources": sorted(set(unavailable)), "coverage": "bounded-recent-records; not a complete missed-signal census",
             "use": "원천 재확인·전송 실패·반복 억제와 가설 반증을 구분해 다음 연구를 정합니다. 발송 수와 조건 일치는 예측 성과가 아닙니다. "
                    "규칙 개선이 필요하면 현재 사용 가능한 사실을 인용해 develop-hypothesis로 요청합니다. "
                    "누락·자료 부족은 실패한 예측으로 세지 않습니다. 운영/관리 정책과 코드 문제는 serviceFeedback으로 제안합니다. "
                    "관리 제한의 적용·해제·재적용과 설정 목표 변경을 구분합니다. 남은 제한과 미확인 제한은 해소로 세지 않습니다. "
                    "이 기록은 현재 시장 근거나 규칙 수정·배포 권한이 아니며 기존 격리 실험과 승격 검증을 유지합니다."}]
