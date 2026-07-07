# Orbit Alpha Design System

Orbit Alpha의 화면은 로컬 운영 콘솔입니다. 투자 판단, 계정, 알림, 모델 기준을 자주 확인하는 사용자가 빠르게 스캔하고 같은 위치에서 같은 행동을 할 수 있게 만드는 것이 우선입니다.

## 원칙

- 구현이 쉬운 방향을 기본값으로 삼지 않는다. 더 어렵더라도 사용자가 덜 생각하고 더 빠르게 판단할 수 있는 구조, 고급스럽고 전문적인 금융앱 품질을 우선한다.
- 정보 밀도를 유지한다. 운영 화면은 마케팅형 hero나 장식 카드보다 표, 리스트, 상태 칩, 짧은 설명을 우선한다.
- 관제/요약 UI를 카드 더미로 만들지 않는다. 여러 지표를 보여줄 때는 얇은 상태 레일, 표형 행, 리스트 헤더를 우선하고 설명형 카드 제목과 장문 보조문구는 줄인다.
- 심플함은 비어 보이는 것이 아니라 신뢰할 수 있는 정보 위계다. 화면은 적은 프레임, 명확한 선, 충분한 행간, 일관된 라벨 위치로 읽혀야 한다.
- 여백은 정보 신뢰도의 일부다. 표형 ledger라도 텍스트가 border나 다음 행에 붙어 보이면 실패로 보고, 외곽 margin, 섹션 gap, 행 내부 padding을 먼저 조정한다.
- 화면마다 같은 골격을 쓴다. PC와 981px 이상 태블릿은 좌측 고정 내비게이션과 우측 작업 영역, 861-980px 좁은 태블릿은 상단 밀도형 내비게이션과 8컬럼 작업 영역, 모바일은 상단 운영 메뉴와 하단 핵심 탭이다.
- 주요 액션은 예측 가능한 오른쪽에 둔다. 페이지 전역 액션은 topbar 오른쪽, 패널 저장/갱신은 `panel-head` 오른쪽, 긴 설정 화면의 저장 액션은 상태형 스마트 저장 바에 둔다. 하단에 떠 있는 sticky 저장 패널은 쓰지 않는다.
- 모바일에서 보기 좋은 카드형 반복 UI도 PC에서는 그대로 키우지 않는다. 1181px 이상에서는 계정, 관심종목, 모니터링, 모델 요약 같은 반복 항목을 행 리스트나 ledger로 재배치한다.
- PC에서 반복 항목은 “큰 카드”가 아니라 “구획이 정확한 행과 셀”로 읽혀야 한다. 알림 판단, 종목 검색, 보유/관심, 모델 결과, 설정 상태처럼 반복되는 정보는 1181px 이상에서 border-bottom 기반 ledger, 좌측 상태선, 고정 라벨 열, 제한 폭 metric cell을 우선한다.
- 위험 액션은 `danger` 톤만 쓴다. 삭제, 제거, 되돌릴 수 없는 작업은 일반 버튼과 색상/라벨을 분리한다.
- 텍스트는 줄바꿈을 허용한다. 종목명, URL, 공식, 계정 ID처럼 긴 값은 `overflow-wrap: anywhere` 또는 말줄임 규칙을 적용한다.
- 로컬 우선과 보안 상태는 UI에 반영한다. secret 원문은 다시 표시하지 않고 `설정됨`, `미설정`, `저장됨` 같은 상태 칩으로 표현한다.

## Institutional Finance Tone

전문 금융앱 같은 룩앤필은 장식이 아니라 안정감, 예측 가능성, 빠른 비교에서 나온다. Orbit Alpha는 전통 금융 터미널에 가까운 밝은 회색 작업면, 딥 네이비 내비게이션, 표형 패널, 조밀한 상태 셀을 기본으로 쓴다. 브랜드 모티프는 중앙 코어, 얇은 궤도선, 작은 신호점으로 제한하고, 장식보다 데이터 구조를 우선한다.

