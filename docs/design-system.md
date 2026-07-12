# Orbit Alpha Design System

Orbit Alpha의 화면은 로컬 운영 콘솔입니다. 투자 판단, 계정, 알림, 모델 기준을 자주 확인하는 사용자가 빠르게 스캔하고 같은 위치에서 같은 행동을 할 수 있게 만드는 것이 우선입니다.

## 원칙

- 구현이 쉬운 방향을 기본값으로 삼지 않는다. 더 어렵더라도 사용자가 덜 생각하고 더 빠르게 판단할 수 있는 구조, 고급스럽고 전문적인 금융앱 품질을 우선한다.
- 정보 밀도를 유지한다. 운영 화면은 마케팅형 hero나 장식 카드보다 표, 리스트, 상태 칩, 짧은 설명을 우선한다.
- 관제/요약 UI를 카드 더미로 만들지 않는다. 여러 지표를 보여줄 때는 얇은 상태 레일, 표형 행, 리스트 헤더를 우선하고 설명형 카드 제목과 장문 보조문구는 줄인다.
- 심플함은 비어 보이는 것이 아니라 신뢰할 수 있는 정보 위계다. 화면은 적은 프레임, 명확한 선, 충분한 행간, 일관된 라벨 위치로 읽혀야 한다.
- 여백은 정보 신뢰도의 일부다. 표형 ledger라도 텍스트가 border나 다음 행에 붙어 보이면 실패로 보고, 외곽 margin, 섹션 gap, 행 내부 padding을 먼저 조정한다.
- 화면마다 같은 골격을 쓴다. PC와 861px 이상 태블릿은 상단 금융 터미널형 탭 내비게이션과 전체 폭 작업 영역, 모바일은 상단 운영 메뉴와 하단 핵심 탭이다.
- 주요 액션은 예측 가능한 오른쪽에 둔다. 페이지 전역 액션은 PC 상단 통합 command rail 또는 패널 `panel-head` 오른쪽에 두고, 긴 설정 화면의 저장 액션은 상태형 스마트 저장 바에 둔다. 하단에 떠 있는 sticky 저장 패널은 쓰지 않는다.
- 모바일에서 보기 좋은 느슨한 카드형 반복 UI를 PC에서 그대로 키우지 않는다. 1181px 이상에서는 계정, 관심종목, 모니터링, 모델 요약 같은 반복 항목을 금융 원장형 카드 그리드로 재배치한다.
- PC에서 반복 항목은 “큰 장식 카드”가 아니라 “구획이 정확한 금융 카드와 셀”로 읽혀야 한다. 알림 운영, 종목 검색, 보유/관심, 모델 결과, 운영 설정 상태처럼 반복되는 정보는 1181px 이상에서 얇은 좌측 상태선, 명확한 border, 고정 라벨 열, 제한 폭 metric cell을 우선한다.
- 위험 액션은 `danger` 톤만 쓴다. 삭제, 제거, 되돌릴 수 없는 작업은 일반 버튼과 색상/라벨을 분리한다.
- 텍스트는 줄바꿈을 허용한다. 종목명, URL, 공식, 계정 ID처럼 긴 값은 `overflow-wrap: anywhere` 또는 말줄임 규칙을 적용한다.
- 로컬 우선과 보안 상태는 UI에 반영한다. secret 원문은 다시 표시하지 않고 `설정됨`, `미설정`, `저장됨` 같은 상태 칩으로 표현한다.

## Institutional Ledger Tone

전문 금융앱 같은 룩앤필은 장식이 아니라 안정감, 예측 가능성, 빠른 비교에서 나온다. Orbit Alpha는 브로커리지/운용사 콘솔에 가까운 밝은 회색 작업면, 차콜 내비게이션, 표형 패널, 조밀한 상태 셀을 기본으로 쓴다. 브랜드 모티프는 작은 레저 마크와 신호점으로 제한하고, 스페이스 장식보다 데이터 구조를 우선한다.

