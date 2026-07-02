# Exit Lens Design System

Exit Lens의 화면은 로컬 운영 콘솔입니다. 투자 판단, 계정, 알림, 모델 기준을 자주 확인하는 사용자가 빠르게 스캔하고 같은 위치에서 같은 행동을 할 수 있게 만드는 것이 우선입니다.

## 원칙

- 정보 밀도를 유지한다. 운영 화면은 마케팅형 hero나 장식 카드보다 표, 리스트, 상태 칩, 짧은 설명을 우선한다.
- 화면마다 같은 골격을 쓴다. 상단은 현재 탭 제목과 상태/새로고침/설정 액션, 본문은 좌측 탭 내비게이션과 우측 작업 영역이다.
- 주요 액션은 예측 가능한 오른쪽에 둔다. 페이지 전역 액션은 topbar 오른쪽, 패널 저장/갱신은 `panel-head` 오른쪽, 긴 설정 화면의 저장 액션은 하단 sticky 영역에 둔다.
- 위험 액션은 `danger` 톤만 쓴다. 삭제, 제거, 되돌릴 수 없는 작업은 일반 버튼과 색상/라벨을 분리한다.
- 텍스트는 줄바꿈을 허용한다. 종목명, URL, 공식, 계정 ID처럼 긴 값은 `overflow-wrap: anywhere` 또는 말줄임 규칙을 적용한다.
- 로컬 우선과 보안 상태는 UI에 반영한다. secret 원문은 다시 표시하지 않고 `설정됨`, `미설정`, `저장됨` 같은 상태 칩으로 표현한다.

## Finance App Tone

전문 금융앱 같은 룩앤필은 장식이 아니라 안정감, 예측 가능성, 빠른 비교에서 나온다. Exit Lens는 토스처럼 흰색/저채도 회색 표면, 파란 주요 액션, 녹색/빨간색 상태색만 제한적으로 쓴다.

- 표면은 평평하게 둔다. `.panel`, `.tab-bar`, `.snackbar` 외에는 큰 그림자를 쓰지 않고, 패널 그림자도 얕게 유지한다.
- 카드 안에 다시 카드처럼 보이는 중첩 프레임을 만들지 않는다. 반복 항목은 8px radius의 행, 칩, 구분선으로 구분한다.
- 숫자, 수익률, 개수, 시간은 스캔이 쉽도록 tabular number 규칙을 따른다.
- 보유/관심/알림/모델 값은 같은 위치에 같은 톤으로 반복한다. 새 화면도 `main + side/actions` 행 구조를 기본값으로 쓴다.
- 파랑은 저장, 검색, 갱신, 현재 선택 같은 주요 액션에만 쓴다. 보조 정보는 회색 표면과 muted text로 낮춘다.
- 업종명, 계정명, 템플릿 변수처럼 길어질 수 있는 값은 오른쪽 정렬 영역에서도 줄바꿈을 허용해 화면 폭을 넘기지 않는다.

## Tokens

CSS의 기준 토큰은 [public/styles.css](../public/styles.css)의 `--ds-*` 변수다. 기존 컴포넌트 호환을 위해 `--bg`, `--ink`, `--blue` 같은 legacy 변수는 `--ds-*` alias로 남긴다.

- Color: `--ds-color-bg`, `--ds-color-panel`, `--ds-color-panel-soft`, `--ds-color-ink`, `--ds-color-muted`, `--ds-color-line`
- Action text: `--ds-color-on-action`
- State color: `--ds-color-action`, `--ds-color-positive`, `--ds-color-warning`, `--ds-color-danger`, `--ds-color-accent`와 각 `*-soft`
- Spacing: `--ds-space-1`부터 `--ds-space-9`까지 4px 기반 계단
- Radius: `--ds-radius-control`, `--ds-radius-panel`은 8px
- Control height: `--ds-control-height-sm` 30px, `--ds-control-height-md` 38px, `--ds-control-height-lg` 44px
- Layout: `--ds-shell-width` 1280px, `--ds-sidebar-width` 224px
- Shadow/focus: `--ds-shadow-panel`, `--ds-shadow-floating`, `--ds-focus`

## Page Layout

- 모든 앱 화면은 `main.shell` 아래에 배치한다.
- `section.topbar`는 현재 탭의 `h1`, 짧은 subtitle, 우측 상태/새로고침/설정 버튼을 가진다.
- desktop `861px+`에서는 `workspace-layout`을 `sidebar nav + content`로 두고, `tab-bar`는 sticky left rail이다.
- mobile `860px 이하`에서는 `tab-bar`를 하단 고정 내비게이션으로 쓰고, 본문 그리드는 1열로 접는다.
- 화면 본문은 기본적으로 `admin-grid`를 사용한다. 핵심 패널이나 설정/관리 패널은 `grid-column: 1 / -1`로 전체 폭을 사용한다.

