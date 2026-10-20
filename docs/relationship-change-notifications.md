# 관계 변화 알림

## 예측 가설이 없는 크립토 관측 (2026-10-20)

BTC·ETH의 변동 관측은 `causal-context` 주장과 네이티브 TypeDB 조건 증거를
가진 참고 관계다. 투자 행동 단계나 선택된 예측 가설이 없어도 이 원본 증거를
알림 후보로 연결한다. 계좌·종목·ABox·추론 세대가 일치하는 종목 관측 발행물을
만든 뒤 기존 `PUBLISH_TYPEDB` 경로로 전달하며 행동은 `NO_ACTION`이다.
예측 가설이나 계정 정책이 섞인 결과는 기존 판단 경로를 따른다.

전달 비교는 동결된 규칙 주장·관측 조건과 성공 영수증을 기준으로 한다.
가격·수집 시각·추론 세대만 갱신되면 같은 관측을 다시 발송하지 않는다.
미발송 초안은 성공 기준이 될 수 없으며, 다른 기간·방향·변동 구간의 규칙은
별도로 확인한다. 크립토 변동률과 기준값은 실제 TypeDB가 사용한 시장 사건의
조건 증거에 보존하고, 투자 예측이나 주문으로 확대하지 않는다.

## 가설 연결의 연속성과 대응 계획 (2026-10-19)

수명주기 v8은 같은 계정·종목·world·가설 계열에서 규칙 묶음이 바뀐 경우
공통 원본 규칙의 주장 지문이 같고 일대일로 확인되는 이전 묶음을 비교 기준으로
이어받는다. 지문이 없는 과거 기록을 새 카탈로그로 채우지 않는다. 이전 원문은 수정하지
않고 새 기록에 전임 식별자를 보존한다. 이전 묶음은 `superseded`로 이력에 남기며
조건 해제·반증 알림으로 보내지 않는다. 새 묶음의 실제 근거 추가·제거와 원천
갱신 검사는 그대로 수행한다. 여러 묶음이 겹쳐 기준을 특정할 수 없으면 임의로
골라 합치지 않는다. 현재 네이티브 규칙 경로가 남아 있으면 목록에서 빠졌다는
이유로 마지막 판단을 무효화하거나 판단의 원천 시각을 갱신하지 않는다.
새 묶음의 후속 알림도 같은 주장·목적의 성공 영수증만 원본으로 이어받는다.

네이티브 실행 v15는 `native-action-applicability-v1`에 실제 작성된 제한과
대상 역할에 따른 적용 불가를 따로 보존한다. 관심 종목은 보유분이 없으므로
추가매수·보유분 축소·매도가 적용되지 않는다는 경계를 투자 위험 제한으로
알리지 않는다. 실행 허용·차단 목록과 RuleBox의 수치 조건은 바꾸지 않는다.
이 분류는 현재 InferenceBox의 역할 근거가 있는 새 판단에만 적용한다.

저장된 종합 판단의 `native-response-plan-v1`은 기존 네이티브 관리 대응,
실제 제한, 적용 불가 행동, 검증 중인 후보, 검토 가능한 후보와 해제 기준을
분리한다. 관리 대응과 제약이 후보 검토의 경계이며, 서로 다른 시장 후보 중
승자를 Python이나 메시지 작성 단계에서 고르지 않는다. 매매 확정·주문 권한은
추가하지 않는다. 간결 알림 v3은 이 구분을 유지하고 예측 검증이 남은 후보를
확정 대응처럼 표시하지 않는다.

같은 규칙·조건·기간의 원본 자료가 이전 성공 알림에 있으면 기간 등락을
`이전 알림 → 이번`으로 표시한다. 종목·자료 버전·시각이 검증되지 않거나
이전 값이 없으면 비교 불가를 명시한다. 숫자 차이를 규칙 임계값 통과로
단정하지 않으며 전체 근거·시각·기준값은 상세 기록에 보존한다.
회귀 검증은 `relationship_reconciliation_checks.py`와 기존 수명주기·관리 판단
검증에 포함한다. 과거 발송 원문은 변경하거나 자동 재발송하지 않는다.

## 개별 근거 검증과 변화·대응 분리 (2026-10-19)

네이티브 실행 v14는 N-of-M 조건의 성립 여부를 계속 TypeDB에서 판정한다.
전체 상세 진단을 끈 경우에도 성립한 대체 조건을 TypeDB로 조회해 원래 조건 ID,
측정값, 출처와 유효기간을 보존한다. `any-condition-group`은 구조 표시이며
측정값처럼 시각을 검사하지 않는다. 발송 검사는 해당 그룹의 개별 증거가 닫혀
있는지 먼저 확인한 후 실제 측정값의 원천 시각·명시적 사용 제한을 검사한다.
그룹 표시만 남은 과거 기록을 현재 자료로 채워 발송하지 않는다.

개별 조건 조회는 같은 네이티브 실행에서 검증한 ABox 인덱스의 원본·관계 행으로
고정한다. 일반 과거 범위 조회로 대체하지 않는다. 인덱스가 없거나 그룹의 개별
관측값을 보존하지 못했으면 해당 규칙은 판단 근거로 사용할 수 없다. 실제 운영
TypeDB에서 같은 프로필 조건을 원본 행으로 제한한 읽기 검사는 236ms에 성립을
확인했다. 이는 개별 조회 측정이며 전체 추론 지연을 보장하는 수치는 아니다.

고정된 투자 성향·계좌 정책값은 해당 ABox에 동결된 설정이다. 시세의 출처나
원천 시각, 10분 유효기간을 빌리지 않는다. 평가 수익률과 계좌 비중 등 변하는
값은 기존 원천 시각과 유효기간을 그대로 따른다.

한 변화의 원본 규칙 근거가 없으면 그 변화만 상세 이력에 남긴다. 같은 묶음의
다른 검증된 변화는 별도로 발송할 수 있고, 제외한 변화의 전달 이력은 만들지
않는다. 가설 계약은 실제 변화에 연결된 자기 규칙만 설명한다. 비교의 이전
근거가 비었고 현재 모델 조건은 성립하는데 가설 해제만 기록된 모순은
`rule_dispatch_transition_conflict`로 보존한다. 이를 조건 해제 알림으로 보내지 않는다.

전달 비교는 `rule-notice-decision-impact-v1`에 이번에 바뀐 관리 대응과 목표,
계속 적용되는 대응, 해제한 규칙을 구분해 동결한다. 가격 전망이나 주문 권한을
새로 만들지 않는다. 간결 알림 v2는 가설 근거가 바뀐 알림의 첫 문장에 그 변화를
쓰고, 기존 대응은 별도 맥락으로 표시한다. 이전 성공 영수증에 같은 대응이
보존된 경우에만 ‘유지 중’이라고 하며, 처음 보는 경우는 ‘현재 적용 중’이라고 한다.
다음 확인도 해당 가설의 조건을 먼저 표시한다. 전체 자료는 웹 상세에 보존하고,
일부 채널이 이미 전달한 재시도의 원문 바이트는 변경하지 않는다.

회귀 검증은 `relationship_sequential_checks.py`에서 네이티브 대체 조건의 실제
조회·접지·발송 유효성, 근거 누락 사건의 분리, 모순된 해제 기록, 동결 입력의
불변성과 변화·대응 표시를 검사한다. 저장된 개인 자료 재현은 로컬에서만 실행한다.

## 실시간 시세와 관리 알림 연결 복구 (2026-10-19)

시세 수신 상태와 근거 유효성을 분리한다. 공급자의 `realtime`, `live`, `fresh`
표시는 `providerStatus`에 보존하고, 판단용 `freshnessStatus`는 원천 시각과
허용 유효기간으로 계산한다. 수신이나 재조회만으로 만료된 시세를 되살리지 않는다.
시각 누락·해석 실패·미래 시각과 명시적인 품질 제한은 계속 사용 불가다.
휴장 종가의 기존 참고 유효기간은 시세에만 적용하고 수급에 빌려주지 않는다.
입력 캐시 계약을 v7으로 구분해 이전 품질 판정 결과를 재사용하지 않는다.

`typedb-management-observation-v2`는 같은 계좌·종목·world·ABox·추론 세대에
저장된 원본 추론의 주장 계약에서 알림 목적을 읽는다. 축약된 규칙의 빈 주장
필드가 원본 계약을 덮어쓰지 않는다. 원본 계약이 없거나, 다른 범위의 근거이거나,
비어 있지 않은 계약 사본의 목적이 유효하지 않거나 서로 다르면 전송하지 않는다.
규칙 카탈로그를 새로 읽어 과거 원본 계약을 대체하지 않는다. 기존 성공 영수증,
조건 적용·해제·재적용, 반복 억제와 최종 발송 시점 유효기간 검사는 유지한다.

관리 판단이 생성됐지만 알림 계약 검증에 실패한 경우 해당 종목 사례는
`BLOCKED`, 발행 경로는 `INVALID`, 전달 상태는 `failed`로 저장한다. 원인 코드,
규칙 ID와 정확한 입력 식별자를 보존하고 추론 결과의 발송 진단과 워커 건강도에
노출한다. `변화 없음`으로 기록하거나 고객에게 검증 실패 초안을 보내지 않는다.
다른 종목의 정상 발행은 계속 처리한다.

`relationship_recovery_checks.py`는 기존 회귀 검증 안에서 실시간 상태별 원천
시각 검사, 누락·미래·만료·품질 제한, 네이티브 읽기와 축약 규칙 조립, 가설 없는
관리 판단의 후보·발행·접수·워커 영수증, 반복 억제 및 계약 오류의 별도 기록을
검사한다. 실제 저장 입력의 무전송 재현과 운영의 새 알림 전달 확인은 구분한다.

