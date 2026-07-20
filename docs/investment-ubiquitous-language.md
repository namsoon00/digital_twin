# 투자 보편언어

이 문서는 TypeDB, AI 판단, 알림, 웹 화면이 같은 투자 개념을 같은 말로 표현하기 위한 기준이다. 내부 식별자는 TypeDB 조회와 규칙 연결에만 사용하고, 사용자 화면에는 TBox의 한국어 도메인 용어를 표시한다.

## 표현 원칙

- `PlatformGrowth`, `growth` 같은 내부 식별자를 알림과 AI 최종 문장에 노출하지 않는다.
- 내부 식별자는 바꾸지 않는다. TBox 한국어 이름과 함께 저장해 규칙과 과거 데이터의 연결을 유지한다.
- `타입`, `프록시`, `인컴`, `팩터`처럼 초보자가 뜻을 바로 알기 어려운 말은 문맥에 맞는 완전한 한국어 표현으로 바꾼다.
- 종목 분류는 **종목 성격**, 계좌에서의 목적은 **계좌에서의 역할**, 행동 제한은 **관리 원칙**으로 부른다.
- 점수는 상승·하락 확률이 아니라 **확인 필요 점수**로 표현한다.

## 종목 성격

| 내부 ID | 사용자에게 보이는 보편언어 |
| --- | --- |
| `AIGrowth` | 인공지능 성장주 |
| `BitcoinProxy` | 비트코인 가격에 민감한 주식 |
| `BitcoinSensitiveIncome` | 비트코인에 민감한 배당형 종목 |
| `CryptoAssetProfile` | 가상자산 |
| `CrossListedSecurity` | 해외 교차상장 종목 |
| `CyclicalGrowth` | 경기 흐름에 민감한 성장주 |
| `DailyLeveragedProduct` | 매일 수익률이 재조정되는 레버리지 상품 |
| `EquityGeneral` | 일반 주식 |
| `HighVolatilityGrowth` | 가격 변동이 큰 성장주 |
| `InverseProduct` | 가격 하락에 투자하는 상품 |
| `MegaCapQuality` | 대형 우량주 |
| `PlatformGrowth` | 플랫폼 성장주 |
| `PreferredIncome` | 배당 중심 우선주 |
| `SemiconductorCyclical` | 반도체 업황 민감주 |
| `SemiconductorHBM` | HBM 반도체 성장주 |
| `SingleStockETF` | 단일종목 ETF |
| `BroadMarketProxy` | 전체 시장 흐름 지표 |
| `CommodityProxy` | 원자재 가격 흐름 지표 |
| `CreditStressProxy` | 신용시장 불안 지표 |
| `CryptoLiquidityProxy` | 가상자산 거래 활력 지표 |
| `CurrencyProxy` | 환율과 달러 흐름 지표 |
| `DurationProxy` | 채권 가격의 금리 민감도 지표 |
| `ForeignMarketProxy` | 해외 시장 흐름 지표 |
| `GrowthMarketProxy` | 성장주 시장 흐름 지표 |
| `IPOCycleProxy` | 신규 상장 시장 흐름 지표 |
| `KoreaMarketProxy` | 한국 시장 흐름 지표 |
| `MarketProxyInstrument` | 시장 흐름 확인용 종목 |
| `RateSensitivityProxy` | 금리 변화 영향 지표 |
| `RiskAppetiteProxy` | 위험자산 선호 지표 |
| `RiskOffProxy` | 안전자산 선호 지표 |
| `SectorCycleProxy` | 업종 경기 흐름 지표 |
| `SmallCapRiskProxy` | 소형주 투자심리 지표 |
| `VolatilityProxy` | 시장 변동성 지표 |

## 계좌에서의 역할

| 내부 ID | 짧은 이름 | 알림 문장 |
| --- | --- | --- |
| `core` | 핵심 보유 | 계좌에서는 오래 가져갈 핵심 보유 종목으로 관리합니다. |
| `growth` | 성장 투자 | 계좌에서는 성장 가능성을 보고 투자하는 종목으로 관리합니다. |
| `trading` | 기회 대응 | 계좌에서는 가격 변화에 맞춰 비중을 조절하는 종목으로 관리합니다. |
| `income` | 배당·현금흐름 | 계좌에서는 배당과 현금흐름을 기대하는 종목으로 관리합니다. |
| `market-signal` | 시장 흐름 확인 | 계좌에서는 직접 매매보다 시장 흐름을 확인하는 지표로 사용합니다. |

## 판단과 데이터

| 사용할 말 | 뜻 | 피할 표현 |
| --- | --- | --- |
| 권장 대응 | 지금 검토할 행동 | 액션, action |
| 종목 성격 | 종목이 어떤 방식으로 움직이고 어떤 위험을 갖는지 나타내는 분류 | 타입, archetype |
| 계좌에서의 역할 | 이 계좌에서 종목을 보유하는 목적 | position intent, growth, core |
| 관리 원칙 | 추가매수, 비중 축소, 물타기 제한 기준 | policy |
| 확인 필요 점수 | 지금 관계와 근거를 다시 볼 필요의 강도 | 예측 확률, 매도 확률 |
| 시장 요인 민감도 | 금리, 환율, 경기, 가상자산 같은 변화의 영향 | 팩터 민감도 |
| 가격 흐름 | 5일·20일·60일 평균 가격과 현재가의 관계 | 추세 훼손, 기준선 이탈 |
| 거래량·매수매도 | 거래량, 체결 강도, 주문 대기 물량을 합친 설명 | 수급 압력, 오더북 |

## 구현 계약

1. `ontology_tbox.py`의 클래스 ID는 내부 계약이고 `label`은 사용자 도메인 용어다.
2. ABox의 종목 성격과 계좌 역할 엔티티는 내부 ID와 표시 이름을 함께 저장한다.
3. AI 입력에는 `instrumentArchetypeLabels`, `instrumentPositionIntentLabel`, `instrumentPositionIntentDescription`을 포함한다.
4. 알림 조립기는 표시 이름과 설명 문장만 사용한다. 알 수 없는 영문 ID는 그대로 노출하지 않는다.
5. 새 종목 성격을 추가할 때는 TBox 한국어 이름, 보편언어 문서, 전체 기본 프로필 매핑 테스트를 함께 수정한다.
