import json
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.notification_reverse_reasoning import (  # noqa: E402
    TRACE_VERSION,
    build_notification_reverse_reasoning_trace,
)
from digital_twin.domain.notifications import NotificationJob  # noqa: E402
from digital_twin.infrastructure.web_server import (  # noqa: E402
    notification_action_flow,
    notification_job_detail_payload,
    notification_job_list_payload,
    notification_job_public_payload,
)


def notification_context():
    return {
        "messageType": "investmentInsight",
        "accountId": "main",
        "displayTarget": "삼성전자 / 005930",
        "referenceDate": "2026-07-23T01:30:00Z",
        "clientSecret": "must-not-leak",
        "deliveryDecision": "send",
        "deliveryGateState": "passed",
        "deliveryGateReason": "새 공시 근거와 위험 관계가 함께 확인됐습니다.",
        "deliveryReasons": ["관계 변화가 재알림 기준을 넘었습니다."],
        "cooldownReason": "새 근거로 반복 보류를 해제했습니다.",
        "dataFreshnessStatus": "fresh",
        "dataFreshnessReason": "필수 시장 데이터가 허용 시간 안에 수집됐습니다.",
        "newsHeadlines": {
            "items": [{
                "title": "삼성전자 공시 관련 기사",
                "url": "https://example.test/samsung-disclosure",
                "domain": "example.test",
                "publishedAt": "2026-07-23T01:20:00Z",
                "stockImpactLabel": "주의",
            }],
        },
        "notificationAiValidatedResponse": {
            "action": "TRIM",
            "actionLabel": "분할축소",
            "summary": "가격 흐름과 공시 위험이 함께 약해져 일부 비중 축소를 우선 검토합니다.",
            "validationState": "ready",
            "validationLabel": "검증 완료",
            "dataState": "sufficient",
            "dataStateLabel": "판단에 필요한 자료 있음",
            "reviewLevel": "act",
            "reviewLabel": "대응 준비",
            "precomputedAction": "HOLD",
            "disagreementReason": "매수 호가 우위보다 공시와 중기 추세 약화의 영향이 더 컸습니다.",
            "selectedHypothesisId": "hypothesis:risk",
            "hypothesisComparisonState": "completed",
            "hypothesisSelectionSource": "ai-comparison",
            "unresolvedQuestions": ["공시 원문에서 발행 조건을 확인합니다."],
            "decisionReadiness": "ready",
            "causalChain": [{
                "driver": "신규 자금조달 공시와 가격 약화",
                "channel": "risk",
                "expectedEffect": "보유 위험을 높임",
                "evidenceIds": ["evidence:disclosure"],
                "status": "supported",
            }],
            "alternativeAction": {
                "action": "HOLD",
                "actionLabel": "보유",
                "whyNotSelected": "공시 위험이 아직 해소되지 않았습니다.",
                "switchCondition": "공시 위험이 해소되고 가격이 회복되면 보유로 바꿉니다.",
            },
            "hypotheses": [
                {"hypothesisId": "hypothesis:risk", "verdict": "supported", "reasoning": "공시와 하락 추세가 같은 위험 방향입니다."},
                {"hypothesisId": "hypothesis:support", "verdict": "weakened", "reasoning": "호가 우위는 단기 신호여서 중기 위험을 뒤집기 부족합니다."},
                {"hypothesisId": "hypothesis:safety", "verdict": "supported", "reasoning": "반대 근거가 있어 전량 매도 대신 분할축소가 적절합니다."},
            ],
            "rawResponse": "must-not-leak",
        },
        "ontologyRelationContext": {
            "engineVersion": "typedb-inferencebox-relation-context-v1",
            "graphStore": "typedb",
            "graphStoreUsed": True,
            "nativeTypeDbReasoningUsed": True,
            "fallbackUsed": False,
            "inferenceGenerationId": "generation:20260723:005930",
            "inferenceGenerationAt": "2026-07-23T01:30:00Z",
            "ruleboxShortHash": "rulebox-abc123",
            "portfolioWorldId": "portfolio:local:main",
            "marketWorldId": "market:shared:KR",
            "subject": {"symbol": "005930", "name": "삼성전자", "market": "KR"},
            "facts": {
                "currentPrice": 70000,
                "averagePrice": 78000,
                "profitLossRate": -10.2,
                "ma20": 76000,
                "ma20Distance": -7.9,
                "foreignNetVolume": -500000,
                "institutionNetVolume": -220000,
                "missingData": [{"label": "공시 본문", "effect": "발행 조건 확인 전 위험 강도를 제한합니다."}],
            },
            "decision": {
                "label": "공시 이벤트 위험 점검",
                "selectedRuleId": "graph.disclosure.event-risk.v1",
                "candidateAction": "TRIM",
            },
            "actionEnvelope": {
                "status": "HOLDING_REVIEW",
                "statusLabel": "보유 판단 재확인",
                "preferredAction": "TRIM",
                "targetRole": "holding",
                "dataReadiness": {"state": "ready", "dataState": "sufficient", "usable": True},
                "effectLabels": [{"effect": "constrain", "label": "제약 조건", "ruleIds": ["graph.disclosure.event-risk.v1"]}],
                "nextChecks": ["공시 원문과 다음 가격 반응을 확인"],
                "invalidationConditions": ["공시 위험 관계가 사라지고 가격 회복 관계가 생기면 재검토"],
            },
            "executionPlan": {"primaryActionLabel": "분할축소 우선 검토"},
            "activeRules": [{
                "ruleId": "graph.disclosure.event-risk.v1",
                "label": "보유 종목 + 공시 이벤트 → 공시 위험 확인",
                "inferenceTraceId": "trace:disclosure",
                "reviewLabel": "대응 준비",
                "dataStateLabel": "판단에 필요한 자료 있음",
                "evidenceRole": "risk",
                "evidence": ["신규 공시", "보유 종목"],
            }],
            "graphStoreInference": {
                "entityCount": 24,
                "relationCount": 18,
                "traceCount": 1,
                "traces": [{
                    "id": "trace:disclosure",
                    "ruleId": "graph.disclosure.event-risk.v1",
                    "matchedConditions": [
                        {"conditionId": "holding", "summary": "삼성전자 보유 수량 10주"},
                        {"conditionId": "disclosure", "summary": "신규 자금조달 공시 확인"},
                    ],
                }],
            },
            "investmentBrain": {
                "hypothesisSet": {
                    "hypotheses": [
                        {
                            "hypothesisId": "hypothesis:risk",
                            "templateLabel": "공시와 추세가 겹친 위험 경로",
                            "claim": "공시 이벤트와 중기 추세 약화가 함께 위험을 설명합니다.",
                            "stance": "risk",
                            "evidenceState": "supported",
                            "supportingRuleIds": ["graph.disclosure.event-risk.v1"],
                            "supportingEvidenceIds": ["evidence:disclosure"],
                            "counterEvidenceIds": ["evidence:bid"],
                            "causalPathIds": ["trace:disclosure"],
                            "assumptions": ["공시 영향이 아직 가격에 반영 중입니다."],
                            "invalidationConditions": ["공시 내용이 위험하지 않고 가격이 회복하면 약화됩니다."],
                            "horizon": "short-term",
                            "verificationStatus": "typedb-current-generation",
                        },
                        {
                            "hypothesisId": "hypothesis:support",
                            "templateLabel": "단기 호가 방어 경로",
                            "claim": "매수 호가 우위가 단기 반등을 지지합니다.",
                            "stance": "support",
                            "evidenceState": "contested",
                            "supportingRuleIds": ["graph.orderbook.support.v1"],
                        },
                        {
                            "hypothesisId": "hypothesis:safety",
                            "templateLabel": "증거 충분성 안전 경로",
                            "claim": "반대 근거가 있어 전량 처분 판단은 보수적으로 봅니다.",
                            "stance": "context",
                            "evidenceState": "supported",
                            "supportingRuleIds": [],
                        },
                    ],
                },
            },
        },
        "decisionTransition": {
            "kind": "action-changed",
            "summary": "보유 유지에서 분할축소 검토로 바뀌었습니다.",
            "previousAction": "HOLD",
            "currentAction": "TRIM",
            "material": True,
        },
    }


