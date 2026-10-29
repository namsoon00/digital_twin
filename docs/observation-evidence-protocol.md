# 독립 AI 근거 전달 계약

## 가격 변동 조사 계약 (조회 v19, 작성 v33)

기업 시장 관찰은 `price-move-investigation-v1`로 설명할 시세를 먼저 고정한다.
`priceMoveAnchor`는 동일 ABox의 가격·등락률·원천 시각·사용 가능 목적만 보존한다.
오래된 시세나 마감 참고값도 원천 시각이 유효하면 그 시점의 설명에 쓸 수 있지만,
최신 현재가로 바꾸거나 수집·발송 시각을 시세 시각으로 대체하지 않는다. 비교 시작가와
기간이 확인되지 않은 제공처 등락률을 임의의 기간 수익률로 확대하지 않는다.
최신 시세 반영은 기존 수집 → ABox 반영 → 다음 관찰 경로가 담당한다.

reasoning의 `ObservationEvidenceSession.price_investigation()`은 최근 일주일의
제한된 사건 목록과 시장 비교 후보를 제공한다. 일주일은 탐색 범위이며 수익률의
비교 기간이나 모든 자료의 신선도 한도가 아니다. 가격 관측 자체, 공개 시각을 모르는
재무 스냅샷, 동일 원문의 중복을 사건 목록에서 분리한다. 공개 시각 미확인과
시세 이후 공개된 자료를 표시하며, 목록을 원문 근거나 전체 웹 검색 결과로 취급하지 않는다.
`sourceContent`는 해당 레코드에 실제 원문 텍스트가 있는지도 구분한다. 과거 수집기의
`bodyRead=true`나 기존 AI 요약을 이번 작성자가 본문을 읽었다는 증거로 쓰지 않는다.
기존 AI 해석을 원문 대신 인용하는 가능한 가격 설명은 차단한다. 원문이 없으면
한계를 밝히고 필요한 원문만 기존 질문 조사 경로로 요청한다.

조회 모델은 사건·시장 후보가 있는 각 그룹에서 대표 원문을 읽고 선택한 뒤 완료한다.
이미 읽은 원문을 반복할 필요는 없으며 읽지 않은 후보를 보충 자료 부족으로 분류해
재무 요약만으로 조사를 마칠 수 없다. 읽기는 동일 동결 inventory에서 수행하며
기존 최대 3회 결정·회당 2개 요청·1회 교정·입력 한도를 그대로 사용한다.
`priceInvestigationCoverage`는 후보 부재, 미조회, 원문 조회를 구분한다. 조회 성공이
관련성·동일 기간 시장 비교·인과관계 확인을 뜻하지는 않는다. 실제 조회 영수증과
선택 사실을 저장 시 다시 대조하며 대기나 용량 초과를 조사 완료로 바꾸지 않는다.

작성 결과의 `priceExplanation`은 고정된 시세 ID와 `possible-explanation` 또는
`cause-unconfirmed`, 관련 원천 ID를 기록한다. 가능한 설명에는 기존의 원문·기업 노출
검증을 통과한 조건부 외부 영향 경로가 필요하다. 본문은 가격 상황, 가능한 배경,
대안과 미확인 원인을 설명한다. 독립 검토 v22는 목표가 과거 재무 설명으로 바뀌지
않았는지, 이후 보도를 당시 원인으로 소급하지 않았는지도 확인한다. 검토 후 결과를
바꾸면 서명 비교에서 차단하며 실제 전달 영수증에도 조사 결과와 가격 기준을 남긴다.
원인 미확인만으로 알림을 보내거나 같은 질문을 반복할 권한은 추가하지 않는다.

조회 v18·작성 v32 및 이전 동결 입력은 당시 생성기로 재현한다. 계약·통합 테스트는
근거 전달과 검증 경로를 확인할 뿐 실제 설명의 정확도나 투자 성과를 인증하지 않는다.

## 질문별 근거 선택과 대기 상태 (조회 v3)

현재 런타임은 `independent-observation-retrieval-v3-working-set`을 사용한다.
이전 조회 v1/v2의 프롬프트·스키마·감사 기록은 그대로 재현한다.

