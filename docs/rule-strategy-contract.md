# RuleBox 단기 전략 연구 계약

`rule-strategy-contract-v1`은 개별 규칙을 관찰·진입 검토·진입 판단 취소·보유 종료 검토의 네 단계로 묶는 동결 계약이다. 최대 보유 기간은 1~5 거래 세션, 신호 유효 기간은 30~3,600초다. 숫자는 계약 범위이며 추천 매매 설정이나 검증된 최적값이 아니다.

## 소유권과 실행

- 시장 데이터 모듈은 원천 시각과 측정값을 제공한다. 통계 모델은 정확한 가설 계약에 대한 근거만 생성한다.
- RuleBox의 `RuleClaimContract.strategyContract`가 전략 정의의 원본이다. 계약 지문, 원본 RuleBox 지문, 참조 모델의 규칙 지문·주장·실행 조건을 함께 보존한다.
- 설계·검증은 전역 기본 DB가 아닌 알림 담당 배포의 `graphStoreBinding`을 조회한다. 배포 ID와 DB 연결 식별자도 계약에 동결한다. 담당 배포가 달라지면 기존 제안을 다른 배포에서 조용히 검증하지 않고 `source-deployment-changed`를 반환한다. 배포 연결이 없으면 기본 DB로 대체하지 않는다.
- 등록되어 활성화된 정확한 `HAS_MODEL_SIGNAL` 모델 중 장중 원천 시계가 구현된 가격·수급 릴리스만 선택한다. 비활성·미등록·장중 시계 미지원 모델은 설계 카탈로그에서 제외 사유와 함께 표시한다. 현재 원천 시계가 없는 기업·이벤트·시장 간 모델을 장중 조건으로 가장하지 않는다. 수급 전용 모델이 없으면 가격 모델을 수급 모델이라고 대체하지 않는다.
- 각 단계의 모델 조건은 AND 결합이다. 모델 소유자가 실제 충족된 가설에만 생성하는 정확한 계약 노드를 사용하며, TypeDB가 시장·정규장·기존 적격성·원천 경과 시간을 검사한다. JSON 표시용 필드를 TypeDB 조건이라고 간주하지 않으며 전체 조건의 native profile은 ready여야 한다. 네 개의 비활성 후보 규칙으로 컴파일하며, RuleBox 구조 검증은 일부 단계만 활성화하는 것을 거부한다. 컴파일러 버전도 계약 지문에 포함한다.
- 시장 범위는 기존 `TRADED_IN` 관계와 Market의 `ontology-id`로 확인한다. 물리 Stock 스키마에 없는 `market` 속성을 조회하지 않는다. 현재 ABox에서 시장 관계를 읽을 수 없으면 해당 단계는 미평가다. 배포 전 시장 관계와 모델 근거를 포함한 실제 ABox 검증이 필요하다.
- `Strategy` TBox 개념과 `VALIDATES_STRATEGY` 관계를 재사용한다. 각 단계는 예측 가설이 아닌 참고용 조건 관측이다. Python과 알림 렌더러는 투자 행동을 선택하지 않는다.
- 판단 기록의 `strategyObservations`는 동일 세계·ABox·추론 세대에서 TypeDB가 확인한 단계와 수치를 보존한다. 확인되지 않은 단계는 unknown이며, 진입과 취소/종료 조건이 동시에 확인되면 conflicting-conditions다.

## 작성·확인 경로

1. `GET /api/investment-strategy-proposals/design-catalog`에서 현재 RuleBox와 사용 가능한 모델 ID, 제외 사유를 읽는다.
2. `POST /api/investment-strategy-proposals/design`에 title, symbols, sourceRuleboxFingerprint와 strategyDesign을 제출한다.
3. strategyDesign은 market(KR/US), maximumHoldingSessions, signalValiditySeconds, phases(setup/entry/invalidation/exit의 모델 ID 배열)를 모두 포함한다. 원본 RuleBox가 바뀌면 제출을 거부한다.
4. 기존 전략 제안 상세 API에서 동결된 전체 계약을 확인하고, `/validate`로 네 단계의 현재 TypeDB 조건을 함께 검증한다. `strategyPhaseEvaluation`이 각 단계의 실제 쿼리 실행 여부와 누락 사유를 구분한다. 근거가 없어 쿼리가 생략된 경우 `not-evaluated`이며 조건 불충족이나 검증 성공으로 해석하지 않는다. 운영 RuleBox/InferenceBox는 수정하지 않는다.
5. 중앙 AI의 기존 RuleChangeCandidateProposalService에 trigger=short-term-strategy-design을 전달하면 같은 선택 계약으로 제안한다. 현재 추론 완료 여부에 종속되지 않으며 무거운 InferenceBox 조회도 하지 않는다. AI가 실행 규칙·새 임계값·성공 확률을 작성할 수 없다. 네 단계 묶음은 전략 제안 저장소에 보존하며, 진입 규칙 하나를 일반 후보로 배포하지 않는다. 일반 가설 연구와 별도 요청이다. 이 기능을 주기적으로 호출하는 새 스케줄러는 추가하지 않았다.

재현 가능한 네이티브 검증: `PYTHONPATH=python_service:python_service/tests python3 python_service/tests/native_strategy_smoke.py`. 임시 TypeDB에서 정상·오래된 자료·장외·부적격·다른 릴리스·진입 근거 일부 누락·다른 시장 7개 사례의 28개 실제 쿼리를 실행하고 임시 DB를 삭제한다. 운영 그래프와 알림을 변경하지 않는다.

## V1의 사용 범위

V1은 연구용 계약과 조건 판정 경로다. 현재 자료에서 조건이 맞았다고 매매 전략이 검증된 것은 아니다. 구조 검증만으로 전략 상태를 validated/approved로 승격하지 않으며 forceApproved도 매매 권한을 만들지 않는다. 자동 주문과 고객 매매 지시는 생성하지 않는다.

운영 적용에는 시장 관계가 현재 ABox에 연결되고 모델 근거의 `marketSession`, `sourceAgeSeconds`가 해당 배포의 물리 TypeDB 스키마와 입력에 제공되어야 한다. 컴파일러의 `nativeReasoningProfile=ready`는 문법·필드 매핑 지원을 뜻하며 배포 스키마·자료 준비 완료를 뜻하지 않는다. 공유 지식의 시장 관계를 가져오는 경로와 필요한 시각 속성은 후보 배포에서 함께 검증해야 한다. 현재 배포에 직접 스키마나 사실을 덧붙여 검증을 통과시키지 않는다.

거래 세션 기준 최대 보유 기간은 검증 목표로 저장한다. 실제 진입 영수증부터 거래 세션을 세는 종료 시계, 체결 가능 가격, 수수료·세금·스프레드·슬리피지, 독립 표본의 순수익, 시장 국면별 비교, 미래 모의거래, 발송 지연 검증이 남아 있다. 원래 가설의 outcomeContract는 모델 출처로 보존하며, 이를 전략 전체의 순수익 검증으로 대체하지 않는다. 이 경로들을 구현·검증한 후 별도 계약 버전과 배포 검토를 통해 매매 권한을 도입해야 한다.

기존 운영 RuleBox와 기존 알림 영수증은 다시 쓰지 않는다. 소스 코드 추가만으로 실행 중인 불변 배포에 새 전략을 삽입하지 않는다.
