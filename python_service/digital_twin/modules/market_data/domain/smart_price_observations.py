"""Compose the first smart quote-alert policy with the legacy cumulative lane."""
from copy import deepcopy

from digital_twin.modules.notifications.contracts import price_movement_lines, position_price_lines, price_context_lines, price_current_snapshot
from digital_twin.modules.portfolio.contracts import AlertEvent
from .price_observation_episodes import enabled, policy, advance_episodes, VERSION
from .price_observation_windows import finite, positions
from .market_observations import quote_witness
from .price_observation_context import advance_price_context
from .volume_observation import position_volume_pace_snapshot


def position_effect(item, role, portfolio, start_price):
    quantity = finite(item.get("quantity"))
    if role != "holding" or not quantity or quantity <= 0:
        return {}
    price = finite(item.get("current_price"))
    if price is None or price <= 0:
        return {}
    effect = {"nativeAmount": quantity * (price - start_price), "quantity": quantity,
              "basis": "current-quantity-and-fx-fixed", "startPrice": start_price}
    average = finite(item.get("average_price"))
    if average is not None and average > 0:
        effect.update(averagePrice=average, unrealizedNativeAmount=quantity * (price - average),
                      unrealizedReturnPct=(price / average - 1) * 100,
                      unrealizedBasis="current-quote-minus-average-cost-before-fees-and-tax")
    total = finite(portfolio.get("total"))
    fx = 1 if item.get("currency") == "KRW" else finite(item.get("exchange_rate"))
    if item.get("currency") != "KRW" and item.get("valuation_fx_state") not in {"fresh", "available", "verified", "usable"}:
        fx = None
    if total and total > 0 and fx and fx > 0:
        effect.update(accountImpactPct=effect["nativeAmount"] * fx / total * 100,
                      weightPct=quantity * price * fx / total * 100, fxRate=fx,
                      accountDenominator=total, denominatorBasis="current-portfolio-total")
    return effect


def smart_market_observations(snapshot, previous, legacy_events, settings):
    subjects = positions(snapshot.to_monitor_state())
    legacy_events = [event for event in legacy_events
                     if quote_witness(subjects.get(event.symbol, ({}, ""))[0], snapshot.generated_at)]
    current = snapshot.to_monitor_state()
    def with_current_data(events):
        for event in events:
            item, role = subjects.get(event.symbol, ({}, "watchlist"))
            observation = event.metadata.get("marketObservation") or {}
            effect = position_effect(item, role, current.get("portfolio") or {}, observation.get("baselinePrice", 0))
            event.metadata["priceCurrentSnapshot"] = price_current_snapshot(item,
                account_id=snapshot.account_id, role=role, captured_at=snapshot.generated_at,
                effect=effect, volume_pace=position_volume_pace_snapshot(item))
        return events
    paths, states, proposals = advance_episodes(current, previous, settings)
    _, contexts = advance_price_context(current, previous, paths)
    for event in legacy_events:
        context = contexts.get(event.symbol) or {}
        event.metadata["priceContext"] = deepcopy(context)
        event.lines.extend(price_context_lines(context, str(subjects.get(event.symbol, ({}, ""))[0].get("currency") or "")))
    if not enabled(settings):
        return with_current_data(legacy_events)
    mode = str(settings.get("marketObservationRawDeliveryMode") or "critical-only")
    subjects = positions(current)
    # Existing ordinary candidates still request the normal graph follow-up.
    result = []
    for event in legacy_events:
        item, role = subjects.get(event.symbol, ({}, "watchlist"))
        episode = states.get(event.symbol) or {}
        usable_path = (paths.get(event.symbol) or {}).get("status") in {"available", "unchanged-source"}
        if event.symbol in proposals or (usable_path and episode and episode.get("lastQueuedStage") != "recovery"):
            if not event.metadata.get("deliveryDeferred"):
                event.metadata["deliveryDeferred"] = True
            result.append(event)
            continue
        observation = event.metadata.get("marketObservation") or {}
        observation["subjectRole"] = role
        if role == "watchlist" and not event.metadata.get("deliveryDeferred"):
            threshold = observation.get("deliveryThresholdPct", 3) * policy(settings)["watchlistMultiplier"]
            observation["deliveryThresholdPct"] = threshold
            if abs(observation.get("changePct", 0)) < threshold:
                event.metadata["deliveryDeferred"] = True
            event.criteria = ["설정: 관심 종목 누적 변동 " + format(threshold, ".2f") + "% 이상"]
        event.lines.insert(0, "구분: " + ("보유 종목" if role == "holding" else "관심 종목"))
        effect = position_effect(item, role, current.get("portfolio") or {}, observation.get("baselinePrice", 0))
        if effect:
            event.metadata["positionPriceEffect"] = effect
            event.lines.extend(position_price_lines(effect, str(item.get("currency") or ""), "알림 기준가 대비"))
        summary = [line for prefix in ("기준 대비:", "현재가:", "이번 가격 변동분:")
                   for line in event.lines if line.startswith(prefix)]
        event.lines = summary + [line for line in event.lines if line not in summary]
        result.append(event)
    if mode in {"disabled", "typedb-first"}:
        return with_current_data(result)
    for symbol, movement in proposals.items():
        item, role = subjects[symbol]
        currency, source = str(item.get("currency") or ""), str(item.get("quote_source") or "시세 공급자")
        effect = position_effect(item, role, current.get("portfolio") or {}, movement["anchorPrice"])
        movement["positionEffect"] = effect
        # A compact source witness proves the path used for this transition.
        movement["sourcePath"] = deepcopy(paths[symbol])
        observation = {"observationOnly": True, "baselinePrice": movement["anchorPrice"],
            "baselineKind": "price-movement-start", "currentPrice": movement["currentPrice"],
            "changePct": movement["changePct"], "direction": movement["direction"], "currency": currency,
            "source": source, "subjectRole": role, "thresholdPct": movement["trigger"]["thresholdPct"],
            "deliveryThresholdPct": movement["trigger"]["thresholdPct"],
            "quoteWitness": quote_witness(item, snapshot.generated_at)}
        result = [event for event in result if event.symbol != symbol]
        result.append(AlertEvent(snapshot.account_id, snapshot.account_label, "WATCH", "marketObservation",
            ":".join([snapshot.account_id, "price-movement", movement["episodeId"], str(movement["sequence"])]),
            item.get("name") or symbol,
            price_movement_lines(movement, currency, source, item.get("market_session_label") or item.get("market_session"), effect)
                + price_context_lines(contexts.get(symbol) or {}, currency,
                                      compact=movement["stage"] in {"expanded", "recovery"},
                                      window_minutes=movement["trigger"]["windowMinutes"]), symbol,
            criteria=["설정: " + str(movement["trigger"]["windowMinutes"]) + "분 기준 변동률 " +
                      format(movement["trigger"]["thresholdPct"], ".2f") + "% · 실제 관측 시간으로 보정"],
            metadata={"marketObservation": observation, "priceMovement": movement, "priceContext": contexts.get(symbol) or {},
                "observationOnly": True, "investmentJudgement": False, "deliveryDeferred": False,
                "reasoningFollowupRequired": True,
                "deliveryMode": "deterministic-outbox-before-typedb", "reasoningFollowup": "queued-from-persisted-snapshot"},
            generated_at=snapshot.generated_at))
    return with_current_data(result)


def movement_cadence(event, default_minutes):
    value = (event.metadata or {}).get("priceMovement") or {}
    return 1 if value.get("version") == VERSION and value.get("stage") in {"expanded", "recovery", "reversed"} else default_minutes