- 표면은 평평하게 둔다. `.panel`, `.tab-bar`, `.snackbar` 외에는 큰 그림자를 쓰지 않고, 패널 그림자도 얕게 유지한다.
- 카드 안에 다시 카드처럼 보이는 중첩 프레임을 만들지 않는다. 반복 항목은 6px 내외의 낮은 radius 행, 칩, 구분선으로 구분한다.
- 카드형 관제 패널은 쓰지 않는다. `알림 관제`처럼 여러 상태값을 모아 보여주는 영역은 전폭 상태 레일이나 표형 ledger로 만들고, 별도의 그림자·둥근 프레임·홍보성 제목을 붙이지 않는다.
- 패널은 그림자보다 border와 헤더 구분선으로 신뢰감을 만든다. 라벨은 기본적으로 muted 톤이며, 파랑은 활성 탭과 주요 액션에만 쓴다.
- 숫자, 수익률, 개수, 시간은 스캔이 쉽도록 tabular number 규칙을 따른다.
- 보유/관심/알림/모델 값은 같은 위치에 같은 톤으로 반복한다. 새 화면도 `main + side/actions` 행 구조를 기본값으로 쓴다.
- 파랑은 저장, 검색, 갱신, 현재 선택 같은 주요 액션에만 쓴다. 보조 정보는 회색 표면과 muted text로 낮춘다.
- PC와 태블릿에는 탭 역할에 맞는 `deskbar`를 둔다. 홈/모니터링은 데이터 모드, 포트폴리오, 모델 기준, 온톨로지 관계, 알림 상태를 모두 보여주는 full deskbar를 쓰고, 계정/관심종목/전체종목/알림/전략/온톨로지/설정처럼 업무를 수행하는 화면은 Data·Portfolio·Alerts만 남긴 compact 상태줄을 쓴다.
- 모든 탭 본문은 `managed-page` 템플릿을 통과한다. 탭별로 임의의 카드 묶음을 만들지 말고, `page-command-strip`으로 업무 흐름과 핵심 지표를 먼저 보여준 뒤 12컬럼 작업대를 배치한다.
- 공식, 모델링, 온톨로지처럼 순서가 중요한 화면은 `process-rail`, `formula-ledger`, `relation-matrix`로 흐름과 근거를 먼저 보여준 뒤 상세 입력/그래프를 둔다.
- 업종명, 계정명, 템플릿 변수처럼 길어질 수 있는 값은 오른쪽 정렬 영역에서도 줄바꿈을 허용해 화면 폭을 넘기지 않는다.
- 단순 구현이 UI를 빈약하게 만들면 다시 설계한다. 입력 흐름, 비교 구조, 상태 설명, 예외 상태까지 자연스럽게 이어지는 완성도를 기준으로 판단한다.
- 스페이스 톤은 배경을 어둡게 덮거나 장식 오브젝트를 늘리는 방식으로 쓰지 않는다. `app-brand-mark`, 파비콘, 얇은 궤도선, 미세한 그리드 패턴처럼 금융 화면의 가독성을 방해하지 않는 레벨에서만 쓴다.

기본 라이트 팔레트는 아래 값을 기준으로 한다.

- Background: `#EDF2F7`
- Panel: `#FFFFFF`
- Soft surface: `#F7F9FC`
- Ink: `#111827`
- Muted: `#5F6F84`
- Line: `#CBD6E3`
- Action: `#1F5EFF`
- Orbit line: `#3F6FD8`
- Positive: `#008A63`
- Accent: `#475569`
- Danger: `#C9364A`

## Tokens

CSS의 기준 토큰은 [public/styles.css](../public/styles.css)의 `--ds-*` 변수다. 기존 컴포넌트 호환을 위해 `--bg`, `--ink`, `--blue` 같은 legacy 변수는 `--ds-*` alias로 남긴다.