- **조회 이력과 판단 입력 분리:** 조회한 사실은 고정된 실행 입력과 조회 기록에 남는다.
  AI는 매 결정의 `selectedFactIds`에 이미 읽은 사실 중 판단에 사용할 항목을 명시한다.
  새로 읽은 사실이 자동으로 판단 입력에 누적되지 않는다. 최종 작성·검토는 선택된
  현재 사실만 인용할 수 있다. 제외한 사실의 ID·해시와 포함/제외 범위를 보존한다.
  필수 시세 및 계약상 기본 거시 근거는 소유자의 선택 계약에 따라 유지한다.
  이미 읽은 데이터 품질 경고도 선택 해제로 숨길 수 없다.
- **두 용량을 명시:** 조회 이력의 96KiB 한도와 최종 근거 묶음의 96,000바이트
  한도를 구별한다. `authorBudget`에 실제 선택 크기·다음 기록 예약 공간·남은 공간·
  필수 프롬프트 크기를 제공한다. 선택 변경 시 최종 묶음과 전체 작성 프롬프트를
  모두 사전 검사한다. 실패한 선택은 한 번 교정하고, 한도를 늘려 숨기지 않는다.
- **정확한 보고서 발췌:** `read_report(factId, metrics)`는 동일 캡처의 재무 보고서에서
  최대 열두 지표와 각 지표의 단위·출처·비교/계산 근거를 그대로 반환한다. 기간·
  범위·신선도·검증 상태·누락 정보는 보존한다. 원본 해시, 선택 지표와 제외 지표를
  `evidenceView`에 기록하고 최종 선택에서도 원본과 정확히 대조한다. ID는 원본과
  같으며 같은 ID를 다시 읽으면 마지막 조회 형태를 다음 선택에 사용한다. 원본을
  수정하거나 새로운 투자 사실을 만들지 않는다. 제외는 자료 부재를 뜻하지 않는다.
  보고서 인덱스는 최신 출처 순 최대 서른두 건이며 나머지는 기존 페이지 조회로 접근한다.
- **판단과 자료 대기 분리:** `defer`에는 필요한 자료·기간·제약을 기록한다. 미완료
  조회는 작성 모델을 호출하지 않고 `awaiting-evidence`로 저장한다. 근거 부족은
  `data-wait`, 조회 용량·호출 한도 문제는 `retrieval-blocked`, 작성 후 검증 실패는
  `output-invalid`로 구분한다. 이 상태는 판단·질문 해결·가설 등록·알림 발송이 아니다.
- **변화 없는 재호출 방지:** 같은 근거 목록 해시·시세 품질 상태·질문/연구 상태·
  실행 정책이면 세 시간 이내 같은 보류 결과로 모델을 다시 호출하지 않는다.
  출처 근거, 질문/연구 결과, 확인 조건의 검증된 전환, 정책 변경 또는 예정된 재검토
  시점은 재개 사유다. 대기 결과는 판단 기억에 섞지 않고 `processingOutcome`으로
  별도 보존하며 관찰 확인 시각만 갱신한다. 과거 판단은 기존 연속성 기억으로 유지한다.

이 계약은 기존의 숫자·근거·질문 전환·독립 검토·발송 검증을 완화하지 않는다.
원문이 부족한 작업을 승인된 판단으로 집계하거나, 보류 감소를 예측 정확도 향상으로
표시해서는 안 된다.

반복된 데이터 누락의 원인은 함수 인터페이스만 있고 전달 범위·누락 상태·변경 의미를
공유하는 계약이 없었던 데 있다. `sourceIdsBySymbol`은 규칙 실행을 계획하기 위한
종목 인덱스이므로 전체 근거 목록으로 사용할 수 없다.

## 소유권과 인터페이스