## 관리 판단의 적용·해제 연결 (2026-10-19)

`rule-policy-lifecycle-v1`은 성공 영수증을 기준으로 규칙별 이전 관리 대응,
현재 대응, 해제, 남은 제한과 아직 재확인하지 못한 기존 제한을 구분한다.
중간 알림에 오래된 규칙이 빠져도 마지막으로 전달한 제한 이력은 유지한다.
같은 목표의 수치 변화는 반복 발송하지 않고, 해제 후 다시 성립한 제한은
재적용으로 전달한다. 첫 확인과 설정 목표 변경을 새 시장 악화로 표현하지 않는다.

`typedb-policy-resolution-v1`은 이미 승인된 `management-response-v1.objective`를
TypeQL로 직접 조회한다. 기존 작성 계약이 필수 정책 조건의 정확한 역조건인지
검사한 목표만 사용한다. 새 임계값·매매 행동·예측 가설은 추가하지 않는다.
활성 ABox의 정확한 물리 ID와 기존 쓰기 임대를 사용하고, 원천 관측·신선도를
다시 연결한 `InferenceTrace`만 저장한다. 해제 증명으로 기존 `BLOCKS_ACTION`
관계를 만들지 않는다. 조회 실패, 규칙 부재, 만료, 시각 누락은 해제가 아니다.
현재 지원 범위는 손익 경계 회복과 계좌 비중 상한 충족이다. 프로필 관계의
부재나 미수집을 정책 해제로 취급하지 않는다.

해제만 남은 종목도 이전 성공 영수증의 같은 계정·종목·world에서 해당 제한을
전달한 사실이 있으면 판단 저장 → `OBSERVATION` → `PUBLISH_TYPEDB` → 최종 영수증
비교·유효기간 검사로 연결한다. 후보 시점의 기준선은 잠정 자료이고, 전송 직전
잠금 구간에서 최신 성공 영수증을 다시 읽는다. 미발송 이력과 다른 계정의 제한은
해제 알림의 기준선이 될 수 없다. 기존 재알림 간격과 답글 연결은 유지한다.

짧은 본문은 해제된 대응을 먼저 표시하고 다른 제한이 남으면 같은 문장에 알린다.
해제 조건에서 실제 비교한 이전·현재 수치를 표시한다. 나머지 제한을 확인하지
못한 경우에도 전부 해제됐다고 표시하지 않는다. 전체 조건은 웹 상세에 보존한다.
`readable-relation-change-v15`로 대기 문서의 표시를 갱신하되 부분 전송과 성공
영수증 본문은 유지한다. 네이티브 읽기 동작은 v13으로 구분하고 v12 카탈로그,
기존 판정 쿼리, 스키마 골든은 불변으로 보존한다.

`policy_resolution_checks.py`는 기존 관리 회귀 검증 안에서 실제 조회문 생성,
해제 증명의 근거 연결, 후보·발행·접수·짧은 전송 본문, 원본 답글,
반복 억제·재적용·부분 해제와 자료/계정/세대 불일치의 무전송을 검사한다.

## 발송 시점 재검증과 비교 기준 통합 (2026-10-18)

`rule-notice-dispatch-validity-v1`은 TypeDB 관계 알림에 실제 사용된 규칙 조건의
원천 시각·허용 유효기간·사용 가능 상태를 발송 전에 다시 확인한다. 자료가 만료됐거나
원천 시각을 확인하지 못하면 그 알림은 전송하지 않고 기존 계좌 수집 예약을 통해
새 원천 자료 → TypeDB 추론 → 새 알림 접수로 이어진다. 대기 문서의 숫자를 최신
시세로 덮어써 과거 판단을 새 판단으로 만들지 않는다. 재확인 요청 실패도 기록한다.

조건에 저장된 `freshnessRequired`, `maxAgeMinutes`, `observedAt`, `validUntil`을
보존한다. 이전 조건에 유효기간이 없으면 기존 시세 허용기간을 보수적으로 사용한다.
정적 정책 근거는 별도로 취급하고, 과거 결과·자료 중단 답글을 현재 시세처럼 만료시키지
않는다. 관련 없는 오래된 호가가 공시 근거를 막거나, 같은 시세의 재조회 시각이 가격
근거를 갱신하지 못한다. 독립 AI 관찰과 일반 운영 알림의 기존 정책은 유지한다.

`rule-notice-receipt-comparison-v1`은 발행·접수·발송 직전 검사에 같은 고객 이벤트
판정을 사용한다. 실제 접수와 전송은 같은 계정·종목·world의 성공 영수증을 읽고
비교하며, 후보·억제 이력을 성공 알림으로 대체하지 않는다. 초기 발행의 비교는
잠정 상태로 표시한다. 새 의미가 없으면 `ontologyRelationDiff`도 중요 변화로
표시하지 않는다. 비교 기준 영수증, 규칙 ID, 고객 이벤트 ID와 최종 사유를 저장한다.
기존 최종 발송 잠금과 쿨다운은 유지한다.

`rule_delivery_checks.py`는 유효기간 경계, 원천 시각 누락·미래 시각, 정적 정책,
공시 유효기간, 재확인 중복 방지, 실제 워커의 무전송, 성공 영수증 기준 일관성을
기존 관리 판단 회귀 검증 안에서 확인한다.

## 가설 없는 관리 판단의 알림 연결 (2026-10-18)

`typedb-management-observation-v1`은 예측 가설이 없는 종목에서도 같은 계정·종목·
world·ABox·추론 세대의 네이티브 관리 대응을 알림 후보로 연결한다. 저장된
`management-decision-v1`을 원본 규칙·수치·자격과 다시 대조하고, 네이티브 증명과
`management-policy` 발송 목적을 모두 확인한다. 원시 비중, 상태 플래그, 오래된
자료, 다른 세대의 대응만으로는 후보를 만들지 않는다. 가설이 있는 종목의 기존
검토 경로는 유지하며 관리 대응을 가격 전망 가설이나 주문 권한으로 바꾸지 않는다.

후보는 종목 판단 저장과 `OBSERVATION` 발행을 거쳐 독립 `PUBLISH_TYPEDB` 경로로
접수한다. 후보 생성 단계의 발송 간격 표시는 최종 차단 근거로 쓰지 않고, 성공
영수증을 읽은 잠금 구간에서 관리 대응·목표의 새 의미와 실제 발송 간격을 함께
검사한다. 같은 목표를 유지한 수치 변화는 반복 발송하지 않는다. 새 관리 목표도
기존 재알림 간격을 우회하지 않는다. 첫 유효한 관리 대응은 예측 가설이 없다는
이유로 비실행 기준선에 묻히지 않는다.

`ManagementObservationFlowChecks`는 기존 관리 판단 회귀 검증 안에서 후보 생성부터 판단 저장, 실제 접수 정책,
짧은 전송 본문까지 검사한다. 자료·증명·목적 누락, 계정/세대 혼입, 목표 변조,
같은 의미의 반복, 목표 변경, 사전/최종 간격 구분, AI 미호출과 실험 전송 차단을
함께 검증한다. 실제 과거 판단의 무전송 재현과 고객의 실제 수신은 별도로 기록한다.

## 실제 적용과 전달 확인 (2026-10-18)

새 관리 대응은 운영·후보 비교에서 별도의 판단 내용으로 검증한다. `NO_ACTION`이
같더라도 비중 조정 목표, 추가매수 제한, 조건 해제가 다르면 같은 판단으로 취급하지
않는다. 비교에는 실제 입력·저장 ABox의 의미 지문이 필요하며, 원천 스냅샷만 같거나
과거 근거가 없는 경우는 검증 표본에서 제외한다. 후보의 추론 성공만으로 고객 발송
적용을 표시하지 않고 기존 배포 승격 기준을 유지한다.

운영 화면의 **알림이 없는 이유 확인**은 추론 지연, 원천 자료 대기, 후보 규칙 미적용,
최근 완료된 종목 분석의 알림 후보 수, 실제 저장된 접수 사유를 구분한다. 분석되지
않은 다른 종목까지 ‘유효한 신호 없음’으로 설명하지 않는다. 원천 스냅샷 생성 시각과
시세 원천 시각을 구분하고, 실제 전달은 알림 전달 진단의 성공 영수증으로 확인한다.
이 진단은 운영 화면에만 추가하며 고객에게 별도의 자료 상태 푸시를 만들지 않는다.

TypeDB 발행 경로의 접수 결과도 최초 발행 의도와 구분한다. `semanticReasonCode`와
`semanticReason`은 그래프가 관찰을 발행 경로로 보낸 목적이며, `reasonCode`는 실제
접수 성공·기존 등록 또는 알림 저장소가 판정한 억제 코드다. 예를 들어 새 의미 변화가
없어 억제된 결과를 `material-typedb-stage-observation`이라는 발송 사유로 기록하지
않는다. 이 구분은 새 추론 결과와 운영 진단에 보존하며 과거 발송 기록은 수정하지 않는다.

## 저장된 종합 판단과 짧은 본문 (2026-10-16)

`investment-decision-synthesis-v10`은 네이티브 결과에서 만든
`rulebox-judgment-v1`을 판단 사례와 함께 저장한다. 계정·종목·world·추론 세대·ABox가
일치하는 관리 대응, 제한, 같은 행동을 지지하는 가설과 근거, 반대 근거,
비교가 필요한 대안, 정확한 조건값과 후속 확인을 묶는다. 알림 작성 단계에서
가설들을 읽고 임의의 매매 결론을 만들지 않는다. 자료 갱신에 따른 숫자 변화와
관리 목표/행동 후보의 의미 변화는 다른 항목으로 보존한다.