- Color: `--ds-color-bg`, `--ds-color-panel`, `--ds-color-panel-soft`, `--ds-color-ink`, `--ds-color-muted`, `--ds-color-line`
- Action text: `--ds-color-on-action`
- State color: `--ds-color-action`, `--ds-color-positive`, `--ds-color-warning`, `--ds-color-danger`, `--ds-color-accent`와 각 `*-soft`
- Spacing: `--ds-space-1`부터 `--ds-space-9`까지 4px 기반 계단, 화면 리듬은 `--ds-page-gap`, `--ds-section-gap`, `--ds-panel-pad-x`, `--ds-panel-pad-y`, `--ds-row-pad-x`, `--ds-row-pad-y`를 우선 사용
- Radius: `--ds-radius-control`, `--ds-radius-panel`은 6px
- Control height: `--ds-control-height-sm` 30px, `--ds-control-height-md` 38px, `--ds-control-height-lg` 44px
- Layout: `--ds-shell-width` 1720px, `--ds-sidebar-width` 256px
- Shadow/focus: `--ds-shadow-panel`은 기본 `none`, 실제 overlay만 `--ds-shadow-floating`을 쓰고 focus는 `--ds-focus`를 따른다.

## Spacing Rhythm

- PC와 태블릿의 기본 본문 간격은 `--ds-page-gap` 16px이다. 탭 본문, `admin-grid`, 주요 패널 사이에는 임의의 10-12px gap을 쓰지 않는다.
- 패널 헤더와 본문은 `--ds-panel-pad-x` 16px, `--ds-panel-pad-y` 14px를 기준으로 한다. 본문이 표형으로 바뀌어도 좌우 16px 외곽 padding은 유지한다.
- 행 리스트와 ledger의 반복 행은 `--ds-row-pad-y` 12px 이상, `--ds-row-pad-x` 14px 이상을 기준으로 한다. 단순히 `padding: 12px 0`으로 두면 PC에서 정보가 구분선에 붙어 보이므로 쓰지 않는다.
- 상태 레일, 관계 매트릭스, 공식/모델 단계처럼 `gap: 0`을 쓰는 컴포넌트는 내부 cell padding과 외곽 padding이 반드시 있어야 한다. 구분선만으로 밀도를 만들고 여백을 없애지 않는다.
- 모바일은 shell 외곽 14px, 섹션 gap 12px을 기본으로 한다. 좁은 화면에서도 패널, 버튼, 행 텍스트가 화면 가장자리나 하단 탭과 붙지 않아야 한다.

## Page Layout

- 모든 앱 화면은 `main.shell` 아래에 배치한다.
- desktop `1181px+`에서는 `main.shell`을 `--ds-sidebar-width + minmax(0, 1fr)` 2열로 나눈다. `nav.app-nav`는 좌측 고정 작업 허브이며 브랜드, 전체 탭, 현재 데이터 상태, 새로고침을 세로로 배치한다.
- `section.topbar`는 우측 작업 영역 최상단에서 현재 탭의 `h1`과 짧은 subtitle만 가진다. 상태/새로고침/관리 진입은 topbar 밖의 앱 네비게이션에서 처리한다.
- desktop에서는 `shell`이 100dvh 작업 콘솔처럼 동작한다. topbar와 deskbar는 같은 자리에 머물고, 본문은 `workspace-main` 내부에서 스크롤한다. 긴 목록은 페이지 전체 스크롤을 늘리지 않고 패널 내부 스크롤로 제한한다.
- tablet `981px-1180px`에서는 PC와 같은 좌측 사이드바를 유지하되 폭을 224px로 줄이고 본문은 8컬럼으로 구성한다. 1024px급 화면도 모바일 웹처럼 보이지 않아야 한다.
- compact tablet `861px-980px`에서만 좌측 사이드바를 접고 `app-nav`를 상단 sticky 바에 둔다. 본문은 8컬럼을 유지한다.
- desktop/tablet `861px+`에서는 모든 탭을 `app-nav`에 표시하고, 하단 `tab-bar`는 숨긴다.
- mobile `860px 이하`에서는 100dvh 앱 셸처럼 보이게 한다. `app-nav`는 safe-area를 반영한 상단 운영 메뉴이고 아래로 스크롤하면 접히며, 위로 스크롤하거나 상단에 가까워지면 다시 열린다. `tab-bar`는 화면 하단 edge에 붙은 핵심 내비게이션이다. 하단은 홈/관심종목/모니터링/투자전략처럼 자주 쓰는 도메인 업무만 두고, 계정/전체종목/알림/설정은 `app-nav`의 `운영` 메뉴로 묶는다. 모바일 본문 그리드는 1열로 접고 가로 스크롤 탭을 만들지 않는다.
- PC `1181px+`에서는 관리 화면을 모바일 카드 확대판으로 만들지 않는다. `admin-grid`는 12컬럼을 기본으로 쓰며, 홈/알림/모니터링/설정은 페이지 목적에 맞게 4/5/6/7/8컬럼 단위로 패널을 배치한다.
- PC `1181px+`의 반복 항목은 `auto-fit` 카드 그리드보다 한 줄 row ledger를 우선한다. 배경색 면으로 상태를 설명하지 말고 얇은 좌측 상태선, 칩, 구분선으로 읽게 만든다.
- PC `1181px+`에서 metric/card cell은 화면 폭 전체로 늘어나지 않게 `auto-fill`과 최대 셀 폭을 둔다. 긴 리스트는 내용 열과 상태/액션 열을 분리하고, 행 내부 구획선과 라벨 열이 보이지 않으면 실패로 본다.
- 태블릿 `981px-1180px`에서는 `admin-grid`를 8컬럼으로 줄이고 좌측 내비게이션은 유지한다. `861px-980px`에서는 상단 내비게이션으로 바꾸되 비교가 필요한 패널은 가능한 2열을 유지한다. 860px 이하에서만 좁은 화면용 단일 흐름으로 접는다.
- 화면 본문은 기본적으로 `admin-grid`를 사용한다. 핵심 편집 패널이나 단일 워크벤치는 `grid-column: 1 / -1`로 전체 폭을 사용한다.
- 초기 로딩도 앱의 일부다. 저장된 스냅샷이 없을 때만 계좌, 관심 종목, 알림, 모델처럼 데이터 소스별 준비 상태를 보여주고, 리로드나 탭 복귀에서는 직전 화면을 유지한 채 백그라운드로 갱신한다.