## Navigation

- 탭은 `tabs` 배열이 단일 source of truth다. 새 탭은 배열에 추가하고 `renderActiveTab`에서 같은 순서로 처리한다.
- desktop 탭은 라벨과 설명을 함께 보여준다. mobile 탭은 라벨 중심으로 압축한다.
- 활성 탭 버튼은 `active`와 `aria-current="page"`를 함께 가진다.
- 설정은 별도 overlay가 아니라 `settings` 탭이다. 상단 설정 버튼은 현재 흐름을 기억하고 설정 화면의 `이전` 버튼으로 돌아간다.

## Components

- Panel: 모든 주요 컨테이너는 `.panel`과 `.panel-head`를 사용한다. 헤더 왼쪽은 label/title, 오른쪽은 metric/status/action이다.
- Button: 기본은 `.text-button`, 주요 저장/갱신은 `.text-button.primary`, 보조 소형 액션은 `.mini-button`, 아이콘 단독 액션은 `.icon-button`이다.
- Form: `label.setting-field` 안에 label text와 input/select/textarea를 넣는다. 2열 폼은 `.settings-grid`, 전체 폭 입력은 `.setting-field.wide`를 사용한다.
- Status: 상태는 `.status-pill`, 판단 톤은 `.tone-chip`, 작은 속성은 `.chip`을 사용한다.
- List row: 반복 항목은 `*-row` 클래스로 만들고, 내부는 `main + side/actions` 구조를 따른다.
- Snackbar: 저장/갱신 결과는 `.snackbar`를 쓰며, 화면 하단 탭과 겹치지 않게 `--bottom-tabs-height`를 반영한다.

## Page Contracts

- 홈: 운영 현황, 계정 DB, 계정별 관심 종목, 모니터링 요약을 같은 패널 그리드 안에서 보여준다. 빠른 이동 버튼은 `home-action`을 쓰고 별도 랜딩 섹션을 만들지 않는다.
- 계정: API secret 원문은 표시하지 않고 `account-credential-*` 상태 요약만 보여준다. 저장/삭제는 계정 폼과 행 오른쪽 액션 위치를 따른다.
- 관심종목: 계정 선택 rail과 편집 workbench를 한 패널 안에 둔다. 관심 종목 추가/수정/삭제는 선택 계정에만 적용되고, 전체 종목 검색 결과를 이 탭에 섞지 않는다.
- 전체종목: `symbols` 탭은 시장 카탈로그 전용 화면이다. 검색/표시 수/추가 대상/select/갱신 버튼 순서를 유지하고, 행 오른쪽은 업종, 추가 대상, 액션만 둔다.
- 모니터링: 보유와 관심을 같은 `monitoring-instrument-row` 패턴으로 보여주고, 보유/관심 칩으로 상태를 구분한다.
- 알림: 메시지 타입 행 안에 사용 여부, cadence, 스케줄, 템플릿 편집을 함께 둔다. 시스템 템플릿은 별도 섹션으로 분리한다.
- 모델링: 모델 기준, feature 설명, 재현성 결과는 dense grid를 쓰고 장식형 hero를 만들지 않는다.
- 설정: 앱 표시/전달/외부 연결 설정만 관리한다. 계정 secret과 모델 공식은 각각 계정/모델링 탭에서 관리하고, 저장은 상단과 하단 sticky 패널에 모두 제공한다.

## Button Placement

- 전역: topbar 오른쪽 순서는 상태, 새로고침, 설정이다.
- 패널: 저장, 갱신, 테스트 같은 패널 액션은 `panel-head` 오른쪽에 둔다.
- 폼 하단: 긴 설정처럼 입력이 많은 화면은 `.settings-save-panel`을 하단 sticky로 유지한다.
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

- topbar와 tab navigation을 우회하는 별도 내비게이션을 만들지 않았는가
- 주요 액션이 topbar 또는 panel-head 오른쪽에 있는가
- 저장/삭제/갱신 버튼의 톤이 역할과 일치하는가
- desktop 2열 이상, mobile 1열 전환에서 텍스트와 버튼이 겹치지 않는가
- secret 또는 개인 데이터 원문이 화면, 정적 preview, 문서, 테스트에 들어가지 않는가
- 새 CSS 색상/간격을 직접 추가하지 않고 `--ds-*` 토큰을 재사용했는가
- 전체 종목, 알림 템플릿, 계정별 관심 종목처럼 긴 텍스트가 실제 데이터로 넘치지 않는가