- 표면은 평평하게 둔다. `.panel`, `.tab-bar`, `.snackbar` 외에는 큰 그림자를 쓰지 않고, 패널 그림자도 얕게 유지한다.
- 카드 안에 다시 카드처럼 보이는 장식 프레임을 만들지 않는다. 반복 항목은 `--ds-card-*` 토큰을 쓰는 낮은 radius 금융 카드로 만들고, 좌측 상태선·헤더·라벨/값 구획을 반드시 가진다.
- 카드형 관제 패널을 단순 박스 더미로 만들지 않는다. `알림 관제`처럼 여러 상태값을 모아 보여주는 영역은 전폭 상태 레일이나 금융 원장형 카드 그리드로 만들고, 그림자·둥근 프레임·홍보성 제목보다 수치/상태/근거의 위치 일관성을 우선한다.
- 카드형 표면은 전면 교체 대상이다. `card`, `row`, `cell`, `metric`, `stat`, `summary`, `panel`, `surface` 의미를 가진 클래스는 예외 없이 같은 금융 카드 시스템을 통과해야 한다. 홈 액션, 데스크바, 알림 관제, 모니터링 실행 상태, 관계 분석, 공식 원장, 전략 데이터, 설정 상태도 개별 스타일이 아니라 같은 레코드 카드 문법을 따른다.
- 패널은 그림자보다 border와 헤더 구분선으로 신뢰감을 만든다. 라벨은 기본적으로 muted 톤이며, 파랑은 활성 탭과 주요 액션에만 쓴다.
- 숫자, 수익률, 개수, 시간은 스캔이 쉽도록 tabular number 규칙을 따른다.
- 보유/관심/알림/모델 값은 같은 위치에 같은 톤으로 반복한다. 새 화면도 `main + side/actions` 행 구조를 기본값으로 쓴다.
- 각 카드의 기본 골격은 `header/status + primary value + secondary ledger + action`이다. 종목/계정/알림/모델 카드가 이 네 구획 중 무엇을 생략했는지 설명할 수 없다면 다시 설계한다.
- 파랑은 저장, 검색, 갱신, 현재 선택 같은 주요 액션에만 쓴다. 보조 정보는 회색 표면과 muted text로 낮춘다.
- PC와 태블릿의 큰 `deskbar`는 관제 홈 화면에만 둔다. 계정·연결/관심 관리/종목 탐색/알림 운영/투자 판단/뉴스·근거/운영 설정처럼 사용자가 작업하는 업무 탭에서는 상단 상태 카드 묶음을 렌더링하지 않는다.
- 모든 탭 본문은 `managed-page` 템플릿을 통과한다. PC에서는 화면 맥락, `이전 입력 -> 현재 처리 -> 다음 출력` flow spine, 핵심 지표, 결과/설정 전환을 상단 `app-nav-command`로 승격하고 본문은 `page-routine-panel`과 실제 작업대를 바로 보여준다. 모바일은 같은 정보를 `page-command-strip-compact`로 본문 안에 둔다.
- 공식, 모델링, 관계 분석처럼 순서가 중요한 화면은 `process-rail`, `formula-ledger`, `relation-matrix`로 흐름과 근거를 먼저 보여준 뒤 상세 입력/그래프를 둔다.
- 업종명, 계정명, 템플릿 변수처럼 길어질 수 있는 값은 오른쪽 정렬 영역에서도 줄바꿈을 허용해 화면 폭을 넘기지 않는다.
- 단순 구현이 UI를 빈약하게 만들면 다시 설계한다. 입력 흐름, 비교 구조, 상태 설명, 예외 상태까지 자연스럽게 이어지는 완성도를 기준으로 판단한다.
- 스페이스 톤은 배경을 어둡게 덮거나 장식 오브젝트를 늘리는 방식으로 쓰지 않는다. `app-brand-mark`, 파비콘, 얇은 신호선처럼 금융 화면의 가독성을 방해하지 않는 레벨에서만 남긴다.

기본 라이트 팔레트는 아래 값을 기준으로 한다.

- Background: `#F3F5F8`
- Panel: `#FFFFFF`
- Soft surface: `#F8FAFC`
- Ink: `#101820`
- Muted: `#5D6675`
- Line: `#D5DBE5`
- Action: `#1457A8`
- Signal line: `#2F6FBB`
- Positive: `#137A63`
- Accent: `#344054`
- Danger: `#B42318`

## Web Style Contract

Orbit Alpha의 현재 웹 스타일 계약은 `orbit-alpha-console-v2`다. 이 계약은 "차세대 금융 콘솔"을 화면 구조로 고정하기 위한 기준이며, `scripts/smoke-test.js`가 각 탭 렌더 결과를 검사한다.