| 경계 | 소유 모듈과 인터페이스 | 보장 |
| --- | --- | --- |
| 그래프 → 근거 | reasoning의 `ObservationEvidenceReader`와 `ObservationEvidenceSource` | 규칙 성립과 무관한 종목 근거, 연결된 직접 근거, 명시된 거시 근거 조회 |
| 근거 → AI | `observation-evidence-v1`, `independent-observation-v2-macro-prints` | 소유 계정·종목·스냅샷·출처와 영역별 포함/제외 보고서 |
| 변경 → 재분석 | reasoning의 `evidence_change_identity` | 소비자가 별도 필드 목록을 작성하지 않고 동일 계약으로 변경 판정 |
| 호출 → 감사 기록 | ai_orchestration의 `ai-observation-execution-v1` | 실제 프롬프트·응답 스키마·현재 근거·선택한 과거 분석·연구 결과를 호출 전에 고정 저장 |

현재 모듈의 `public.py`/`contracts.py`를 통해 호출한다. 별도 HTTP 서비스나 큐를
추가하지 않는다. 저장소와 TypeQL은 reasoning 어댑터가 소유하며 AI는 계약을
소비한다. 시장 자료 수집·모델 계산·투자 권한은 기존 소유자에게 있다.

## 근거 묶음의 불변 조건

- 지원하는 `protocolVersion`과 `profile` 및 계정·종목·world·캡처 시각을 갖는다.
- 각 사실은 원본 ID와 소유 world, 캡처한 스냅샷 ID를 갖는다.
- 캡처 전후 활성 스냅샷이 다르면 불완전한 묶음을 실행하지 않는다.
- 시세, 평가, 기업, 연구, 거시, 기술, 수급, 품질의 조회 결과를 각각 보고한다.
- 영역별 `available = included + excluded`이며 포함 수는 실제 사실 수와 같아야 한다.
- `missing`은 조회 범위에 자료가 없음, `partial`은 용량 제한이나 선택 조회로 일부 제외,
  `unsupported`는 프로필에 아직 분류하지 않은 종류다. 조회 오류를 자료 부재로 바꾸지 않는다.
- 사실은 전체 단위로 포함하거나 제외한다. EPS·DCF payload를 조용히 잘라내지 않는다.
- 영역별 예산과 종류 순환 선택으로 대량 자료가 다른 종류를 모두 밀어내지 않는다.
  같은 종류에서는 출처 시각이 최신인 자료를 우선하고 동일 시각은 안정적인 ID 순서를 쓴다.
- 필수 시세 부재, 소유권·버전·포함 수 불일치가 있으면 AI를 호출하지 않는다.

조회 범위는 종목 속성이 있는 노드, 종목의 직접 관계 끝점, 명시된 거시 종류다.
임의 깊이로 모든 그래프를 탐색한다는 뜻이 아니다. 간접 평가 근거는 종목 속성으로
조회한다. 새 종류·탐색 범위는 프로필 버전과 계약 테스트를 함께 갱신한다.
`complete`도 출처의 진실성이나 투자 적격성을 새로 보증하지 않는다. 각 사실의
기존 품질·출처·승인 상태는 유지한다.

프로필 v2는 기존 TBox의 `MacroPrint`와 ABox `macro-print`를 거시 조회에 포함한다.
전산업생산·선행지수·소매판매처럼 월별로 발표되는 지표도 값·단위·대상 기간·출처를
보존하며, 스코프는 `macro:market`으로 분류한다. 기존 거시 영역의 8000바이트 예산과
종류별 순환 선택은 유지한다. 이 범위의 기본 거시 사실은 첫 조회와 최종 판단에 필수로
포함하고, 제외된 상세 사실은 내부 조회로 추가할 수 있다. v1 입력은 당시 프로필로
재현하며 과거 저장 내용을 새 프로필로 수정하지 않는다.
월별 지표는 감시 계좌의 서버 설정에서 결정한 공용 MarketWorld에서도 읽는다. 계좌와
공용 시장의 스냅샷을 각 저장소의 native READ 경계에서 고정하며, 공용 조회는 계좌 소유 정보가
없는 `macro-print`만 추가한다. 설정된 시장 스냅샷이 없으면 명시적으로 보류한다.