## App-Like Responsiveness

- 모바일은 웹 페이지 축소판이 아니라 앱 화면처럼 동작해야 한다. 상단 앱바, 본문, 하단 탭이 같은 셸 안에서 움직이고 safe-area를 침범하지 않는다.
- `app-nav`는 모바일에서 `position: sticky`와 배경 blur를 쓰되, 스크롤을 내릴 때는 자연스럽게 접고 위로 스크롤하면 즉시 복귀한다. 상단 메뉴가 열려 있으면 스크롤 시작 시 닫는다.
- `tab-bar`는 모바일에서 bottom edge에 붙인다. 카드처럼 떠 있는 네비게이션은 쓰지 않고, 하단 safe-area를 포함해 터치 영역을 확보한다.
- 모바일 입력은 16px 이상으로 유지해 브라우저 자동 확대를 막고, 주요 터치 버튼은 `touch-action: manipulation`과 짧은 active feedback을 가진다.
- 모바일 outer margin은 줄이고, 패널 그림자는 낮춰 화면 전환이 웹 카드 모음이 아니라 앱 화면처럼 느껴지게 한다.

## Navigation

- 탭은 `tabs` 배열이 단일 source of truth다. 새 탭은 배열에 추가하고 `renderActiveTab`에서 같은 순서로 처리한다.
- desktop 탭은 좌측 `.app-nav` 안에서 라벨 중심의 세로 내비게이션으로 표시한다. 관리성 탭은 divider 뒤에 배치해 핵심 업무 탭과 시각적으로 구분한다.
- 981px 이상 tablet 탭은 PC처럼 좌측 `.app-nav`를 유지한다. 861-980px compact tablet 탭만 상단 `.app-nav` 안에서 한 줄 스크롤 가능한 네비게이션으로 표시한다.
- mobile primary 탭은 `홈`, `관심종목`, `모니터링`, `투자전략`을 하단 고정으로 둔다. 계정, 전체종목, 알림, 설정처럼 운영/관리 성격이 강한 화면은 상단 `운영` 메뉴에서 진입시킨다.
- 활성 탭 버튼은 `active`와 `aria-current="page"`를 함께 가진다.
- 설정은 별도 overlay가 아니라 `settings` 탭이다. 상단 설정 버튼은 현재 흐름을 기억하고 설정 화면의 `이전` 버튼으로 돌아간다.
- 한 탭 안에서 스크롤이 길어지는 관리 화면은 내부 섹션 탭을 둔다. 기본 섹션은 현황/요약만 보여주고, 편집/고급 설정은 사용자가 선택한 섹션에만 렌더링한다. 전역 관리 진입은 앱 네비게이션이나 모바일 관리 메뉴로만 제공하고, topbar 안에 같은 목적의 버튼을 중복 배치하지 않는다.
- 각 탭은 독립된 스크롤 위치를 가진다. PC/태블릿의 내부 작업대 스크롤과 모바일의 window 스크롤을 같은 탭 키로 관리하고, 알림·전략 운영처럼 내부 섹션 탭이 있는 화면은 섹션별 스크롤을 별도로 저장한다.

