# IN-02 Financial Evidence Repair

실행일은 2026-09-25 KST다. 이 기록은 PLTR·NVDA만 대상으로 한 현재 재무
근거 복구의 범위와 검증 결과를 남긴다. 계정 정보, 메시지 본문, 원본 압축
AI 입력은 문서에 복사하지 않는다.

## 안전 계약

- Preview는 읽기만 하며 vendor 호출, 수집 queue 갱신, 알림 발송을 하지 않는다.
- Apply는 명시한 종목과 preview가 만든 manifest ID가 모두 있어야 한다.
- Manifest는 전체 company cache fingerprint, 대상 전후 fingerprint, 대상이 읽은
  source revision·payload hash, 역사 조사 한도를 포함한다.
- Apply transaction은 source 행과 company cache 행을 잠근 뒤 manifest를 다시
  확인한다. 바뀌었으면 덮어쓰지 않고 중단한다.
- 동일 manifest의 correction/audit/event ID는 결정적이다. 두 번째 apply는
  `already-applied`이며 유효 변경과 재평가·재수집 요청이 모두 0이다.
- 과거 판단, AI 문장, 전달 본문은 수정하지 않는다. 활성 queue와 추적·calibration
  상태만 owner 계약을 통해 차단하거나 정정 metadata를 추가한다.

## Preview 결과

대상은 `NVDA`, `PLTR`, 역사 조사 한도는 3,000건이다. Preview에서 현재 cache
두 행 모두 `checked`에서 `checked-with-exclusions`로 정규화 대상임을 확인했다.
보존된 case 293건을 조사했고, 원본 AI 실행 입력으로 잘못된 재무 근거 사용을
확인한 것은 65건이었다. 실행 artifact가 없어 확인할 수 없는 216건은
`unknown`으로 분리했다. 이미 전달된 메시지 9건은 원문 보존 대상으로
확인했다. Preview 중 write, 외부 수집, 고객 정정 발송은 모두 0건이었다.

## 적용 결과

최초 적용 manifest는 `1191f95bf241001c8f0c8d537b12a80a`다. 현재 cache의
NVDA·PLTR 두 행을 바꿨고 다른 12개 종목 행은 범위 밖으로 유지했다. 전체 cache
복구가 아니므로 전역 schema version은 기존 v3를 유지하고
`financialRepairCoverage.fullCacheScope=false`로 기록했다.

최초 적용 후 두 종목 모두 현재 재무 기간 9개가 불변 source revision에 연결됐다.
당시 계약이 없던 NVDA 7개, PLTR 5개 기간은 현재 사실에서 제외하고 원본
source/history에는 남겼다. 활성 AI 요청과 발송 대기 publication은 영향 대상이
없었다. pending 관측 target 8개를 제외했고, 과거 전달 메시지 9건과 그 본문은
수정하지 않았다. 고객 정정 메시지는 자동 발송하지 않았다.

같은 transaction에서 두 종목을 대상으로 reasoning 재평가 event 1건을 만들었다.
선택된 report contract가 실제로 가리킨 `sec.company_facts`,
`yfinance.fundamental`만 수집 queue에서 갱신 대상으로 만들었으며 해당
dataset/종목 partition 4개가 예약됐다. correction audit와 두 domain event가
durable store에 존재함을 확인했다.

재수집 결과 새 불변 source 계약이 들어와 제외 기간이 모두 정상 계약으로
대체됐다. 이를 새 source/cache 전제로 고정한 두 번째 manifest
`49af82f04c7d60689952fb075d84408b`를 적용했다. 최종 상태는 두 종목 각각 기간
9개, source-bound 9개, integrity `checked`, 제외 0개다. source revision만
달라지고 재무 의미가 같을 때 유효 변경으로 세지 않도록 decision fingerprint와
integrity 상태를 비교한다. 최종 post-apply preview의 유효 변경은 0건이었다.

각 manifest를 같은 인자로 다시 적용한 결과는 모두 `already-applied`였다. 유효
변경 0, AI supersede 0, reasoning 재평가 0, source refresh 0으로 끝났다.

## 해석 제한

이 복구는 재무 기간과 불변 source 계약을 현재 상태에 연결한다. 재무 개선이
주가 변동을 일으켰다고 증명하거나 적정가치를 확정하지 않는다. 과거 artifact가
없는 216건은 복원할 수 없으므로 성공 건수에 포함하지 않는다.