- 앱 루트는 `main.shell.console-shell.web-style-shell`과 `data-web-style="orbit-alpha-console-v2"`를 가진다.
- 모든 탭은 `web-style-nav`, `web-style-topbar`, `web-style-workspace`, `web-style-main` 영역을 같은 순서로 렌더링한다. PC에서 보이는 화면 제목/맥락은 `web-style-nav` 안의 `app-nav-command`가 담당하고, `topbar`는 모바일·시맨틱 제목 영역으로 남긴다.
- 관제 홈은 `data-style-rail="full"` 데스크바를 쓴다. 나머지 업무 탭은 deskbar를 렌더링하지 않고, 데이터/포트폴리오/알림 상태는 상단 nav 오른쪽 상태와 `app-nav-command` 지표로만 보조 표시한다.
- 모든 화면 본문은 `managed-page web-style-page`, `data-style-screen="<tab>"`, `data-structure-group`, `data-structure-layer`, `data-structure-entity`, `page-command-strip web-style-command-strip`, `admin-grid`를 통과한다. PC에서는 본문 `page-command-strip`을 보이지 않게 하고 같은 정보가 `app-nav-command`에 합쳐져야 한다.
- PC 탭 구조는 `관제 홈`, `데이터 관리`, `판단 운영`, `운영 관리` 4개 업무 그룹으로 고정한다. 새 탭은 `tabs`, `navigationGroups`, `pageStructureCatalog`에 함께 등록해 전역 네비게이션, 화면 목적, command rail이 같은 정보 구조를 공유해야 한다.
- PC의 `app-nav-command`는 현재 업무 그룹, 화면 레이어, 현재 탭, 화면 목적, `page-flow-spine`, 결과/설정 전환, 핵심 지표를 한 영역에 합친다. 모바일의 `page-command-strip-compact`는 같은 정보를 본문 상단에서 얇게 보여준다.
- PC에서 `page-routine-panel`은 상단 통합 command rail 바로 다음에 보이는 첫 판단 영역이어야 한다. 모바일에서는 `page-command-strip` 바로 아래에 둔다. 모든 주요 탭은 첫 화면에서 `현재 상태`, `왜 봐야 하나`, `다음 행동`을 같은 위치에 보여줘 사용자가 매일 같은 루틴으로 움직일 수 있어야 한다.
- 관리 항목이 많은 탭은 `data-page-mode="results|settings"`를 가진다. 기본 진입은 `results`이며, 설정/정책/고급 편집은 `settings` 모드로 분리한다.
- 긴 입력값은 접힌 고급 설정 안에 둘 수 있지만, 열렸을 때는 작은 내부 스크롤 박스로 만들지 않는다. `textarea`와 긴 메시지 본문은 자동으로 높이를 늘려 페이지 스크롤 흐름 안에서 읽히게 한다.
- 검증 대상 탭은 `overview`, `accounts`, `watchlist`, `symbols`, `notifications`, `modeling`, `experiments`, `feed`, `system`, `settings`다.
- 새 탭이나 새 화면은 먼저 이 계약의 DOM 구조와 CSS 레이어를 통과해야 하며, 예외가 필요하면 디자인 시스템 문서와 스모크 테스트를 함께 갱신한다.

## Tokens

CSS의 기준 토큰은 [public/styles.css](../public/styles.css)의 `--ds-*` 변수다. 기존 컴포넌트 호환을 위해 `--bg`, `--ink`, `--blue` 같은 legacy 변수는 `--ds-*` alias로 남긴다.

- Color: `--ds-color-bg`, `--ds-color-panel`, `--ds-color-panel-soft`, `--ds-color-ink`, `--ds-color-muted`, `--ds-color-line`
- Action text: `--ds-color-on-action`
- State color: `--ds-color-action`, `--ds-color-positive`, `--ds-color-warning`, `--ds-color-danger`, `--ds-color-accent`와 각 `*-soft`
- Financial card: `--ds-card-bg`, `--ds-card-row-bg`, `--ds-card-head-bg`, `--ds-card-border`, `--ds-card-rule`, `--ds-card-status-neutral`
- Spacing: `--ds-space-1`부터 `--ds-space-9`까지 4px 기반 계단, 화면 리듬은 `--ds-page-gap`, `--ds-section-gap`, `--ds-panel-pad-x`, `--ds-panel-pad-y`, `--ds-row-pad-x`, `--ds-row-pad-y`를 우선 사용
- Radius: `--ds-radius-control`, `--ds-radius-panel`은 6px
- Control height: `--ds-control-height-sm` 30px, `--ds-control-height-md` 38px, `--ds-control-height-lg` 44px
- Layout: `--ds-shell-width` 1720px, `--ds-content-width` 1880px, `--ds-top-nav-height` 104px
- Shadow/focus: `--ds-shadow-panel`은 기본 `none`, 실제 overlay만 `--ds-shadow-floating`을 쓰고 focus는 `--ds-focus`를 따른다.

## Spacing Rhythm

- PC와 태블릿의 기본 본문 간격은 `--ds-page-gap` 16px이다. 탭 본문, `admin-grid`, 주요 패널 사이에는 임의의 10-12px gap을 쓰지 않는다.
- 패널 헤더와 본문은 `--ds-panel-pad-x` 16px, `--ds-panel-pad-y` 14px를 기준으로 한다. 본문이 표형으로 바뀌어도 좌우 16px 외곽 padding은 유지한다.
- 행 리스트와 ledger의 반복 행은 `--ds-row-pad-y` 12px 이상, `--ds-row-pad-x` 14px 이상을 기준으로 한다. 단순히 `padding: 12px 0`으로 두면 PC에서 정보가 구분선에 붙어 보이므로 쓰지 않는다.
- PC 최종 감사 레이어는 모든 탭의 내부 작업 영역까지 본다. `symbol-filter-form`, `settings-body`, `process-rail`, `investment-bridge-flow`, `formula-ledger`, `relation-matrix`, `notification-ops-rail`, 섹션 탭 바처럼 패널 안에 들어가는 grid/list도 `--ds-section-gap`과 panel padding을 다시 확인한다.
- 상태 레일, 관계 매트릭스, 공식/모델 단계처럼 `gap: 0`을 쓰는 컴포넌트는 내부 cell padding과 외곽 padding이 반드시 있어야 한다. 구분선만으로 밀도를 만들고 여백을 없애지 않는다.
- 모바일은 shell 외곽 14px, 섹션 gap 16px을 기본으로 한다. 좁은 화면에서도 패널, 버튼, 행 텍스트가 화면 가장자리나 하단 탭과 붙지 않아야 한다. 패널 헤더 바로 아래에 `model-guide-grid`, `process-rail`, `investment-bridge-flow`, 리스트형 body가 붙어 보이면 실패이며, `settings-status-band`, `settings-api-row`, `admin-stat-grid span`, `notification-ops-cell`, `monitor-primary-state`, `monitor-ledger-cell`, `ontology-ledger-item` 같은 패널 내부 요약 카드도 `--ds-row-pad-*` 기준의 내부 여백을 가져야 한다. 모바일 최종 spacing 레이어에서 상단 padding, board gap, nested card padding을 다시 보정한다.
- 모바일에서 단계형 플로우(`process-rail`, `investment-bridge-flow`, `formula-ledger`)는 가로 3-4열을 유지하지 않는다. 반드시 1열 타임라인/ledger로 접고, 한글 텍스트는 글자 단위로 쪼개지지 않도록 `word-break: keep-all` 계열 규칙을 적용한다.