`readable-relation-change-v14`의 기본 전송 본문은 `compact-rulebox-v1`이다.

1. 현재 관리 대응 또는 검토 상태를 한 문장으로 표시한다.
2. **알림 이유**에는 실제 발송 대상으로 인정된 근거 전환을 표시한다.
3. **핵심 수치**에는 연결된 규칙의 비교값과 기준을 최대 3개 표시한다.
4. **함께 보면**에서는 같은 대응을 지지하는 근거, 대안 충돌, 관리 판단의 범위를 설명한다.
5. **현재**에는 현재가·전일 등락·보유 수익률을 표시한다.
6. **다음 확인**에는 동결된 관리 목표 또는 재확인 조건을 표시한다.

제목과 위 내용을 포함해 본문은 7줄이며 상세 링크·시각/번호를 포함해 최대 9줄이다.
문서의 핵심 내용은 850자 이내인지 검사한다. 기본 템플릿이 원시 관계 정보나 긴
진단 문단을 뒤에 다시 붙이지 못하도록 최종 전송 경로에서도 이 문서를 사용한다.
이전 버전으로 작성된 대기 문서는 발송 전에 갱신한다. 부분 전송 재시도와 원본
성공 영수증의 전송 문자열은 바꾸지 않는다.

- ‘이전 → 현재’는 이전 성공 알림의 **같은 규칙·조건·필드**에 실제로 저장된
  측정값으로만 표시한다. 다른 규칙의 참고 수치를 과거 조건값으로 대체하지 않는다.
  모델 입력 변화도 발송 원인 규칙의 검증된 비교에 한해 표시한다. 이전 입력이 없으면
  현재 입력 또는 비교 불가를 표시한다. 기준값은 같은 ABox에서 확정된 값을 사용한다.
- 정책이 요구하는 대응과 검증 전인 가격 전망 후보를 구분한다. 여러 후보의 승자,
  매매 시점·수량, 검증 자격을 Python에서 새로 선택하지 않는다. 확정되지 않은 경우
  그 상태를 명시하고, 근거의 사용 가능 여부를 보유 유지나 위험 해소로 표현하지 않는다.
- 전체 가설·조건·시세·수급·체결강도·호가·거래량·재무·시각과 한계는
  `customerInvestmentDetailDocument`와 원본 `relationChangeEvidence`에 유지한다.
  웹 상세는 전체 문서를, 목록과 Telegram은 짧은 문서를 사용한다. 웹 조회는
  복사본에서 표시만 갱신하며 저장된 판단과 발송 본문을 수정하지 않는다.
- 과거 사례는 검증된 동결 brief의 읽기 어댑터를 사용하고 `legacy-frozen-brief`로
  구분한다. 새로 저장된 판단 사례나 새 관리 규칙이 실행됐다고 표시하지 않는다.
- 자료 중단·복구 및 사후 확인 결과는 기존 원본 알림 답글 경로를 유지한다.
  쿨다운·휴장·원천 시각 갱신·중복 발송 판정과 독립 AI 경로는 바꾸지 않는다.

검증: `CompactRuleBoxJudgmentTests`는 판단 저장과 scope 격리, 상충 대안,
정확한 규칙 값 비교, 짧은 실제 전송/기존 캐시 갱신, 부분 전송 불변성,
전체 웹 상세 보존과 조회 불변성을 검사한다. 기존 상세 내용 검사는 전체 문서를
대상으로 유지한다. 새 관리 규칙의 운영 전환은 별도 배포 검증을 통과해야 한다.

운영 검증 중 실제 후보 배포의 저장된 네이티브 MSTR 결과에서 관리 대응이
읽기 단계에 누락되는 문제를 확인했다. `graph_store_inferencebox`가 이미 저장된
`managementResponse`와 `boundFacts`를 그대로 전달하도록 수정했다. 같은 규칙의
`REQUIRES_NEXT_CHECK`와 관리 대응을 갖는 `BLOCKS_ACTION`이 기존 인과 경로 키를
공유할 때도 두 의미를 각각 보존한다. 미래 재확인 요청이 관리 대응을 가리지 않게
하되 과거/예측 규칙의 중복 제거 방식은 유지한다. 이 결과 해석 변경은
`typedb-direct-typeql-rule-engine-v11`로 구분하며 v10 캐시를 재사용하지 않는다.
v10의 모든 쿼리 본문·규칙 카탈로그·스키마 계획은 이전 골든과 동일하게 검사한다.

재시작 후 실제 완료된 추론에서는 ABox의 계좌 비중(소수 둘째 자리)과 설명
컨텍스트의 반올림 전 계산값을 그대로 비교해 대응을 제외하는 문제도 확인했다.
`native_account_weight_pct`로 기존 ABox 표현을 양쪽에서 공유하고,
`typedb-direct-typeql-rule-engine-v12`로 구분한다. 예를 들어 원래 계산값
78.450062%와 저장된 78.45%는 같은 표현으로 비교하며, 저장된 78.45%를 조정
규모 계산에 사용한다. 다른 비중·기준값·계정·자료 세대 불일치는 계속 거부한다.
임의 허용 오차나 새 비중 임계값을 도입하지 않는다.

웹 상세의 첫 summary API도 전체 문서를 직접 내려준다. 전체 추론 이력을
요청하는 옵션과 고객 상세 문서 요청을 분리해, 수급·체결강도를 읽기 위해 무거운
추론 조회를 강제하지 않는다. 목록은 짧은 문서를 유지하며, 실제 상세 진입점도
`CompactRuleBoxJudgmentTests`에서 확인한다.

## 관리 대응과 후보 제외 사유 (2026-10-15 실제 발송 검증)

`readable-relation-change-v12`는 관리 기준만 바뀐 알림에서 적용할 제한,
규칙이 요청한 점검, 실제 비교 수치, 유지·축소를 결정하지 못한 이유,
재확인 경계를 앞에 둔다. 관리 알림에는 무관한 가설의 긴 기간 분석을 붙이지
않으며 전체 가설·원본 조건·수치는 동결 상세 근거에 남긴다. 품질 검사는 이 경우
가설 문단 대신 목적·규칙 데이터·판단 영향·후속 확인을 필수로 검사한다.
최종 전송 템플릿도 확인된 관리 이벤트의 제목 `관리 기준 점검`을 유지한다.
기존 `relation-change` 분류와 전송 정책은 그대로 사용한다.

- `rulebox-decision-brief-v1.unavailableCandidates`는 규칙이 제시했으나 이번 판단에
  사용할 수 없는 행동의 제외 사유와 연결 가설의 자격을 보존한다. 허용 행동이나
  운영 후보 목록이 아니며 최종 행동을 고르지 않는다. 조건부 보유와 추가 확인을
  보유 후보로 만들지 않는다. 같은 행동을 뒷받침하는 다른 유효 근거가 남으면 일부
  근거 제외와 후보 전체 제외를 구분한다.
- 이전 축소 후보가 사라졌다는 이유로 `일부 축소 → 없음`, 보유 유지 전환, 위험 해소를
  표시하지 않는다. 이번 원천 자료의 사용 제한과 가설 검증 미충족을 별도로 설명한다.
  최신 시세 하나 또는 표본 수 충족만으로 운영 자격을 얻는다고 약속하지 않는다.
- 규칙 하나가 새로 제한해도 전체 추가매수 제한이 이전부터 있었다면 제한의 지속과
  새 관리 조건을 구분한다. 종목 관리 설정의 손실 중 추가매수 제한도 숫자 조건과 함께
  표시한다. 손실 경계 `0%`도 원본 연산자에 따라 설명하되 다른 제한의 해제와 구분한다.
- 집중도 비교는 **native 조건의 field·observedValue·동결 threshold**를 사용한다.
  `positionAccountWeight`는 현금을 포함한 계좌 전체 평가액 대비 비중이고,
  `positionWeight`는 보유 종목 평가액 내 비중이다. 후자를 계좌 비중으로 대체하지 않는다.
  계좌 식별정보 필터에서 정확히 이 이름의 숫자 사실만 보존하며 자격증명 필터는 유지한다.
  측정값이나 기준이 없으면 비교 불가를 표시한다. 임의의 축소 수량·매도 가격은 만들지 않는다.

검증은 실제 성공 발송의 원본 컨텍스트와 최종 선택 이벤트를 보존한 무전송 재현,
`test_rulebox_decision_brief`, `test_rule_notification_intent`,
`test_typedb_independent_observation`으로 수행한다. 이전 발송 영수증과 부분 전송
본문은 불변이며, AI 경로·RuleBox 조건·허용 행동·쿨다운·발송 계기는 바꾸지 않는다.

## 발송 계기와 대응 판단 구분 (2026-10-13 검증 후 개선)

`readable-relation-change-v10`은 새로 추가·해제된 규칙을 첫 문장에 표시한다.
기존 손실 기준 이탈과 실제 발송 전환을 구분하며, 변경된 규칙에 연결되지 않은
현재 손익을 새 알림의 원인으로 앞세우지 않는다. 이전 입력이 없으면 점수
`미보존 → 1` 대신 새 규칙 연결과 이번 입력의 기간별 등락·관측 수·시각을 표시한다.
비교 가능한 실제 입력 변화가 있으면 그 수치를 우선한다. 점수 비교만 보존된 경우는
정밀도를 유지한 점수와 기준을 보조 설명으로 쓰고 확률과 구분한다.