## Components

- Panel: 모든 주요 컨테이너는 `.panel`과 `.panel-head`를 사용한다. 헤더 왼쪽은 label/title, 오른쪽은 metric/status/action이다.
- Deskbar: PC/태블릿 화면의 `topbar` 아래에는 `.deskbar`를 둔다. 홈/모니터링은 `.deskbar-full`로 데이터 모드, 포트폴리오, 모델, 온톨로지, 알림 상태를 같은 순서로 표시한다. 나머지 업무 탭은 `.deskbar-compact`로 Data·Portfolio·Alerts만 얇게 보여줘 본문 작업 공간을 방해하지 않는다.
- Managed page: 모든 탭은 `.managed-page` 안에 `.page-command-strip`과 `.admin-grid`를 둔다. `page-command-strip`은 3단계 업무 흐름과 3개 핵심 지표만 표시하고, 카드처럼 떠 있는 소개 영역이 아니라 얇은 상태표처럼 보여야 한다.
- Process rail: 데이터 처리 순서, 모델링 순서, 추론 순서는 `.process-rail` 또는 `.ontology-control-strip`을 사용한다. 단계 번호, 단계명, 입력/출력 개수를 함께 보여준다.
- Formula ledger: 공식은 카드 설명이 아니라 `.formula-ledger` 표형 행으로 표시한다. 영역, 공식명, 실제 표현식을 한 줄에서 비교할 수 있어야 한다.
- Relation matrix: 온톨로지 관계 타입은 `.relation-matrix`로 보여준다. schema-only 관계와 ABox에서 실제 사용된 관계를 구분한다.
- Button: 기본은 `.text-button`, 주요 저장/갱신은 `.text-button.primary`, 보조 소형 액션은 `.mini-button`, 아이콘 단독 액션은 `.icon-button`이다.
- Form: `label.setting-field` 안에 label text와 input/select/textarea를 넣는다. 2열 폼은 `.settings-grid`, 전체 폭 입력은 `.setting-field.wide`를 사용한다.
- Status: 상태는 `.status-pill`, 판단 톤은 `.tone-chip`, 작은 속성은 `.chip`을 사용한다.
- List row: 반복 항목은 `*-row` 클래스로 만들고, 내부는 `main + side/actions` 구조를 따른다.
- Desktop list row: PC에서는 `.source-row`, `.position-row`, `.notification-decision-row`, `.symbol-result-row`, `.alert-row`, `.signal-row`, `.valuation-row`, `.lab-row`처럼 반복 행을 panel 안의 mini card로 만들지 않는다. 배경을 투명하게 두고 border-bottom, 좌측 상태선, 2열 이상 정보 구조로 구획한다.
- Summary strip: 패널 내부 요약은 카드처럼 감싸지 않고 `*-metric`, `*-status`, `*-row` 형태의 좌측 accent, 칩, 구분선으로 표현한다.
- Snackbar: 저장/갱신 결과는 `.snackbar`를 쓰며, 화면 하단 탭과 겹치지 않게 `--bottom-tabs-height`를 반영한다.

## Page Contracts