## Page Layout

- 모든 앱 화면은 `main.shell` 아래에 배치한다.
- desktop `1181px+`에서는 `main.shell`을 단일 컬럼으로 두고 `nav.app-nav`, 홈 deskbar 또는 작업 영역을 위에서 아래로 쌓는다. `nav.app-nav`는 상단 sticky 금융 터미널 탭 바이며 브랜드, 전체 탭, 현재 데이터 상태, 새로고침, 현재 화면 command rail을 한 영역 안에 배치한다.
- `section.topbar`는 모바일·시맨틱 제목 영역이다. PC에서는 같은 정보가 `app-nav-command`로 합쳐지므로 시각적으로 숨긴다.
- desktop에서는 어떤 탭도 100dvh 안에 갇힌 고정 화면으로 만들지 않는다. 상단 네비게이션 아래 작업 영역은 전체 폭을 사용하되, `shell`, `workspace-main`, `managed-page`, `admin-grid`는 페이지 스크롤을 따른다. 탭 본문 안의 긴 목록, 사이드 컬럼, 메시지 본문, 근거 목록은 자체 스크롤바를 갖지 않는다. 내용은 페이지 전체에 펼치거나 결과/설정·섹션·상세 화면으로 분리한다.
- tablet `981px-1180px`에서도 상단 탭형 내비게이션을 유지하고 본문은 8컬럼으로 구성한다. 1024px급 화면도 좌측 메뉴 때문에 작업 영역이 좁아지면 실패다.
- compact tablet `861px-980px` 역시 상단 sticky 바를 유지하되 탭은 가로 스크롤 한 줄로 압축한다. 본문은 8컬럼을 유지한다.
- desktop/tablet `861px+`에서는 모든 탭을 `app-nav`에 표시하고, 하단 `tab-bar`는 숨긴다.
- mobile `860px 이하`에서는 100dvh 앱 셸처럼 보이게 한다. `app-nav`는 safe-area를 반영한 상단 운영 메뉴이고 아래로 스크롤하면 접히며, 위로 스크롤하거나 상단에 가까워지면 다시 열린다. `tab-bar`는 화면 하단 edge에 붙은 핵심 내비게이션이다. 하단은 관제 홈/관심 관리/알림 운영/투자 판단처럼 자주 쓰는 도메인 업무만 두고, 계정·연결/종목 탐색/뉴스·근거/구조·흐름/운영 설정은 `app-nav`의 `운영` 메뉴로 묶는다. 모바일 본문 그리드는 1열로 접고 가로 스크롤 탭을 만들지 않는다.
- PC `1181px+`에서는 관리 화면을 모바일 카드 확대판으로 만들지 않는다. `admin-grid`는 12컬럼을 기본으로 쓰며, 관제 홈/알림 운영/모니터링/운영 설정은 페이지 목적에 맞게 4/5/6/7/8컬럼 단위로 패널을 배치한다. 1440px 미만 PC에서는 3등분보다 2열/전폭을 우선해 카드 본문 폭을 지킨다.
- PC `1181px+`의 반복 항목은 화면 폭 전체로 길게 늘어지는 한 줄 리스트를 피하고, 읽기 폭이 제한된 금융 카드 그리드 또는 원장형 레코드 카드로 배치한다. 배경색 면으로 상태를 설명하지 말고 얇은 좌측 상태선, 칩, 구획선으로 읽게 만든다.
- PC `1181px+`에서 metric/card cell은 화면 폭 전체로 늘어나지 않게 `auto-fit`과 읽기 폭 제한을 둔다. 긴 리스트는 내용 열과 상태/액션 열을 분리하고, 카드 내부 라벨 열과 값 영역이 보이지 않으면 실패로 본다. 최근 알림 판단처럼 시간순 감사가 중요한 데이터는 카드 그리드가 아니라 전폭 원장형 행으로 유지한다.
- 태블릿 `981px-1180px`에서는 `admin-grid`를 8컬럼으로 줄이고 상단 탭형 내비게이션을 유지한다. `861px-980px`에서는 탭을 한 줄 스크롤로 압축하되 비교가 필요한 패널은 가능한 2열을 유지한다. 860px 이하에서만 좁은 화면용 단일 흐름으로 접는다.
- 화면 본문은 기본적으로 `admin-grid`를 사용한다. 핵심 편집 패널이나 단일 워크벤치는 `grid-column: 1 / -1`로 전체 폭을 사용한다.
- 초기 로딩도 앱의 일부다. 저장된 스냅샷이 없을 때만 계좌, 관심 종목, 알림, 모델처럼 데이터 소스별 준비 상태를 보여주고, 리로드나 탭 복귀에서는 직전 화면을 유지한 채 백그라운드로 갱신한다.

