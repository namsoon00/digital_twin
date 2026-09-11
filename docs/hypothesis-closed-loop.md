# 가설과 판단의 사후 검증

## 목적과 범위

새 가설을 제안하는 것으로 끝내지 않고, 검증에 필요한 자료가 부족하면 다시 확인하고, 당시 판단 이후의 실제 관측 결과를 다음 판단에 전달합니다. 이번 변경은 기존 TypeDB 추론·가설 검증·AI 판단·발송 정책을 연결하고 복구 경로를 보완합니다. 별도의 Python 매매 규칙이나 점수를 추가하지 않습니다.

전체 흐름은 다음과 같습니다.

```text
검증된 사실 / 기존 판단의 문제
  -> 가설 제안 병합 (Model Registry)
  -> 선별 -> 후보 규칙 생성 -> TypeDB 검증 / 과거 재현
       자료 부족 -> 영속 재시도 -> 같은 가설 재검증
       검증 통과 -> 운영 반영 승인 대기
  -> 승인된 규칙 + 시점이 고정된 ABox -> TypeDB 근거와 경쟁 가설
  -> 이전 판단의 관측 기록을 한 번 캡처 -> AI 판단
  -> 판단 저장 -> 기존 발송 정책 -> 알림 또는 발송 보류
  -> 사전에 등록한 기간·조건·비교 지수에 따른 관측
  -> 결과 / 자료 부족 / 평가 제외 구분
  -> 다음 AI 판단의 기억과 웹 상세, 개선 제안
```

규칙 생성·구조 검증 성공은 투자 성과 검증 성공이 아닙니다. 새로운 운영 규칙은 기존 승인·배포 절차를 유지합니다. 관측 횟수를 독립 실험 수 또는 예측 성공률로 표시하지 않습니다.

## 소유권과 저장

| 역할 | 구현 / 저장 |
| --- | --- |
| 가설 개발과 재검증 | `modules/model_registry/application/hypothesis_development_service.py`, `hypothesis_development_cases` / `hypothesis_development_events` |
| 투자 추론 | 기존 TypeDB 추론 경로와 버전이 고정된 규칙·ABox·근거 ID |
| 결과 관측 예약 | `investment_decision_outcome_targets`, `investment_hypothesis_observation_targets` |
| 결과 평가와 복구 | `modules/outcomes/application/investment_outcome_observation_service.py`, `modules/outcomes/domain/outcome_recovery.py` |
| 원자적 결과 저장 | `infrastructure/transactions/decision_history_parts/observation_records.py`, `shadow_observations.py` |
| 다음 판단의 기억 | `modules/decisions/domain/decision_continuity.py`의 `DecisionContinuityPacket` |
| AI 판단의 입력 기록 | `AIInsightEpisode.insight.decisionContinuity` |
| 웹 조회 | `modules/read_models/application/investment_case_query_service.py`의 `decisionReview` |

교차 도메인 사용은 기존 `contracts.py`와 트랜잭션 경계를 사용합니다. 웹이 새로운 추론을 실행하거나 최신 데이터로 과거 판단을 다시 쓰지 않습니다.

## 자료 부족 가설의 재시도

- `needs-data`는 후보 규칙이 아직 없는 **생성 단계**도 재시도 대상입니다.
- `retry`에 시도 횟수, 마지막 시각, 입력 지문, 다음 정기 확인 시각, 요구 자료, 상태를 저장합니다. 워커 재시작으로 이력이 초기화되지 않습니다.
- 새 입력이 확인돼도 기본 최소 간격 15분을 둡니다. 입력이 같으면 기본 180분마다 재확인합니다. `hypothesisDevelopmentChangedRetryMinutes`와 `hypothesisDevelopmentUnchangedRetryMinutes` 설정으로 조정합니다.
- 최신 케이스 몇 개만 보는 대신 대기 중인 오래된 시도부터 조회합니다. 계정·종목별 가설의 병합과 처리는 같은 MySQL 이름 기반 잠금으로 중복 실행을 막습니다.
- 처리 중인 제안은 원본 제안 저장소에 남으며 다음 backlog 확인에서 병합합니다. 완료·폐기·승인 대기·배포 상태는 자동 재실행하지 않습니다.
- 예외는 `dependency-error`와 차단 이유로 저장합니다. 웹 실험실에서 요구 자료, 마지막 시도, 다음 정기 확인을 볼 수 있습니다.

이 재시도는 기존 자료 수집과 후보 생성 서비스를 다시 호출합니다. 공급자가 제공하지 않는 자료를 새로 만들어 내거나 모든 요구 자료의 수집기를 자동 생성하지 않습니다.

## 관측과 복구

### 판단 시점의 기준값 보존