종목 속성과 거시 종류의 목록 조회는 같은 활성 범위 조건을 적용한 두 쿼리로
실행한다. 하나의 큰 OR 조건이 활성 범위 연결의 비용을 늘리는 경우를 줄인다.
중복 사실은 동일 ID·내용일 때만 합치며, 내용 충돌이나 합집합 2,000개 초과는
명시적으로 실패한다. 쿼리별 2,001개 감시 한도, 직접 관계 조회, 캡처 전후 스냅샷
검사는 유지한다. 국내·미국 각 한 종목의 실제 읽기 비교에서 동일 사실을 확인했고
목록 조회 합산 시간은 각각 약 3.80→2.20초, 3.53→1.82초였다. 이는 제한된
시점의 비교이며 장기 처리량이나 모든 조회의 시간 초과 해소를 보증하지 않는다.

### 일관된 캡처와 연결 근거 조회

운영 TypeDB 어댑터는 같은 DB의 메타데이터, 종목·거시 목록, 양방향 연결 근거와 마지막
세대 확인을 하나의 native READ transaction에서 수행한다. TypeDB의
[snapshot isolation](https://typedb.com/docs/core-concepts/typedb/transactions/)으로
캡처 중 writer가 다음 세대를 활성화해도 처음 읽은 세대가 유지된다. 전용 연결은
캡처가 끝나거나 실패하면 닫히며, AI 호출까지 열어 두지 않는다. 전체 읽기 예산은
기존 쿼리 제한의 두 배(최소 30초, 최대 60초)이고 각 쿼리의 기존 제한도 유지한다.
scope가 없는 호환 어댑터도 전후 세대·계정·종목 검증을 통과해야 한다.

연결 탐색은 활성 종목의 정확한 physical storage ID를 고정하고, 활성 관계의
양방향 endpoint ID를 찾은 뒤 최대 64개씩 원문을 읽는다. scoped Manifest의
`scopeGenerationIds`와 원문의 `(world, scope, physical generation)`이 일치하는
endpoint만 포함한다. 하나의 쿼리에서 종목·관계·endpoint의 scope pointer를
모두 조인하지 않아도 같은 활성 범위를 보장한다. 다른 종목, 퇴역 세대, 내용 충돌,
2,000개를 초과하는 연결 목록에 대한 보호를 유지한다.

종목·거시 목록은 활성 물리 식별자와 사실 종류만 먼저 조회한다. 연결 endpoint와
합친 뒤 원문을 최대 64개씩 한 번만 읽어 큰 JSON을 활성 scope 조인에 반복해서
붙이지 않는다. 목록의 논리 ID·종류와 원문의 값이 다르거나 활성 원문이 누락되면
캡처를 실패시킨다. 메타데이터 재사용은 같은 native READ 캡처 안에서만 허용하며
실패와 종료 시 해제한다. 원천 인덱스로 실제 TypeDB 사실 목록을 대체하지 않는다.

## 변경 감지

새 필드는 기본적으로 변경 감지에 포함한다. 저장 세대 ID·조회 시각 같은 명시된
메타데이터만 제외한다. 중첩된 재무 입력의 시점·출처 버전·사용 가능 상태는 보존한다.
시세 시각만 새로 찍힌 것은 재분석 사유가 아니지만 시세 값과 신선도·시각 존재 상태는
재분석 사유다. 제외된 사실도 `inventoryHash`에 참여하므로 문맥 예산에 밀린 자료의
변화를 놓치지 않는다. 추가·삭제, 새 필드, 연구 결과, 확인 질문도 변경 판정에 포함한다.
AI 호출 한도와 관찰 간격은 별도 운영 정책으로 유지한다.

## 호출 전 저장과 재현

`ai_control_inputs`는 lease 소유권을 확인한 후 실제 호출 입력을 로컬에 압축 저장한다.
기본 256KiB 문맥 한도를 맞추며 제외한 과거 분석과 연구 결과 수도 기록한다. 현재 근거
묶음의 96,000바이트 한도는 별도이며, 전체 프롬프트 한도를 늘려도
이 근거 한도는 바뀌지 않는다. 자동 선택에는 영역별 예산을 적용하고, 내부 도구로
선택한 묶음에는 아래 조회 예산과 명시적 `not-retrieved` 회계를 적용한다. 호출은
`ai_control_input_calls`로 입력과 연결되며 작업 ID 및 프롬프트 해시가 일치해야 한다.
저장 실패·lease 상실·지원하지 않는 계약·프롬프트 불일치 시 호출을 시작하지 않는다.
모델 시간 초과나 응답 검증 실패에도 입력이 남는다. 성공 결과는 `executionInputId`를 갖는다.

프롬프트와 기억은 개인정보가 있는 로컬 자료다. 일반 상태 API와 호출 목록에 원문을
추가하지 않는다. 새 입력 기록은 중앙 작업 이력처럼 현재 자동 삭제하지 않는다.
보존 정책을 추가할 때 입력과 호출 참조를 함께 다뤄야 하며 더 짧은 원본 자료 보존
기간과 혼동하지 않는다. 과거 작업을 새 계약으로 덮어쓰지 않는다.
재현은 동일 입력과 프롬프트를 복원해 해시·근거 연결을 검증한다는 뜻이다.
비결정적인 모델이 같은 문장을 다시 생성한다는 보장은 아니다.

## 내부 연구와 고객 알림 검증

동결된 `question-research-scope-v1`의 `mode=research-only` 작업은 고객 알림
비교·가격 확인 조건의 검증 대상이 아니다. `notification.send=false`일 때
`quality.status=not-applicable`과 `authority=no-notification`으로 기록한다.
잠정 지식과 사업 연구는 기존 동결 `researchQuality`의 독립 검토를 계속 거친다.
처리 결과는 `research-reviewed`, `research-review-required`, `research-only`로
구분하며, 연구 검토 실패의 동일 근거 재시도에는 기존 운영 제한을 적용한다. 고객 알림은 기존
근거 검증·독립 검토·발행·전달 영수증 조건을 모두 충족해야 한다. 지원하지 않는
연구 범위나 고객 분석에는 기존 전체 검증을 적용한다. 과거 저장 기록은 변경하지
않으며, 모니터링은 과거 내부 연구의 알림 검증 거절과 실제 연구 검토 실패를 구분한다.

## 검증과 적용 범위

`test_observation_evidence_protocol.py`는 어댑터의 종목/거시 전달, 포함·제외 회계,
미지원 종류, 신규 필드·품질·중첩 출처 변경, 프롬프트 변조, 입력 저장 실패 시 호출
차단을 검증한다. MySQL 통합 테스트는 호출 전 입력 저장, 실패 호출의 감사 연결,
잘못된 해시 거부, lease 상실을 검증한다.

`ObservationEvidenceReader.capture_session()`은 같은 검증을 거친 전체 후보 목록을
읽기 전용 세션으로 보존한다. 모델은 근거 종류·페이지 또는 이미 알려진 사실 ID로
전체 사실을 조회할 수 있다. 처음의 자동 선택 예산에 제외된 사실도 조회 가능하다.
모든 도구는 캡처된 목록만 읽으며, 모델 호출 중 DB transaction을 유지하지 않는다.
최종 묶음에는 필수 시세·기본 거시 사실과 실제 읽은 사실을 담고, 분류별 재고 해시·포함 수·미조회
수를 다시 계산한다. 자료의 품질·스냅샷·원천 시각을 갱신하거나 추정하지 않는다.

조회 v2에서는 서버가 페이지당 4개와 이동 위치를 관리한다. 모델은 같은 세션·도구·
분류에서 발급한 `nextCursor`만 사용한다. 인자 오류는 기존 3회 조회 호출 안에서 한 번
보정하며, 성공한 근거를 유지한다. 보정 실패는 자료 부족과 구분한 작업 실패로 남겨
판단 작성 전에 중단한다. 각 응답·검증 오류·조회 결과는 해당 입력과 시도·lease에 묶어
`ai_control_retrieval_rounds`에 저장한다. 원응답은 최대 32KiB까지만 보존하고 초과 시
해시·크기·제외 사실을 남긴다. 조회 v1의 프롬프트·스키마·기록은 당시 규격으로 재현한다.
사실 페이지 본문은 32KiB, 메타데이터를 포함한 도구 응답은 40KiB,
관찰의 전체 조회 결과는 96KiB 이내이며 원문을 중간에서
자르지 않는다. 한도에 걸린 사실과 기억은 해시와 사유로 기록한다.

`packet-budget-pages-v1`에서는 4개를 페이지의 최댓값으로 사용한다. 최종 근거
묶음의 96,000바이트 한도와 남은 조회 기록 공간까지 확인해 1~4개의 온전한
레코드를 반환한다. 두 출처를 함께 요청하면 둘 다 들어갈 수 있는 경우 각각
최소 한 사실의 공간을 예약한다. 큰 첫 페이지가 반대 근거 조회를 밀어내지 않는다.
축소한 페이지의 다음 커서는 실제 반환한 마지막 위치에서 이어지며 시험 조회의
커서는 노출하지 않는다. 한 사실도 들어가지 않으면 명시적으로 보류하고, 해당
사실을 건너뛴 채 다음 페이지로 진행하거나 완료로 표시하지 않는다. 크기 조정은
이미 캡처한 목록에서만 수행하며 추가 DB·모델 호출을 만들지 않는다.

작성 결과의 필드 검증 실패는 `ai-task-failure-v2`로 사유와 필드를 기록한다.
개발자가 정의한 오류와 구조화된 진단만 저장하며 원문 예외 메시지는 저장하지 않는다.
조회가 완료된 입력의 수정 가능한 오류에는 동일한 고정 근거로 한 번만 수정
기회를 준다. 초안·진단·수정 프롬프트는 기존 입력 저장소에 보존하고, 이 호출은
기존 품질 수정 기회와 공유한다. 수정 후에도 원래 필드 검증·품질 검증·독립 검토를
그대로 적용한다. 조회 부족이나 알 수 없는 실행 오류에 같은 입력 수정 호출을
추가하지 않으며, 수정 실패·호출 한도·lease 상실은 성공으로 처리하지 않는다.
조회 단계 입력과 최종 판단·수정 입력을 구별하며, 조회 결정만으로 판단 완료·발송·과제
종료를 증명할 수 없다. 최종 입력에는 전체 조회 이력과 그 해시가 함께 저장된다.

판단 v12·수정 v8은 `observation-continuity-v1`의 필수 기억을 함께 저장한다.
최근 사용 가능한 판단 1건과 당시 비교값, 미해결 질문·서비스 제안·조사 상태의 요약은
상세 기억 조회를 선택하지 않아도 전달한다. 요약은 과거 문맥이며 현재 사실의 근거로
인용할 수 없다. 발췌 여부와 출처 기억 해시를 기록하고 필수 요약 예산 초과는 명시적으로
실패한다. 과제 검토·서비스 제안의 근거 ID 1~8개 조건은 생성 스키마와 검증기가 함께
적용한다. 이전 판단 v11·수정 v7과 v10·수정 v6의 프롬프트와 스키마도 그대로 재현한다.
독립 검토 v4에도 작성 단계와 같은 과거 판단 요약을 전달한다. 이전에 무엇을 판단했는지
확인하는 용도이며 현재 시장 사실의 인용 권한을 추가하지 않는다. 검토 v3도 재현 가능하다.

독립 AI가 첫 적용 대상이다. 수집→정규화→ABox의 기존 계약을 전면 교체한 것은
아니다. 해당 경계를 변경할 때도 소유자가 포함·제외 회계와 버전 검증을 제공해야 한다.
모델/규칙 승인, RuleBox 릴리스, 거래 지시 금지, 알림 쿨다운은 유지한다.


설명 품질 개선에서는 생성 입력과 별도로 검토 입력을 저장한다. 검토 대상 문장과
인용 항목, 후속 조건 및 응답 스키마가 원본 초안과 일치해야 한다. 발송 시 완료된
검토 호출과 초안 해시를 다시 확인하며, 필수 계약이 없는 과거 초안을 재발송하지 않는다.
자세한 발송·검토·후속 조건 계약은 `docs/ai-control.md`에 있다.

## 원천 시각과 현재 사용 시점

새 캡처에는 `observation-quote-clock-v1`의 `quoteAssessment`를 함께 저장한다.
TypeDB 원본의 `sourceAsOf`, `freshnessStatus`, `judgementEvidenceUsable`과 출처의
마감 참고값 표시는 그대로 보존한다. 별도 평가에는 캡처 시각, 원천 시각, 경과 분,
원본의 `maxAgeMinutes`, 현재 경과 기준의 상태를 기록한다. 새 캡처·조회 시각으로
오래된 시세를 갱신한 것처럼 취급하지 않는다. 원천 시각 부재, 시간대 부재·오류·미래
시각, 갱신 기준 부재는 각각 명시하며 모두 신선하다고 간주하지 않는다.

생성·수정·독립 검토는 동일한 평가를 고정 입력으로 받는다. 원본의 장 세션 표시도
원본 시점의 정보이므로 현재 장이 열렸거나 닫혔다는 근거로 쓰지 않는다. 발송 본문은
고정된 렌더링 시각에서 다시 경과 시간을 계산하고, 갱신 기준을 넘긴 가격을
`기준 시점 가격`과 `과거 시점 참고 자료`로 표시한다. 성공한 발송 기록에는 캡처와
실제 발송 직전의 평가를 모두 남기며, 기존 원본 입력은 갱신하지 않는다.

이 평가는 기존 정책대로 참고 정보다. 휴장·연휴의 과거 자료 분석을 일괄 차단하거나
새로운 10분 발송 제한을 추가하지 않는다. 현재 상황이라는 잘못된 설명은 독립 검토에서
확인하며, 기존 30분 입력 유효 기간·24시간 원천 시각 조건과 검토·중복 제한은 유지한다.
신선도 상태 전환은 재분석 사유지만 같은 상태에서 경과 시간만 늘어난 것은 반복 호출
사유가 아니다. 이전 프롬프트·검토 입력은 당시 계약으로 계속 재현한다.

조회 실패는 `evidence-read:<단계>:<오류 종류>`로 작업에 기록한다. 메타데이터,
캡처 전후 스냅샷, 종목·거시 근거 목록, 직접 연결 근거 단계를 구별하고, 원문 SQL·계정·제공자
오류 메시지는 넣지 않는다. 조회 실패를 근거 없음이나 성공한 AI 관찰로 바꾸지 않으며
기존 작업의 재시도 간격을 따른다. 이 진단 추가만으로 조회 지연 원인이 해결됐다고
판단하지 않는다.

## 운영 그래프와 질문 원문 연결

중앙 AI의 계좌 근거는 현재 active/delivery 배포에 연결된 DB를 읽는다. 공용
MarketWorld의 월별 거시 근거는 공용 projection 작업자가 사용하는 `typedbDatabase`
설정의 DB를 읽는다. 계좌 DB에서 공용 world를 찾거나, 계좌 그래프가 없다고 공용
DB의 과거 계좌 그래프로 대체하지 않는다.

`active-observation-binding-v2-world-routes`는 계좌 `graphStoreBinding`과 공용
`sharedGraphStoreBinding`을 함께 고정한다. 캡처 전후 제어 버전·배포·두 DB 연결을
비교하고 하나라도 바뀌면 입력을 폐기한다. 같은 저장소이면 한 native READ를 쓰고,
서로 다른 저장소이면 각각의 native READ에서 불변 스냅샷을 보존한다. DB 사이의
원자적 동일 시점 트랜잭션을 보장한다는 뜻은 아니다. `reasoningBinding`과 world별
`sourceSnapshots`으로 실제 읽은 두 세대를 기록하며 오류 시 두 트랜잭션을 모두 닫는다.
이 조회는 배포·규칙·그래프를 변경하지 않는다. 이전 저장 입력의 연결은 재작성하지 않는다.

필수 그래프의 준비 상태나 스냅샷이 없으면 모델 호출 전에 같은 작업을
`awaiting-evidence`/`data-wait`로 보류한다. 큐 상태는 `pending`을 유지하고 최초
대기 시각·조건·재검사 시각·대기 횟수를 기록한다. 재검사 간격은 1분부터 최대
15분까지 증가하며, 대기 횟수는 모델 실패 횟수에서 제외한다. 기존 근거 갱신
이벤트의 재개 경로도 유지한다. world·계정·유형 불일치와 실제 조회 예외는 이 준비
대기로 바꾸지 않는다. 대기 결과는 분석 완료·독립 검토·발행·전달 성공이 아니다.

조회 V6의 `exact-question-document-v1`은 검증된 원문 전체 발췌 또는 구조화된
보고 수치·기간·단위·계보를 온전히 보존하고 중복 분석 필드를 제외한다. 원래 사실
해시와 제외 필드를 남기며 같은 캡처의 원본에서 이 투영을 다시 계산해 검증한다.
본문 일부를 요약·잘라 답변 기준을 충족시킨 것처럼 취급하지 않는다. 구조화된
보고 수치에는 공시 전문·주석·정정 이력이 없다는 한계를 함께 유지한다.
질문의 부분 평가, 최종 답변 및 가설·판단 영향은 별도 상태로 저장한다.

### Portfolio context read coverage (2026-10-25)

The `independent-observation-v5-portfolio-context` profile retains the existing
category/tool grammar and historical profile replay. In the same active native
PortfolioWorld, a bounded additional kind query reads existing `portfolio`,
`portfolio-risk-snapshot`, `sector-exposure`, `currency-exposure` and
`cash-exposure` ABox facts. Subject `position-risk-metric` facts are also admitted
under the `company` context category. All remain exact graph facts with source
snapshot identity, partial-data state and byte-budget coverage; this does not
compute a new risk score, correlation, allocation or trade. No other subject is
traversed and no portfolio kind query runs against the shared MarketWorld.
Unavailable facts remain unavailable, and omitted facts stay in the model's
bounded inventory for explicit reads. Existing numeric, provenance, novelty and
publication guards still apply. Account observations are not real-time prices;
their source clock and data state must be retained when cited.

### Delivery investigation recovery (2026-10-30)

The stock profile `independent-observation-v6-market-comparison` admits existing
subject-owned `relative-performance-observation` facts. Exact returns, proxy,
source clocks, alignment and eligibility remain unchanged. This adds readable
context, not a calculated return or proof of a common return period. Historical
profiles and frozen prompts retain their validation; the crypto profile is unchanged.

`article-source-reference-v1` exposes only preserved body passages that match
the captured article text and their source-owned passage IDs. It retains URL,
publication/observation clocks, body and passage hashes and an excerpt limitation.
Oversized or unverified passages are omitted whole. Headline, preview and AI
summary text cannot substitute for the body. Parent governance and investment
eligibility remain unchanged, including rejection of low-quality evidence.
Projection input V8 preserves up to 32 KB of exact body/grounding verification
inputs through compaction; the ABox exposes at most 8 KB of verified references.
An oversized proof stays in the source store and is never silently truncated.

Question routing V20 (`question-gap-union-v1`) conservatively joins the two
bounded missing-evidence lists for a single assessment. It cannot turn a partial
answer into a complete one. Both remaining requirements reach the author and
persisted question progress without a paid wording repair. V16 and other frozen
inputs retain their original contract. A stalled research question blocks only
the same question, original contract or normalized query set with overlapping
sources and periods. Sharing a source type alone no longer blocks unrelated work;
per-question attempt and unchanged-material limits remain enforced.

Under `preparation-borrow-v2`, retrieval may borrow unused preparation capacity,
leaving two source-preparation reservations. The shared 60% preparation ceiling,
40% completion protection and daily limit remain enforced. A quote that expires
during retrieval stops current-state authoring before the next paid call and
rechecks source readiness after five minutes. Source-dated explanation remains
allowed; publication still independently checks source clocks.

For three hours after an unsent, observation-only, cause-unconfirmed explanation,
unchanged source inventory can defer repeated paid investigation. This inventory
includes excluded source facts, portfolio composition, question review receipts,
delivery context and source eligibility. New sources, verified follow-up changes,
due business reviews and elapsed time release the gate. A changed session,
price base or direction, a price move of at least 0.5%, or a reported change-rate
move of at least 0.5 percentage points also releases it. This bounds suppression,
not alert admission. Small quote ticks alone do not repair absent causal evidence.
Waiting is not an authored judgment, successful
answer, publication or delivery receipt. Deployment verification must report
those outcomes separately; none of these changes forces an investment alert.