## App-Like Responsiveness

- 모바일은 웹 페이지 축소판이 아니라 앱 화면처럼 동작해야 한다. 상단 앱바, 본문, 하단 탭이 같은 셸 안에서 움직이고 safe-area를 침범하지 않는다.
- `app-nav`는 모바일에서 `position: sticky`와 배경 blur를 쓰되, 스크롤을 내릴 때는 자연스럽게 접고 위로 스크롤하면 즉시 복귀한다. 스크롤 위치가 복원된 상태에서도 본문 카드 위에 남아 있으면 안 되며, 상단 메뉴가 열려 있으면 스크롤 시작 시 닫는다.
- `tab-bar`는 모바일에서 bottom edge에 붙인다. 카드처럼 떠 있는 네비게이션은 쓰지 않고, 하단 safe-area를 포함해 터치 영역을 확보한다.
- 모바일 입력은 16px 이상으로 유지해 브라우저 자동 확대를 막고, 주요 터치 버튼은 `touch-action: manipulation`과 짧은 active feedback을 가진다.
- 모바일 outer margin은 줄이고, 패널 그림자는 낮춰 화면 전환이 웹 카드 모음이 아니라 앱 화면처럼 느껴지게 한다.

## Navigation

- 탭은 `tabs` 배열이 단일 source of truth다. 새 탭은 배열에 추가하고 `renderActiveTab`에서 같은 순서로 처리한다.
- desktop 탭은 상단 `.app-nav` 안에서 업무 그룹 순서를 유지한 단일 행 탭으로 표시한다. 그룹은 `관제 홈`(관제 홈), `데이터 관리`(계정·연결/관심 관리/종목 탐색/뉴스·근거), `판단 운영`(투자 판단/알림 운영/전략 검증), `운영 관리`(구조·흐름/운영 설정) 순서다. PC에서 탭 라벨은 한 줄이어야 하며, 설명은 탭 안에 노출하지 않고 같은 `app-nav` 안의 `app-nav-command`가 담당한다.
- 상단 네비게이션 버튼은 `data-nav-group`을 가진다. 현재 화면 shell은 `data-active-group`, managed page는 `data-structure-group`, command strip은 `data-command-group`을 가져야 하며, 셋이 불일치하면 정보 구조 실패로 본다.
- `accounts`, `notifications`, `modeling`, `feed`처럼 관리 항목이 많은 탭은 `결과`와 `설정` 모드를 먼저 나눈다. 결과 모드는 상태, 판단, 수집 결과, 실행 큐처럼 사용자가 먼저 봐야 할 것만 보여주고, 설정 모드는 계정 관리, 알림 정책/템플릿/고급, 규칙·프롬프트, 피드 수집 설정만 보여준다.
- 861px 이상 tablet/desktop 탭은 모두 상단 `.app-nav` 안에서 한 줄 네비게이션으로 표시한다. 폭이 좁으면 탭 행만 가로 스크롤하고 본문 폭을 침범하지 않는다.
- mobile primary 탭은 `관제 홈`, `관심 관리`, `알림 운영`, `투자 판단`, `전략 검증`을 하단 고정으로 둔다. 계정·연결, 종목 탐색, 뉴스·근거, 구조·흐름, 운영 설정처럼 운영/관리 성격이 강한 화면은 상단 `운영` 메뉴에서 진입시킨다.
- 모바일 하단 탭과 상단 운영 메뉴는 스크롤 관성 중에도 즉시 반응해야 한다. 탭 전환은 `click`만 기다리지 말고 `pointerup/touchend` 경로를 함께 유지하며, 터치 이동이 감지된 드래그는 탭 전환으로 처리하지 않는다.
- 활성 탭 버튼은 `active`와 `aria-current="page"`를 함께 가진다.
- 설정은 별도 overlay가 아니라 `settings` 탭이다. 상단 설정 버튼은 현재 흐름을 기억하고 설정 화면의 `이전` 버튼으로 돌아간다.
- 한 탭 안에서 내용이 길어지는 관리 화면은 내부 스크롤 영역을 만들지 말고 내부 섹션 탭이나 결과/설정 모드로 나눈다. 기본 섹션은 현황/요약만 보여주고, 편집/고급 설정은 사용자가 선택한 섹션에만 렌더링한다. 전역 관리 진입은 앱 네비게이션이나 모바일 관리 메뉴로만 제공하고, topbar 안에 같은 목적의 버튼을 중복 배치하지 않는다.
- 각 탭은 독립된 페이지 스크롤 위치를 가진다. PC/태블릿과 모바일 모두 기본은 window/page 스크롤이며, 알림·전략 운영처럼 내부 섹션 탭이 있는 화면은 섹션별 위치만 저장한다.