관측 시점이 오기 전부터 예약된 종목과 비교 지수를 수집 대상으로 읽습니다. 대상 종목과 비교 지수의 판단 시점 이전 관측값을 찾아 예약의 `baselineObservations`에 가격·출처·시각만 한 번 저장합니다. 판단 이후 시세로 과거 기준가를 대신하지 않습니다.

예약을 다시 저장해도 이 기준값은 유지됩니다. 원시 데이터 보관을 늘리는 방식이 아니며, 하루 단위 원시 시세 정리와 별개로 검증에 필요한 작은 기준값만 보존합니다.

### 자료가 늦게 들어온 경우

`excluded-contract-data-gap`, `excluded-criterion-data-gap` 결과의 예약은 `needs-data`로 둡니다. 마지막 평가 후 15분이 지나면 재확인할 수 있고, 기존 `observed` 결과 중 같은 자료 부족 상태도 조회합니다.

재평가 시 다음 항목은 변경할 수 없습니다.

- 원래 판단 ID와 관측 시각
- 원래 관측 가격과 판단 기준 가격
- 계약 지문과 관측 기간

저장된 `observationFacts`와 해당 시점의 이력만 사용합니다. 이후 현재가로 과거 결과를 보충하지 않습니다. 이미 평가가 완료된 결과는 같은 ID의 재요청으로 덮어쓰지 않습니다. 보충 전 상태는 최근 5회 평가 이력으로 남습니다. 거래가 없었던 시점이나 보관 기한이 지난 자료를 복구할 수 없으면 자료 부족 상태를 유지합니다.

비교 지수가 없으면 사용자/AI용 성과 JSON에서 비교 수익률과 초과 수익률을 `null`로 표시합니다. 기존 SQL 숫자 열의 호환용 0은 실제 관측치가 아니므로 `missingData`와 정규화된 JSON을 함께 사용해야 합니다.

## AI와 웹에서 같은 기록 사용

`DecisionContinuityPacket.reviewSummary`는 이전 판단, 검증된 조건 변화, 관측 결과, 다음 조건을 담습니다. 기존 패킷 버전에 추가된 필드이며 과거에 이 필드가 없는 기록을 최신 시세로 재작성하지 않습니다.

| 상태 | 의미 |
| --- | --- |
| `evaluated` | 정해진 평가에 사용할 수 있는 결과. 그 자체가 투자 성공을 의미하지 않음 |
| `data-gap` | 필수 자료 부족으로 판단 보류 |
| `excluded` | 지연 관측, 불완전 계약, 과거 적격성 미기록 등 평가 대상 제외 |
| `pending` | 연결된 이전 판단이 있지만 아직 결과 관측 대기 |
| `not-recorded` | 연결된 이전 판단 기록 없음 |
| `partial` | 여러 결과가 서로 다른 상태 |

`transitionVerified`와 전이 시각이 없는 후속 조건은 새로 성립한 변화로 보여주지 않습니다. AI 입력 압축 시에도 평가 적격성과 자료 부족을 보존합니다. AI가 관측 결과를 실제 계좌 수익이나 알림을 따라 거래한 성과로 표현하지 않도록 지시합니다.

웹 투자 판단 상세의 **이전 판단과 검증**은 해당 AI 판단이 사용한 패킷을 읽습니다. 이전 판단, 확인된 변화, 사후 검증, 다음 확인을 같은 계정·종목 범위에서 표시합니다. 이후 도착한 결과는 다음 판단의 패킷에 들어가며, 이미 저장된 판단의 설명을 소급 변경하지 않습니다.

## 검증과 남은 한계

```bash
npm test
npm run frontend:test:browser
PYTHONPATH=python_service:python_service/tests python3 -m unittest test_hypothesis_closed_loop test_stabilization_transactions
```

회귀 테스트는 재시도 간격·재시작, 중복 실행, 종결 상태, 기준값 보존, 과거 결과 복구, 계정 격리, AI와 웹 기록 일치, 입력 길이 제한을 확인합니다. 트랜잭션 테스트는 운영 DB가 아닌 분리된 테스트 DB를 사용합니다. 브라우저 검증은 데스크톱·모바일의 테스트 데이터를 사용합니다.

실제 운영 데이터는 읽기 전용으로 점검합니다. 기존 자료 부족 가설이 재검증 대상에 들어가는 것은 확인할 수 있지만, 향후 관측 기간이 지나지 않은 가설의 예측 성과까지 즉시 검증할 수는 없습니다. 추후 실제 독립 관측 결과가 쌓이면 가설별 지지·반증과 벤치마크 대비 결과를 검토해야 합니다. 기존 불완전 계약이나 제외된 관측을 임의로 적격 결과로 승격하지 않습니다.