- 관리 원칙의 제한, 검증이 끝나지 않은 대응 후보, 최종 선택을 구분한다.
  `일부 축소 검토 보류`에는 가설별 이름·독립 사후 결과 수·당시에 저장된 운영
  기준과 미충족 항목을 붙인다. 표본 수만 채우면 매매가 확정된다고 약속하지 않는다.
  정책에 없는 숫자를 새로 정하거나 자격을 재평가하지 않는다. 원본 규칙의 행동 범위,
  검증 자격, 독립 AI 경로는 유지한다.
- 손실 기준의 전후 비교는 같은 규칙·조건에 보존된 연산자·기준값을 대조한다.
  고정 기준 변경, `<` 경계의 동일값, 기준 누락·불일치에는 과거 위반을 단정하지 않는다.
  환산 가격은 평균매입가에 따른 **손실 기준 경계**라고 명시하고 반등·매수 기준과 구분한다.
- 기간별 설명은 각각의 문단으로 읽히게 하며 반복 설명을 줄인다. 현재 주가, 보유 손익,
  외국인·기관·개인, 체결강도, 호가, 거래량, 재무 자료와 원천 시각은 유지한다.
  표시 예산 밖의 추가 가설은 상세 근거에 있다고 알리고, 전체 원본은 동결 근거에 남긴다.