class NotificationReverseReasoningTests(unittest.TestCase):
    def test_trace_reconstructs_the_saved_inference_chain(self):
        trace = build_notification_reverse_reasoning_trace(notification_context(), job_id="job-1", job_status="done")

        self.assertEqual(TRACE_VERSION, trace["version"])
        self.assertEqual("ready", trace["status"])
        self.assertTrue(trace["snapshotBound"])
        self.assertEqual("generation:20260723:005930", trace["snapshot"]["inferenceGenerationId"])
        self.assertEqual("분할축소", trace["finalDecision"]["actionLabel"])
        self.assertTrue(trace["aiComparison"]["changed"])
        self.assertEqual("ready", trace["aiComparison"]["decisionReadiness"])
        self.assertEqual("evidence:disclosure", trace["aiComparison"]["causalChain"][0]["evidenceIds"][0])
        self.assertEqual("HOLD", trace["aiComparison"]["alternativeAction"]["action"])
        self.assertEqual("hypothesis:risk", trace["selectedHypothesis"]["hypothesisId"])
        self.assertTrue(trace["matchedRules"][0]["selected"])
        self.assertEqual("holding", trace["inferenceTraces"][0]["conditions"][0]["label"])
        self.assertEqual(2, len(trace["alternativeHypotheses"]))
        self.assertEqual("https://example.test/samsung-disclosure", trace["sources"][0]["url"])
        self.assertIn("공시 본문: 발행 조건 확인 전 위험 강도를 제한합니다.", trace["missingData"])

        serialized = json.dumps(trace, ensure_ascii=False)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("rawResponse", serialized)
        self.assertNotIn("clientSecret", serialized)

    def test_trace_makes_missing_historical_context_explicit(self):
        trace = build_notification_reverse_reasoning_trace({"title": "이전 알림"}, job_id="legacy-job")

        self.assertEqual("unavailable", trace["status"])
        self.assertFalse(trace["snapshotBound"])
        self.assertIn("저장되지 않았습니다", trace["reason"])

    def test_detail_trace_preserves_all_saved_rows_and_full_text(self):
        context = notification_context()
        relation = context["ontologyRelationContext"]
        long_evidence = "전체 근거 " + ("가나다라마바사 " * 90).strip()
        context["completeSources"] = [
            {"url": "https://example.test/complete-source-" + str(index)}
            for index in range(60)
        ]
        relation["facts"].update({"customFact" + str(index): "값 " + str(index) for index in range(30)})
        relation["activeRules"] = []
        relation["graphStoreInference"]["traces"] = []
        hypotheses = []
        ai_hypotheses = []
        for index in range(25):
            rule_id = "graph.complete.rule." + str(index)
            trace_id = "trace:complete:" + str(index)
            relation["activeRules"].append({
                "ruleId": rule_id,
                "label": "전체 규칙 " + str(index),
                "inferenceTraceId": trace_id,
                "evidence": [long_evidence, "근거 " + str(index)],
            })
            relation["graphStoreInference"]["traces"].append({
                "id": trace_id,
                "ruleId": rule_id,
                "matchedConditions": [
                    {"conditionId": "condition:" + str(condition), "summary": "관측값 " + str(condition)}
                    for condition in range(15)
                ],
            })
            hypothesis_id = "hypothesis:complete:" + str(index)
            hypotheses.append({
                "hypothesisId": hypothesis_id,
                "templateLabel": "전체 가설 " + str(index),
                "claim": long_evidence + " / " + str(index),
                "supportingRuleIds": [rule_id],
                "assumptions": ["전제 " + str(value) for value in range(8)],
                "invalidationConditions": ["무효 " + str(value) for value in range(8)],
            })
            ai_hypotheses.append({
                "hypothesisId": hypothesis_id,
                "verdict": "supported",
                "reasoning": long_evidence,
            })
        relation["investmentBrain"]["hypothesisSet"]["hypotheses"] = hypotheses
        context["notificationAiValidatedResponse"]["hypotheses"] = ai_hypotheses
        context["notificationAiValidatedResponse"]["selectedHypothesisId"] = "hypothesis:complete:0"
        context["notificationAiValidatedResponse"]["summary"] = long_evidence

        trace = build_notification_reverse_reasoning_trace(context)

        self.assertFalse(trace["completeness"]["truncated"])
        self.assertGreaterEqual(len(trace["inputFacts"]), 30)
        self.assertEqual(25, len(trace["matchedRules"]))
        self.assertEqual(25, len(trace["inferenceTraces"]))
        self.assertEqual(15, len(trace["inferenceTraces"][0]["conditions"]))
        self.assertEqual(25, len(trace["hypotheses"]))
        self.assertEqual(24, len(trace["alternativeHypotheses"]))
        self.assertGreaterEqual(len(trace["sources"]), 60)
        self.assertEqual(long_evidence, trace["finalDecision"]["summary"])
        self.assertEqual(long_evidence, trace["matchedRules"][0]["evidence"][0])
        self.assertEqual(
            ["abox-facts", "typedb-rule", "hypothesis", "ai-decision", "delivery"],
            [step["id"] for step in trace["steps"]],
        )

    def test_web_renders_reasoning_in_execution_order_without_slicing_rules(self):
        source = (Path(__file__).resolve().parents[2] / "public" / "app.js").read_text(encoding="utf-8")
        render_source = source[
            source.index("function renderNotificationReverseReasoning"):
            source.index("function renderNotificationDecisionDetail")
        ]
        markers = [
            'renderNotificationReasoningStep(1, "원천 데이터·ABox 사실"',
            'renderNotificationReasoningStep(2, "TypeDB 규칙 실행"',
            'renderNotificationReasoningStep(3, "경쟁 가설 구성"',
            'renderNotificationReasoningStep(4, decisionStepTitle',
            'renderNotificationReasoningStep(5, "판단·실행·성과 수명주기"',
            'renderNotificationReasoningStep(6, "알림 발송"',
        ]

        positions = [render_source.index(marker) for marker in markers]

        self.assertEqual(sorted(positions), positions)
        self.assertNotIn("slowRules", render_source)
        self.assertNotIn("decisionGuardrails.slice", render_source)
        self.assertIn("var causalChain = Array.isArray(comparison.causalChain)", render_source)
        self.assertIn("state.notificationJobDetails[key] || notificationJobByKey(key)", source)
        self.assertIn('state.workDetailLayer.type === "notification-job"', source)
        self.assertIn('renderNotificationDetailMetric("자료 상태"', source)
        self.assertIn("var missing = Array.isArray(trace.missingData)", render_source)
        self.assertIn('class="notification-reasoning-step-disclosure"', source)
        self.assertIn("function renderNotificationDetailDisclosure", source)
        self.assertIn("function renderNotificationUnifiedPipeline", source)
        self.assertIn('"전체 처리 계보"', source)
        self.assertIn("이 단계의 전체 저장 데이터", source)
        self.assertIn('"추론 과정 상세"', source)
        self.assertIn('detailFacts.length + "개 사실', source)
        self.assertIn("notificationDetailDisclosureOpen", source)
        self.assertIn('data-notification-detail-disclosure-key', source)
        self.assertIn('reasoningDisclosurePrefix + "6"', source)

    def test_detail_endpoint_exposes_trace_without_bloating_list_payload(self):
        job = NotificationJob.create(
            "알림 본문",
            account_id="main",
            account_label="기본 계정",
            message_type="investmentInsight",
            context=notification_context(),
        )

        class Queue:
            def get(self, job_id):
                return job if job_id == job.job_id else None

        class AIStore:
            def trace_for_notification(self, _job_id):
                return {}

        class ExecutionStore:
            def execution_trace_for_inference_generation(self, *_args, **_kwargs):
                return {"status": "ready", "runCount": 0, "runs": []}

        class InvestmentStore:
            def lifecycle_trace(self, _episode_id):
                return {"status": "unavailable"}

        with mock.patch("digital_twin.infrastructure.web_server.notification_queue_store", return_value=Queue()), \
                mock.patch("digital_twin.infrastructure.web_server.stores.ai_inference_queue_store", return_value=AIStore()), \
                mock.patch("digital_twin.infrastructure.web_server.stores.ontology_projection_run_store", return_value=ExecutionStore()), \
                mock.patch("digital_twin.infrastructure.web_server.stores.investment_domain_store", return_value=InvestmentStore()):
            detail = notification_job_detail_payload(job.job_id)

        self.assertIn("reasoningTrace", detail["job"])
        self.assertEqual("ready", detail["job"]["reasoningTrace"]["status"])
        self.assertEqual("분할축소", detail["job"]["actionFlow"]["currentActionLabel"])
        self.assertEqual("action-changed", detail["job"]["actionFlow"]["transition"]["kind"])
        self.assertEqual("분할축소 검토 시작", detail["job"]["actionFlow"]["transition"]["label"])
        self.assertEqual("sufficient", detail["job"]["actionFlow"]["dataReadiness"]["dataState"])
        self.assertTrue(detail["job"]["reasoningTrace"]["missingData"])
        self.assertEqual("notification-trace-v2", detail["job"]["notificationTrace"]["contractVersion"])
        self.assertEqual(9, detail["job"]["notificationTrace"]["pipeline"]["stageCount"])
        self.assertNotIn("reasoningTrace", notification_job_list_payload(job, stale_minutes=30))
        self.assertNotIn("actionFlow", notification_job_list_payload(job, stale_minutes=30))

    def test_action_flow_explains_watchlist_entry_pause_without_sale_language(self):
        context = notification_context()
        context["displayTarget"] = "NVIDIA / NVDA"
        context["symbol"] = "NVDA"
        relation = context["ontologyRelationContext"]
        relation["facts"].update({"source": "watchlist", "isWatchlist": True})
        relation.update({"targetRole": "watchlist", "actionPolicy": "ENTRY_ONLY"})
        relation["decision"].update({"candidateAction": "HOLD", "targetRole": "watchlist", "actionPolicy": "ENTRY_ONLY"})
        relation["actionEnvelope"].update({
            "status": "ENTRY_DEFERRED",
            "statusLabel": "진입 조건 추가 확인",
            "preferredAction": "HOLD",
            "targetRole": "watchlist",
            "actionPolicy": "ENTRY_ONLY",
        })
        context["notificationAiValidatedResponse"].update({"action": "HOLD", "actionLabel": "관심 유지"})
        context["headline"] = "[관찰] 🧭 NVIDIA: 신규 진입 대기: 조건 재확인"
        context["titleIcon"] = "🧭"
        context["decisionTransition"] = {
            "kind": "action-changed",
            "previousAction": "BUY",
            "currentAction": "HOLD",
            "previousStatus": "ENTRY_ELIGIBLE",
            "currentStatus": "ENTRY_DEFERRED",
            "material": True,
        }

        transition = notification_action_flow(context)["transition"]

        self.assertEqual("entry-paused", transition["category"])
        self.assertEqual("신규 매수 보류", transition["label"])
        self.assertIn("매도 신호가 아니라", transition["summary"])

        job = NotificationJob.create("알림 본문", message_type="investmentInsight", context=context)
        payload = notification_job_public_payload(job, detail=True)
        self.assertEqual("⏸️", payload["messageTypeIcon"])
        self.assertIn("⏸️ NVIDIA", payload["title"])

    def test_web_uses_current_safe_presentation_for_persisted_ai_alert(self):
        context = notification_context()
        context["messageDeliveryLevel"] = "beginner"
        context["decisionTransition"] = {
            "kind": "envelope-changed",
            "currentStatus": "entry_observing",
            "summary": "이전 조건에서 entry_observing으로 바뀌었습니다.",
            "material": True,
        }
        context["notificationAiValidatedResponse"].update({
            "action": "HOLD",
            "actionLabel": "관심 유지",
            "evidence": ["거시 부담이 유지됩니다. supportingEvidenceIds: relation-evidence:abc"],
            "missingDataImpact": [
                "연구 사이클에서 changedEvidenceCount가 0이고 reasoningRefreshed도 false라, "
                "기존 뉴스·조사 내용을 새 판단 근거처럼 강화할 수 없습니다.",
            ],
        })
        context["ontologyRelationContext"]["actionEnvelope"].update({
            "status": "ENTRY_OBSERVING",
            "statusLabel": "관심 유지",
            "preferredAction": "HOLD",
            "targetRole": "watchlist",
        })
        job = NotificationJob.create("old rendered message", message_type="investmentInsight", context=context)

        payload = notification_job_public_payload(job, detail=True)

        self.assertIn("[관심 유지] 현재 행동은 관심 유지입니다. 매수 판단으로 바뀐 것은 아닙니다.", payload["fullText"])
        self.assertIn("새 뉴스·조사 근거가 아직 갱신되지 않아 기존 정보만 참고합니다.", payload["fullText"])
        for internal in ["old rendered message", "entry_observing", "supportingEvidenceIds", "relation-evidence", "changedEvidenceCount", "reasoningRefreshed"]:
            self.assertNotIn(internal, payload["fullText"])


if __name__ == "__main__":
    unittest.main()
