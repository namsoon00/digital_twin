"""Evaluate promised observation checks, not investment prediction returns."""
from datetime import timedelta

from digital_twin.modules.ai_orchestration.contracts import resolve_observation_ref, compare_observation_values, observation_instant, comparable_observation_refs
from digital_twin.modules.reasoning.contracts import content_hash


FIELDS = {"currentPrice", "ma5", "ma20", "ma60", "ma20Slope", "ma60Slope", "volumeRatio",
          "tradeStrength", "bidAskImbalance", "foreignNetVolume", "institutionNetVolume", "positionWeight"}


def usable(fact, field):
    if fact.get("freshnessStatus") in {"stale", "invalid", "missing", "expired"} or fact.get("judgementEvidenceUsable") is False:
        return False
    if field in {"foreignNetVolume", "institutionNetVolume"}:
        participant = "foreign" if field.startswith("foreign") else "institution"
        return (fact.get("investorFlowParticipantStatus") or {}).get(participant) not in {"unsupported", "missing"}
    return True


def prepare_observation_conditions(conditions, packet):
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= 3:
        raise ValueError("one to three observable follow-up conditions required")
    result = []
    for row in conditions:
        if not isinstance(row, dict) or set(row) != {"description", "left", "operator", "right", "effect", "horizonMinutes"}:
            raise ValueError("follow-up condition shape")
        if row["effect"] not in {"supports", "weakens", "invalidates"} or not isinstance(row["description"], str) or not 8 <= len(row["description"]) <= 180:
            raise ValueError("follow-up meaning missing")
        if type(row["horizonMinutes"]) is not int or not 60 <= row["horizonMinutes"] <= 10080:
            raise ValueError("follow-up horizon invalid")
        selected, clocks = {}, []
        for side in ("left", "right"):
            ref = row[side]
            fact, value = resolve_observation_ref(packet, ref)
            if ref["period"] != "current" or ref["field"] not in FIELDS or fact.get("kind") != "stock":
                raise ValueError("unsupported automatic follow-up")
            clock = observation_instant(fact.get("sourceAsOf") or fact.get("asOf"))
            if clock is None or not usable(fact, ref["field"]):
                raise ValueError("follow-up baseline not observable")
            selected[side] = {"field": ref["field"], "kind": fact["kind"], "symbol": fact.get("symbol") or packet["symbol"],
                              "currency": fact.get("currency", ""), "baselineValue": value}
            clocks.append(clock)
        comparable_observation_refs(packet, row["left"], row["right"])
        matched = compare_observation_values(selected["left"]["baselineValue"], row["operator"], selected["right"]["baselineValue"])
        created = observation_instant(packet["capturedAt"])
        key = content_hash({"sides": selected, "operator": row["operator"], "effect": row["effect"], "createdAt": packet["capturedAt"]})
        result.append({**row, **selected, "conditionId": key, "createdAt": packet["capturedAt"],
                       "expiresAt": (created + timedelta(minutes=row["horizonMinutes"])).isoformat(),
                       "baselineObservedAt": min(clocks).isoformat(), "lastObservedAt": min(clocks).isoformat(),
                       "previousMatched": matched, "armed": not matched, "status": "pending", "transitionVerified": False})
    return result


def evaluate_observation_conditions(packet, baseline, previous=()):
    baseline = baseline or {}
    if any(baseline.get(key) and baseline[key] != packet.get(key) for key in ("accountId", "symbol")):
        raise ValueError("follow-up baseline ownership mismatch")
    prior = {row["conditionId"]: row for row in previous if row.get("baselineJobId") == baseline.get("jobId")}
    result = []
    now = observation_instant(packet.get("capturedAt"))
    for condition in baseline.get("followUpConditions", []):
        state = {**condition, **prior.get(condition["conditionId"], {}), "baselineJobId": baseline.get("jobId", ""), "transitionVerified": False}
        if state["status"] in {"triggered", "expired"}:
            result.append(state)
            continue
        observations, clocks = {}, []
        try:
            for side in ("left", "right"):
                target = condition[side]
                fact = next(row for row in packet.get("facts", []) if row.get("kind") == target["kind"]
                            and (row.get("symbol") or packet["symbol"]) == target["symbol"])
                clock = observation_instant(fact.get("sourceAsOf") or fact.get("asOf"))
                if clock is None or clock > now or not usable(fact, target["field"]) or fact.get("currency", "") != target.get("currency", ""):
                    raise ValueError("unusable follow-up observation")
                observations[side] = fact[target["field"]]
                clocks.append(clock)
            observed = min(clocks)
            deadline = observation_instant(condition["expiresAt"])
            if observed > deadline or now > deadline and observed <= observation_instant(state["lastObservedAt"]):
                state.update(status="expired", reason="확인 기간 안의 새로운 관측이 없어 평가하지 못했습니다.")
            elif observed <= observation_instant(state["lastObservedAt"]):
                state["reason"] = "동일하거나 더 오래된 시세이므로 전환으로 계산하지 않았습니다."
            else:
                matched = compare_observation_values(observations["left"], condition["operator"], observations["right"])
                transition = bool(state["armed"] and not state["previousMatched"] and matched)
                state.update(status="triggered" if transition else "pending", transitionVerified=transition,
                             previousMatched=matched, armed=bool(state["armed"] or not matched), lastObservedAt=observed.isoformat(),
                             observedValues=observations, reason="새 관측에서 확인 조건이 성립했습니다." if transition else "확인 조건의 새로운 성립 전이가 없습니다.")
        except (ValueError, KeyError, StopIteration, TypeError):
            expired = now and now > observation_instant(condition["expiresAt"])
            state.update(status="expired" if expired else "unavailable", reason="필요한 시점의 자료가 없어 평가하지 못했습니다.")
        result.append(state)
    return result