- KIS `FHKST01010900`의 투자자별 거래금액은 백만원에서 원으로 수집 단계에서
  한 번 환산한다. `amountUnit=KRW`, `sourceAmountUnit=million-KRW`,
  `amountNormalizationVersion=kis-investor-amount-krw-v1`을 coverage와 영수증에 보존한다.
  [공식 API 문서와 응답 예시](https://apiportal.koreainvestment.com/apiservice-apiservice?/uapi/domestic-stock/v1/quotations/inquire-investor)를 기준으로 수량과 금액을 구분한다.
  예전 캐시의 단위 미확인 금액은 새 수집에서 재사용하지 않고 갱신을 요청한다.
  갱신 실패 시 수량·시세는 유지한다. 과거 동결 알림의 금액은 임의 환산하지 않으며,
  새 형식으로 재현할 때 단위가 없는 금액은 `금액: 단위 미확인`으로 표시한다.

검증은 실제 발송 기록의 무전송 재현, `test_rulebox_decision_brief`,
`test_kis_realtime_integrity`, `test_relation_observation_narrative`,
`test_typedb_independent_observation`, `test_relation_market_tracking`으로 수행한다.
원본 성공 영수증·부분 전송 재시도·쿨다운·자료 상태 답글 정책은 바꾸지 않는다.

## 관리 원칙과 행동 후보 표시 (2026-10-13)

`rulebox-decision-brief-v1`은 이미 확인된 RuleBox 판단의 읽기 계약이다.
계정·종목·판단 사례·ABox·추론 세대가 일치하고, 현재 사용 가능한 것으로 평가된
규칙의 결과만 보존한다. 원본 규칙·행동 범위·사후 검증 자격은 변경하지 않는다.

- 현재 제한을 먼저 설명하고, 검토 후보와 추가 확인 요청을 분리한다.
  사례와 행동 범위 양쪽의 금지 행동 및 해당 규칙의 결과가 일치해야 제한으로 표시한다.
- 후보는 사례가 허용한 행동 중 현재 규칙이 뒷받침하는 것만 표시한다.
  `REQUIRES_NEXT_CHECK` 및 `defer`는 보유 의견이 아니다. `constrain`의 `HOLD`도
  다른 행동을 제한하는 근거이며 적극적인 보유 의견으로 비교하지 않는다.
  실제 `support`의 보유 후보는 유지한다. 후보를 정렬하거나 승자를 고르지 않는다.
- 가설 자격이 부족하면 확인된 사후 결과 수와 미확정 사유를 표시한다.
  후보가 하나인 경우도 최종 매매 결정으로 승격하지 않는다. 예전 AI 큐에 보내거나
  AI 비교를 기다린다는 문장을 만들지 않는다. 독립 AI 관찰과 기존 발송 정책은 유지한다.
- 손실 기준의 `field` 참조는 같은 동결 ABox의 실제 값으로 해석한다. 원본 조건과
  `resolvedThreshold`를 함께 저장한다. 값이 없으면 미보존으로 표시하며 카탈로그의
  `default`나 현재 계좌 설정으로 과거 기준을 추측하지 않는다.
- 이전 영수증에서도 같은 손실 기준을 벗어났다면 최초 이탈로 표현하지 않는다.
  기준값이 달라졌거나 이전 기준이 없으면 상태 지속 비교를 생략한다.
- 비중과 상한을 함께 보여 손실 기준 이탈과 비중 상한 초과를 구분한다.
  손실 조건을 다시 확인할 수익률 경계를 표시하되 다른 제한의 해제까지 약속하지 않는다.
  현재가·평균매입가로 계산한 수익률이 관측 수익률과 일치할 때만 해당 경계를 참고
  가격으로 환산한다. 환율 등 다른 계산 기준이면 가격 환산을 생략한다.

`readable-relation-change-v9`는 Telegram과 웹이 사용하는 동일한 고객 문서에서
현재 판단 → 변경 수치 → 계좌 기준 비교 → 미확정 사유 → 재확인 조건을 설명한다.
자료 중단·복구 답글은 기존 짧은 형식을 유지한다. 과거 성공 영수증과 부분 전송
재시도 본문은 변경하지 않는다. 신규 결정·주문·자격 승격·임의의 축소 비율은 만들지 않는다.
판단 요약은 발송 영수증의 동결 스냅샷에도 남는다.

검증: `test_rulebox_decision_brief`는 계좌·세대 격리, 오래된 규칙 제외,
행동 범위·검증 자격 유지, 확인 요청과 보유 의견 구분, 실제 기준 해석,
이전 기준 변경, 가격 환산의 계산 기준 및 실제 고객 문서 품질을 확인한다.

관계 변화(`OBSERVATION`) 푸시는 검증된 TypeDB 결과의 가설 수명주기 전환을 소비한다. Python은 새 투자 규칙이나 매매 판단을 만들지 않고 기존 공개 lifecycle 계약으로 의미 있는 전환인지 확인한다.

## 발송 기준

- 새로 관찰된 관계, 반증·해소는 발송 후보가 된다. 새 근거 변화 없이 RuleBox 유효기간만 지난 경우는 웹 이력에 남긴다.
- 강화·약화는 기존 계약이 인정하는 의미 있는 근거 차이가 있어야 한다. 세대 ID 갱신이나 유지 상태만으로는 발송하지 않는다.
- 가격 임계치 통과, 뉴스 수집, 재검토 조건 도달, notification-policy 규칙 성립만으로 관계 변화 푸시를 허용하지 않는다. 이러한 입력은 추론을 시작할 수 있지만 결과의 의미 변화가 필요하다.
- 실제 발행 계약, 수신 설정, 성공 발송 기준 쿨다운·유사 알림 차단·전송 검증을 계속 적용한다. AI 해석의 `REVIEW_ONLY` 발행 조건은 바꾸지 않는다.
- 이미 보낸 전환 ID는 동일 종목·계정의 마지막 성공 영수증과 비교하여 차단한다. 큐 등록 후 다른 작업이 발송될 수 있으므로 종목 발송 잠금 안에서 다시 비교한다.
- 주기 기본값은 기존 설정을 유지한다. 일반 의미 변화는 material 60분, 기존 검증된 방향 전환·심각도 상승 조건을 함께 만족하면 immediate 10분이 적용된다. 시간 경과만으로 관계 푸시를 만들지 않는다.

## 근거와 비교 기준

`relationChangeEvidence`는 가설(지지·반대 근거 및 반증 조건), 규칙(조건과 관측값), ABox 사실, 추론 세대·ABox 식별자·관측 시각·출처를 해당 알림의 컨텍스트에서 복사한다. 이전 값은 마지막 성공한 관계 알림의 `notification_delivery_attempts.metadata_json.relationChangeSnapshot`에서 가져온다. 대기·억제·실패 알림이나 현재 그래프로 이전 값을 대체하지 않는다.

성공 영수증은 대형 작업 본문보다 오래 보존되므로 작업 본문 정리 후에도 비교할 수 있다. 기존 영수증에 스냅샷이 없거나 보관 기간이 끝났으면 이전 값은 미보존으로 표시한다. 이 경우 현재 전환의 이전 상태는 lifecycle 기록으로 설명하되, 이전 관측값을 복원했다고 표시하지 않는다.

Telegram에는 변화, 가설·규칙·관측값 각각 최대 3개, 출처·시각, 반대 근거와 한계를 요약한다. 사용자 언어 수준에 따라 가설이 ‘가능한 설명’ 등으로 표현될 수 있다. 상세 화면에는 저장된 전체 비교를 표시하고 근거 식별자를 연결 관계별로 함께 보여준다. 미보존·미기록은 근거가 없다는 의미가 아니다. 원천 출처와 시각이 없는 값에는 이를 명시한다.

시장 신호 전환의 체결강도 `observedValue`는 `rawTradeStrength - 100`으로 저장되므로 메시지에서 원래 체결강도로 복원한다.

## 검증

`test_notification_delivery_reliability.py`는 원시 가격·뉴스만으로 발송 불가, 검증된 전환 발송, 중복·다른 추론 세대 차단, 스냅샷 불변성, 가설/규칙/관측값 표시, 체결강도 복원, MySQL 성공 영수증·계정 격리·실패 영수증 제외를 검증한다. 기존 TypeDB/AI 분기 테스트는 두 발행 경로의 독립성을 유지한다. 프런트엔드 테스트는 전후 값, 근거 연결, 미보존 표시 및 HTML 이스케이프를 검증한다.

## 체결강도·시간 보정·추적 표시 (2026-10-03)

표시 버전 `readable-relation-change-v6`는 체결강도와 거래량을 별도 섹션으로 보존한다.
체결강도는 현재 값, 마지막 성공 알림의 값과 차이(포인트), 각 기준 시각,
원천이 기록한 매수·매도 체결 표본(주)을 표시한다. 장 마감·지연·품질 제한 값은
참고값과 사유를 붙이고, 미제공과 실제 관측된 0을 구분한다. 표본 주수를 관측
횟수로 표현하지 않는다. 체결강도 자체에 시간 보정 계수를 곱하지 않는다.
웹 요약에서도 체결·거래량·추적 섹션과 계산 한계를 유지한다.

점검에서 일봉 거래량 비율과 가격 갱신 시각이 결합되는 경로, 장전 8%·장후 6%라는
고정 가정으로 시간 보정하는 경로를 확인했다. 이제 `market_data`가 선택된 비율과
동일한 원천의 `marketSignalCoverage.volume`을 소유한다. 비율의 값, 분자·분모,
집계 범위와 시각을 함께 보존하고 추론 사실·ABox·모니터·결과 추적이 같은 계산을 쓴다.

- Toss 일봉 비율은 마지막 양수 거래량 일봉 ÷ 마지막 봉을 포함한 최근 최대 20개
  양수 거래량 일봉 평균이다. 관측 날짜·표본 수·분자·분모를 표시한다. 현재가 시각이나
  실시간 누적 거래량과 섞어 시간 보정하지 않는다. 기존 원본 비율의 의미는 유지한다.
- KIS `prdy_vrss_vol_rate`/`PRDY_VOL_VRSS_ACML_VOL_RATE`는 전일 대비 비율이며
  20일 평균이나 동시간 비교값이 아니다. [KIS 공식 현재가 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_price/chk_inquire_price.py)와
  [공식 실시간 필드 목록](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions_ws.py)을 기준으로 집계 종류를 보존한다.
  실시간 거래량은 `BSOP_DATE` + `STCK_CNTG_HOUR`의 한국 시간대를 사용한다.
  조회 시각·수신 시각을 거래소 체결 시각으로 대체하지 않는다.
- `regular-u-curve-v2`는 원천이 정규장이라고 확인한 장중 누적 비율에만 적용한다.
  산식은 원본 비율 ÷ 고정 U자형 분포의 기대 누적 비중이다. 한국/미국 현지 시각과
  미국 서머타임을 사용한다. 미확인 시각·집계 범위, 다른 원천의 비율, 미래 시각,
  장전·장후, 개장 직후 분모 0에는 보정값을 만들지 않는다.
- 이 분포는 종목별 과거 동시간 데이터로 검증한 모델이 아니다. 휴장·단축장 달력을
  자체 검증하지 않으므로 원천 정규장 확인을 요구하고, 표준 거래시간이라는 한계를
  표시한다. 향후 동시간 실측 비교는 별도의 집계·검증 계약이 필요하다.
- 과거에 동결된 보정값은 다시 계산하거나 지우지 않고 검증되지 않은 참고 기록으로
  표시한다. 부분 전송 재시도는 이미 보낸 정확한 본문을 유지한다. 원자료 부족은
  해당 수치의 한계로 표시하며 관계 알림 전체를 숨기는 새 게이트를 추가하지 않는다.

체결강도와 같은 집계 기준·공급자의 거래량 비율은 성공 알림 간의 전후 값을 보여준다.
이는 지속 수집된 모든 틱 이력과 다르다. 가설에 연결된 기간 자료에 체결강도와 거래량
비율의 마지막 값이 저장돼 있으면 가격 경로와 함께 설명한다. 기본값 0은 관측값으로
추정하지 않고, 기간 기록을 현재 측정값으로 바꾸지 않는다. 등록된 자동 추적 조건이 있는 경우에는
등록 영수증의 계정·종목을 확인한 뒤 시작값 → 직전 관측 → 최근 관측, 원천 시각,
연속 확인 횟수와 필요 횟수를 최대 4개 조건까지 보여준다. 제안만 된 조건이나
등록되지 않은 값을 자동 추적이라고 부르지 않는다. `transitionVerified=true`인
도달만 확인 완료로 표시한다. 보정 비율의 추적 시각 역시 가격 시각으로 대체하지 않는다.

`test_relation_market_tracking.py`는 계산식·서머타임·잘못된 입력, 공급자 병합,
원천 시각의 추론/결과 일치, 실제 0과 미제공 구분, 참고값·전후 값·조건 등록 증명 및
최종 메시지의 계산 근거를 검증한다.

## 동일 원천 재조회와 자료 상태 분리 (2026-10-04)

`typedb-hypothesis-lifecycle-v5`는 TypeDB의 `matchedConditions.relationId`를
규칙·조건의 의미에 연결한다. 원천 assertion과 과거 derived-relation 해시를
중복 집계하지 않는다. 의미를 확인하지 못한 `unresolved-slot` 및 이전 버전
이관용 슬롯은 감사에는 남지만 강화·약화의 근거가 되지 않는다. 기존 대기
알림도 최종 공개 계약에서 이 기준으로 다시 확인한다. 실제 새 지지·반대
근거, 규칙의 성립·해제와 명시적 유효기간 종료는 계속 처리한다.

`dataAvailabilityChange`는 시장 가설의 전환과 별도로 저장한다. 자료가
부족해진 것만으로 가설을 반증하지 않고 `자료 상태` 알림으로 설명한다.
복구 알림은 마지막 성공 영수증에 해당 자료의 사용 불가 상태가 있을 때
발송하며, 이미 전달한 상태는 다시 보내지 않는다. AI 호출·기존 알림 한도나
쿨다운 설정을 추가하거나 변경하지 않는다.

닫힌 시장의 명시적인 마감 가격은 재조회 시각 대신 원천 시각을 기준으로
유효성을 확인한다. 기존 `dataFreshnessTechnicalMaxAgeMinutes`(기본 4,320분)
범위 안의 참고값으로만 사용하고, 재조회가 이 기한을 연장하지 않는다.
수집 지연은 `collectionStatus`에 별도로 남긴다. 체결·수급 자료에는 이
가격 참고 예외를 적용하지 않으며 미래·누락된 원천 시각도 인정하지 않는다.
이는 휴장 달력이나 종목별 체결 유효기간을 새로 검증한 기능은 아니다.

문서는 `이번 알림이 온 이유`에서 해당 규칙에 연결된 전후 측정값·기준값과
추가/해제된 근거를 설명한다. 비교할 수 없는 항목은 미확인으로 남긴다.
`test_hypothesis_lifecycle.py`, `test_external_api_sources.py`,
`test_typedb_independent_observation.py`가 근거 개수 진동, 실제 반대 근거,
자료 부족/복구, 성공 영수증 비교, 마감 원천 시각과 최종 문서를 검증한다.
비공개 발송 기록 5건을 로컬에서 재검사했으며 개수 차이만으로 발송됐던
관계 변화 5건은 재발송 후보에서 제외됐다. 이는 동결된 결과의 비교·발송
판정 재현이며 과거 TypeDB 전체를 다시 실행하거나 예측 성능을 검증한 것은 아니다.

## 시장 변화·재평가·자료 미확인 구분 (2026-10-10)

`typedb-hypothesis-lifecycle-v6`는 규칙의 해제와 자료의 부재를 구분한다.
정상·정렬된 TypeDB 세대에 사용한 종목별 모델 입력을 함께 저장하며,
강도 기준 아래로 내려간 모델과 미검증 평가도 비교 대상으로 보존한다.
모델 입력 영수증은 투자 판단 권한이 아니며 TypeDB의 결과를 설명하는 데만 쓴다.

- 현재 모델 근거가 없거나 사용 불가이면 이전 가설의 비교 기준을 보존하고
  `자료 확인 필요`로 안내한다. 반대 근거의 부재만으로 가설을 강화하지 않는다.
- 동일한 원천 시세에서 기간·입력 구성이 달라진 경우 `분석 재평가`로 안내한다.
  그래프의 성립 상태는 감사에 남지만 시장의 새로운 강화·약화로 표현하지 않는다.
- 현재 입력과 새 원천 관측으로 확인된 규칙 변화는 기존 관계 변화로 처리한다.
  이전 기록의 원천 비교가 불완전하면 그 한계를 명시하고 시장 변화로 단정하지 않는다.
- 가격 경로는 재조회 버킷 대신 실제 원천 시각과 거래일을 사용한다. 동일 거래일의
  최신 시세는 해당 일봉을 대체하며, 날짜가 넘어갔다고 새 거래일 표본을 만들지 않는다.
  공급자의 확정 일봉은 종가 시각과 실제 입수 시각이 모두 분석 시점 이전이면
  사용한다. 미래·미확인 원천 시각은 수집 시각으로 대체하지 않는다.

표시 버전 `readable-relation-change-v7`은 `이번 알림이 온 이유 · 규칙 데이터`에
직전 **분석**과 이번 분석의 모델 점수, RuleBox 강도 조건에 해당하는 기준,
계산 구간의 변화와 제한을 표시한다. 모델 점수는 확률이 아니다. 이 비교는
마지막 성공 **알림**과의 시세 비교와 별도이며, 현재 주가·보유 수익률·수급·체결·거래량은
`현재 전체 데이터` 이후의 기존 상세 항목에 보존한다. 종료된 가설의 식별자가
세대마다 바뀌어도 규칙 식별자로 이름을 연결한다.

기간 입력의 캐시 계약과 native 증명 버전을 갱신해 이전 압축 결과를 섞어 쓰지 않는다.
점수 산식·가중치·RuleBox 임계값·AI 호출·기존 쿨다운은 변경하지 않는다.
새 시세가 없는 날짜 경계, 자료 누락, 실제 새 관측, 재평가 후 재성립,
종목·추론 세대 격리 및 최종 Telegram 발행을 로컬 테스트로 검증한다.
이 검증은 예측 성능의 실증 검증을 의미하지 않는다.

## 같은 사건의 알림을 답글로 연결

시세(`marketObservation`), 관계 관찰(`investmentInsight`), AI 해석(`aiObservation`)은
같은 계좌·종목에서 실제로 공유한 근거가 있을 때 첫 메시지의 Telegram 답글로 이어진다.
종목 일치, 비슷한 문구, 가까운 발송 시각만으로 연결하지 않는다.

- 모니터는 시세 알림과 그 사이클에서 저장한 추론 요청 사건을 같은 트랜잭션으로 연결한다.
- 관계 분석은 사용한 요청의 원본 사건 ID를 알림까지 전달한다.
- AI 해석은 관계 관찰과 동일한 world·ABox 스냅샷을 사용했을 때 연결한다.
  이후 별도 스냅샷을 읽었더라도, 영수증이 있는 이전 AI 알림의 확인 조건이
  검증되어 전환된 경우에는 해당 이전 대화를 이어갈 수 있다.
- 여러 기존 대화의 원인이 함께 들어오면 임의로 합치지 않고 새 메시지를 만든다.
  다른 계좌·종목·world, 연결 근거가 없는 새로운 분석은 별도 메시지로 남는다.
- 각 단계는 기존 발송 정책을 통과한 경우에만 발송한다. 답글을 채우기 위해
  관찰이나 AI 판단을 생성하지 않는다. 관계 관찰·AI 설명이 매매 권한이나 검증된
  투자 판단으로 승격되는 것도 아니다.

원본 사건 별칭은 `notification_conversation_sources`, 알림 소속은
`notification_conversation_jobs`에 저장한다. 원문 Telegram 영수증은 기존
`notification_research_threads` 저장소를 공유하지만, 연구 질문의 별도 연결 규칙은
유지한다. 이 작은 연결 기록은 본문 보존 기간과 독립적으로 유지된다.
원문 대기·동시 발송 직렬화·부분 전송 재시도·수신처 격리도 연구 답글과 동일한
영수증 기반 전송 경로를 사용한다. 원문 발송이 영수증 없이 최종 실패하면 다음 실제
발송 메시지가 새 앵커가 될 수 있다. Telegram에서 사용자가 원문을 삭제한 경우에는
API의 `allow_sending_without_reply` 설정으로 단독 발송을 허용하며, 삭제된 원문의
복구나 자동 재연결을 보장하지 않는다. 이미 발송한 메시지는 재발송하거나 수정하지 않는다.
표시 시각은 기존 한국 시간(KST) 규칙을 그대로 따른다.

검증: `test_investment_conversations`, `test_research_replies`,
`test_notification_delivery_reliability`. Telegram 전송 테스트는 가짜 HTTP 영수증을
사용하며 실제 고객에게 테스트 알림을 보내지 않는다.

## 새 원천 자료가 있을 때만 판단 갱신 (2026-10-11)

사용자 승인으로 가격·수급 기반 가설은 `hypothesis-source-refresh-v1`을 적용한다.
휴장 또는 같은 자료의 재조회만으로 기존 근거를 해제하거나 가설을 강화·약화·만료시키지 않는다.
TypeDB 실행 기록과 자료 진단은 계속 남기되, lifecycle의 마지막 판단·원천 근거·판단 시각을
보존한다. `judgementRefresh`와 `lastJudgedAt`이 현재 판단과 마지막 확인 기록을 구분한다.
이는 10월 10일의 재평가 알림 허용 정책을 대체한다. 기존 큐에 남은 `reassessment`도
공개 발송 계약에서 `deliverable=false`로 재검증한다.

- 가격 규칙은 원천 시세 시각, 수급 규칙은 해당 수급 원천 시각을 비교한다. 가격 갱신으로
  오래된 수급을 갱신됐다고 보지 않는다. 수집 시각·새 스냅샷 ID·새 점수는 갱신 증거가 아니다.
- 해당 가설의 필요한 자료가 모두 사용 가능하고 새로운 원천 관측이 있으면 판단을 재개한다.
  첫 휴장 스냅샷은 참고 기준만 저장하고, 최초 새 거래 자료에서 처음 판단한다.
- 휴장 중 갱신 없음은 조용히 대기한다. 장중 자료 사용 불가는 별도의 자료 상태 전환으로만
  전달하며, 같은 상태의 반복 전송은 기존 영수증·쿨다운 계약을 따른다.
  정상적인 장 재개만으로 자료 복구 알림을 만들지 않으며, 실제 사용 불가 상태를 알렸던
  경우에만 복구 전환을 전달한다. 휴장 대기는 이 장애 상태의 알림 기준도 보존한다.
- 공시·뉴스·재무 등 다른 모델 계열은 그 원천 계약에 따라 계속 분석한다. 전체 추론이나
  독립 AI를 장 시간으로 일괄 중지하는 변경은 아니다.
- 같은 시세 원천 시각의 반복 행은 가격 관측 한 건이다. 반복 조회는 표본 수·거래일 수를
  늘리지 않는다. 별도 시각의 수급 행은 유지한다. 명시적으로 원천 시각이 없거나 수신보다
  미래인 값은 가격 경로를 만들지 않는다.
- 기간 규칙의 표본·유효성 조건 실패는 `untested`로 기록한다. 실제 가격 조건 미충족인
  `not-supported`와 구분하며, 자료 부족을 반대 근거 해제로 표현하지 않는다.

버전: lifecycle v7, transition v4, 그래프 조립 캐시 v23, native 해석 v9,
모델 계약 평가 v6. 기존 RuleBox 투자 임계치·점수 가중치·AI 경로는 바꾸지 않는다.
`test_hypothesis_lifecycle`, `test_statistical_signals`, `test_ontology_rulebox`,
`test_typedb_independent_observation`은 휴장 반복·자료 누락·새 시세 재개·수급 독립 시각·
공시 분석 지속·대기 재평가 알림 제외를 검증한다.

### 자료 상태 알림의 최종 발송 사유 검증 (2026-10-12)

`customer-delivery-explanation-v3`는 TypeDB 자료 사용 불가·복구 사유를 작성할 때와
검증할 때 같은 관계 관찰 계약을 사용한다. 가설 판단이 유지되는 자료 상태 변화에
별도의 투자 판단 변경 플래그를 요구하지 않는다. 대신 검증된 발행물, 추론 세대,
마지막 성공 영수증 기준 전환에서 사유를 다시 만들고 전후 값·시각·전환 ID가
설명과 일치하는지 확인한다. 다른 투자 판단의 변경 플래그로 이 검사를 우회할 수 없다.

자료 복구는 앞서 사용 불가 상태를 실제 전달한 기준이 있어야 하며, 같은 상태·전환의
재알림은 계속 차단한다. 과거 억제 기록과 성공 발송 본문은 수정하거나 재발송하지 않는다.
회귀 검증은 자료 상태 입력부터 발행·최종 발송 사유 검사까지 가짜 큐로 실행한다.

## 원본 알림의 후속 안내 (2026-10-13 사용자 승인)

`relation-follow-up-v1`은 자료 중단·복구를 새 투자 분석처럼 보내던 정책을 대체한다.
TypeDB 가설·투자 임계값·AI 호출은 바꾸지 않고, 이미 설명한 가설에 영향을 주는
후속 안내만 원본 메시지의 답글로 전달한다.

- 원천 자료를 특정할 수 있는 중단에 한해 같은 계정·종목·world·lifecycle의
  성공 발송 원본을 확인한다. 최초부터 자료가 없거나 원본이 없으면 웹에 기록한다.
  원천 요구사항은 해당 가설의 정책·모델 입력·갱신 평가에서 읽는다. 다른 가설의
  시세·수급 문제를 가져오거나 `model-signal` 미확인만으로 푸시하지 않는다.
- 휴장에 따른 정상 대기와 RuleBox 유효기간만 지난 전환은 발송하지 않는다.
  기존 lifecycle 감사와 투자 판단은 이 발송 정책에서 수정하지 않는다.
- 중단은 한 번만 안내한다. 복구는 해당 중단을 실제 알렸을 때만 보낸다.
  다른 알림의 전체 스냅샷에 사용 불가 상태가 포함됐다는 이유만으로 복구를
  발송하지 않는다. 실패·억제·대기 작업은 성공 기준을 갱신하지 않는다.
- 성공 영수증의 `hypothesisContinuity`가 가설별 원본·마지막 전달 상태를 최대
  64개 보존한다. 다른 가설의 알림이 끼어들어도 같은 종목의 마지막 메시지로
  원본을 바꾸지 않는다. 보존 범위를 벗어나 원본 증명이 없으면 임의 복원하지 않는다.
- 가설의 강화·약화·반증도 확인된 같은 원본에 답글로 연결한다. 서로 다른 원본이
  섞인 중요한 관계 변화는 독립 메시지로 보내며, 연결이 모호한 자료 안내는 웹에
  남긴다. 답글도 기존 수신 설정·쿨다운·성공 영수증 검증을 통과해야 한다.
- Telegram은 시세 메시지로 시작한 대화에서도 **가설을 설명한 메시지 자체**를
  답글 대상으로 사용한다. 수신처별 성공 영수증으로 메시지 번호를 읽어 본문 삭제
  이후에도 연결한다. 다른 수신처에 원본 영수증이 없으면 자료 답글은 발송하지 않는다.
  사용자가 Telegram 원문을 삭제한 경우의 단독 발송 허용은 기존 전송 계약과 같다.
- 부분 전송을 시작한 본문·근거·답글 대상은 재시도 중 변경하지 않는다.
  이미 발송한 알림은 수정·재발송하지 않는다. 이전 버전 자료 안내만으로 가설의
  원본을 추정하지 않으며, 기존 관계 영수증은 동일 scope가 확인될 때만 이관한다.

`readable-relation-change-v8`은 관계 변화의 결론을 현재가보다 먼저 보여준다.
직접 연결된 조건의 이전 값 → 이번 값·기준·원천 시각을 발송 이유의 앞에 둔다.
체결·수급의 기준 시각을 가격 시각으로 대체하지 않는다. 이번에 달라진 가설을
설명하고, 현재 주가·수익률·수급·체결·거래량·재무와 전체 근거는 별도로 보존한다.
과거 측정값이 없는 경우에는 그 비교가 저장되지 않았다고 명시한다.

자료 답글은 `시세 확인 중단`, `체결·수급 확인 재개`처럼 대상과 사건을 제목에
쓴다. 원본 번호, 영향받는 가설, 어떤 원천을 사용할 수 없는지, 원천 시각과 확인
시각, 그 두 시각으로 계산한 경과 시간과 원래의 허용 시간을 보여준다. 원천 시각이
없거나 미래이면 경과 시간을 만들어내지 않는다. 새 가격 악화나 가설 강화로
표현하지 않으며, 전체 시세·보유·재무·원문 링크를 반복해 붙이지 않는다.
복구 답글에는 복구 원천과 동일한 기준 시각의 실제 관측값만 최대 4개 붙인다.
체결강도·수급의 관측 필드와 사용 가능 상태를 확인하며, 다른 시각의 참고값이나
기본값을 새로 복구된 자료로 표시하지 않는다. 강화·약화 전환이 없는 복구는 그 사실도 명시한다.

검증: `test_relation_followups`, `test_typedb_independent_observation`,
`test_relation_observation_narrative`, `test_investment_conversations`.
실제 테스트 DB 영수증과 가짜 Telegram 응답으로 원본 선택·수신처 격리·본문 보존
기간 이후 연결·실패 영수증 제외를 확인한다. 고객에게 테스트 메시지를 보내지 않는다.

## 규칙별 판단 보류 사유 (2026-10-14)

`hypothesis-data-readiness-v1`은 자료를 사용할 수 없는 이유를 해당 규칙과 필수
입력에 연결한다. 원천 자료 지연·누락, 모델 평가 미확인, 필수 조건의 실제 관측값
연결 실패, 같은 원천 자료 재조회, 휴장 대기를 구분한다. 규칙 ID와 표시 이름,
조건 ID, 원천 시각·허용 경과 시간·출처, 다음 확인 내용을 함께 보존한다.
입력을 모두 확인한 `not-supported`는 실제 조건 미충족이며 자료 보류와 다르다.

- 규칙이 요구한 자료만 평가한다. 정상 가격 자료는 수급 자료를 대신하지 않고,
  수급 자료의 문제도 별개의 가격·뉴스 가설 전체를 중단시키지 않는다.
  여러 입력을 함께 요구하는 한 가설은 그 필수 입력이 모두 확인돼야 갱신한다.
  과거 묶음의 요구 자료를 개별 규칙에 연결할 수 없으면 가설 공통 제한으로
  기록하며, 다른 규칙의 정상 기록으로 이를 해제하지 않는다.
- 보류 중에는 마지막 판단과 판단 기준 시각을 유지한다. 이번 확인 시각과
  실패 원인은 `judgementRefresh`, `dataAvailability`에 따로 저장한다.
  현재 입력의 실패를 과거 판단 근거에 덮어쓰거나 조건 해제·가설 강화로
  처리하지 않는다. 공통 대기 문장으로 실제 TypeDB 근거 제한을 지우지 않는다.
- 발송되지 않은 작업도 실제 보류·후속 안내 제외 사유를 남긴다. 이를 단순한
  “관계 변화 없음”이나 쿨다운 사유로 바꾸지 않는다. 쿨다운 자체는 유지한다.
- 자료 중단 답글은 저장된 해당 규칙의 원천 문제만 설명한다. 모델 평가가
  없다는 이유로 시세·수급 수집 장애를 주장하지 않는다. 답글에는 영향받는
  규칙과 재확인할 자료를 덧붙이며 기존 성공 원본·수신처·중복 정책을 따른다.
  휴장·동일 자료 재조회와 원인이 특정되지 않은 보류는 새 푸시로 보내지 않는다.

회귀 검증: 독립 가격·수급 가설의 연속 처리, 필수 관측값 미연결, 모델 누락과
실제 조건 미충족의 구분, 원천 시각 유지, 원본 답글 연결, 최종 발송 사유 보존.

## 2026-10-15: 알림 목적과 발송 사건 계약

관계 상태 이름만으로 푸시를 만들지 않는다. 저장된 RuleBox 가설 계약과
TypeDB가 확인한 변화가 연결돼야 `rule-notification-event-v1` 사건을 만든다.
사건에는 대상 가설, 알림 목적, 지지·반대 근거의 추가·해제, 규칙의 실제
측정값 비교, 이전 성공 발송 대비 관리 제한·검토 후보 변화, 후속 확인 기록,
원본 알림 식별자를 보존한다. 같은 시세를 다시 읽거나 세대·조회 시각만 바뀐
것은 새 의미가 아니다. 기존 휴장·원천 갱신·자료 답글·쿨다운 계약은 유지한다.

목적별로 확인할 판단을 구분한다.

| 목적 | 고객이 확인할 내용 | 권한 |
| --- | --- | --- |
| 가설 근거 변화 | 어느 가설의 지지 또는 반대 근거가 달라졌는지 | 가설 재점검, 매매 확정 아님 |
| 관리 기준 변화 | 설정한 손실·비중 등의 제한이 적용되거나 바뀌었는지 | TypeDB가 이미 도출한 제한만 표시 |
| 거래 제약 변화 | 거래 검토의 제약이 어떻게 달라졌는지 | 거래 실행 권한 없음 |
| 참고 근거 변화 | 해당 설명에 연결된 새로운 사실 | 참고 |
| 자료 중단·복구 | 앞서 보낸 가설을 계속 확인할 수 있는지 | 기존 원본에 답글 |
| 등록된 결과 확인 | 원본에서 안내한 확인 구간의 조건 충족·반박·불확정 | 해당 구간 결과, 인과 검증·매매 확정 아님 |

예를 들어 반등 실패 가설에 상대 강세가 반대 근거로 추가되면 첫 문장은
“반등 실패 가능성에 반대되는 근거가 추가돼 기존 가설을 다시 점검합니다.”가
된다. 이때 관리 제한과 검토 후보가 이전 알림과 같으면 그 사실도 표시한다.
규칙 비교의 첫 번째 행을 제목으로 선택해 상대 강세를 새로운 주가 전망처럼
보여주지 않는다. 해제된 규칙의 이름이 ID로만 남아 있어도 빈 따옴표를 만들지
않고 연결된 가설의 의미로 설명한다.

### RuleBox 설정과 기존 배포

`RuleClaimContract.notificationIntent`는 선택적 구조 필드다. 예시는 다음과 같다.

```json
{
  "version": "rule-notification-intent-v1",
  "purpose": "hypothesis-evidence",
  "customerQuestion": "반등 실패 가설을 지지하거나 반박하는 근거가 달라졌나요?",
  "notifyOn": ["observed", "reactivated", "support-added", "support-removed", "counter-added", "counter-removed", "mixed-evidence", "invalidated", "outcome-confirmed", "outcome-contradicted", "outcome-inconclusive"],
  "comparisonBasis": "last-successful-delivery",
  "nextCheckSource": "rule-claim-contract"
}
```

목적은 claimType의 권한과 일치해야 한다. `internal-only`는 고객 발송 대상에서
제외한다. 잘못된 목적·형식·변화 유형은 RuleBox 계약 검증에서 거절한다.
임계값과 결과 조건은 기존 규칙·outcomeContract가 소유하며 알림 Python 코드에
새 투자 기준이나 매수·매도 선택을 추가하지 않는다. RuleBox 화면에서 목적을
확인하고 원본 JSON으로 설정할 수 있다. 수정된 규칙은 기존 배포 검증 절차를
따라 활성화해야 한다.

기존 활성 배포를 다시 저장하거나 현재 카탈로그로 보충하지 않는다. 기존의
완전한 frozen claim에만 `frozen-rule-claim-adapter-v1` 읽기 변환을 적용한다.
명시적 설정이 없는 계약의 직렬화 결과와 배포 해시는 유지한다. 가설 계약이나
변화와의 연결이 없으면 고객 사건을 만들지 않고 상세 이력에 원인을 남긴다.

### 성공 발송, 원본과 후속 결과

- `notificationIntentContinuity`는 실제 성공 발송 영수증에만 누적한다. 실패한
  전송·초안은 비교 기준이 아니다. 계약 개정과 계좌·종목·world를 구분한다.
- 같은 사안의 의미가 반복되면 푸시를 반복하지 않는다. 지지·반대 역할이
  달라지면 관리 제한이 같더라도 새 정보로 전달한다. 관리 기준은 현재 값의
  작은 이동이 아니라 적용 상태 또는 저장된 기준 변경을 비교한다.
- 관리 제한의 해제는 현재 세대의 신선한 TypeDB 불성립 근거가 있어야 한다.
  누락된 규칙·오래된 자료·결과 미수신은 해제 근거가 아니다.
- 후속 확인은 outcomes 저장소의 실제 등록 episode와 연결한다. 자유 문장만
  있는 다음 조건을 자동 추적 중으로 표시하지 않는다. 등록 기록이 없으면
  그 사실을 구체적으로 표시한다.
- 기존 결과 관측 worker가 평가한 결과를 후속 추론 문맥에서 읽는다. 알림
  렌더러가 가격을 다시 조회하거나 결과를 재평가하지 않는다. 성공 원본에
  등록된 episode·outcome 계약 fingerprint와 일치하고 데이터·관측 시간 검증을
  통과한 결과만 원본 답글로 보낸다. 이미 보낸 결과는 다시 보내지 않는다.
  자료 부족·지연 결과는 성공 또는 반증으로 둔갑시키지 않는다. 여러 원본의
  결과는 임의로 합쳐 새 메시지를 만들지 않는다.
- 결과 알림은 기존 후속 추론·발송 주기에서 처리된다. 확인 구간 도달 순간의
  즉시 푸시나 거래 실행을 보장하는 별도 스케줄러를 추가한 것은 아니다.

고객 문서 품질 검사는 목적·판단 영향·후속 확인 섹션과 비어 있는 규칙명을
검사한다. 원본 메시지 바이트가 동결된 부분 전송 재시도는 그대로 유지한다.
회귀 검증은 `test_rule_notification_intent`, 독립 TypeDB 발송·판단 설명·발송
사유 테스트와 웹 알림 상세 테스트에 포함한다. 실제 발송 이력 재생은 전송
없이 저장된 원천 시각과 이전 성공 영수증을 사용한다.

### 발송 전 계좌·종목 전달과 실운영 검증

추론 이벤트의 계좌·종목은 `AlertEvent`가 소유한다. 알림 ingress에서 문맥에
복사되기 전에 실행되는 직접 발행·단계 관찰 검사에도 이 식별자를 전달한다.
원본 `SubjectDecisionCase`와 이벤트의 계좌·종목을 먼저 비교하고, 문맥에 이미
다른 식별자가 있으면 덮어쓰지 않고 거절한다. 같은 계좌 정보가 metadata에
중복 저장되지 않았다는 이유만으로 정상 이벤트를 원본 불일치로 분류하지 않는다.

2026-10-15 재시작 후 운영 확인에서 이 전달 순서의 누락을 재현하고 수정했다.
회귀 검증은 계좌 중복 필드가 없는 실제 이벤트 형태의 직접 발행과 단계 관찰,
다른 계좌·종목 및 누락된 이벤트 식별자 거절을 포함한다. 과거 발송 메시지
재생, 실행 중인 프로세스 확인, 새 코드로 생성된 실제 알림의 전송 확인은
서로 다른 검증 결과로 보고한다. 과거 메시지 재생만으로 실운영 전송까지
확인했다고 간주하지 않는다.

### 계정 정책의 관리 대응 결정 (2026-10-15)

`ActionabilityAssessment` / `RiskInvariantClaim`의 비예측 의미를 사용한다.
`graph.position.concentration.guard.v1`과
`graph.instrument_profile.averaging_down_policy.v1`의 v2 파생 계약은
`management-response-v1`을 소유한다. TypeDB의 직접 조건 판정이 이 계약을
InferenceBox에 물질화할 때만 `RESTORE_POSITION_LIMIT`(비중 상한 복원),
`PAUSE_AVERAGING_DOWN`(손실 구간 추가매수 중단)이 발생한다. 렌더러에 정책
조건이나 종목별 행동 매핑을 추가하지 않는다. 같은 규칙의 기존 ADD 차단과
경쟁 가설의 실행 자격은 유지한다.

`management-decision-v1`은 계좌·종목·world·ABox·추론 세대를 확인하고, 해당
규칙의 신선하고 사용 가능한 근거만 받아들인다. `DecisionSynthesis` v9와
`SubjectDecisionCase.synthesis`에 저장한 뒤 compact subject context, decision
brief, 고객 문서로 전달한다. 원천 값만 있거나 대상 정책값을 보존하지 못하면
대응을 새로 만들지 않는다. 가격 전망 가설이 아직 미검증이어도 독립적으로
확인된 계정 정책의 대응은 사라지지 않는다. 관리 대응이 가격 전망의 최종
TRIM/HOLD 판단이나 주문 허가를 대신하지는 않는다.

비중 상한 복원의 산술 시나리오는 현재 계좌 비중 w, 상한 t일 때
`보유분 조정 비율=(w-t)/w`, `상한 초과=w-t %p`,
`매도 없이 필요한 외부 입금/현재 계좌자산=w/t-1`이다. 해당 세대에 실제
보유 수량이 있을 때만 등가 수량을 표시한다. 가격·환율 고정, 매도 대금의
계좌 잔류, 세금·비용 제외라는 가정을 명시하며 거래 단위/매도 가능 수량을
확인하지 않은 등가 수량을 주문 수량으로 반올림하거나 전송하지 않는다.

대응 코드·목표·해제 계약의 변경은 의미 있는 정책 변화로 알린다. 같은 목표가
계속 적용되는 동안 시세나 산술 수량만 바뀐 경우에는 새 정책 알림을 만들지
않는다. 자료가 오래됐거나 규칙이 사라진 것만으로 기존 대응을 해제하지
않으며, 기존 신선한 불성립 증거에 따른 해제 경로를 유지한다.

이 변경은 네이티브 엔진 v10과 새 불변 RuleBox 릴리스에서 사용한다. 기존
배포의 정적 artifact를 제자리에서 수정하지 않는다. 과거 artifact에는 대응
필드를 주입하지 않는다. 검증: `NativeManagementResponseTests`와 기존
RuleBox·독립 TypeDB·알림 목적 회귀, 네이티브 후보 실행 및 배포 게이트.

## Native composition and exact source receipts (2026-10-20)

`rule-source-measurements-v1` preserves each market contract's own matched
predicates, numeric values, thresholds, comparison asset/period and source
clock. A family signal's union of evidence IDs must not be attributed to every
contract. Receipts use the model packet's canonical numeric precision (eight
decimal places), retain day-only macro dates without inventing a time, and do
not borrow quote clocks for stock-level flow fields. Legacy missing receipts
stay missing; notification rendering must not fetch current data to fill them.