- 홈: PC에서는 운영 현황을 8컬럼, 계정 DB를 4컬럼으로 먼저 배치하고, 계정별 관심 종목과 모니터링 요약을 5:7 비율로 이어서 보여준다. 빠른 이동 버튼은 `home-action`을 쓰고 별도 랜딩 섹션을 만들지 않는다.
- 계정: PC에서는 저장 계정 목록과 편집 폼을 같은 화면에 노출한다. 목록 rail은 충분한 폭을 유지하고, 저장 계정 행은 `account-exposure-grid`로 토스 API, 계좌 seq, 텔레그램, 관심종목 상태를 가로 비교할 수 있게 한다. API secret 원문은 표시하지 않는다. 저장/삭제는 계정 폼과 행 오른쪽 액션 위치를 따른다.
- 관심종목: 계정 선택 rail과 편집 workbench를 한 패널 안에 둔다. PC rail은 계정명/상태를 읽을 수 있는 폭을 확보하고, 관심 종목 추가/수정/삭제는 선택 계정에만 적용한다. 전체 종목 검색 결과를 이 탭에 섞지 않는다.
- 전체종목: `symbols` 탭은 시장 카탈로그 전용 화면이다. 검색/표시 수/추가 대상/select/갱신 버튼 순서를 유지한다. 같은 액션이 행마다 반복되면 상단 일괄 액션을 먼저 제공하고, 행 오른쪽 액션은 보조로 낮춘다.
- 모니터링: PC에서는 상태 요약 4컬럼, 알림 센터 8컬럼, 보유·관심 통합 목록 8컬럼, 포트폴리오 노출 4컬럼으로 배치한다. 실행 상태 패널은 명령어 카드가 아니라 현재 실행 상태, 핵심 상태 ledger, 런타임 신호 strip 순서로 보여준다. 보유와 관심은 같은 `monitoring-instrument-row` 패턴으로 보여주고, 보유/관심 칩으로 상태를 구분한다.
- 알림: 기본 진입은 `현황`이며 탭 스트립 바로 아래에 `notification-ops-rail`로 대기/발송/보류/실패/룰/주기/템플릿/스케줄을 한 줄 상태표로 보여준 뒤 최근 판단 목록을 전폭으로 배치한다. 최근 판단 항목은 둥근 카드가 아니라 구분선 기반 행 리스트로 유지한다. `정책`, `템플릿`, `고급` 내부 섹션으로 나누고, 내부 섹션 이동은 카드형 버튼이 아니라 알림탭 최상단 탭 스트립으로 제공한다. 주요 저장/갱신 액션은 탭 스트립 오른쪽에 둔다. 정책 그룹은 접힌 상태에서 시작한다. 템플릿/룰 상세 편집은 행 안에 inline으로 누적하지 않고 선택한 메시지 타입의 우측 상세 패널에만 표시한다. 템플릿 섹션도 목록과 단일 편집 패널 구조를 쓰며, 정책/템플릿 선택 목록은 제한된 내부 스크롤 영역으로 둬 페이지 전체 스크롤을 늘리지 않는다. 채널/임계값/장 시간/유사 메시지 같은 고급 설정은 `고급` 섹션에 둔다.
- 모델링: 기본 진입에서 `Strategy Workflow`로 데이터 정합, feature 산출, 공식 평가, 기준 적용, 결과, 알림까지 순서를 보여준다. 판단 기준 섹션은 `Formula Ledger`로 모든 공식을 먼저 보여준 뒤 편집 폼과 버전 저장을 둔다. feature 설명, 재현성 결과는 dense grid를 쓰고 장식형 hero를 만들지 않는다.
- 온톨로지: `Ontology Control`은 그래프보다 먼저 TBox -> ABox -> Relation -> Evidence -> Belief -> Opinion 순서를 보여준다. `Relation Matrix`로 schema-only 관계와 실제 ABox relation row 사용량을 구분하고, 이후 TBox/ABox 관계 그래프와 row projection, rule trace를 제공한다.
- 설정: 앱 표시/전달/외부 연결 설정만 관리한다. 설정 상단은 hero가 아니라 저장 상태, 로컬 우선, 잠금/오류를 보여주는 상태 밴드다. 계정 secret과 모델 공식은 각각 계정/모델링 탭에서 관리하고, 저장은 상태 밴드 아래의 스마트 저장 바로 제공한다. 저장할 변경이 없으면 저장 버튼은 `저장됨` 상태로 비활성화하고, 입력 변경/저장 실패 때만 주요 액션으로 승격한다.