## Components

- Panel: 모든 주요 컨테이너는 `.panel`과 `.panel-head`를 사용한다. 헤더 왼쪽은 label/title, 오른쪽은 metric/status/action이다.
- Deskbar: PC/태블릿의 `.deskbar-full`은 홈 관제 화면에만 둔다. 업무 탭에는 `.deskbar-compact`를 만들지 않는다. 같은 상태 정보가 필요하면 상단 nav 오른쪽 상태, `app-nav-command` 지표, 각 패널 내부 상태 셀에서만 제공한다.
- Managed page: 모든 탭은 `.managed-page` 안에 `.page-command-strip`과 `.admin-grid`를 둔다. PC에서는 `page-command-context`, `page-flow-spine`, 핵심 지표, 결과/설정 전환을 `app-nav-command`로 합치고 본문 `page-command-strip`은 시각적으로 숨긴다. 모바일에서는 `page-command-strip-compact`가 한 줄 컨텍스트 바이며 `page-flow-spine`으로 `이전 입력`, `현재 처리`, `다음 출력`을 반드시 보여준다. 사용자가 실제로 봐야 할 첫 판단은 `page-routine-panel`이 맡는다.
- Routine panel: `page-routine-panel`은 사용자가 처음 보는 결론이다. `현재 상태`는 숫자/상태, `왜 봐야 하나`는 판단 이유, `다음 행동`은 하나의 primary 액션만 둔다. 이 패널에 여러 버튼이나 긴 설명을 넣으면 실패다.
- Flow spine: `page-flow-spine`은 전체 데이터 파이프라인에서 현재 탭의 위치를 보여주는 얇은 레일이다. 업무 탭마다 `이전 입력`, `현재 처리`, `다음 출력` 세 노드를 유지하고, 카드나 설명 박스로 확장하지 않는다.
- Page mode switch: PC에서는 `page-mode-switch`를 `app-nav-command` 안에 두고, 모바일에서는 `page-command-context` 안에 둔다. 이 스위치가 있는 화면은 기본 `결과` 모드에서 설정 폼과 고급 정책을 노출하지 않는다. 사용자가 설정 모드로 들어가야 저장/편집/고급 항목이 나타난다.
- Process rail: 데이터 처리 순서, 모델링 순서, 추론 순서는 `.process-rail` 또는 `.ontology-control-strip`을 사용한다. 단계 번호, 단계명, 입력/출력 개수를 함께 보여준다.
- Formula ledger: 공식은 카드 설명이 아니라 `.formula-ledger` 표형 행으로 표시한다. 영역, 공식명, 실제 표현식을 한 줄에서 비교할 수 있어야 한다.
- Relation matrix: 관계 분석 타입은 `.relation-matrix`로 보여준다. 규칙 구조에만 있는 관계와 현재 데이터에서 실제 사용된 관계를 구분한다.
- Button: 기본은 `.text-button`, 주요 저장/갱신은 `.text-button.primary`, 보조 소형 액션은 `.mini-button`, 아이콘 단독 액션은 `.icon-button`이다.
- Form: `label.setting-field` 안에 label text와 input/select/textarea를 넣는다. 2열 폼은 `.settings-grid`, 전체 폭 입력은 `.setting-field.wide`를 사용한다. 모든 `input`, `select`, `textarea`는 전역 `Professional form control replacement system` 레이어를 통과해야 하며, select는 native 화살표 그대로 두지 않고 커스텀 chevron, 44px 높이, 명확한 focus ring, disabled/placeholder 상태를 가진다. `textarea`는 자동 높이 확장을 쓰고 자체 스크롤바를 만들지 않는다. checkbox/radio도 native accent만 쓰지 않고 직접 그린 체크/라디오 마크, checked/focus/disabled 상태, 선택된 label surface를 가진다.
- Status: 상태는 `.status-pill`, 판단 톤은 `.tone-chip`, 작은 속성은 `.chip`을 사용한다.
- List row: 반복 항목은 `*-row` 클래스로 만들고, 내부는 `main + side/actions` 구조를 따른다.
- Desktop record card: PC에서는 `.notification-decision-row`, `.symbol-result-row`, `.alert-row`, `.signal-row`, `.valuation-row`, `.lab-row`, `.account-card`, `.monitoring-instrument-row` 같은 반복 항목을 낮은 radius 금융 카드로 만든다. 카드가 과하게 둥글거나 그림자에 의존하면 실패이며, 좌측 상태선, 명확한 border, 2열 이상 정보 구조를 우선한다.
- Summary strip: 패널 내부 요약은 `*-metric`, `*-status`, `*-row` 형태의 작은 금융 셀로 표현한다. 각 셀은 카드처럼 독립되되, 색 면보다 라벨/값/상태선으로 의미를 만든다.
- Full card replacement: `.deskbar-cell`, `.page-command-metric`, `.notification-ops-cell`, `.monitor-ledger-cell`, `.monitor-runtime-row`, `.ontology-control-step`, `.relation-matrix-row`, `.ontology-surface`, `.formula-ledger-row`, `.strategy-data-row`, `.settings-status-band`처럼 화면 골격 또는 도메인 모델을 표현하는 모든 카드형 요소는 `Full financial card replacement system` 레이어의 적용 대상이다. 새 카드형 UI를 만들 때는 이 레이어에 포함되는 클래스명을 쓰거나 해당 selector에 추가한다.
- Snackbar: 저장/갱신 결과는 `.snackbar`를 쓰며, 화면 하단 탭과 겹치지 않게 `--bottom-tabs-height`를 반영한다.