The V17 catalog adds three non-predictive composition rules using existing `NextCheck` assertions:
confirmed support and risk conditions together, support with the account-weight
ceiling, and support with the averaging-down restriction. They execute native
TypeQL predicates and persist `compositionResponse` in InferenceBox. The two
policy combinations require the original policy's independent native response.
These rules neither qualify predictions nor authorize actions. Unapproved flow
models remain disabled; showing an investor-flow number is distinct from using
it in an approved predictive rule.

`native-response-plan-v2` saves the scoped `native-evidence-composition-v1`
read model with exact model receipts, native combinations, shared observation
links and differing horizons. Duplicate/reordered rules do not create more
independent evidence. Unknown independence remains unknown. Existing individual
investment predicates, account targets and execution qualification are retained.

`compact-rulebox-v4` shows actual source measurements before unrelated account
reference values. It keeps market conditions and policy restrictions visible
together, names the comparison asset and period, and uses only compatible
previous receipts for before/after numbers. Within the compact measurement
budget, a rule's distinguishing input precedes confirmation values shared with
other rules. Active policy release targets remain in the next check even when
market evidence has no matching composition rule; a changed hypothesis keeps
its own authored follow-up alongside those targets. A new combination is released in a
separate immutable candidate database and must pass paired-input promotion
checks; updating Python alone does not deploy new RuleBox predicates.