## Button Placement

- 전역: 앱 네비게이션 오른쪽 순서는 상태, 새로고침이다. 설정은 별도 버튼이 아니라 네비게이션 탭/관리 메뉴로 진입한다.
- 패널: 저장, 갱신, 테스트 같은 패널 액션은 `panel-head` 오른쪽에 둔다.
- 긴 설정: 하단 floating/sticky 저장 영역을 만들지 않는다. 현재 저장 상태, 설명, 저장 버튼을 `.settings-smart-save`에 묶고 입력 변경 시 버튼 라벨과 활성 상태를 즉시 갱신한다.
- 리스트 행: 수정/삭제는 행 오른쪽 또는 행 하단 `.row-actions`에 둔다.
- 삭제/제거는 항상 `danger` 클래스를 붙인다.

## Accessibility

- 모든 button, link, input, select, textarea는 `:focus-visible` 상태를 가진다.
- 아이콘 단독 버튼은 `aria-label`과 `title`을 함께 둔다.
- 탭 버튼은 `type="button"`과 활성 상태의 `aria-current="page"`를 가진다.
- 터치 대상은 기본 38px 이상, 체크박스는 18px 이상으로 유지한다.
- 애니메이션은 `prefers-reduced-motion: reduce`를 존중한다.

## Page Checklist

새 화면이나 큰 UI 변경 전에는 아래를 확인한다.

- app-nav와 tab navigation을 우회하는 별도 내비게이션을 만들지 않았는가
- PC 1181px 이상에서 좌측 내비게이션과 우측 작업 영역이 분리되어 있고, 본문이 12컬럼 폭을 충분히 쓰는가
- 태블릿 981-1180px에서 PC형 사이드바가 유지되고, 861-980px에서만 상단 내비게이션으로 전환되는가
- PC/태블릿에서 홈/모니터링은 full deskbar, 업무 탭은 compact deskbar를 쓰고 process rail, formula ledger, relation matrix가 필요한 화면에 빠지지 않았는가
- 주요 액션이 app-nav, topbar 인접 영역 또는 panel-head 오른쪽에 있는가
- 저장/삭제/갱신 버튼의 톤이 역할과 일치하는가
- 구현 편의 때문에 사용자가 더 많이 추측하거나 이동해야 하는 화면이 되지 않았는가
- 반복 버튼이나 펼쳐진 고급 설정 때문에 사용자가 핵심 상태를 놓치지 않는가
- 긴 관리 화면이 기본 진입부터 모든 설정을 펼치지 않고, 현황/편집/고급 섹션과 선택 상세 패널로 재구성되어 있는가
- 모바일 기본 내비게이션이 5개 이하이며 가로 스크롤 없이 핵심 업무에 닿는가
- 모바일에서 app-nav는 safe-area를 지키며 스크롤 방향에 따라 접히고, tab-bar는 bottom edge에 붙어 있으며, 100dvh 셸을 반영하는가
- PC/태블릿에서 계좌/관심종목/홈/알림/전략 화면이 너무 일찍 1열로 접히지 않고 비교/편집에 맞는 2열 이상 구조를 유지하는가
- 로딩/오류/잠금 상태가 어떤 데이터 소스에서 발생했는지 드러나는가
- 고급 금융앱처럼 정보 위계, 여백, 상태 피드백, 예외 처리가 충분히 전문적으로 보이는가
- desktop 2열 이상, mobile 1열 전환에서 텍스트와 버튼이 겹치지 않는가
- secret 또는 개인 데이터 원문이 화면, 정적 preview, 문서, 테스트에 들어가지 않는가
- 새 CSS 색상/간격을 직접 추가하지 않고 `--ds-*` 토큰을 재사용했는가
- 전체 종목, 알림 템플릿, 계정별 관심 종목처럼 긴 텍스트가 실제 데이터로 넘치지 않는가