## Page Contracts

- 관제 홈: PC에서는 운영 현황과 계정 DB를 7:5로 먼저 배치하고, 계정별 관심 종목과 모니터링 요약은 6:6으로 이어서 보여준다. 계정 DB처럼 좁은 보조 패널 안의 계정 카드는 1열로 접어 텍스트와 칩이 눌리지 않게 한다. 빠른 이동 버튼은 `home-action`을 쓰고 별도 랜딩 섹션을 만들지 않는다.
- 계정·연결: PC에서는 저장 계정 목록과 편집 폼을 같은 화면에 노출한다. 목록 rail은 충분한 폭을 유지하고, 저장 계정 행은 `account-exposure-grid`로 토스 API, 계좌 seq, 텔레그램, 관심종목 상태를 가로 비교할 수 있게 한다. API secret 원문은 표시하지 않는다. 저장/삭제는 계정 폼과 행 오른쪽 액션 위치를 따른다.
- 관심 관리: 계정 선택 rail과 편집 workbench를 한 패널 안에 둔다. PC rail은 계정명/상태를 읽을 수 있는 폭을 확보하고, 관심 종목 추가/수정/삭제는 선택 계정에만 적용한다. 종목 탐색 결과를 이 탭에 섞지 않는다.
- 종목 탐색: `symbols` 탭은 시장 카탈로그 전용 화면이다. 검색/표시 수/추가 대상/select/갱신 버튼 순서를 유지한다. 같은 액션이 행마다 반복되면 상단 일괄 액션을 먼저 제공하고, 행 오른쪽 액션은 보조로 낮춘다.
- 모니터링: PC에서는 실행 상태와 알림 센터를 상단 6:6 균형형으로 배치하고, 보유·관심 통합 목록 8컬럼, 포트폴리오 노출 4컬럼으로 이어간다. 실행 상태 패널은 명령어 카드가 아니라 `monitor-board` 안에서 현재 실행 상태, 핵심 상태 ledger, 런타임 타임라인, 최근 모니터링 알림 요약, 시장별 현금 순서의 구획형 섹션으로 보여준다. 현재 실행 상태의 대표 카드는 `monitor-primary-head`와 `monitor-primary-copy`로 칩/제목/메타를 분리해 좁은 화면에서도 텍스트가 눌리거나 겹치지 않아야 한다. 최근 알림처럼 긴 문맥이 필요한 정보는 작은 ledger cell에 압축하지 않고 별도 요약 박스로 분리한다. 보유와 관심은 같은 `monitoring-instrument-row` 패턴으로 보여주고, 보유/관심 칩으로 상태를 구분한다.
- 알림 운영: 기본 진입은 `현황`이며 탭 스트립 바로 아래에 `notification-ops-rail`로 대기/발송/보류/실패/룰/주기/템플릿/스케줄을 한 줄 상태표로 보여준 뒤 최근 판단 목록을 전폭으로 배치한다. 최근 판단 항목은 제목보다 `점수 변화`, `기준/이전 대비 delta`, `상승·감점 요인`, `게이트와 보류 조건`을 먼저 보여준다. 긴 메시지는 본문 열에만 두며 상태/액션 열을 분리한다. `정책`, `템플릿`, `진단` 내부 섹션으로 나누고, 내부 섹션 이동은 카드형 버튼이 아니라 알림탭 최상단 탭 스트립으로 제공한다. 신호 섹션은 실행 상태/알림 센터 6:6, 통합 종목/계좌 노출 8:4로 배치한다. 고급 설정은 진단 섹션에 둔다.
- 투자 판단: 내부 섹션은 `오늘의 판단`, `투자 근거`, `통합 차트`, `전략 룰`, `온톨로지`, `검증·리뷰` 순서로 유지한다. 사용자는 `오늘의 판단 -> 투자 근거 -> 검증·리뷰` 3단계로 읽어야 하며, 룰 편집은 설정 모드에만 노출한다. 기본 진입은 오늘의 판단이며 액션 큐와 현재 판단 결과만 짧게 보여준다. 투자 근거는 reasoning card와 실행 계획, 통합 차트는 가격·수급·자금흐름·매크로 신호의 같은 축 비교, 온톨로지는 TBox/ABox/InferenceBox 그래프와 `Relation Matrix`, 전략 룰은 설정 모드의 RuleBox·프롬프트 관리, 검증·리뷰는 재계산 결과와 데이터 품질·관계 추적을 담당한다.
- 뉴스·근거: 기본 진입은 `영향 인박스`이며 기사 제목 목록이 아니라 본문 요약과 주가 영향 판단을 먼저 보여준다. 각 카드의 상단은 `호재/악재/중립`, 관련 종목, 영향 점수이고, 본문은 한글 요약과 판단 이유이며, 기사 제목·출처·시간·원문 링크는 카드 하단 `기사` 영역에만 둔다. `근거 DB`는 같은 순서로 상세 근거를 펼치고, `수집원`은 채널/품질 상태만 담당한다. 뉴스 아카이브, 공시 원천, 중요도 게이트 같은 입력 정책은 `settings` 모드의 `피드 설정`으로 분리한다.
- 운영 설정: 앱 표시/전달/외부 연결 설정만 관리한다. 설정 상단은 hero가 아니라 저장 상태, 로컬 우선, 잠금/오류를 보여주는 상태 밴드다. 바로 아래에는 계정·연결, 알림 운영, 투자 판단, 뉴스·근거의 결과 화면과 설정 화면 책임을 연결하는 `Control Map`을 둔다. 화면/알림 전달은 `기본 설정`, 외부 API·게이트·매핑은 접힌 `고급 설정`, 저장 가능 여부와 오류는 `진단`으로 분리한다. 계정 secret과 모델 공식은 각각 계정/모델링 탭에서 관리하고, 저장은 상태 밴드 아래의 스마트 저장 바로 제공한다. 저장할 변경이 없으면 저장 버튼은 `저장됨` 상태로 비활성화하고, 입력 변경/저장 실패 때만 주요 액션으로 승격한다.

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
- PC 1181px 이상에서 상단 탭 바가 한 줄로 유지되고, 본문이 화면 폭을 충분히 쓰는가
- 태블릿 861-1180px에서도 상단 탭형 내비게이션이 유지되고, 탭이 한 줄 스크롤로 압축되는가
- PC/태블릿에서 full deskbar는 홈에만 있고, 업무 탭은 deskbar와 본문 command strip 없이 `app-nav-command`, routine panel, 실제 콘텐츠 순서로 바로 내려가는가
- 각 업무 탭이 전체 파이프라인의 `이전 입력`, `현재 처리`, `다음 출력`을 보여줘 탭 사이 데이터 흐름을 잃지 않는가
- 주요 액션이 app-nav command rail 또는 panel-head 오른쪽에 있는가
- 저장/삭제/갱신 버튼의 톤이 역할과 일치하는가
- 구현 편의 때문에 사용자가 더 많이 추측하거나 이동해야 하는 화면이 되지 않았는가
- 반복 버튼이나 펼쳐진 고급 설정 때문에 사용자가 핵심 상태를 놓치지 않는가
- 긴 관리 화면이 기본 진입부터 모든 설정을 펼치지 않고, 현황/편집/고급 섹션과 선택 상세 패널로 재구성되어 있는가
- 탭 본문 안의 목록, 사이드 컬럼, 메시지 본문, 근거 목록이 자체 스크롤바를 갖지 않고 페이지 전체 스크롤이나 별도 화면으로 처리되는가
- 긴 입력값과 메시지 원문도 작은 내부 스크롤 박스가 아니라 자동 높이 확장이나 별도 화면으로 처리되는가
- 모바일 기본 내비게이션이 5개 이하이며 가로 스크롤 없이 핵심 업무에 닿는가
- 모바일에서 app-nav는 safe-area를 지키며 스크롤 방향에 따라 접히고, tab-bar는 bottom edge에 붙어 있으며, 100dvh 셸을 반영하는가
- PC/태블릿에서 계좌/관심종목/홈/알림/전략 화면이 너무 일찍 1열로 접히지 않고 비교/편집에 맞는 2열 이상 구조를 유지하는가
- 로딩/오류/잠금 상태가 어떤 데이터 소스에서 발생했는지 드러나는가
- 고급 금융앱처럼 정보 위계, 여백, 상태 피드백, 예외 처리가 충분히 전문적으로 보이는가
- desktop 2열 이상, mobile 1열 전환에서 텍스트와 버튼이 겹치지 않는가
- secret 또는 개인 데이터 원문이 화면, 정적 preview, 문서, 테스트에 들어가지 않는가
- 새 CSS 색상/간격을 직접 추가하지 않고 `--ds-*` 토큰을 재사용했는가
- 전체 종목, 알림 템플릿, 계정별 관심 종목처럼 긴 텍스트가 실제 데이터로 넘치지 않는가