Validation: `test_rule_evidence_composition` covers original scorer-to-ABox
receipts, altered/future/wrong-scope proof, duplicate and horizon treatment,
native composition authority, exact thresholds/benchmarks and missing history.
V17 compiler fixtures retain the pre-existing rule predicate hashes separately.
The TBox is unchanged: these rules do not introduce a new kind of market fact.
Release preflight checks every permitted model release in a composition predicate.

The additive release builder copies the active release artifact, appends
only three composition rules, and preserves every original rule, static
node and TBox metadata value. `native-composition-migration-review-v1` retains the raw paired replay
comparison. It explains only references to these three declared context rules,
after verifying immutable artifacts, identical source inputs, 100% fact/rule
coverage, zero shadow delivery and unchanged investment actions, policy targets
and qualification. It rejects edits to existing rules, static ontology or
undeclared actions. Such a review is not an exception to freshness or empirical
qualification and cannot approve an investment action.

Runtime validation of the initial candidate exposed that adding a dedicated
composition class also changes lifecycle TBox metadata stamped on every ABox
fact. V17 uses the existing NextCheck vocabulary and requires an identical
TBox. Input-parity checks remain strict, including retained source dependencies;
no incomparable replay counts as successful promotion evidence.

The subsequent production comparison still failed input equality: portfolio
lifecycle enrichment reads the latest persisted risk/exposure records at
execution time, while the paired market snapshot is frozen. The active graph
also retained subject evidence scopes absent from the candidate. These are
separate rollout blockers, not authorized composition differences. Before
promoting the three new rules, capture the portfolio context at the source
boundary and establish identical, complete subject dependency sets in both
databases. Preserve the raw failed comparison; do not count matching displayed
prices or management targets as proof of identical inference inputs.

The follow-up input contract `paired-frozen-portfolio-dependencies-v2` captures
the portfolio lifecycle once at the market source cutoff and binds its compressed
receipt to the account, source snapshot and request fingerprint. Both independent
jobs consume that receipt; temporal backend reads and native rule evaluation
remain independent. Live lifecycle reads now exclude observations and mutable
heads written after their market cutoff. Unavailable historical heads remain
unavailable. Complete paired subject snapshots retire absent subject scopes
only after reference-safety checks; unrelated/shared omissions retain their
previous generations. The existing strict input-parity gate is unchanged.
