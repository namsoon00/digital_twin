import { configuredChip } from "../accounts/directory.mjs";
import { newsProviderLabel, newsStateSettingLabel, settingEnabled } from "../decisions/signals.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { isConfiguredSetting, renderSettingField, renderSettingSelect, renderSettingsApiCard, settingValue, settingsStatusLabel, settingsStatusTone } from "../settings/fields.mjs";
import { renderSettingsAiRuntimeGroup, renderSettingsGroup, renderSettingsSmartSavePanel } from "../settings/legacy.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { configuredCount } from "../shell/commands.mjs";

function renderFeedSettingsEditorPanel() {
  var archiveScope = (settingValue("newsCollectionMaxSymbols") || defaultSettings.newsCollectionMaxSymbols || "40") + "종목 · "
    + (settingValue("newsCollectionLookbackMinutes") || defaultSettings.newsCollectionLookbackMinutes || "180") + "분";
  var reasoningScope = "TypeDB · "
    + (settingValue("ontologyReasoningIntervalSeconds") || defaultSettings.ontologyReasoningIntervalSeconds || "10") + "초";
  return [
    '<article class="panel feed-settings-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Feed Operations</p>',
    '<h2>피드 수집 설정</h2>',
    '</div>',
    '<span class="tone-chip ' + settingsStatusTone() + '" data-settings-status>' + settingsStatusLabel() + '</span>',
    '</div>',
    '<div class="settings-body feed-settings-body">',
    '<div class="settings-api-grid feed-settings-summary">',
    renderSettingsApiCard("원천 준비도", "뉴스·공시·SEC", [
      configuredChip("KIS 수급", settingEnabled("kisMarketSignalsEnabled"), configuredCount(["kisAppKey", "kisAppSecret"]) + "/2"),
      configuredChip("뉴스", settingEnabled("externalNewsEnabled"), newsProviderLabel(settingValue("externalNewsProvider") || defaultSettings.externalNewsProvider)),
      configuredChip("OpenDART", settingEnabled("externalDartEnabled"), isConfiguredSetting("opendartApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("공식 일별 시세", settingEnabled("externalPublicDataStockEnabled"), isConfiguredSetting("publicDataPortalServiceKey") ? "키 저장됨" : "키 필요"),
      configuredChip("공식 기업·지수", settingEnabled("externalPublicDataReferenceEnabled"), isConfiguredSetting("publicDataPortalServiceKey") ? "키 저장됨" : "키 필요"),
      configuredChip("SEC", settingEnabled("externalSecEnabled"), "무키"),
      configuredChip("Alpha", settingEnabled("externalAlphaEnabled"), isConfiguredSetting("alphaVantageApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("FRED", settingEnabled("externalFredEnabled"), isConfiguredSetting("fredApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("CoinGecko", settingEnabled("externalCoinGeckoEnabled"), isConfiguredSetting("coingeckoApiKey") ? "키 저장됨" : "키 없음")
    ]),
    renderSettingsApiCard("아카이브 범위", archiveScope, [
      configuredChip("관심", settingValue("newsCollectionIncludeWatchlist") !== "0", "포함"),
      configuredChip("보유", settingValue("newsCollectionIncludeHoldings") !== "0", "포함"),
      configuredChip("관련성", true, newsStateSettingLabel("relevance", settingValue("newsCollectionMinimumRelevanceState") || defaultSettings.newsCollectionMinimumRelevanceState)),
      configuredChip("저장 중요도", settingEnabled("newsCollectionQualityGateEnabled"), newsStateSettingLabel("materiality", settingValue("newsCollectionMinimumMaterialityState") || defaultSettings.newsCollectionMinimumMaterialityState))
    ]),
    renderSettingsApiCard("추론 흐름", reasoningScope, [
      configuredChip("추론", settingEnabled("ontologyReasoningEnabled"), settingValue("ontologyReasoningBatchSize") || defaultSettings.ontologyReasoningBatchSize || "20"),
      configuredChip(
        "AI 판단",
        settingEnabled("notificationAiGateEnabled") && String(settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount || "0") !== "0",
        String(settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount || "0") + "개 실행"
      ),
      configuredChip("게이트", settingEnabled("materialityGateEnabled"), "상태 전이"),
      configuredChip("뉴스 기준", true, newsStateSettingLabel("materiality", settingValue("newsDigestMinimumMaterialityState") || defaultSettings.newsDigestMinimumMaterialityState))
    ]),
    '</div>',
    '<div class="feed-settings-sections">',
    renderSettingsGroup("장중 시세·수급", "KIS WebSocket 체결·호가와 REST 투자자 수급 호출량을 조정합니다.", [
      renderSettingSelect("kisRealtimeWebSocketEnabled", "KIS 체결·호가 WebSocket", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("kisRealtimeWebSocketMaxSymbols", "WebSocket 종목 수", "number", "20"),
      renderSettingField("kisRealtimeWebSocketEventIntervalSeconds", "WebSocket 추론 묶음(초)", "number", "15"),
      renderSettingSelect("kisMarketSignalsEnabled", "KIS 수급 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("kisMarketSignalMaxSymbols", "KIS 수급 종목 수", "number", "20"),
      renderSettingField("kisMarketSignalCacheMinutes", "KIS 수급 캐시(분)", "number", "3"),
      renderSettingField("kisMarketSignalGapSeconds", "KIS 호출 간격(초)", "number", "0.35"),
      renderSettingSelect("kisMarketSignalPreferLiveDuringMarketHours", "장중 KIS 최신 시세 우선", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("kisMarketSignalLiveRefreshSeconds", "장중 시세 최소 조회 간격(초)", "number", "60"),
      renderSettingSelect("kisInvestorIntradayEstimateEnabled", "장중 외국인·기관 추정 수급", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("kisMarketSignalUnchangedStaleCount", "레거시 동일 수급값 판정 횟수", "number", "3")
    ].join(""), "market feed-compact"),
    renderSettingsGroup("뉴스 헤드라인", "빠른 외부 뉴스 조회 범위를 관리합니다.", [
      renderSettingSelect("externalNewsEnabled", "뉴스 헤드라인 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("externalNewsProvider", "뉴스 공급자", [
        { value: "auto", label: "자동" },
        { value: "gdelt", label: "GDELT" },
        { value: "alpha_vantage", label: "Alpha Vantage" }
      ]),
      renderSettingField("externalNewsMaxSymbols", "뉴스 조회 종목 수", "number", "3"),
      renderSettingField("externalNewsLookbackHours", "뉴스 조회 기간(시간)", "number", "48"),
      renderSettingField("externalResearchEvidenceMaxItems", "AI 전달 최신 근거 수", "number", "8")
    ].join(""), "research feed-compact"),
    renderSettingsGroup("뉴스 아카이브", "저장형 뉴스 수집 워커의 대상과 속도입니다.", [
      renderSettingSelect("newsCollectionEnabled", "뉴스 아카이브 실시간 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("newsCollectionIntervalSeconds", "뉴스 수집 주기(초)", "number", "60"),
      renderSettingField("newsCollectionMaxSymbols", "뉴스 수집 종목 수", "number", "40"),
      renderSettingField("newsCollectionLookbackMinutes", "뉴스 조회 기간(분)", "number", "180"),
      renderSettingField("newsCollectionPerSymbolLimit", "종목별 저장 기사 수", "number", "8"),
      renderSettingField("newsCollectionProviders", "뉴스 수집 채널", "text", "yahoo_search,yahoo_finance"),
      renderSettingSelect("newsCollectionQualityGateEnabled", "저장 전 뉴스 품질 검증", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("newsCollectionMinimumRelevanceState", "저장할 최소 관련성", [
        { value: "direct", label: "종목 직접 기사만" },
        { value: "related", label: "관련 기사부터" },
        { value: "context", label: "관련 맥락부터" }
      ]),
      renderSettingSelect("newsCollectionMinimumMaterialityState", "저장할 최소 중요성", [
        { value: "material", label: "중요 사건만" },
        { value: "notable", label: "확인할 정보부터" }
      ]),
      renderSettingSelect("newsCollectionMinimumSourceTrustState", "저장할 최소 출처", [
        { value: "standard", label: "일반 언론 이상" },
        { value: "trusted", label: "신뢰 출처만" }
      ]),
      renderSettingSelect("newsCollectionRequireArticleBody", "저장 시 원문 본문", [
        { value: "1", label: "본문 검증 필수" },
        { value: "0", label: "제목·요약도 허용" }
      ]),
      renderSettingSelect("newsDigestMinimumRelevanceState", "알림에 넣을 최소 관련성", [
        { value: "related", label: "관련 기사부터" },
        { value: "direct", label: "종목 직접 기사만" },
        { value: "context", label: "관련 맥락부터" }
      ]),
      renderSettingSelect("newsDigestMinimumMaterialityState", "알림에 넣을 최소 중요성", [
        { value: "notable", label: "확인할 정보부터" },
        { value: "material", label: "중요 정보부터" },
        { value: "critical", label: "즉시 확인 정보만" },
        { value: "routine", label: "일상 정보부터" }
      ]),
      renderSettingSelect("newsDigestMinimumNeutralMaterialityState", "중립 기사 최소 중요성", [
        { value: "material", label: "중요 정보부터" },
        { value: "critical", label: "즉시 확인 정보만" },
        { value: "notable", label: "확인할 정보부터" }
      ]),
      renderSettingSelect("newsDigestMinimumSourceTrustState", "최소 출처 신뢰", [
        { value: "standard", label: "일반 출처부터" },
        { value: "trusted", label: "신뢰 출처부터" },
        { value: "primary", label: "공식 원문만" },
        { value: "limited", label: "제한적 출처도 포함" }
      ]),
      renderSettingSelect("newsCollectionRequireArticleBodyForRss", "RSS 원문 본문 필수", [
        { value: "1", label: "본문 있는 RSS만 저장" },
        { value: "0", label: "제목/RSS 요약도 저장" }
      ]),
      renderSettingSelect("newsCollectionIncludeWatchlist", "관심종목 뉴스 포함", [
        { value: "1", label: "포함" },
        { value: "0", label: "제외" }
      ]),
      renderSettingSelect("newsCollectionIncludeHoldings", "보유종목 뉴스 포함", [
        { value: "1", label: "포함" },
        { value: "0", label: "제외" }
      ]),
      renderSettingField("newsCollectionRateLimitSeconds", "뉴스 호출 간격(초)", "number", "0.25"),
      renderSettingField("newsEvidenceCleanupIntervalSeconds", "뉴스 정리 주기(초)", "number", "900"),
      renderSettingField("newsEvidenceCleanupBatchSize", "뉴스 정리 잠금 배치", "number", "50"),
      renderSettingField("researchEvidenceWriteBatchSize", "뉴스 저장 배치", "number", "50"),
      renderSettingField("mysqlDeadlockRetryCount", "DB 데드락 재시도", "number", "3"),
      renderSettingSelect("newsAiAnalysisEnabled", "기사 AI 분석", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("newsAiAnalysisUseCodex", "기사 분석 Codex 사용", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("newsAiAnalysisCommand", "기사 분석 명령", "text", ""),
      renderSettingField("newsAiAnalysisTimeoutSeconds", "기사 분석 타임아웃(초)", "number", "90")
    ].join(""), "research feed-wide"),
    renderSettingsAiRuntimeGroup("ai feed-compact"),
    renderSettingsGroup("그래프 추론", "수집 데이터가 그래프 저장소 관계 추론으로 넘어가는 경로입니다.", [
      renderSettingSelect("ontologyReasoningEnabled", "데이터 변경 추론", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("ontologyTypeDbEnabled", "TypeDB 그래프 저장소", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("typedbAddress", "TypeDB 주소", "text", "127.0.0.1:1729"),
      renderSettingField("typedbUser", "TypeDB 사용자", "text", "admin"),
      renderSettingField("typedbDatabase", "TypeDB DB", "text", "orbit_alpha_ontology"),
      renderSettingSelect("typedbTlsEnabled", "TypeDB TLS", [
        { value: "0", label: "사용 안 함" },
        { value: "1", label: "사용" }
      ]),
      renderSettingField("typedbTimeoutSeconds", "TypeDB 타임아웃(초)", "number", "20"),
      renderSettingField("typedbRetryCount", "TypeDB 재시도(회)", "number", "2"),
      renderSettingField("ontologyTenantId", "포트폴리오 테넌트", "text", "local"),
      renderSettingField("ontologySharedMarketTenantId", "공유 시장 테넌트", "text", "shared"),
      renderSettingField("ontologySharedMarketWorldRetentionHours", "공유 시장 관측 보관(시간)", "number", "72"),
      renderSettingField("ontologySharedMarketWorldMaxSymbols", "공유 시장 종목 한도", "number", "1200"),
      renderSettingSelect("ontologySharedMarketWorldAsyncProjectionEnabled", "공유 시장 비동기 갱신", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("typedbInferenceGenerationKeepCount", "InferenceBox 보관 세대", "number", "1"),
      renderSettingSelect("typedbAutoResetEnabled", "TypeDB 자동 재생성", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("typedbCapacityAutoRotateEnabled", "TypeDB 용량 자동 회전", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("typedbCapacityThrottlePercent", "TypeDB 배경 쓰기 완화 기준(%)", "number", "70"),
      renderSettingField("typedbCapacityAutoRotatePercent", "TypeDB 안전 재구축 기준(%)", "number", "75"),
      renderSettingField("typedbCapacityAutoRotateCooldownMinutes", "TypeDB 자동 회전 재시도 간격(분)", "number", "60"),
      renderSettingField("ontologyAboxMaintenanceMaxReasoningDeferralSeconds", "ABox 정리 최대 유예(초)", "number", "120"),
      renderSettingField("ontologyAboxMaintenanceBusyRetrySeconds", "ABox 정리 유휴 구간 재확인(초)", "number", "10"),
      renderSettingField("ontologyAboxMaintenancePriorityInactiveManifestCount", "ABox 적체 우선 선택 기준", "number", "8"),
      renderSettingField("typedbDataRetentionHours", "TypeDB 보관 시간", "number", "24"),
      renderSettingField("typedbDataMaxSizeMb", "TypeDB 최대 용량(MB)", "number", "4096"),
      renderSettingField("ontologyReasoningIntervalSeconds", "추론 요청 확인 주기(초)", "number", "10"),
      renderSettingField("ontologyReasoningBatchSize", "추론 요청 배치", "number", "20"),
      renderSettingSelect("ontologyReasoningMailboxEnabled", "실시간 최신 상태만 유지", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("ontologyReasoningMailboxBatchSize", "최신 상태 대기열 처리 수", "number", "200"),
      renderSettingField("ontologyReasoningMailboxRetentionHours", "완료 대기열 이력(시간)", "number", "72"),
      renderSettingSelect("ontologyReasoningSourceFreshnessEnabled", "추론 입력 원천 시각 검증", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("ontologyReasoningRealtimeEventMaxAgeMinutes", "실시간 입력 최대 경과(분)", "number", "15"),
      renderSettingField("ontologyReasoningResearchEventMaxAgeMinutes", "리서치 입력 최대 경과(분)", "number", "360"),
      renderSettingField("ontologyReasoningTelemetryHistoryLimit", "추론 실행 이력 수", "number", "80"),
      renderSettingSelect("ontologyReasoningQueueAlertEnabled", "추론 대기 지연 운영 알림", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("ontologyReasoningQueueWarningAgeMinutes", "대기 지연 경고 시간(분)", "number", "30"),
      renderSettingField("ontologyReasoningQueueCriticalAgeMinutes", "대기 심각 시간(분)", "number", "90"),
      renderSettingField("ontologyReasoningQueueWarningPendingCount", "대기 요청 경고 수", "number", "100"),
      renderSettingField("ontologyReasoningQueueCriticalPendingCount", "대기 요청 심각 수", "number", "200"),
      renderSettingField("ontologyReasoningQueueWarningOverdueSymbols", "대기 한도 초과 종목 경고 수", "number", "3"),
      renderSettingField("ontologyReasoningQueueCriticalOverdueSymbols", "대기 한도 초과 종목 심각 수", "number", "8"),
      renderSettingField("ontologyReasoningQueueConsecutiveObservations", "지연 연속 확인 횟수", "number", "3"),
      renderSettingField("ontologyReasoningQueueNoProgressMinutes", "처리 진행 신호 정체 시간(분)", "number", "15"),
      renderSettingField("ontologyReasoningQueueAlertReminderMinutes", "지연 운영 알림 재전송(분)", "number", "60"),
      renderSettingField("temporalWindowHistoryLimit", "기간 판단 히스토리 수", "number", "96"),
      '<label><span>기간 판단 구간</span><div class="form-control-shell"><textarea data-setting="temporalWindowPeriods" rows="7" autocomplete="off" placeholder="15M=15m:4">' + escapeHtml(settingValue("temporalWindowPeriods") || defaultSettings.temporalWindowPeriods) + '</textarea></div></label>'
    ].join(""), "gate feed-wide"),
    renderSettingsGroup("변화 게이트", "실제 가격·추세·거래량 변화와 뉴스 상태가 알림 후보로 들어가는 조건입니다.", [
      renderSettingSelect("materialityGateEnabled", "중요 변경 게이트", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("marketMaterialityPriceChangePct", "가격 중요 변화율(%)", "number", "0.6"),
      renderSettingField("marketMaterialityTrendDistancePct", "추세 중요 이격(%)", "number", "2"),
      renderSettingField("marketMaterialityVolumeRatio", "거래량 중요 배율", "number", "1.5"),
      renderSettingField("marketMaterialityInvestorFlowRatioPct", "외국인·기관 수급 중요 비중(%)", "number", "15"),
      renderSettingSelect("newsDigestMinimumMaterialityState", "뉴스 중요성 상태", [
        { value: "notable", label: "확인할 정보부터" },
        { value: "material", label: "중요 정보부터" },
        { value: "critical", label: "즉시 확인 정보만" },
        { value: "routine", label: "일상 정보부터" }
      ])
    ].join(""), "gate feed-compact"),
    renderSettingsGroup("공시·외부 원천", "공시, 미장, 거시, 크립토 원천의 사용 여부입니다.", [
      renderSettingSelect("externalDartEnabled", "OpenDART 공시 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalDartLookbackDays", "공시 조회 기간(일)", "number", "14"),
      renderSettingSelect("externalDartDocumentTextEnabled", "OpenDART 원문 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalDartDocumentTextMaxChars", "공시 원문 최대 글자", "number", "6000"),
      renderSettingField("externalDartDocumentMaxPerSymbol", "종목별 중요 공시 원문 수", "number", "3"),
      renderSettingSelect("externalPublicDataStockEnabled", "공식 국내 일별 시세 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalDataPublicStockCadenceSeconds", "공식 일별 시세 확인 주기(초)", "number", "21600"),
      renderSettingField("externalDataPublicStockFreshnessSeconds", "공식 일별 시세 유효 기간(초)", "number", "259200"),
      renderSettingField("externalDataPublicStockMaxPartitions", "공식 일별 시세 최대 종목", "number", "100"),
      renderSettingSelect("externalPublicDataReferenceEnabled", "공식 기업·재무·지수·기업행동 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalDataPublicReferenceCadenceSeconds", "공식 기준정보 확인 주기(초)", "number", "21600"),
      renderSettingField("externalDataPublicReferenceFreshnessSeconds", "공식 기준정보 유효 기간(초)", "number", "604800"),
      renderSettingField("externalDataPublicReferenceMaxPartitions", "공식 기준정보 최대 종목", "number", "100"),
      renderSettingField("externalPublicDataTimeoutSeconds", "공공데이터포털 요청 제한(초)", "number", "12"),
      renderSettingSelect("externalSecEnabled", "SEC EDGAR 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalSecMaxSymbols", "SEC 조회 종목 수", "number", "3"),
      renderSettingSelect("externalAlphaEnabled", "Alpha Vantage 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("externalAlphaRelatedSymbolsEnabled", "ADR·ETF 관련 종목 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalAlphaRelatedMaxSymbols", "ADR·ETF 관련 종목 수", "number", "8"),
      renderSettingSelect("externalYFinanceEnabled", "yfinance 종합 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalYFinanceMaxSymbols", "yfinance 조회 종목 수", "number", "8"),
      renderSettingSelect("externalFredEnabled", "FRED 거시 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("externalCoinGeckoEnabled", "CoinGecko 크립토 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalApiFetchIntervalMinutes", "외부 API 캐시(분)", "number", "30")
    ].join(""), "external feed-compact"),
    renderSettingsGroup("긴 매핑값", "종목 코드, CIK, 거시·코인 목록처럼 긴 입력값입니다.", [
      renderSettingField("externalFredSeries", "FRED 지표", "text", "DGS10,DGS2,DFF"),
      renderSettingField("externalCryptoIds", "CoinGecko 코인 ID", "text", "bitcoin,ethereum"),
      '<label class="setting-field wide">',
      '<span class="setting-field-label">ADR·ETF 증권 라인 매핑</span>',
      '<div class="form-control-shell"><textarea data-setting="securityLineMappings" rows="4" autocomplete="off" placeholder="000660|SK하이닉스|adr|SKHY|SK hynix ADR|US|USD|Nasdaq|0.1|2026-07-29|0|000660|https://...|2026-07-13">' + escapeHtml(settingValue("securityLineMappings") || defaultSettings.securityLineMappings || "") + '</textarea></div>',
      '</label>',
      '<label class="setting-field wide">',
      '<span class="setting-field-label">OpenDART 종목 매핑</span>',
      '<div class="form-control-shell"><textarea data-setting="externalDartCorpCodes" rows="3" autocomplete="off" placeholder="005930=00126380">' + escapeHtml(settingValue("externalDartCorpCodes") || defaultSettings.externalDartCorpCodes) + '</textarea></div>',
      '</label>',
      '<label class="setting-field wide">',
      '<span class="setting-field-label">SEC CIK 매핑</span>',
      '<div class="form-control-shell"><textarea data-setting="externalSecCompanyCiks" rows="3" autocomplete="off" placeholder="AAPL=0000320193">' + escapeHtml(settingValue("externalSecCompanyCiks") || defaultSettings.externalSecCompanyCiks) + '</textarea></div>',
      '</label>',
    ].join(""), "mapping feed-wide"),
    '</div>',
    renderSettingsSmartSavePanel(),
    '</div>',
    '</article>'
  ].join("");
}

export { renderFeedSettingsEditorPanel };
