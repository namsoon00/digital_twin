const childProcess = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const net = require("net");
const os = require("os");
const path = require("path");
const vm = require("vm");

const rootDir = path.resolve(__dirname, "..");
const requestTimeoutMs = Number(process.env.SMOKE_REQUEST_TIMEOUT_MS || 30000);

function randomPort() {
  return 43000 + (crypto.randomBytes(2).readUInt16BE(0) % 1000);
}

function waitForServer(child) {
  return new Promise(function (resolve, reject) {
    let settled = false;
    let output = "";
    const timer = setTimeout(function () {
      if (settled) return;
      settled = true;
      reject(new Error("서버 시작 시간이 초과되었습니다."));
    }, 10000);

    function finish(port) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(port);
    }

    function read(chunk) {
      output += chunk.toString();
      const match = output.match(/http:\/\/127\.0\.0\.1:(\d+)/);
      if (match) finish(Number(match[1]));
    }

    child.stdout.on("data", read);
    child.stderr.on("data", function (chunk) {
      output += chunk.toString();
    });
    child.on("error", function (error) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.on("exit", function (code) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error("서버가 시작 전에 종료되었습니다. exit=" + code + "\n" + output.trim()));
    });
  });
}

function request(port, pathname, options) {
  return new Promise(function (resolve, reject) {
    const method = options && options.method ? options.method : "GET";
    const inputHeaders = options && options.method ? options.headers || {} : options || {};
    const body = options && options.body ? options.body : "";
    const headers = Object.assign({}, inputHeaders || {});
    const hasContentLength = Object.keys(headers).some(function (name) {
      return name.toLowerCase() === "content-length";
    });
    headers.Host = "127.0.0.1:" + port;
    headers.Connection = "close";
    if (body && !hasContentLength) headers["Content-Length"] = Buffer.byteLength(body);

    const socket = net.createConnection({ host: "127.0.0.1", port: port }, function () {
      const lines = [method + " " + pathname + " HTTP/1.1"];
      Object.keys(headers).forEach(function (name) {
        const value = headers[name];
        if (value == null) return;
        lines.push(name + ": " + value);
      });
      lines.push("", "");
      socket.write(lines.join("\r\n"));
      if (body) socket.write(body);
      socket.end();
    });
    const chunks = [];
    socket.setTimeout(requestTimeoutMs);
    socket.on("data", function (chunk) {
      chunks.push(chunk);
    });
    socket.on("timeout", function () {
      socket.destroy(new Error("요청 시간이 초과되었습니다: " + pathname));
    });
    socket.on("error", reject);
    socket.on("end", function () {
      try {
        const payload = Buffer.concat(chunks);
        const headerEnd = payload.indexOf(Buffer.from("\r\n\r\n"));
        if (headerEnd < 0) throw new Error("HTTP 응답 헤더를 찾지 못했습니다: " + pathname);
        const headerText = payload.slice(0, headerEnd).toString("latin1");
        const lines = headerText.split("\r\n");
        const statusMatch = lines.shift().match(/^HTTP\/\d(?:\.\d)?\s+(\d+)/);
        if (!statusMatch) throw new Error("HTTP 상태 줄이 올바르지 않습니다: " + headerText.split("\r\n")[0]);
        const responseHeaders = {};
        lines.forEach(function (line) {
          const index = line.indexOf(":");
          if (index < 0) return;
          const name = line.slice(0, index).trim().toLowerCase();
          const value = line.slice(index + 1).trim();
          responseHeaders[name] = responseHeaders[name] ? responseHeaders[name] + ", " + value : value;
        });
        resolve({
          statusCode: Number(statusMatch[1]),
          headers: responseHeaders,
          body: payload.slice(headerEnd + 4).toString("utf8")
        });
      } catch (error) {
        reject(error);
      }
    });
  });
}

function websocketHandshake(port) {
  return new Promise(function (resolve, reject) {
    const key = crypto.randomBytes(16).toString("base64");
    const socket = net.createConnection({ host: "127.0.0.1", port: port }, function () {
      socket.write([
        "GET /ws HTTP/1.1",
        "Host: 127.0.0.1:" + port,
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: " + key,
        "Sec-WebSocket-Version: 13",
        "",
        ""
      ].join("\r\n"));
    });
    let data = "";
    const timer = setTimeout(function () {
      socket.destroy();
      reject(new Error("웹소켓 핸드셰이크 시간이 초과되었습니다."));
    }, 5000);
    socket.on("data", function (chunk) {
      data += chunk.toString("latin1");
      if (data.indexOf("\r\n\r\n") >= 0) {
        clearTimeout(timer);
        socket.end();
        resolve(data);
      }
    });
    socket.on("error", function (error) {
      clearTimeout(timer);
      reject(error);
    });
  });
}

function assertOk(condition, message) {
  if (!condition) throw new Error(message);
}

function checkFrontendAdminRender() {
  const appDefaultsCode = fs.readFileSync(path.join(rootDir, "public", "app-default-settings.js"), "utf8");
  const code = appDefaultsCode + "\n" + fs.readFileSync(path.join(rootDir, "public", "app.js"), "utf8");
  const styles = fs.readFileSync(path.join(rootDir, "public", "styles.css"), "utf8");
  const indexHtml = fs.readFileSync(path.join(rootDir, "public", "index.html"), "utf8");
  const designSystemDoc = fs.readFileSync(path.join(rootDir, "docs", "design-system.md"), "utf8");
  assertOk(styles.indexOf("--ds-color-bg") >= 0, "전역 디자인 시스템 색상 토큰이 없습니다.");
  assertOk(styles.indexOf("--ds-color-on-action") >= 0, "주요 액션 텍스트 토큰이 없습니다.");
  assertOk(styles.indexOf("--ds-color-bg: #f3f5f8") >= 0, "기관형 금융 콘솔 배경 토큰이 적용되지 않았습니다.");
  assertOk(styles.indexOf("--ds-color-action: #1457a8") >= 0, "기관형 액션 블루 토큰이 적용되지 않았습니다.");
  assertOk(styles.indexOf("--ds-color-orbit-line: #2f6fbb") >= 0, "기관형 신호 라인 토큰이 적용되지 않았습니다.");
  assertOk(styles.indexOf("--ds-color-orbit-signal: #137a63") >= 0, "기관형 시그널 그린 토큰이 적용되지 않았습니다.");
  assertOk(code.indexOf('id: "orbit-alpha-console-v2"') >= 0, "웹 스타일 계약 ID가 앱 코드에 정의되지 않았습니다.");
  assertOk(styles.indexOf("Orbit Alpha web style contract: orbit-alpha-console-v2") >= 0, "웹 스타일 계약 CSS 레이어가 없습니다.");
  assertOk(designSystemDoc.indexOf("Web Style Contract") >= 0 && designSystemDoc.indexOf("orbit-alpha-console-v2") >= 0, "디자인 시스템 문서에 웹 스타일 계약이 정의되지 않았습니다.");
  assertOk(styles.indexOf("--ds-color-page-top") >= 0 && styles.indexOf("--ds-color-page-bottom") >= 0, "페이지 배경 그라데이션 토큰이 없습니다.");
  assertOk(/html\[data-theme="dark"\]\s*\{[\s\S]*--ds-color-page-top: #0f1720;[\s\S]*--ds-color-page-bottom: #070b10;[\s\S]*--ds-shadow-bottom-tabs:/.test(styles), "다크모드 전용 페이지 배경/하단 탭 토큰이 없습니다.");
  assertOk(/body\s*\{[\s\S]*background: linear-gradient\(180deg, var\(--ds-color-page-top\) 0, var\(--bg\) 240px, var\(--ds-color-page-bottom\) 100%\);/.test(styles), "body 배경이 테마 토큰을 따르지 않습니다.");
  assertOk(styles.indexOf("background: linear-gradient(180deg, #f7f9fc") < 0, "body 배경에 라이트 고정 그라데이션이 남아 있습니다.");
  assertOk(code.indexOf('document.documentElement.setAttribute("data-theme", theme)') >= 0 && code.indexOf('document.documentElement.setAttribute("data-theme-setting", currentAppTheme())') >= 0, "테마 설정이 document root 속성으로 반영되지 않습니다.");
  assertOk(code.indexOf('window.matchMedia("(prefers-color-scheme: dark)")') >= 0 && code.indexOf('if (name === "appTheme") applyAppTheme();') >= 0, "시스템/설정 변경 시 테마 재적용 경로가 없습니다.");
  assertOk(styles.indexOf("--surface: var(--ds-color-panel-soft)") >= 0, "보조 표면 alias가 없습니다.");
  assertOk(styles.indexOf("--ds-control-height-md") >= 0, "전역 컨트롤 높이 토큰이 없습니다.");
  assertOk(styles.indexOf("--ds-page-gap") >= 0 && styles.indexOf("--ds-row-pad-x") >= 0, "전역 레이아웃 간격 토큰이 없습니다.");
  assertOk(styles.indexOf("--ds-card-bg") >= 0 && styles.indexOf("--ds-card-border") >= 0, "금융 카드 토큰이 없습니다.");
  assertOk(styles.indexOf("Institutional finance card layer") >= 0, "금융 카드 레이어 규칙이 없습니다.");
  assertOk(styles.indexOf("Full financial card replacement system") >= 0, "전면 금융 카드 교체 레이어가 없습니다.");
  assertOk(styles.indexOf("Professional form control replacement system") >= 0, "전문 form control 교체 레이어가 없습니다.");
  assertOk(/select\s*\{[\s\S]*appearance: none;[\s\S]*background-image:[\s\S]*data:image\/svg\+xml/.test(styles), "select 박스가 커스텀 chevron 스타일을 쓰지 않습니다.");
  assertOk(/input:not\(\[type="checkbox"\]\):not\(\[type="radio"\]\):focus,[\s\S]*select:focus,[\s\S]*textarea:focus\s*\{[\s\S]*box-shadow: 0 0 0 3px var\(--blue-soft\)/.test(styles), "입력 요소 focus ring이 금융 콘솔 스타일로 통일되지 않았습니다.");
  assertOk(code.indexOf("bindAutoGrowingTextareas") >= 0 && code.indexOf("resizeTextareaToContent") >= 0, "긴 입력값을 내부 스크롤 없이 자동 확장하는 textarea 경로가 없습니다.");
  assertOk(/textarea\s*\{[\s\S]*overflow-y: hidden;[\s\S]*resize: none;/.test(styles), "textarea가 자체 스크롤/수동 리사이즈 상자로 남아 있습니다.");
  assertOk(/\.setting-field textarea,[\s\S]*\.notification-template-row textarea,[\s\S]*\.admin-message-template textarea\s*\{[\s\S]*overflow-y: hidden;[\s\S]*resize: none;/.test(styles), "구체 textarea 컴포넌트가 자동 확장 규칙으로 고정되지 않았습니다.");
  assertOk(/\.notification-full-message,[\s\S]*pre\.notification-full-message\s*\{[\s\S]*max-height: none;[\s\S]*overflow: visible;/.test(styles), "긴 알림 본문이 내부 스크롤 박스로 남아 있습니다.");
  assertOk(/input\[type="checkbox"\],[\s\S]*input\[type="radio"\]\s*\{[\s\S]*appearance: none;/.test(styles), "checkbox/radio가 native accent 컨트롤로 남아 있습니다.");
  assertOk(/input\[type="checkbox"\]:checked::before\s*\{[\s\S]*rotate\(-45deg\) scale\(1\);/.test(styles), "커스텀 checkbox 체크 마크가 없습니다.");
  assertOk(/:where\(\.notification-rule-toggle, \.admin-check-field, \.notification-rule-market-option\):has\(input:checked\)/.test(styles), "선택된 checkbox label surface 스타일이 없습니다.");
  assertOk(styles.indexOf(".notification-ops-cell") >= 0 && styles.indexOf(".ontology-control-step") >= 0 && styles.indexOf(".formula-ledger-row") >= 0, "도메인별 카드형 UI가 전면 카드 시스템에 포함되지 않았습니다.");
  assertOk(styles.indexOf(".settings-status-band") >= 0 && styles.indexOf(".deskbar-cell") >= 0 && styles.indexOf(".page-command-metric") >= 0, "상태/네비게이션 카드형 UI가 전면 카드 시스템에 포함되지 않았습니다.");
  assertOk(styles.indexOf("font-variant-numeric: tabular-nums") >= 0, "금융 숫자 표시 규칙이 없습니다.");
  assertOk(styles.indexOf(".app-shell") >= 0 && styles.indexOf("100dvh") >= 0, "앱형 100dvh 셸 규칙이 없습니다.");
  assertOk(styles.indexOf("touch-action: manipulation") >= 0 && styles.indexOf("@media (hover: none)") >= 0, "모바일 터치 반응성 규칙이 없습니다.");
  assertOk(code.indexOf("syncAppNavScrollState") >= 0 && styles.indexOf(".app-nav.is-hidden") >= 0, "모바일 상단 앱바 자동 접힘 규칙이 없습니다.");
  assertOk(code.indexOf("bindTabNavigation") >= 0 && code.indexOf('button.addEventListener("pointerup"') >= 0 && code.indexOf('button.addEventListener("touchend"') >= 0, "스크롤 중 모바일 탭 전환을 위한 pointer/touch 입력 경로가 없습니다.");
  assertOk(code.indexOf("stopActiveScrollMomentum") >= 0 && code.indexOf("activateTabButton") >= 0, "탭 전환 시 스크롤 관성을 멈추는 경로가 없습니다.");
  assertOk(/\.tab-bar\s*\{[\s\S]*touch-action: manipulation;/.test(styles) && /\.tab-bar button\s*\{[\s\S]*touch-action: manipulation;/.test(styles), "하단 탭 터치 조작 최적화 CSS가 없습니다.");
  assertOk(/@media \(max-width: 860px\)[\s\S]*\.tab-bar\s*\{[\s\S]*box-shadow: var\(--ds-shadow-bottom-tabs\);/.test(styles), "모바일 하단 탭 그림자가 테마 토큰을 따르지 않습니다.");
  assertOk(code.indexOf("scrollY > 120 && delta >= 0") >= 0 && code.indexOf("scrollY > 72 && delta > 6") >= 0, "모바일 스크롤 복원 상태에서 상단 앱바가 충분히 빨리 접히지 않습니다.");
  assertOk(/@media \(max-width: 860px\)[\s\S]*\.app-brand-copy strong[\s\S]*color: var\(--ink\);/.test(styles), "모바일 상단 앱바 브랜드명이 명확하게 보이지 않습니다.");
  const mobileSpacingAuditLayerStart = styles.indexOf("/* Mobile spacing audit layer */");
  const mobileSpacingAuditLayer = mobileSpacingAuditLayerStart >= 0 ? styles.slice(mobileSpacingAuditLayerStart) : "";
  assertOk(mobileSpacingAuditLayerStart >= 0, "모바일 최종 spacing 감사 레이어가 없습니다.");
  assertOk(/\.app-nav\s*\{[\s\S]*margin: 0 0 var\(--ds-space-8\);/.test(mobileSpacingAuditLayer), "모바일 앱바 아래 간격이 최종 레이어에서 보정되지 않습니다.");
  assertOk(/\.managed-page,[\s\S]*\.settings-view\s*\{[\s\S]*gap: var\(--ds-space-7\);/.test(mobileSpacingAuditLayer), "모바일 페이지/탭 카드 간격이 최종 레이어에서 통일되지 않습니다.");
  assertOk(/\.model-guide-grid,[\s\S]*\.source-stack\s*\{[\s\S]*padding: var\(--ds-section-gap\) var\(--ds-panel-pad-x\) var\(--ds-panel-pad-y\);/.test(mobileSpacingAuditLayer), "모바일 패널 헤더와 카드 본문 사이 padding 보정이 없습니다.");
  assertOk(/\.process-rail,[\s\S]*\.investment-bridge-flow,[\s\S]*\.more-action-list\s*\{[\s\S]*grid-template-columns: 1fr;[\s\S]*gap: var\(--ds-space-5\);/.test(mobileSpacingAuditLayer), "모바일 process/bridge/ledger 플로우가 1열 간격 구조로 보정되지 않습니다.");
  assertOk(/\.investment-bridge-step strong,[\s\S]*\.investment-bridge-step i\s*\{[\s\S]*word-break: keep-all;/.test(mobileSpacingAuditLayer), "모바일 투자 분석 브리지 카드의 한글 줄바꿈 보정이 없습니다.");
  assertOk(/\.settings-status-band,[\s\S]*\.settings-api-row,[\s\S]*\.message-schedule-summary > span:not\(\.tone-chip\)\s*\{[\s\S]*padding: var\(--ds-row-pad-y\) var\(--ds-row-pad-x\);/.test(mobileSpacingAuditLayer), "모바일 패널 내부 요약 카드 padding 보정이 없습니다.");
  assertOk(/\.deskbar-cell,[\s\S]*\.notification-ops-cell,[\s\S]*\.notification-rule-state,[\s\S]*\.flow-lane,[\s\S]*\.ontology-ledger-item,[\s\S]*\{[\s\S]*padding: var\(--ds-row-pad-y\) var\(--ds-row-pad-x\);/.test(mobileSpacingAuditLayer), "모바일 전면 카드 시스템의 작은 상태 카드 padding 보정이 없습니다.");
  assertOk(/\.monitor-board\s*\{[\s\S]*gap: var\(--ds-space-5\);[\s\S]*padding: var\(--ds-section-gap\) var\(--ds-panel-pad-x\) var\(--ds-panel-pad-y\);/.test(mobileSpacingAuditLayer), "모바일 모니터링 보드 외곽 padding/gap 보정이 없습니다.");
  assertOk(/\.monitor-board-section\s*\{[\s\S]*gap: var\(--ds-space-5\);[\s\S]*padding: var\(--ds-panel-pad-y\) var\(--ds-panel-pad-x\);/.test(mobileSpacingAuditLayer), "모바일 모니터링 보드 섹션 padding/gap 보정이 없습니다.");
  assertOk(/\.monitor-primary-state,[\s\S]*\.monitor-runtime-row,[\s\S]*\.monitor-alert-summary,[\s\S]*\.monitor-ledger-cell,[\s\S]*\.monitoring-detail-metric,[\s\S]*\{[\s\S]*padding: var\(--ds-row-pad-y\) var\(--ds-row-pad-x\);/.test(mobileSpacingAuditLayer), "모바일 모니터링 내부 카드 padding 보정이 없습니다.");
  assertOk(/\.admin-monitoring-panel \.monitor-primary-state\s*\{[\s\S]*min-height: 132px;[\s\S]*padding: var\(--ds-panel-pad-y\) var\(--ds-panel-pad-x\);/.test(mobileSpacingAuditLayer), "모바일 대표 모니터링 상태 카드의 구조적 여백 보정이 없습니다.");
  assertOk(styles.indexOf("@media (max-width: 1180px) and (min-width: 981px)") >= 0 && styles.indexOf("@media (max-width: 980px) and (min-width: 861px)") >= 0, "PC/태블릿 레이아웃 분기 규칙이 없습니다.");
  assertOk(
    styles.indexOf("--ds-shell-width: 1720px") >= 0 &&
      styles.indexOf("--ds-content-width: 1760px") >= 0 &&
      styles.indexOf("--ds-page-content-width: 1680px") >= 0 &&
      styles.indexOf("--ds-desktop-page-gutter: clamp(40px, 4.8vw, 92px)") >= 0 &&
      styles.indexOf("--ds-top-nav-height: 104px") >= 0,
    "PC 중심 상단 통합 금융 콘솔 레이아웃 토큰이 없습니다."
  );
  assertOk(
    styles.indexOf("PC top area stability layer") >= 0 &&
      /\.console-shell > \.topbar,[\s\S]*\.console-shell \.managed-page > \.page-command-strip,[\s\S]*\.console-shell \.managed-page > \.page-routine-panel\s*\{[\s\S]*display: none !important;/.test(styles) &&
      /\.console-shell \.app-nav-tabs\s*\{[\s\S]*flex-wrap: nowrap;[\s\S]*overflow-x: auto;/.test(styles) &&
      /\.console-shell \.app-nav-section-label,[\s\S]*\.console-shell \.app-nav-divider,[\s\S]*\.console-shell \.nav-tab-description\s*\{[\s\S]*display: none !important;/.test(styles) &&
      /\.console-shell \.app-nav-command \.page-command-metrics\s*\{[\s\S]*display: none;/.test(styles) &&
      /\.console-shell \.app-nav-routine > span:not\(\.app-nav-routine-action-cell\)\s*\{[\s\S]*display: none;/.test(styles) &&
      /@media \(min-width: 861px\) and \(max-width: 1180px\)[\s\S]*\.console-shell \.app-nav-flow,[\s\S]*\.console-shell \.app-nav-command \.page-command-metrics,[\s\S]*\.console-shell \.app-nav-current em,[\s\S]*\.console-shell :is\([\s\S]*\.feed-section-tabs span[\s\S]*\)\s*\{[\s\S]*display: none;/.test(styles) &&
      indexHtml.indexOf("styles.css?v=20260718-calendar-year-window-v1") >= 0,
    "PC 상단 영역이 탭별로 여러 줄/넘침으로 깨지지 않도록 하는 안정화 레이어가 없습니다."
  );
  assertOk(
    code.indexOf('class="shell loading-shell"') >= 0 &&
      code.indexOf('class="loading-progress"') >= 0 &&
      code.indexOf('class="loading-skeleton-board"') >= 0 &&
      code.indexOf("먼저 볼 수 있는 화면을 준비합니다") < 0 &&
      code.indexOf('renderLoadingSource("계좌 연결"') < 0 &&
      styles.indexOf("Initial loading skeleton screen") >= 0 &&
      /\.loading-shell\s*\{[\s\S]*place-items: center;/.test(styles) &&
      /\.loading-progress span\s*\{[\s\S]*animation: loadingProgress/.test(styles) &&
      /\.loading-skeleton-grid\s*\{[\s\S]*grid-template-columns: repeat\(4, minmax\(0, 1fr\)\);/.test(styles) &&
      /@keyframes loadingProgress/.test(styles) &&
      indexHtml.indexOf("app.js?v=20260718-calendar-year-window-v1") >= 0,
    "초기 로딩 화면이 운영 정보 카드 대신 progress/skeleton 화면으로 고정되지 않았습니다."
  );
  assertOk(
    styles.indexOf("Desktop comfort rail") >= 0 &&
      /\.managed-page\s*\{[\s\S]*max-width: var\(--ds-page-content-width\);[\s\S]*justify-self: center;/.test(styles) &&
      /\.app-nav-command \.page-command-metrics\s*\{[\s\S]*display: none;/.test(styles) &&
      /(?:\.accounts-view \.account-history-panel|\.account-history-panel)[\s\S]*grid-column: 1 \/ -1;/.test(styles) &&
      /\.account-balance-panel \.account-balance-hero\s*\{[\s\S]*grid-template-columns: minmax\(0, 1fr\) auto;/.test(styles) &&
      /\.account-balance-audit\s*\{[\s\S]*grid-template-columns: minmax\(360px, 0\.34fr\) minmax\(0, 1fr\);/.test(styles) &&
      /\.account-balance-ledger\s*\{[\s\S]*border: 1px solid var\(--ds-card-border\);/.test(styles) &&
      /\.account-balance-audit \.source-stack\.compact\s*\{[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/.test(styles) &&
      code.indexOf("account-balance-total") >= 0 &&
      code.indexOf("account-balance-ledger") >= 0 &&
      code.indexOf("<strong>계좌 금액 검증</strong>") < 0,
    "PC comfort rail과 계좌 검증 감사 레저 구조가 없습니다."
  );
  assertOk(
    styles.indexOf("Desktop breathing room") >= 0 &&
      /\.ontology-experiment-overview-panel > \.ontology-experiment-metrics,[\s\S]*\.ontology-experiment-latest-panel > \.ontology-experiment-run-grid\s*\{[\s\S]*margin: var\(--ds-section-gap\) var\(--ds-panel-pad-x\) 0;/.test(styles) &&
      /\.ontology-experiment-recommendations\s*\{[\s\S]*gap: 12px;[\s\S]*padding-bottom: var\(--ds-panel-pad-y\);/.test(styles) &&
      /\.panel > :where\(\.lab-stats-grid,[\s\S]*\.account-quality-ledger\):not\(\.account-balance-grid\)\s*\{[\s\S]*gap: 12px;/.test(styles) &&
      /\.account-overview-ledger \.account-quality-ledger,[\s\S]*\.account-command-layout \.account-quality-ledger\s*\{[\s\S]*gap: 12px;[\s\S]*padding: var\(--ds-section-gap\) 0 0;/.test(styles),
    "PC 중첩 카드의 여백 보정 레이어가 없습니다."
  );
  assertOk(
    /\.system-detail-disclosure-body,[\s\S]*\.settings-detail-disclosure-body\s*\{[\s\S]*grid-template-columns: minmax\(0, 1fr\);/.test(styles) &&
      /\.system-detail-disclosure:not\(\[open\]\) > \.system-detail-disclosure-body,[\s\S]*\.settings-detail-disclosure:not\(\[open\]\) > \.settings-detail-disclosure-body\s*\{[\s\S]*display: none;/.test(styles) &&
      /\.system-detail-disclosure-body > \.panel,[\s\S]*\.settings-detail-disclosure-body > \.panel\s*\{[\s\S]*grid-column: 1 \/ -1;[\s\S]*min-width: 0;/.test(styles),
    "상세 펼침 내부 패널이 바깥 PC 컬럼 규칙을 누수하지 않도록 고정되지 않았습니다."
  );
  assertOk(
    styles.indexOf("Desktop notification console repair") >= 0 &&
      /\.notifications-view \.notification-command-center \.notification-section-tabs\s*\{[\s\S]*display: inline-flex;[\s\S]*width: fit-content;/.test(styles) &&
      /\.notifications-view \.notification-command-center \.notification-ops-rail\s*\{[\s\S]*grid-template-columns: repeat\(7, minmax\(118px, 1fr\)\);[\s\S]*gap: 10px;/.test(styles) &&
      /\.notifications-view \.notification-decision-panel,[\s\S]*\.notifications-view \.notification-diagnostics-summary-panel\s*\{[\s\S]*grid-column: 1 \/ -1;/.test(styles) &&
      /\.notifications-view \.notification-diagnostics-summary-panel \.notification-diagnostics-grid\s*\{[\s\S]*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);/.test(styles),
    "PC 알림 탭의 상단 콘솔/진단 패널 레이아웃 보정 계약이 없습니다."
  );
  assertOk(
    styles.indexOf("Desktop no-three-line rhythm") >= 0 &&
      /\.shell > \.topbar h1,[\s\S]*\.shell > \.topbar \.subtle,[\s\S]*\.loading-brand h1,[\s\S]*\.loading-brand \.subtle\s*\{[\s\S]*white-space: nowrap;/.test(styles) &&
      /\.app-nav-command-kicker\s*\{[\s\S]*display: none;/.test(styles) &&
      /\.app-nav-routine-reason\s*\{[\s\S]*display: none;/.test(styles) &&
      /\.account-history-panel \.account-command-grid,[\s\S]*\.account-command-panel \.account-command-grid\s*\{[\s\S]*grid-template-columns: repeat\(4, minmax\(0, 1fr\)\);/.test(styles) &&
      /max-width: 1560px\)[\s\S]*\.account-history-panel \.account-command-grid,[\s\S]*\.account-quality-ledger\s*\{[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/.test(styles),
    "PC 화면의 상단/탭/계정 카드가 3줄 리듬으로 무너지는 것을 막는 최종 계약이 없습니다."
  );
  assertOk(
    styles.indexOf("Desktop card comfort audit") >= 0 &&
      /--ds-card-comfort-gap: 14px;/.test(styles) &&
      /\.managed-page,[\s\S]*\.system-view\s*\{[\s\S]*gap: var\(--ds-page-gap\);/.test(styles) &&
      /\.loading-skeleton-board\s*\{[\s\S]*gap: var\(--ds-space-5\);/.test(styles) &&
      /\.symbol-result-workbench \.symbol-result-list,[\s\S]*\.investment-action-workbench \.investment-action-list\s*\{[\s\S]*gap: var\(--ds-card-comfort-gap\);/.test(styles) &&
      /\.notification-decision-list\s*\{[\s\S]*display: grid;[\s\S]*gap: var\(--ds-card-comfort-gap\);/.test(styles) &&
      styles.indexOf("Financial data card format system") >= 0 &&
      /\.console-shell \[data-card-type\]\s*\{[\s\S]*border-left-width: 1px !important;/.test(styles) &&
      /\.console-shell :is\([\s\S]*\.symbol-result-row,[\s\S]*\.notification-decision-row,[\s\S]*\.feed-impact-card,[\s\S]*\.research-evidence-item,[\s\S]*\.investment-action-row[\s\S]*\)\s*\{[\s\S]*border-left-width: 1px !important;/.test(styles),
    "PC 카드 간격/내부 여백을 최종 레이어에서 복원하는 계약이 없습니다."
  );
  assertOk(
    styles.indexOf("Desktop QA density pass") >= 0 &&
      /--ds-page-gap: 24px;/.test(styles) &&
      /--ds-card-comfort-gap: 16px;/.test(styles) &&
      /--ds-pc-type-panel-title: 18px;/.test(styles) &&
      /\.panel-head h2\s*\{[\s\S]*font-size: var\(--ds-pc-type-panel-title\);/.test(styles) &&
      /\.managed-page,[\s\S]*\.system-view\s*\{[\s\S]*gap: var\(--ds-page-gap\);/.test(styles) &&
      /\.loading-skeleton-head,[\s\S]*\.loading-skeleton-grid,[\s\S]*\.loading-skeleton-list\s*\{[\s\S]*gap: var\(--ds-space-4\);/.test(styles) &&
      /:is\([\s\S]*\.account-card strong,[\s\S]*\.ontology-experiment-recommendation strong[\s\S]*\)\s*\{[\s\S]*font-size: var\(--ds-pc-type-card-title\);/.test(styles) &&
      /\[data-card-type="metric-cell"\],[\s\S]*\.inline-detail-metrics > \*\s*\{[\s\S]*min-height: 92px;/.test(styles) &&
      /\.notification-decision-row\s*\{[\s\S]*min-height: 104px;/.test(styles),
    "PC 최종 QA 밀도 레이어의 간격/폰트/카드 높이 계약이 없습니다."
  );
  assertOk(
    styles.indexOf("Desktop QA enforcement pass") >= 0 &&
      /\.console-shell \.managed-page,[\s\S]*\.console-shell \.system-view\s*\{[\s\S]*gap: var\(--ds-page-gap\);/.test(styles) &&
      /\.console-shell :is\([\s\S]*\.symbol-result-row,[\s\S]*\.investment-lineage-row[\s\S]*\)\s*\{[\s\S]*padding: var\(--ds-card-comfort-pad-y\) var\(--ds-card-comfort-pad-x\);/.test(styles) &&
      /\.console-shell :is\([\s\S]*\[data-card-type="metric-cell"\],[\s\S]*\.inline-detail-metrics > \*[\s\S]*\),[\s\S]*\.console-shell \.notifications-view \.notification-command-center \.notification-ops-cell\s*\{[\s\S]*min-height: 92px;[\s\S]*padding: 16px 18px;/.test(styles),
    "PC 컴팩트 규칙을 마지막에 다시 덮는 QA enforcement 계약이 없습니다."
  );
  assertOk(
    styles.indexOf("Desktop information hierarchy pass") >= 0 &&
      /\.console-shell :is\([\s\S]*\.feed-command-metrics,[\s\S]*\.inline-detail-metrics[\s\S]*\)\s*\{[\s\S]*grid-template-columns: repeat\(auto-fit, minmax\(148px, 1fr\)\);/.test(styles) &&
      /\.console-shell :is\([\s\S]*\.feed-impact-metric,[\s\S]*\[data-card-type="metric-cell"\][\s\S]*\)\s*\{[\s\S]*min-width: 148px;/.test(styles) &&
      /\.console-shell :is\([\s\S]*\.symbol-result-workbench,[\s\S]*\.investment-action-workbench[\s\S]*\)\s*\{[\s\S]*grid-template-columns: minmax\(0, 0\.68fr\) minmax\(360px, 0\.32fr\);/.test(styles) &&
      /\.console-shell \.investment-calendar-list\s*\{[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/.test(styles),
    "PC 정보 계층/metric 최소 폭/긴 리스트 축약 계약이 없습니다."
  );
  assertOk(
    /var DEFAULT_SYMBOL_UNIVERSE_LIMIT = 8;/.test(code) &&
      /investmentCalendarFilters: \{ symbol: "", eventType: "", limit: "80" \}/.test(code) &&
      code.indexOf("renderInvestmentCalendarMonthPanel") >= 0 &&
      code.indexOf("renderInvestmentCalendarMonthGrid") >= 0 &&
      code.indexOf("data-calendar-day-select") >= 0 &&
      code.indexOf("investmentCalendarQueryWindow") >= 0 &&
      code.indexOf("investment-calendar-month-count") >= 0 &&
      code.indexOf("investmentCalendarCandidatePageInfo") >= 0 &&
      code.indexOf("data-calendar-candidate-page") >= 0 &&
      styles.indexOf("Calendar month board: date-first investment event view.") >= 0 &&
      styles.indexOf("Calendar review queue and primary layout") >= 0 &&
      /\.investment-calendar-month-grid\s*\{[\s\S]*grid-template-columns: repeat\(7, minmax\(0, 1fr\)\);/.test(styles) &&
      /\.investment-calendar-primary-grid\s*\{[\s\S]*display: grid;[\s\S]*gap: var\(--ds-page-gap\);/.test(styles) &&
      /\.investment-calendar-candidate-panel \.investment-calendar-list\s*\{[\s\S]*grid-template-columns: repeat\(auto-fit, minmax\(320px, 1fr\)\);/.test(styles) &&
      code.indexOf("investmentActionPageInfo") >= 0 &&
      code.indexOf("data-investment-action-query") >= 0 &&
      code.indexOf("data-investment-action-page") >= 0 &&
      /var visibleJobs = jobs\.slice\(0, 4\);/.test(code) &&
      /var visibleAlerts = alerts\.slice\(0, 6\);/.test(code) &&
      /rows\.slice\(0, 5\)\.map\(function \(row\)/.test(code) &&
      /\(portfolio\.sectors \|\| \[\]\)\.slice\(0, 6\)\.map\(function \(sector\)/.test(code),
    "PC 기본 화면의 종목/캘린더 월간 보드/액션 큐 표시 계약이 없습니다."
  );
  assertOk(styles.indexOf("Professional finance top navigation shell") >= 0 && styles.indexOf("grid-template-columns: minmax(0, 1fr)") >= 0, "PC shell이 단일 컬럼 상단 탭형 작업 영역으로 전환되지 않습니다.");
  assertOk(/Professional finance top navigation shell[\s\S]*\.app-nav[\s\S]*grid-template-columns: minmax\(180px, 0\.17fr\) minmax\(0, 1fr\) minmax\(108px, auto\);/.test(styles), "PC 상단 금융 탭 네비게이션 3구획 구조가 없습니다.");
  assertOk(styles.indexOf(".app-nav-command") >= 0 && styles.indexOf(".app-nav-flow") >= 0 && styles.indexOf(".app-nav-mode") >= 0, "PC 상단 통합 command rail 스타일 정의가 없습니다.");
  assertOk(code.indexOf("renderAppNavCommand") >= 0 && code.indexOf("app-nav-routine") >= 0 && code.indexOf('data-style-layer="unified-command"') >= 0, "상단 네비게이션이 현재 화면 command/routine rail을 함께 렌더링하지 않습니다.");
  assertOk(/Professional finance top navigation shell[\s\S]*\.console-shell \.topbar,[\s\S]*\.console-shell \.managed-page > \.page-command-strip,[\s\S]*\.console-shell \.managed-page > \.page-routine-panel[\s\S]*\{[\s\S]*display: none;/.test(styles), "topbar, 본문 command strip, 본문 routine panel이 상단 통합 rail로 합쳐지지 않았습니다.");
  assertOk(code.indexOf("renderWorkDetailLayer") >= 0 && code.indexOf("data-work-detail") >= 0, "긴 독립 상세를 공통 work detail layer로 여는 렌더 경로가 없습니다.");
  assertOk(
    code.indexOf("runWithSuppressedRender") >= 0 &&
      /function markRealtimeState\(connected, eventName\)\s*\{[\s\S]*state\.realtime\.lastEventAt = new Date\(\)\.toISOString\(\);[\s\S]*\}\s*\n\s*function runWithSuppressedRender/.test(code) &&
      /queueRealtimeReload\(eventType\)[\s\S]*runWithSuppressedRender/.test(code),
    "WebSocket 이벤트가 전체 화면을 즉시 재렌더링하지 않도록 실시간 렌더 억제 계약이 없습니다."
  );
  assertOk(
    code.indexOf("data-feed-detail-toggle") >= 0 &&
      code.indexOf("data-research-evidence-toggle") >= 0 &&
      code.indexOf("data-investment-action-toggle") >= 0 &&
      code.indexOf("data-settings-runtime-toggle") >= 0,
    "짧은 상세 정보를 인라인/패널로 여는 렌더 경로가 없습니다.",
  );
  assertOk(styles.indexOf(".work-detail-layer") >= 0 && styles.indexOf("Layer reduction: inline detail surfaces") >= 0, "PC 요약 화면과 상세 표면을 위한 최종 콘솔 레이어가 없습니다.");
  assertOk(designSystemDoc.indexOf("inline-detail-surface") >= 0 && designSystemDoc.indexOf("전체 데이터를 다 펼치지 않는다") >= 0, "디자인 시스템 문서에 요약 우선/상세 표면 계약이 없습니다.");
  assertOk(code.indexOf("cardTypeAttrs") >= 0 && code.indexOf('data-card-type="') >= 0, "카드 의미 타입을 렌더링하는 공통 계약이 없습니다.");
  assertOk(styles.indexOf("PC card type taxonomy layer") >= 0 && styles.indexOf('[data-card-type="health-card"]') >= 0 && styles.indexOf('[data-card-type="evidence-card"]') >= 0, "PC 카드 타입별 스타일 레이어가 없습니다.");
  assertOk(designSystemDoc.indexOf("Card type taxonomy") >= 0 && designSystemDoc.indexOf("health-card") >= 0 && designSystemDoc.indexOf("action-queue-card") >= 0, "디자인 시스템 문서에 카드 타입 분류 계약이 없습니다.");
  assertOk(code.indexOf("cardFormatAttrs") >= 0 && code.indexOf('data-card-format="') >= 0, "데이터 형식별 카드 포맷을 렌더링하는 공통 계약이 없습니다.");
  assertOk(styles.indexOf("Financial data card format system") >= 0 && styles.indexOf('[data-card-format="decision-ticket"]') >= 0 && styles.indexOf('[data-card-format="document-card"]') >= 0 && styles.indexOf('[data-card-format="market-ledger-row"]') >= 0, "금융 데이터 포맷별 카드 표면 레이어가 없습니다.");
  assertOk(styles.indexOf("Summary-first list UX pass") >= 0 && styles.indexOf('[data-card-format="summary-list-card"]') >= 0 && styles.indexOf(".investment-evidence-columns") >= 0 && styles.indexOf(".model-preview-row[data-card-format=\"summary-list-card\"]") >= 0, "긴 카드 정보를 리스트 요약/상세 진입으로 분리하는 summary-first 카드 계약이 없습니다.");
  assertOk(code.indexOf("investmentReasoningCardWorkDetailPayload") >= 0 && code.indexOf("ontologyExperimentWorkDetailPayload") >= 0 && code.indexOf('renderWorkDetailButton("investment-reasoning-card"') >= 0 && code.indexOf('renderWorkDetailButton("ontology-experiment"') >= 0, "요약 리스트 카드의 상세 레이어 payload 연결이 없습니다.");
  assertOk(designSystemDoc.indexOf("Card format taxonomy") >= 0 && designSystemDoc.indexOf("summary-list-card") >= 0 && designSystemDoc.indexOf("pagination-strip") >= 0, "디자인 시스템 문서에 카드 포맷 분류 계약이 없습니다.");
  ["process-card", "source-card", "diagnostic-card", "signal-card", "relationship-card", "reference-card", "calendar-event"].forEach((cardType) => {
    assertOk(code.indexOf(cardType) >= 0 && styles.indexOf('[data-card-type="' + cardType + '"]') >= 0 && designSystemDoc.indexOf(cardType) >= 0, "확장 카드 타입 계약이 누락되었습니다: " + cardType);
  });
  assertOk(/Professional finance top navigation shell[\s\S]*\.app-nav-tab,[\s\S]*\.app-nav-menu-item[\s\S]*min-height: var\(--ds-top-nav-tab-height\);/.test(styles), "PC 상단 탭이 낮은 한 줄 금융 탭 높이를 쓰지 않습니다.");
  assertOk(/Professional finance top navigation shell[\s\S]*\.nav-tab-description\s*\{[\s\S]*display: none;/.test(styles), "PC 상단 탭이 설명까지 노출해 여러 줄로 늘어날 수 있습니다.");
  assertOk(styles.indexOf("grid-template-columns: repeat(12, minmax(0, 1fr))") >= 0, "PC 본문 12컬럼 그리드가 없습니다.");
  assertOk(styles.indexOf("@media (min-width: 1181px)") >= 0, "PC 전용 데스크톱 최적화 분기 규칙이 없습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.managed-page[\s\S]*gap: var\(--ds-page-gap\);/.test(styles), "PC 본문 간격이 공통 page gap을 따르지 않습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.account-card,[\s\S]*padding: calc\(var\(--ds-row-pad-y\) \+ 1px\) var\(--ds-row-pad-x\);/.test(styles), "PC 행 리스트 내부 padding이 공통 row 토큰을 따르지 않습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.account-card-list[\s\S]*display: grid;/.test(styles), "PC 계정 카드 목록이 금융 카드 그리드로 전환되지 않습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.monitoring-instrument-list[\s\S]*display: grid;/.test(styles), "PC 모니터링 종목 목록이 금융 카드 그리드로 전환되지 않습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.account-card-list,[\s\S]*\.symbol-result-list[\s\S]*grid-template-columns: repeat\(auto-fill, minmax\(430px, 1fr\)\);/.test(styles), "PC 반복 카드의 읽기 폭 제한 그리드가 없습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.source-row[\s\S]*grid-template-columns: minmax\(120px, 0\.34fr\) minmax\(0, 1fr\);/.test(styles), "PC source row가 라벨/값 2열 ledger로 정리되지 않습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.notification-decision-row[\s\S]*grid-template-columns: minmax\(220px, 0\.38fr\) minmax\(0, 1fr\);/.test(styles), "PC 최근 알림 판단 행이 2열 정보 구조로 정리되지 않습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.symbol-result-list[\s\S]*grid-template-columns: repeat\(auto-fit, minmax\(440px, 1fr\)\);/.test(styles), "PC 전체종목 결과가 과도하게 넓은 단일 리스트로 남아 있습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.alert-list[\s\S]*grid-template-columns: repeat\(auto-fit, minmax\(420px, 1fr\)\);/.test(styles), "PC 알림 리스트가 읽기 폭을 제한하지 않습니다.");
  assertOk(/@media \(min-width: 1181px\)[\s\S]*\.variable-grid,[\s\S]*\.monitoring-detail-signal-grid[\s\S]*grid-template-columns: repeat\(auto-fill, minmax\(160px, 230px\)\);/.test(styles), "PC metric/card cell이 화면 폭 전체로 늘어나는 것을 막지 않습니다.");
  const desktopLayoutAuditLayerStart = styles.indexOf("/* Desktop layout audit layer */");
  const desktopLayoutAuditLayer = desktopLayoutAuditLayerStart >= 0 ? styles.slice(desktopLayoutAuditLayerStart) : "";
  assertOk(desktopLayoutAuditLayerStart >= 0, "PC 최종 레이아웃 감사 레이어가 없습니다.");
  assertOk(/\.home-view \.admin-overview-panel\s*\{\s*grid-column: span 7;/.test(desktopLayoutAuditLayer), "홈 운영 요약이 PC 7컬럼 배치로 최종 고정되지 않습니다.");
  assertOk(/\.home-view \.account-watchlist-panel,[\s\S]*\.home-view \.admin-monitoring-panel\s*\{\s*grid-column: span 6;/.test(desktopLayoutAuditLayer), "홈 하단 패널이 PC 6:6 배치로 최종 고정되지 않습니다.");
  assertOk(/\.notifications-view \.monitoring-instrument-panel\s*\{\s*grid-column: span 8;/.test(desktopLayoutAuditLayer), "알림 후보 섹션의 통합 종목 패널이 PC 8컬럼으로 고정되지 않습니다.");
  assertOk(/\.notification-decision-list\s*\{\s*grid-template-columns: minmax\(0, 1fr\);/.test(desktopLayoutAuditLayer), "최근 알림 판단 목록이 PC 전폭 원장 행으로 유지되지 않습니다.");
  assertOk(/\.notification-decision-row\s*\{\s*grid-template-columns: minmax\(240px, 0\.34fr\) minmax\(0, 1fr\);/.test(desktopLayoutAuditLayer), "최근 알림 판단 행의 PC 최종 2열 구조가 없습니다.");
  assertOk(/\.strategy-data-grid\s*\{\s*grid-template-columns: repeat\(auto-fit, minmax\(min\(100%, 360px\), 1fr\)\);/.test(desktopLayoutAuditLayer), "전략 데이터 점검 카드가 PC 읽기 폭 제한 그리드로 정리되지 않습니다.");
  assertOk(/:where\([\s\S]*\.symbol-filter-form,[\s\S]*\.settings-body,[\s\S]*\.investment-bridge-flow,[\s\S]*\.formula-ledger,[\s\S]*\.relation-matrix,[\s\S]*\.admin-form-grid[\s\S]*\)\s*\{[\s\S]*gap: var\(--ds-section-gap\);/.test(desktopLayoutAuditLayer), "PC 탭별 내부 작업 영역 gap 감사 규칙이 없습니다.");
  assertOk(/:where\([\s\S]*\.symbol-filter-form,[\s\S]*\.settings-body,[\s\S]*\.investment-bridge-flow,[\s\S]*\.account-credential-grid[\s\S]*\)\s*\{[\s\S]*padding: var\(--ds-space-4\) var\(--ds-panel-pad-x\) var\(--ds-panel-pad-y\);/.test(desktopLayoutAuditLayer), "PC 탭별 내부 작업 영역 padding 감사 규칙이 없습니다.");
  assertOk(/\.notification-section-tabs,[\s\S]*\.ontology-section-tabs[\s\S]*\{[\s\S]*gap: var\(--ds-space-2\);[\s\S]*padding: var\(--ds-space-2\);/.test(desktopLayoutAuditLayer), "PC 섹션 탭 바 간격 감사 규칙이 없습니다.");
  assertOk(/@media \(min-width: 1440px\)[\s\S]*\.notifications-view \.alert-delivery-panel\s*\{[\s\S]*grid-column: span 4;/.test(desktopLayoutAuditLayer), "넓은 PC에서 알림 고급 패널 4:4:4 배치가 없습니다.");
  assertOk(code.indexOf("renderManagedPage") >= 0 && styles.indexOf(".managed-page") >= 0, "전체 탭 공통 관리 페이지 템플릿이 없습니다.");
  assertOk(code.indexOf("renderPageCommandStrip") >= 0 && styles.indexOf(".page-command-strip") >= 0, "페이지 작업 상태 strip 템플릿이 없습니다.");
  assertOk(code.indexOf("renderPageRoutinePanel") >= 0 && styles.indexOf(".page-routine-panel") >= 0, "탭별 현재 상태/이유/다음 행동 루틴 카드가 없습니다.");
  assertOk(code.indexOf("renderPageFlowSpine") >= 0 && styles.indexOf(".page-flow-spine") >= 0, "업무 탭 데이터 흐름 spine이 없습니다.");
  assertOk(styles.indexOf("Desktop free-scroll and focus-mode layer") >= 0, "PC 자유 스크롤/모드 분리 최종 레이어가 없습니다.");
  assertOk(/Desktop free-scroll and focus-mode layer[\s\S]*\.shell,[\s\S]*\.console-shell\s*\{[\s\S]*height: auto;[\s\S]*overflow: visible;/.test(styles), "PC shell이 페이지 스크롤 구조로 풀리지 않았습니다.");
  assertOk(/Desktop free-scroll and focus-mode layer[\s\S]*\.workspace-main,[\s\S]*\.managed-page,[\s\S]*\.admin-grid\s*\{[\s\S]*height: auto;[\s\S]*overflow: visible;/.test(styles), "PC 본문이 내부 고정 스크롤에서 해제되지 않았습니다.");
  assertOk(/Desktop free-scroll and focus-mode layer[\s\S]*\.notification-template-select-list,[\s\S]*\.feed-view \.feed-side-column,[\s\S]*\.notification-decision-body\.has-detail \.notification-decision-list[\s\S]*\{[\s\S]*max-height: none;[\s\S]*overflow: visible;/.test(styles), "PC 탭 내부 목록/사이드 컬럼이 자체 스크롤에서 해제되지 않았습니다.");
  assertOk(/@media \(max-width: 1180px\) and \(min-width: 981px\)[\s\S]*\.shell,[\s\S]*\.console-shell[\s\S]*grid-template-columns: minmax\(0, 1fr\);/.test(styles), "981px 이상 태블릿이 상단 탭형 단일 컬럼 구조를 유지하지 않습니다.");
  assertOk(/@media \(max-width: 980px\) and \(min-width: 861px\)[\s\S]*\.app-nav[\s\S]*grid-template-columns: auto minmax\(0, 1fr\) auto;/.test(styles), "좁은 태블릿에서 상단 네비게이션 3구획이 유지되지 않습니다.");
  assertOk(code.indexOf("renderDeskbar") >= 0 && styles.indexOf(".deskbar") >= 0, "PC/태블릿 데스크 바가 없습니다.");
  assertOk(code.indexOf("loadCachedSnapshot") >= 0 && code.indexOf("writeCachedSnapshot") >= 0 && code.indexOf("snapshotFromCache") >= 0, "리로드 시 직전 화면 유지 로직이 없습니다.");
  assertOk(code.indexOf("snapshotPrerequisites") >= 0 && code.indexOf("supportingBootstrapTasks") >= 0, "초기 데이터 로드가 메인/보조 작업으로 병렬 분리되지 않았습니다.");
  assertOk(styles.indexOf(".deskbar.deskbar-full") >= 0 && styles.indexOf(".shell-page") >= 0, "홈 전용 deskbar와 본문 우선 shell 배치가 없습니다.");
  assertOk(styles.indexOf(".account-exposure-grid") >= 0 && styles.indexOf(".account-manager-panel .admin-form-grid") >= 0, "PC 계좌 노출 최적화 규칙이 없습니다.");
  assertOk(styles.indexOf(".settings-smart-save") >= 0, "설정 화면 스마트 저장 액션 규칙이 없습니다.");
  assertOk(styles.indexOf(".settings-save-panel") < 0, "설정 화면에 하단 sticky 저장 패널 규칙이 남아 있습니다.");
  assertOk(code.indexOf("settingsHasPendingChanges") >= 0 && code.indexOf("refreshSettingsSaveControls") >= 0, "설정 저장 버튼의 상태형 갱신 로직이 없습니다.");
  assertOk(/@media \(max-width: 860px\)[\s\S]*\.account-watchlist-workbench[\s\S]*grid-template-columns: 1fr;/.test(styles), "모바일 관심종목 워크벤치가 1열로 접히지 않습니다.");
  assertOk(/@media \(max-width: 860px\)[\s\S]*\.watch-account-row \.chip-row[\s\S]*justify-content: flex-start;/.test(styles), "모바일 관심종목 계정 칩 정렬이 왼쪽 기준이 아닙니다.");
  assertOk(designSystemDoc.indexOf("Institutional Ledger Tone") >= 0, "디자인 시스템 문서에 기관형 금융앱 룩앤필 기준이 없습니다.");
  assertOk(designSystemDoc.indexOf("Spacing Rhythm") >= 0 && designSystemDoc.indexOf("padding: 12px 0") >= 0, "디자인 시스템 문서에 화면 간격 정책이 없습니다.");
  assertOk(designSystemDoc.indexOf("금융 원장형 카드 그리드") >= 0, "디자인 시스템 문서에 PC 금융 카드 그리드 원칙이 없습니다.");
  assertOk(designSystemDoc.indexOf("구획이 정확한 금융 카드와 셀") >= 0 && designSystemDoc.indexOf("Desktop record card") >= 0, "디자인 시스템 문서에 PC 금융 카드 구획 정책이 없습니다.");
  assertOk(designSystemDoc.indexOf("카드형 표면은 전면 교체 대상") >= 0 && designSystemDoc.indexOf("Full card replacement") >= 0, "디자인 시스템 문서에 전면 카드 교체 기준이 없습니다.");
  assertOk(code.indexOf('appBrandName = "Orbit Alpha"') >= 0, "Orbit Alpha 브랜드명이 앱에 적용되지 않았습니다.");
  assertOk(indexHtml.indexOf("<title>Orbit Alpha</title>") >= 0 && indexHtml.indexOf("favicon.svg") >= 0, "Orbit Alpha 문서 제목 또는 파비콘 링크가 없습니다.");
  assertOk(styles.indexOf(".app-brand-mark") >= 0 && styles.indexOf("--ds-color-orbit-line") >= 0, "Orbit Alpha 신호형 브랜드 마크 규칙이 없습니다.");
  assertOk(fs.existsSync(path.join(rootDir, "public", "favicon.svg")), "Orbit Alpha SVG 파비콘이 없습니다.");
  assertOk(designSystemDoc.indexOf("#F3F5F8") >= 0 && designSystemDoc.indexOf("Relation Matrix") >= 0, "디자인 시스템 문서에 기관형 금융 콘솔 팔레트나 관계 UI 기준이 없습니다.");
  assertOk(designSystemDoc.indexOf("Page Contracts") >= 0, "디자인 시스템 문서에 페이지별 UI 계약이 없습니다.");
  assertOk(designSystemDoc.indexOf("page-routine-panel") >= 0 && designSystemDoc.indexOf("현재 상태") >= 0 && designSystemDoc.indexOf("다음 행동") >= 0, "디자인 시스템 문서에 탭별 루틴 패널 계약이 없습니다.");
  assertOk(designSystemDoc.indexOf("page-flow-spine") >= 0 && designSystemDoc.indexOf("이전 입력") >= 0 && designSystemDoc.indexOf("다음 출력") >= 0, "디자인 시스템 문서에 전체 데이터 흐름 spine 계약이 없습니다.");
  assertOk(designSystemDoc.indexOf("textarea") >= 0 && designSystemDoc.indexOf("자동 높이 확장") >= 0 && designSystemDoc.indexOf("자체 스크롤바") >= 0, "디자인 시스템 문서에 긴 입력값 자체 스크롤 금지 계약이 없습니다.");
  assertOk(designSystemDoc.indexOf("영향 인박스") >= 0 && designSystemDoc.indexOf("기사 제목·출처·시간·원문 링크는 카드 하단") >= 0, "디자인 시스템 문서에 피드 영향 인박스 계약이 없습니다.");
  assertOk(designSystemDoc.indexOf("Control Map") >= 0 && designSystemDoc.indexOf("결과 화면과 설정 화면 책임") >= 0 && designSystemDoc.indexOf("고급 설정") >= 0 && designSystemDoc.indexOf("진단") >= 0, "디자인 시스템 문서에 설정 탭 책임 지도/설정 분리 계약이 없습니다.");
  assertOk(designSystemDoc.indexOf("12컬럼") >= 0 && designSystemDoc.indexOf("8컬럼") >= 0, "디자인 시스템 문서에 PC/태블릿 컬럼 기준이 없습니다.");
  assertOk(designSystemDoc.indexOf("Button Placement") >= 0, "디자인 시스템 문서에 버튼 위치 정책이 없습니다.");
  assertOk(designSystemDoc.indexOf("aria-current") >= 0, "디자인 시스템 문서에 내비게이션 접근성 기준이 없습니다.");
  assertOk(code.indexOf('appTheme: settingValue("appTheme")') >= 0, "설정 저장 payload에 화면 테마가 포함되지 않았습니다.");
  assertOk(code.indexOf('typedbAddress: settingValue("typedbAddress")') >= 0, "설정 저장 payload에 TypeDB 주소가 포함되지 않았습니다.");
  assertOk(code.indexOf('typedbTlsEnabled: settingValue("typedbTlsEnabled")') >= 0, "설정 저장 payload에 TypeDB TLS 설정이 포함되지 않았습니다.");
  assertOk(code.indexOf('temporalWindowPeriods: settingValue("temporalWindowPeriods")') >= 0, "설정 저장 payload에 기간 판단 구간이 포함되지 않았습니다.");
  assertOk(code.indexOf('temporalWindowHistoryLimit: settingValue("temporalWindowHistoryLimit")') >= 0, "설정 저장 payload에 기간 히스토리 제한이 포함되지 않았습니다.");
  assertOk(code.indexOf('renderSettingField("typedbAddress"') >= 0, "설정 화면에 TypeDB 주소 입력 필드가 없습니다.");
  assertOk(code.indexOf('data-setting="temporalWindowPeriods"') >= 0, "설정 화면에 기간 판단 구간 입력 필드가 없습니다.");
  assertOk(code.indexOf('renderSettingField("temporalWindowHistoryLimit"') >= 0, "설정 화면에 기간 히스토리 제한 입력 필드가 없습니다.");
  const payloads = {
    "/api/settings": {
      settings: {
        tossApiBaseUrl: "https://openapi.tossinvest.com",
        notifyProvider: "telegram",
        notifyLinkUrl: "http://127.0.0.1:3000?tab=notifications",
        ontologyTypeDbEnabled: "1",
        typedbAddress: "127.0.0.1:1729",
        typedbUser: "admin",
        typedbDatabase: "orbit_alpha_ontology",
        typedbTlsEnabled: "0",
        typedbTimeoutSeconds: "20",
        temporalWindowPeriods: "1D=1:2\n3D=3:3\n5D=5:4\n20D=20:5",
        temporalWindowHistoryLimit: "96",
        ontologyRuleCandidateAiEnabled: "1",
        ontologyRuleCandidateAiUseCodex: "1",
        ontologyRuleCandidateAiIntervalMinutes: "60",
        ontologyRuleCandidateAiMaxCandidates: "3",
        valuationAssumptions: "005930,6500,12,20\nNVDA,4.2,45,15",
        marketSignalInputs: "005930,118,1.8,620000,480000,18,2.1,71000,68000,14500000000,8200000000,-11200000000\nNVDA,132,2.3,780000,520000,22,3.5,174,159,11800000,7400000,-9300000"
      },
      configured: {
        tossClientId: true,
        tossClientSecret: true,
        tossAccountSeq: true,
        telegramBotToken: true,
        telegramChatId: true
      },
      locked: false
    },
    "/api/ontology/rulebox": {
      configured: false,
      saved: false,
      status: "disabled",
      source: "defaults",
      engineVersion: "typedb-rulebox-graph-reasoner-v1",
      ruleCount: 1,
      conditionCount: 1,
      derivationCount: 1,
      relationTypes: ["HAS_INFERRED_RISK"],
      versionCount: 1,
      versions: [
        {
          id: "rulebox-version:smoke001",
          versionLabel: "smoke001",
          changeReason: "smoke baseline",
          createdAt: "2026-07-10T00:00:00Z",
          ruleCount: 1,
          author: "smoke"
        }
      ],
      changeCandidates: [
        {
          id: "candidate.factor-concentration-context.v1",
          title: "팩터 집중 노출 컨텍스트",
          status: "candidate",
          rationale: "팩터 노출을 포트폴리오 리스크로 검토합니다.",
          requiresData: ["HAS_FACTOR_EXPOSURE"],
          proposedRule: {
            rule_id: "graph.factor.concentration.context.v1",
            label: "보유 종목 + 높은 팩터 노출 -> 팩터 집중 점검",
            version: "candidate-v1",
            source_kind: "stock",
            enabled: false,
            action_group: "factorRisk",
            action_level: "review",
            prompt_hint: "팩터 노출을 포트폴리오 리스크로 설명",
            conditions: [
              { condition_id: "factor-exposure", kind: "relation", description: "팩터 노출", relation_type: "HAS_FACTOR_EXPOSURE", target_kind: "factor", min_weight: 0.25 }
            ],
            derivations: [
              { relation_type: "REQUIRES_NEXT_CHECK", target_kind: "next-check", target_key: "{symbol}:factor-concentration-review", target_label: "{displayName} 팩터 집중 점검", tbox_class: "NextCheck", polarity: "context", weight: 0.68, decision_stage: "FACTOR_CROWDING", stage_priority: 32 }
            ]
          }
        }
      ],
      rules: [
        {
          rule_id: "graph.loss_guard.breakdown.v1",
          label: "손실 방어 추론",
          version: "typedb-rulebox-graph-reasoner-v1",
          source_kind: "smoke",
          enabled: true,
          action_group: "riskControl",
          action_level: "lossGuard",
          prompt_hint: "손실 확대 요인과 회복 조건을 분리",
          conditions: [
            { condition_id: "ma-break", kind: "relation", description: "기준선 이탈", relation_type: "BREAKS_LEVEL", target_kind: "price-level", min_weight: 0.4 }
          ],
          derivations: [
            { relation_type: "HAS_INFERRED_RISK", target_kind: "risk", target_key: "loss-guard:{symbol}", target_label: "{displayName} 손실 방어", tbox_class: "RiskInsight", polarity: "risk", weight: 0.82 }
          ]
        }
      ]
    },
    "/api/investment-calendar/events": {
      generatedAt: "2026-07-13T00:00:00Z",
      events: [],
      summary: { total: 0, upcoming: 0, nextStartsAt: "", byType: [] },
      eventTypes: [
        { type: "earnings", label: "실적발표" },
        { type: "dividend", label: "배당/권리" },
        { type: "macro", label: "거시지표" },
        { type: "centralBank", label: "중앙은행" },
        { type: "disclosure", label: "공시" },
        { type: "shareholderMeeting", label: "주주총회" },
        { type: "lockup", label: "락업해제" },
        { type: "portfolioReview", label: "포트폴리오 점검" },
        { type: "custom", label: "사용자 이벤트" }
      ],
      preview: false
    },
    "/api/ontology/rulebox/candidates": {
      status: "ok",
      trigger: "manual",
      candidateCount: 1,
      savedCount: 1,
      rulebox: null
    },
    "/api/research-evidence": {
      items: [
        {
          evidenceId: "research:005930:news:smoke",
          symbol: "005930",
          kind: "news",
          source: "Smoke News",
          title: "삼성전자 반도체 업황 개선 기대",
          summary: "테스트용 저장 근거",
          url: "https://example.test/news",
          observedAt: "2026-07-08T00:00:00Z",
          publishedAt: "2026-07-08T00:00:00Z",
          polarity: "support",
          impactScore: 6,
          confidence: 0.62,
          payload: { name: "삼성전자" }
        }
      ],
      summary: {
        total: 1,
        latestSeenAt: "2026-07-08T00:00:00Z",
        bySymbol: [{ name: "005930", count: 1, latestSeenAt: "2026-07-08T00:00:00Z" }],
        byKind: [{ name: "news", count: 1, latestSeenAt: "2026-07-08T00:00:00Z" }],
        bySource: [{ name: "Smoke News", count: 1, latestSeenAt: "2026-07-08T00:00:00Z" }],
        byPolarity: [{ name: "support", count: 1, latestSeenAt: "2026-07-08T00:00:00Z" }]
      },
      symbol: "",
      kind: "",
      limit: 80
    },
    "/api/service-accounts": {
      accounts: [
        {
          id: "main",
          label: "DB 계정",
          provider: "toss",
          baseUrl: "https://openapi.tossinvest.com",
          accountSeq: "1",
          enabled: true,
          watchlistSymbols: ["NVDA", "005930"],
          notifyProvider: "telegram",
          notifyLinkUrl: "http://127.0.0.1:3000?tab=notifications",
          clientId: true,
          clientSecret: true,
          telegramBotToken: true,
          telegramChatId: true
        }
      ]
    },
    "/api/notification-templates": {
      templates: [
        {
          messageType: "investmentInsight",
          template: "{readableMessage}",
          description: "투자 인사이트 템플릿",
          enabled: true,
          updatedAt: "2026-07-01T00:00:00.000Z"
        },
        {
          messageType: "modelReview",
          template: "{body}",
          description: "모델 리뷰 템플릿",
          enabled: true,
          updatedAt: "2026-07-01T00:00:00.000Z"
        }
      ],
      variables: ["title", "readableMessage", "dataLines", "triggerSummary", "lines", "rawLines", "body", "messageType"]
    },
    "/api/notification-rules": {
      rules: [
        {
          messageType: "investmentInsight",
          enabled: true,
          threshold: 50,
          baseScore: 35,
          lowScoreAction: "suppress",
          similarityEnabled: true,
          similarityWindowMinutes: 360,
          similarityPenalty: -40,
          similarityBypassScoreDelta: 20,
          similarityFields: ["messageType", "accountId", "symbol", "severity", "title"],
          marketHoursEnabled: true,
          marketHoursMarkets: ["KR", "US"],
          conditions: [
            { id: "severity_watch", label: "관찰 등급", type: "context_equals", field: "severity", value: "WATCH", terms: [], score: 10, enabled: true },
            { id: "status_noise", label: "상태성 노이즈", type: "text_contains_any", field: "", value: "", terms: ["정상 작동", "시세 대기"], score: -25, enabled: true }
          ],
          updatedAt: "2026-07-01T00:00:00.000Z"
        }
      ],
      conditionTypes: [
        { type: "text_contains_any", label: "메시지에 단어 포함" },
        { type: "context_equals", label: "정보 값 일치" }
      ],
      defaultThreshold: 45,
      marketHoursSessions: [
        {
          market: "KR",
          label: "국장",
          timezone: "Asia/Seoul",
          openTime: "08:00",
          closeTime: "20:00",
          weekdays: [0, 1, 2, 3, 4],
          sessions: [
            { key: "pre", label: "프리마켓", openTime: "08:00", closeTime: "08:50" },
            { key: "regular", label: "정규장", openTime: "09:00", closeTime: "15:30" },
            { key: "after", label: "애프터마켓", openTime: "15:30", closeTime: "20:00" }
          ]
        },
        {
          market: "US",
          label: "미장",
          timezone: "America/New_York",
          openTime: "04:00",
          closeTime: "20:00",
          weekdays: [0, 1, 2, 3, 4],
          sessions: [
            { key: "pre", label: "프리마켓", openTime: "04:00", closeTime: "09:30" },
            { key: "regular", label: "정규장", openTime: "09:30", closeTime: "16:00" },
            { key: "after", label: "애프터마켓", openTime: "16:00", closeTime: "20:00" }
          ]
        }
      ]
    },
    "/api/notification-jobs": {
      jobs: [
        {
          jobId: "job-kr-1",
          messageType: "modelReview",
          messageTypeLabel: "모델 리뷰",
          status: "suppressed",
          accountId: "main",
          accountLabel: "DB 계정",
          createdAt: "2026-07-01T00:05:00.000Z",
          updatedAt: "2026-07-01T00:05:00.000Z",
          sourceEventName: "monitoring.alerts_detected",
          title: "035420 판단 리뷰",
          symbol: "",
          textPreview: "035420 판단 리뷰: 조건부 보유에서 손실 관리 기준 확인으로 방어 쪽으로 이동.",
          honeyScore: 55,
          honeyThreshold: 74,
          honeyDecision: "suppressed",
          honeyReasons: ["035420 관찰 등급", "국장 닫힘"],
          honeyFingerprint: "messageType=modelreview|accountId=main|symbol=|title=035420 판단 리뷰",
          honeySimilarityRecentCount: 0,
          honeySimilarityPenalty: 0,
          honeySimilarityWindowMinutes: 360,
          honeySimilarityBypassed: false,
          marketHoursEnabled: true,
          marketHoursMarket: "KR",
          marketHoursLabel: "국장",
          marketHoursStatus: "closed",
          marketHoursDecision: "suppressed",
          marketHoursReason: "국장 닫힘"
        },
        {
          jobId: "job-crypto-1",
          messageType: "externalCryptoMove",
          messageTypeLabel: "크립토 변동",
          status: "suppressed",
          accountId: "main",
          accountLabel: "DB 계정",
          createdAt: "2026-07-01T00:00:00.000Z",
          updatedAt: "2026-07-01T00:00:00.000Z",
          sourceEventName: "monitoring.alerts_detected",
          title: "크립토 변동",
          symbol: "ETH",
          textPreview: "ETH 24h +5.4%, 7d +10.3%",
          lastError: "발송 우선도 30이 기준 45보다 낮아 발송하지 않았습니다.",
          honeyScore: 30,
          honeyThreshold: 45,
          honeyDecision: "suppressed",
          honeyReasons: ["기본 35점", "유사 메시지 360분 내 반복 -55"],
          honeyFingerprint: "messageType=externalcryptomove|symbol=eth",
          honeySimilarityRecentCount: 7,
          honeySimilarityPenalty: -55,
          honeySimilarityWindowMinutes: 360,
          honeySimilarityPreviousScore: 85,
          honeySimilarityBypassed: false,
          honeySuppressionReason: "market_closed",
          marketHoursEnabled: true,
          marketHoursMarket: "US",
          marketHoursLabel: "미장",
          marketHoursStatus: "closed",
          marketHoursDecision: "suppressed",
          marketHoursReason: "미장 닫힘 (프리마켓 04:00-09:30 · 정규장 09:30-16:00 · 애프터마켓 16:00-20:00)",
          marketHoursLocalTime: "2026-07-01T20:30:00-04:00",
          marketHoursOpenTime: "04:00",
          marketHoursCloseTime: "20:00",
          marketHoursTimezone: "America/New_York"
        }
      ],
      summary: { done: 2, suppressed: 2, failed: 0 },
      limit: 40
    },
    "/api/notification-schedules": {
      generatedAt: "2026-07-01T00:00:00.000Z",
      schedules: [
        {
          messageType: "investmentInsight",
          label: "투자 인사이트",
          enabled: true,
          status: "waiting",
          cadenceMinutes: 10,
          cadenceText: "조건이 다시 충족되면 최소 10분 간격으로 보냅니다.",
          triggerSummary: "온톨로지 관계 그래프에서 의미 있는 투자 인사이트가 생성될 때 보냅니다.",
          lastSentAt: "2026-07-01T00:00:00.000Z",
          nextEligibleAt: "2026-07-01T00:10:00.000Z",
          eligibleNow: false,
          recentTargets: [
            { accountId: "main", accountLabel: "DB 계정", target: "", sentAt: "2026-07-01T00:00:00.000Z" }
          ]
        }
      ]
    },
    "/api/symbol-universe": {
      items: [
        {
          symbol: "005930",
          name: "삼성전자",
          market: "KOSPI",
          exchange: "KOSPI",
          currency: "KRW",
          sector: "반도체",
          assetType: "STOCK",
          source: "KRX KIND Listed Companies",
          sourceUrl: "https://kind.krx.co.kr/",
          fetchedAt: "2026-07-01T00:00:00.000Z",
          lastSeenAt: "2026-07-01T00:00:00.000Z",
          stale: false
        },
        {
          symbol: "AAPL",
          name: "Apple Inc.",
          market: "NASDAQ",
          exchange: "NASDAQ Global Select",
          currency: "USD",
          sector: "AI/플랫폼",
          assetType: "STOCK",
          source: "Nasdaq Trader Symbol Directory",
          sourceUrl: "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
          fetchedAt: "2026-07-01T00:00:00.000Z",
          lastSeenAt: "2026-07-01T00:00:00.000Z",
          stale: false
        }
      ],
      summary: {
        total: 2,
        maxAgeHours: 24,
        sources: [],
        markets: [
          { market: "KOSPI", count: 1, lastSeenAt: "2026-07-01T00:00:00.000Z", stale: false, source: "KRX KIND Listed Companies", sourceUrl: "https://kind.krx.co.kr/" },
          { market: "KOSDAQ", count: 0, lastSeenAt: "", stale: true, source: "KRX KIND Listed Companies", sourceUrl: "https://kind.krx.co.kr/" },
          { market: "NASDAQ", count: 1, lastSeenAt: "2026-07-01T00:00:00.000Z", stale: false, source: "Nasdaq Trader Symbol Directory", sourceUrl: "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt" }
        ]
      }
    },
    "admin/config.json": {
      mode: "github-pages-readonly-preview",
      localData: {
        generatedAt: "2026-07-01T00:00:00.000Z",
        accountCount: 1,
        enabledAccountCount: 1,
        accounts: [
          {
            id: "pages-main",
            label: "Pages DB 계정",
            provider: "toss",
            baseUrl: "https://openapi.tossinvest.com",
            accountSeq: true,
            enabled: true,
            watchlistSymbols: ["MSFT", "035720"],
            notifyProvider: "telegram",
            notifyLinkUrl: "https://namsoon00.github.io/orbit-alpha/?tab=notifications",
            clientId: true,
            clientSecret: true,
            telegramBotToken: true,
            telegramChatId: true
          }
        ],
        settings: {
          watchlistSymbols: "MSFT,035720",
          notifyProvider: "telegram",
          notifyLinkUrl: "https://namsoon00.github.io/orbit-alpha/?tab=notifications",
          alertCadenceMinutes: "monitorDecisionChange=10"
        },
        configured: {
          tossClientId: true,
          tossClientSecret: true,
          tossAccountSeq: true,
          telegramBotToken: true,
          telegramChatId: true
        }
      }
    },
    "/api/flow-lens": {
      generatedAt: "2026-07-01T00:00:00.000Z",
      headline: "테스트 스냅샷",
      exitScore: 0,
      toss: {
        mode: "live",
        status: "ok",
        account: {},
        positions: [
          {
            symbol: "005930",
            name: "삼성전자",
            market: "KR",
            currency: "KRW",
            sector: "반도체",
            quantity: 2,
            sellableQuantity: 2,
            averagePrice: 65000,
            currentPrice: 72000,
            marketValue: 144000,
            profitLoss: 14000,
            profitLossRate: 10.7,
            source: "holding"
          }
        ],
        watchlist: [
          { symbol: "NVDA", name: "NVIDIA", market: "US", currency: "USD", sector: "반도체", currentPrice: 180 }
        ]
      },
      tossDecision: {
        items: [
          {
            symbol: "005930",
            name: "삼성전자",
            market: "KR",
            currency: "KRW",
            sector: "반도체",
            source: "holding",
            exitPressure: 64,
            ontologyOpinion: {
              action: "보유",
              tone: "watch",
              thesis: "005930 가격과 반도체 업종 관계를 함께 확인합니다.",
              conviction: 72,
              ontologyPressure: 64,
              dominant_risks: ["005930 단기 변동성"],
              contradictions: ["005930 차익 실현 신호"],
              supporting_beliefs: ["005930 업종 수요 회복"]
            }
          },
          {
            symbol: "NVDA",
            name: "NVIDIA",
            market: "US",
            currency: "USD",
            sector: "반도체",
            source: "watchlist",
            exitPressure: 32,
            ontologyOpinion: {
              action: "관심 종목: 진입 기준 대기",
              tone: "hold",
              thesis: "NVIDIA는 WATCHES 관계로 보유 판단과 분리해 관찰합니다.",
              conviction: 58,
              ontologyPressure: 28,
              dominant_risks: [],
              contradictions: ["가격 기준 추가 확인"],
              supporting_beliefs: ["반도체 업종 관찰 대상"]
            }
          }
        ],
        rules: [],
        holdingCount: 1,
        watchCount: 1,
        overallPressure: 64,
        ontologyStrategy: {
          worldview: { contradictionCount: 1 },
          tbox: {
            classes: ["Portfolio", "Stock", "WatchlistCandidate", "Sector", "Evidence", "Belief", "Opinion", "InterestRate", "YieldCurve", "FXRateSignal"],
            relationTypes: ["HOLDS", "WATCHES", "BELONGS_TO", "REQUESTS_OPINION_FROM", "HAS_EVIDENCE", "HAS_BELIEF", "HAS_OPINION", "HAS_FX_EXPOSURE", "HAS_RATE_SENSITIVITY"],
            reasoningRules: ["회사 관계 기반 판단 근거 생성"]
          },
          abox: { portfolioId: "flow-lens", entityCount: 8, relationCount: 10, beliefCount: 1 },
          entities: [
            { id: "portfolio:flow-lens", label: "테스트 포트폴리오", kind: "portfolio", properties: { ontologyBox: "ABox" } },
            { id: "stock:005930", label: "삼성전자", kind: "stock", properties: { ontologyBox: "ABox", symbol: "005930", sector: "반도체" } },
            { id: "stock:NVDA", label: "NVIDIA", kind: "stock", properties: { ontologyBox: "ABox", symbol: "NVDA", sector: "반도체", source: "watchlist", tboxClasses: ["Stock", "WatchlistCandidate"] } },
            { id: "sector:반도체", label: "반도체", kind: "sector", properties: { ontologyBox: "ABox" } },
            { id: "concept:ai-investment-review", label: "AI 투자 의견", kind: "ai-review", properties: { ontologyBox: "ABox" } },
            { id: "fx-rate:USDKRW", label: "USD/KRW 환율", kind: "fx-rate", properties: { ontologyBox: "ABox", pair: "USDKRW", baseCurrency: "USD", quoteCurrency: "KRW", rate: 1400, provider: "RuntimeSettings", tboxClasses: ["FXRateSignal"] } },
            { id: "interest-rate:DGS10", label: "미국 10년 국채금리", kind: "interest-rate", properties: { ontologyBox: "ABox", seriesId: "DGS10", provider: "FRED", date: "2026-07-01", value: 4.35, tboxClasses: ["InterestRate"] } },
            { id: "yield-curve:yieldSpread10y2y", label: "10Y-2Y 금리 스프레드", kind: "yield-curve", properties: { ontologyBox: "ABox", value: 0.4, tboxClasses: ["YieldCurve"] } }
          ],
          relations: [
            { source: "portfolio:flow-lens", target: "stock:005930", type: "HOLDS", properties: { ontologyBox: "ABox" } },
            { source: "portfolio:flow-lens", target: "stock:NVDA", type: "WATCHES", properties: { ontologyBox: "ABox", source: "watchlist" } },
            { source: "stock:005930", target: "sector:반도체", type: "BELONGS_TO", properties: { ontologyBox: "ABox" } },
            { source: "stock:NVDA", target: "sector:반도체", type: "BELONGS_TO", properties: { ontologyBox: "ABox" } },
            { source: "stock:005930", target: "concept:ai-investment-review", type: "REQUESTS_OPINION_FROM", properties: { ontologyBox: "ABox" } },
            { source: "portfolio:flow-lens", target: "fx-rate:USDKRW", type: "HAS_FX_EXPOSURE", properties: { ontologyBox: "ABox", aiInfluenceLabel: "USD/KRW 환율", source: "fxRates" }, weight: 0.7 },
            { source: "stock:NVDA", target: "fx-rate:USDKRW", type: "HAS_FX_EXPOSURE", properties: { ontologyBox: "ABox", aiInfluenceLabel: "USD/KRW 환율 민감도", source: "fxRates", rate: 1400 }, weight: 0.15 },
            { source: "portfolio:flow-lens", target: "interest-rate:DGS10", type: "HAS_RATE_SENSITIVITY", properties: { ontologyBox: "ABox", aiInfluenceLabel: "미국 10년 국채금리", source: "macro", rateSeriesId: "DGS10" }, weight: 0.72 },
            { source: "stock:NVDA", target: "interest-rate:DGS10", type: "HAS_RATE_SENSITIVITY", properties: { ontologyBox: "ABox", aiInfluenceLabel: "미국 10년 국채금리 민감도", source: "macro", rateSeriesId: "DGS10" }, weight: 0.15 },
            { source: "portfolio:flow-lens", target: "yield-curve:yieldSpread10y2y", type: "HAS_RATE_SENSITIVITY", properties: { ontologyBox: "ABox", aiInfluenceLabel: "금리 스프레드 레짐", source: "macro" }, weight: 0.74 }
          ],
          evidence: [
            { id: "evidence:005930:trend", subject: "stock:005930", kind: "trend", summary: "이동평균과 가격 추세 관계" },
            { id: "evidence:NVDA:market-observation", subject: "stock:NVDA", kind: "market-observation", summary: "관심 종목 현재가와 관찰 상태" }
          ],
          beliefs: [{ id: "belief:005930:risk", subject: "stock:005930", polarity: "risk" }],
          opinions: [
            { symbol: "005930", action: "보유", thesis: "005930은 관계 그래프에서 회사명으로 보여야 합니다.", conviction: 72 },
            { symbol: "NVDA", action: "관심 종목: 진입 기준 대기", thesis: "NVIDIA는 WATCHES 관계로 관찰합니다.", conviction: 58 }
          ],
          reasoningCards: [
            {
              id: "reasoning-card:005930",
              symbol: "005930",
              companyName: "삼성전자",
              source: "holding",
              portfolioRelation: "HOLDS",
              status: "readyForAiReview",
              finalOpinion: { action: "보유", tone: "watch", ontologyPressure: 64, conviction: 72, thesis: "005930 가격과 반도체 업종 관계를 함께 확인합니다." },
              legacyModel: { exitPressure: 64, decisionBasis: "ontologyRelationRules" },
              strategyEvidence: [{ id: "evidence:005930:trend", kind: "trend", summary: "이동평균과 가격 추세 관계" }],
              relationEvidence: [{ id: "portfolio:flow-lens|HOLDS|stock:005930", type: "HOLDS", sourceLabel: "테스트 포트폴리오", targetLabel: "삼성전자" }],
              graphContext: { stockEntityId: "stock:005930", tboxClasses: ["Stock", "Evidence", "Belief", "Opinion"], aboxEntityIds: ["stock:005930"], relationIds: ["portfolio:flow-lens|HOLDS|stock:005930"], evidenceIds: ["evidence:005930:trend"], beliefIds: ["belief:005930:risk"], opinionId: "opinion:005930" },
              aiInference: { role: "ontology-first-investment-opinion", legacyModelRole: "supporting-evidence" }
            },
            {
              id: "reasoning-card:NVDA",
              symbol: "NVDA",
              companyName: "NVIDIA",
              source: "watchlist",
              portfolioRelation: "WATCHES",
              status: "readyForAiReview",
              finalOpinion: { action: "관심 종목: 진입 기준 대기", tone: "hold", ontologyPressure: 28, conviction: 58, thesis: "NVIDIA는 WATCHES 관계로 보유 판단과 분리해 관찰합니다." },
              legacyModel: { decisionBasis: "watchlist-observation" },
              strategyEvidence: [{ id: "evidence:NVDA:market-observation", kind: "market-observation", summary: "관심 종목 현재가와 관찰 상태" }],
              relationEvidence: [{ id: "portfolio:flow-lens|WATCHES|stock:NVDA", type: "WATCHES", sourceLabel: "테스트 포트폴리오", targetLabel: "NVIDIA" }],
              graphContext: { stockEntityId: "stock:NVDA", tboxClasses: ["Stock", "WatchlistCandidate", "Evidence", "Opinion"], aboxEntityIds: ["stock:NVDA"], relationIds: ["portfolio:flow-lens|WATCHES|stock:NVDA"], evidenceIds: ["evidence:NVDA:market-observation"], beliefIds: [], opinionId: "opinion:NVDA" },
              aiInference: { role: "ontology-first-investment-opinion", legacyModelRole: "supporting-evidence" }
            }
          ],
          aiInferencePacket: {
            contract: "investment-ontology-ai-inference-v1",
            promptVersion: "ontology-investment-v2-tbox-abox",
            role: "ontology-first-investment-opinion",
            legacyModelRole: "supporting-evidence",
            inputOrder: ["tbox", "abox", "reasoningCards", "relations", "evidence", "beliefs", "opinions"],
            reasoningCardCount: 2,
            graphInputs: { entityCount: 8, relationCount: 10, evidenceCount: 2, beliefCount: 1, opinionCount: 2 },
            guardrails: ["제공된 관계 데이터만 사용합니다.", "HOLDS와 WATCHES를 구분합니다."]
          }
        },
        investmentAnalysis: {
          mode: "ontology-first",
          contract: "investment-ontology-ai-inference-v1",
          legacyModelRole: "supporting-evidence",
          reasoningCards: [],
          aiInferencePacket: {
            contract: "investment-ontology-ai-inference-v1",
            promptVersion: "ontology-investment-v2-tbox-abox",
            inputOrder: ["tbox", "abox", "reasoningCards", "relations", "evidence", "beliefs", "opinions"],
            reasoningCardCount: 2,
            guardrails: ["제공된 관계 데이터만 사용합니다.", "HOLDS와 WATCHES를 구분합니다."]
          }
        }
      },
      portfolio: {
        total: 1394000,
        invested: 144000,
        cash: 1250000,
        markets: [{ key: "KR", label: "한국장", invested: 144000, cash: 1250000, total: 1394000, cashRatio: 90 }],
        sectors: [
          { sector: "현금", value: 1250000, ratio: 90 },
          { sector: "반도체", value: 144000, ratio: 10 }
        ],
        concentration: 10
      },
      checklist: [],
      summary: []
    },
    "mock-data/market/recent-one-year.json": {
      schemaVersion: 1,
      dataQuality: "mock-synthetic",
      scenario: { id: "recent-one-year", label: "최근 1년 기준", description: "테스트 시계열" },
      request: { symbols: ["NVDA"], staticFile: true },
      series: {
        NVDA: {
          symbol: "NVDA",
          name: "NVIDIA",
          market: "US",
          currency: "USD",
          sector: "반도체",
          candles: [
            {
              date: "2026-06-28",
              open: 100,
              high: 104,
              low: 98,
              close: 100,
              volume: 100000,
              changePercent: 0,
              relativeVolume: 1,
              tradeStrength: 100,
              buyVolume: 52000,
              sellVolume: 48000,
              bidAskImbalance: 4,
              ma20: 100,
              ma60: 98
            },
            {
              date: "2026-07-01",
              open: 101,
              high: 111,
              low: 100,
              close: 110,
              volume: 180000,
              changePercent: 10,
              relativeVolume: 1.8,
              tradeStrength: 126,
              buyVolume: 120000,
              sellVolume: 60000,
              bidAskImbalance: 18,
              ma20: 103,
              ma60: 99
            }
          ]
        }
      }
    }
  };

  function renderForSearch(search, hostname, options) {
    options = options || {};
    let html = "";
    const capturedActions = {};
    const storage = new Map();
    const app = {
      get innerHTML() {
        return html;
      },
      set innerHTML(value) {
        html = String(value);
      },
      querySelector: function (selector) {
        if (
          options.captureNewAccountButton &&
          selector === '[data-action="new-service-account"]' &&
          html.indexOf('data-action="new-service-account"') >= 0
        ) {
          return {
            addEventListener: function (type, handler) {
              if (type === "click") capturedActions.newAccount = handler;
            }
          };
        }
        return null;
      },
      querySelectorAll: function (selector) {
        if (
          options.captureNewAccountButton &&
          selector === '[data-action="new-service-account"]' &&
          html.indexOf('data-action="new-service-account"') >= 0
        ) {
          return [{
            addEventListener: function (type, handler) {
              if (type === "click") capturedActions.newAccount = handler;
            }
          }];
        }
        return [];
      }
    };
    const documentElement = {
      attributes: {},
      setAttribute: function (name, value) {
        this.attributes[name] = String(value);
      }
    };

    vm.runInNewContext(code, {
      console: console,
      setTimeout: setTimeout,
      clearTimeout: clearTimeout,
      URL: URL,
      URLSearchParams: URLSearchParams,
      document: {
        documentElement: documentElement,
        getElementById: function (id) {
          return id === "app" ? app : null;
        }
      },
      window: {
        location: { protocol: "http:", hostname: hostname || "127.0.0.1", search: search || "" },
        matchMedia: function () {
          return {
            matches: false,
            addEventListener: function () {},
            addListener: function () {}
          };
        },
        localStorage: {
          getItem: function (key) {
            return storage.has(key) ? storage.get(key) : null;
          },
          setItem: function (key, value) {
            storage.set(key, String(value));
          },
          removeItem: function (key) {
            storage.delete(key);
          }
        },
        sessionStorage: {
          getItem: function (key) {
            return storage.has(key) ? storage.get(key) : null;
          },
          setItem: function (key, value) {
            storage.set(key, String(value));
          },
          removeItem: function (key) {
            storage.delete(key);
          }
        }
      },
      fetch: function (requestedPath) {
        const key = String(requestedPath).split("?")[0];
        if (!payloads[key]) throw new Error("unexpected frontend fetch: " + requestedPath);
        return Promise.resolve({
          ok: true,
          json: function () {
            return Promise.resolve(payloads[key]);
          },
          text: function () {
            return Promise.resolve(JSON.stringify(payloads[key]));
          }
        });
      }
    }, { filename: "public/app.js" });

    return new Promise(function (resolve, reject) {
      const deadline = Date.now() + 1200;

      function finishWhenRendered() {
        if (html.indexOf("loading-status-panel") >= 0 && Date.now() < deadline) {
          setTimeout(finishWhenRendered, 20);
          return;
        }
        try {
          if (options.clickNewAccount) {
            if (!capturedActions.newAccount) {
              throw new Error("새 계정 버튼 click handler가 등록되지 않았습니다.");
            }
            capturedActions.newAccount();
          }
          resolve(html);
        } catch (error) {
          reject(error);
        }
      }

      setTimeout(finishWhenRendered, 20);
    });
  }

  return Promise.all([
    renderForSearch(""),
    renderForSearch("?tab=accounts&account=management"),
    renderForSearch("?tab=accounts"),
    renderForSearch("?tab=watchlist"),
    renderForSearch("?tab=symbols"),
    renderForSearch("?tab=notifications"),
    renderForSearch("?tab=notifications&notification=policy"),
    renderForSearch("?tab=notifications&notification=templates"),
    renderForSearch("?tab=notifications&notification=diagnostics"),
    renderForSearch("?tab=modeling"),
    renderForSearch("?tab=ontology"),
    renderForSearch("?tab=feed"),
    renderForSearch("?tab=experiments"),
    renderForSearch("?tab=modeling&strategy=evidence"),
    renderForSearch("?tab=modeling&strategy=charts"),
    renderForSearch("?tab=modeling&strategy=graphs"),
    renderForSearch("?tab=modeling&strategy=rules"),
    renderForSearch("?tab=modeling&strategy=trace"),
    renderForSearch("?tab=ontology&ontology=graphs"),
    renderForSearch("?tab=monitoring"),
    renderForSearch("?tab=system"),
    renderForSearch("?tab=settings"),
    renderForSearch("?tab=feed&mode=settings"),
    renderForSearch("?tab=accounts&account=management", "namsoon00.github.io"),
    renderForSearch("?tab=accounts&account=management", null, { captureNewAccountButton: true, clickNewAccount: true }),
    renderForSearch("?tab=feed&feed=evidence"),
    renderForSearch("?tab=feed&feed=sources"),
    renderForSearch("?tab=feed&feed=settings")
  ]).then(function (pages) {
    const overviewHtml = pages[0];
    const accountHtml = pages[1];
    const accountResultsHtml = pages[2];
    const watchlistHtml = pages[3];
    const symbolUniverseHtml = pages[4];
    const notificationHtml = pages[5];
    const notificationPolicyHtml = pages[6];
    const notificationTemplateHtml = pages[7];
    const notificationDiagnosticsHtml = pages[8];
    const modelingHtml = pages[9];
    const legacyOntologyHtml = pages[10];
    const feedHtml = pages[11];
    const experimentsHtml = pages[12];
    const modelingEvidenceHtml = pages[13];
    const modelingChartHtml = pages[14];
    const modelingGraphHtml = pages[15];
    const modelingRulesHtml = pages[16];
    const modelingTraceHtml = pages[17];
    const legacyOntologyGraphHtml = pages[18];
    const monitoringHtml = pages[19];
    const systemHtml = pages[20];
    const settingsHtml = pages[21];
    const feedSettingsHtml = pages[22];
    const staticAccountHtml = pages[23];
    const newAccountHtml = pages[24];
    const feedEvidenceHtml = pages[25];
    const feedSourcesHtml = pages[26];
    const feedExplicitSettingsHtml = pages[27];

    [
      ["overview", overviewHtml],
      ["accounts", accountHtml],
      ["watchlist", watchlistHtml],
      ["symbols", symbolUniverseHtml],
      ["notifications", notificationHtml],
      ["modeling", modelingHtml],
      ["experiments", experimentsHtml],
      ["feed", feedHtml],
      ["system", systemHtml],
      ["settings", settingsHtml]
    ].forEach(function (entry) {
      assertOk(entry[1].indexOf("managed-page managed-page-" + entry[0]) >= 0, "탭이 공통 관리 페이지 템플릿을 거치지 않습니다: " + entry[0]);
      assertOk(entry[1].indexOf("page-command-strip") >= 0, "탭에 페이지 작업 상태 strip이 없습니다: " + entry[0]);
    });
    var expectedStructureGroups = {
      overview: "command",
      accounts: "market",
      watchlist: "market",
      symbols: "market",
      notifications: "decision",
      modeling: "decision",
      experiments: "decision",
      feed: "market",
      system: "control",
      settings: "control"
    };
    [
      ["overview", overviewHtml, true],
      ["accounts", accountHtml, false],
      ["watchlist", watchlistHtml, false],
      ["symbols", symbolUniverseHtml, false],
      ["notifications", notificationHtml, false],
      ["modeling", modelingHtml, false],
      ["experiments", experimentsHtml, false],
      ["feed", feedHtml, false],
      ["system", systemHtml, false],
      ["settings", settingsHtml, false]
    ].forEach(function (entry) {
      var tabId = entry[0];
      var html = entry[1];
      var hasDeskbar = entry[2];
      assertOk(html.indexOf('data-web-style="orbit-alpha-console-v2"') >= 0, "탭이 웹 스타일 계약 ID를 렌더링하지 않습니다: " + tabId);
      assertOk(html.indexOf('data-web-style-version="20260712"') >= 0, "탭이 웹 스타일 계약 버전을 렌더링하지 않습니다: " + tabId);
      assertOk(html.indexOf("web-style-shell") >= 0 && html.indexOf("web-style-nav") >= 0 && html.indexOf("web-style-topbar") >= 0, "탭의 콘솔 셸/네비/topbar 스타일 영역이 없습니다: " + tabId);
      assertOk(html.indexOf("app-nav-command") >= 0 && html.indexOf("app-nav-routine") >= 0 && html.indexOf('data-style-layer="unified-command"') >= 0, "탭의 상단 단일 command/routine rail이 렌더링되지 않습니다: " + tabId);
      if (hasDeskbar) {
        assertOk(html.indexOf("web-style-deskbar") >= 0 && html.indexOf('data-style-rail="full"') >= 0, "홈 탭의 full deskbar 스타일 rail이 없습니다.");
      } else {
        assertOk(html.indexOf("web-style-deskbar") < 0 && html.indexOf("deskbar deskbar-compact") < 0, "업무 탭에 제거해야 할 deskbar가 렌더링됩니다: " + tabId);
      }
      assertOk(html.indexOf("web-style-workspace") >= 0 && html.indexOf("web-style-main") >= 0, "탭의 workspace/main 스타일 영역이 없습니다: " + tabId);
      assertOk(html.indexOf("web-style-page") >= 0 && html.indexOf('data-style-screen="' + tabId + '"') >= 0, "탭의 managed page 스타일 화면 ID가 없습니다: " + tabId);
      assertOk(html.indexOf("web-style-command-strip") >= 0 && html.indexOf('data-style-layer="command-strip"') >= 0, "탭의 command strip 스타일 레이어가 없습니다: " + tabId);
      assertOk(html.indexOf('data-active-group="' + expectedStructureGroups[tabId] + '"') >= 0, "탭 shell이 업무 그룹을 렌더링하지 않습니다: " + tabId);
      assertOk(html.indexOf('data-structure-group="' + expectedStructureGroups[tabId] + '"') >= 0, "탭 managed page가 정보 구조 그룹을 렌더링하지 않습니다: " + tabId);
      assertOk(html.indexOf('data-structure-layer="') >= 0 && html.indexOf('data-structure-entity="') >= 0, "탭 managed page가 레이어/엔티티 구조를 렌더링하지 않습니다: " + tabId);
      assertOk(html.indexOf('data-command-group="' + expectedStructureGroups[tabId] + '"') >= 0 && html.indexOf("page-command-context") >= 0, "탭 command strip이 정보 구조 컨텍스트를 렌더링하지 않습니다: " + tabId);
      if (!hasDeskbar) {
        assertOk(html.indexOf("page-flow-spine") >= 0 && html.indexOf("이전 입력") >= 0 && html.indexOf("현재 처리") >= 0 && html.indexOf("다음 출력") >= 0, "업무 탭에 전체 데이터 흐름 spine이 없습니다: " + tabId);
      }
      assertOk(html.indexOf("admin-grid") >= 0, "탭 본문이 12컬럼 작업대 구조를 통과하지 않습니다: " + tabId);
    });
    assertOk(legacyOntologyHtml.indexOf("managed-page managed-page-modeling") >= 0, "기존 관계 분석 URL이 투자 분석 탭으로 호환 렌더링되지 않습니다.");
    assertOk(monitoringHtml.indexOf("managed-page managed-page-notifications") >= 0, "기존 모니터링 URL이 알림 공통 페이지로 열리지 않습니다.");

    assertOk(overviewHtml.indexOf("계정·알림·모델 운영 콘솔") < 0, "이전 고정 운영 콘솔 제목이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf("<h1>관제 홈</h1>") >= 0, "관제 홈 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("<h1>계정·연결</h1>") >= 0, "계정·연결 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(accountResultsHtml.indexOf("account-section-tabs") >= 0, "계정 결과 탭이 내부 섹션 탭으로 분리되지 않았습니다.");
    assertOk(accountResultsHtml.indexOf('data-account-section="status"') >= 0 && accountResultsHtml.indexOf('data-account-section="connections"') >= 0 && accountResultsHtml.indexOf('data-account-section="balance"') >= 0 && accountResultsHtml.indexOf('data-account-section="history"') >= 0, "계정 결과 섹션 탭에 상태/연결/자산 검증/데이터 이력 탭이 없습니다.");
    assertOk(accountHtml.indexOf('data-page-mode="settings"') >= 0 && accountHtml.indexOf('data-account-section="identity"') >= 0, "계정 설정 모드가 계정 식별 섹션으로 분리되지 않았습니다.");
    assertOk(accountHtml.indexOf('data-scroll-key="accounts:identity"') >= 0, "계정 내부 탭별 스크롤 키가 렌더링되지 않습니다.");
    assertOk(overviewHtml.indexOf('aria-current="page"') >= 0, "활성 탭 접근성 상태가 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("<h1>운영 설정</h1>") >= 0, "운영 설정 탭 제목이 상단에 렌더링되지 않았습니다.");
    [
      ["관제 홈", overviewHtml],
      ["계정·연결", accountHtml],
      ["알림 운영", notificationHtml],
      ["투자 판단", modelingHtml],
      ["뉴스·근거", feedHtml],
      ["운영 설정", settingsHtml]
    ].forEach(function (entry) {
      assertOk(entry[1].indexOf("page-routine-panel") >= 0 && entry[1].indexOf("현재 상태") >= 0 && entry[1].indexOf("왜 봐야 하나") >= 0 && entry[1].indexOf("다음 행동") >= 0, entry[0] + " 탭에 현재 상태/이유/다음 행동 루틴이 없습니다.");
    });
    assertOk(settingsHtml.indexOf("settings-view") >= 0, "설정 화면이 페이지 구조로 렌더링되지 않았습니다.");
    assertOk(code.indexOf("renderAppNavigation") >= 0 && styles.indexOf(".app-nav") >= 0, "앱 네비게이션 바 구조가 렌더링되지 않습니다.");
    assertOk(overviewHtml.indexOf("app-nav") < overviewHtml.indexOf("topbar"), "앱 네비게이션 바가 topbar 위에 렌더링되지 않습니다.");
    assertOk(overviewHtml.indexOf("top-action-bar") < 0, "기존 상단 버튼 나열 구조가 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf("deskbar deskbar-full") >= 0, "홈 탭은 full deskbar를 사용해야 합니다.");
    assertOk(overviewHtml.indexOf("console-shell") >= 0 && styles.indexOf("Next-generation desktop finance console") >= 0, "PC 콘솔 전용 셸 스타일이 적용되지 않았습니다.");
    assertOk(overviewHtml.indexOf("nav-tab-label") >= 0 && styles.indexOf(".nav-tab-label") >= 0, "PC 상단 네비게이션에 탭 라벨 구조가 없습니다.");
    assertOk(code.indexOf("var navigationGroups = [") >= 0 && code.indexOf("pageStructureCatalog") >= 0, "탭 업무 그룹과 화면 정보 구조 카탈로그가 없습니다.");
  assertOk(code.indexOf("pageModeOptions") >= 0 && code.indexOf("setPageViewMode") >= 0 && styles.indexOf(".page-mode-switch") >= 0, "결과/설정 모드 분리 구조가 없습니다.");
  assertOk(code.indexOf('querySelectorAll("button[data-page-mode-page][data-page-mode]")') >= 0, "결과/설정 전환 이벤트가 페이지 컨테이너에 바인딩될 수 있습니다.");
  assertOk(code.indexOf('var pageModeEnabledTabs = ["accounts", "notifications", "modeling", "feed"];') >= 0, "피드 탭이 결과/설정 모드 계약에 포함되지 않았습니다.");
  assertOk(code.indexOf("renderFeedImpactInboxPanel") >= 0 && styles.indexOf(".feed-impact-card") >= 0, "피드 투자 영향 인박스 구조가 없습니다.");
  assertOk(code.indexOf("feed-impact-article") >= 0 && code.indexOf("research-evidence-article") >= 0, "피드/근거 카드에서 기사 메타 영역이 하단으로 분리되지 않았습니다.");
  assertOk(code.indexOf("renderSettingsResponsibilityPanel") >= 0 && styles.indexOf(".settings-responsibility-panel") >= 0, "설정 탭에 탭별 책임 지도 구조가 없습니다.");
  assertOk(accountResultsHtml.indexOf("page-mode-switch") >= 0 && accountResultsHtml.indexOf('data-page-mode="results"') >= 0 && accountResultsHtml.indexOf('data-page-mode="settings"') >= 0, "계정 탭에 결과/설정 전환이 없습니다.");
    assertOk(overviewHtml.indexOf("app-nav-group") >= 0 && overviewHtml.indexOf("관제 홈") >= 0 && overviewHtml.indexOf("데이터 관리") >= 0 && overviewHtml.indexOf("판단 운영") >= 0 && overviewHtml.indexOf("운영 관리") >= 0, "PC 상단 네비게이션이 한국어 업무 그룹 순서로 구조화되지 않았습니다.");
    assertOk(overviewHtml.indexOf("Market Desk") < 0 && overviewHtml.indexOf("Decision Stack") < 0 && overviewHtml.indexOf("Control Plane") < 0, "PC 상단 네비게이션에 이전 영어 그룹명이 남아 있습니다.");
    assertOk(overviewHtml.indexOf('data-nav-group="market"') >= 0 && overviewHtml.indexOf('data-nav-group="decision"') >= 0 && overviewHtml.indexOf('data-nav-group="control"') >= 0, "탭 버튼이 업무 그룹 메타데이터를 렌더링하지 않습니다.");
    assertOk(overviewHtml.indexOf("포트폴리오 스냅샷") >= 0 && notificationHtml.indexOf("알림 판단 기록") >= 0 && modelingHtml.indexOf("투자 의견") >= 0, "주요 화면 command rail이 핵심 엔티티 정보를 렌더링하지 않습니다.");
    assertOk(styles.indexOf(".app-nav-group") >= 0 && styles.indexOf(".app-nav-command") >= 0 && styles.indexOf(".app-nav-routine") >= 0 && styles.indexOf(".page-command-context") >= 0, "업무 그룹 네비게이션과 통합 command/routine context 스타일이 없습니다.");
    assertOk(styles.indexOf("Focused work-tab header layer") >= 0 && styles.indexOf(".page-command-strip-compact") >= 0, "업무 탭 상단 compact command strip 스타일이 없습니다.");
    assertOk(styles.indexOf(".page-flow-node + .page-flow-node::before") >= 0, "업무 탭 데이터 흐름 spine 연결 표시가 없습니다.");
    assertOk(overviewHtml.indexOf("shell-home") >= 0, "홈 탭 shell이 홈 전용 배치를 사용하지 않습니다.");
    [
      ["계정·연결", accountHtml],
      ["관심 관리", watchlistHtml],
      ["종목 탐색", symbolUniverseHtml],
      ["알림 운영", notificationHtml],
      ["투자 판단", modelingHtml],
      ["뉴스·근거", feedHtml],
      ["모니터링", monitoringHtml],
      ["구조·흐름", systemHtml],
      ["운영 설정", settingsHtml]
    ].forEach(function (entry) {
      assertOk(entry[1].indexOf("deskbar deskbar-compact") < 0 && entry[1].indexOf("web-style-deskbar") < 0, entry[0] + " 탭에 업무 흐름을 밀어내는 deskbar가 남아 있습니다.");
      assertOk(entry[1].indexOf("page-command-strip-compact") >= 0, entry[0] + " 탭이 모바일/시맨틱 command strip 계약을 유지하지 않습니다.");
      assertOk(entry[1].indexOf("page-flow-spine") >= 0, entry[0] + " 탭이 전체 데이터 흐름 spine을 사용하지 않습니다.");
      assertOk(entry[1].indexOf("deskbar deskbar-full") < 0, entry[0] + " 탭에 홈 전용 full deskbar가 렌더링됩니다.");
      assertOk(entry[1].indexOf("shell-page") >= 0, entry[0] + " 탭 shell이 본문 우선 배치를 사용하지 않습니다.");
    });
    assertOk(accountHtml.indexOf("Toss/API 인증") >= 0 && accountHtml.indexOf("포트폴리오 스냅샷") >= 0, "계정 탭 흐름 spine이 계정 입력/출력을 설명하지 않습니다.");
    assertOk(feedHtml.indexOf("관심·보유 종목 뉴스/공시") >= 0 && feedHtml.indexOf("투자 판단 근거") >= 0, "뉴스·근거 탭 흐름 spine이 근거 입력/출력을 설명하지 않습니다.");
    assertOk(modelingHtml.indexOf("계정·시세·뉴스 근거") >= 0 && modelingHtml.indexOf("액션 큐·알림 후보") >= 0, "투자 판단 탭 흐름 spine이 판단 입력/출력을 설명하지 않습니다.");
    assertOk(notificationHtml.indexOf("추론 결과·중요도 점수") >= 0 && notificationHtml.indexOf("알림 이력") >= 0, "알림 운영 탭 흐름 spine이 알림 입력/출력을 설명하지 않습니다.");
    assertOk(code.indexOf('data-action="open-settings"') < 0, "topbar 설정 버튼이 상단 관리 탭과 중복됩니다.");
    assertOk(code.indexOf("pushState") >= 0 && code.indexOf("popstate") >= 0, "탭 이동이 브라우저 뒤로가기와 동기화되지 않았습니다.");
    assertOk(code.indexOf("restoreTabBarPosition") >= 0 && code.indexOf("tabBarScrollLeft") >= 0, "하단 탭 위치 복원 로직이 없습니다.");
    assertOk(code.indexOf("tabScrollPositions") >= 0 && code.indexOf("restoreRenderedPageScrollPosition") >= 0 && code.indexOf("rememberRenderedPageScrollPosition") >= 0, "탭별 본문 스크롤 복원 로직이 없습니다.");
    assertOk(overviewHtml.indexOf('data-scroll-key="overview"') >= 0, "탭 본문에 스크롤 관리 키가 렌더링되지 않습니다.");
    assertOk(designSystemDoc.indexOf("각 탭은 독립된 페이지 스크롤 위치") >= 0 && designSystemDoc.indexOf("기본은 window/page 스크롤") >= 0, "디자인 시스템 문서에 탭별 페이지 스크롤 정책이 없습니다.");
    assertOk(designSystemDoc.indexOf("업무 탭에서는 상단 상태 카드 묶음을 렌더링하지 않는다") >= 0 && designSystemDoc.indexOf("app-nav-command") >= 0 && designSystemDoc.indexOf("app-nav-routine") >= 0 && designSystemDoc.indexOf("page-flow-spine") >= 0, "디자인 시스템 문서에 단일 command/routine rail/흐름 계약이 없습니다.");
    assertOk(code.indexOf("syncTopbarScrollState") >= 0 && code.indexOf("topbar-collapsed") >= 0, "상단 제목 영역을 스크롤 상태에 따라 접는 로직이 없습니다.");
    assertOk(styles.indexOf(".shell-page.topbar-collapsed") >= 0 && styles.indexOf(".topbar-collapsed .topbar") >= 0, "상단 제목 영역 접힘 레이아웃 스타일이 없습니다.");
    assertOk(
      /var bottomTabIds = \[[^\]]*"notifications"[^\]]*"modeling"[^\]]*\];/.test(code),
      "하단 핵심 탭에 알림과 투자 분석이 배치되지 않았습니다."
    );
    assertOk(code.indexOf('var managementTabIds = ["accounts", "symbols", "feed", "system", "settings"];') >= 0, "상단 운영 메뉴 탭 구성이 역할과 맞지 않습니다.");
    assertOk(styles.indexOf(".app-nav-tab.active") >= 0 && styles.indexOf(".app-nav-menu") >= 0, "앱 네비게이션 활성 탭과 모바일 관리 메뉴 스타일 규칙이 없습니다.");
    assertOk(styles.indexOf("@media (min-width: 861px)") >= 0 && styles.indexOf(".tab-bar {\n    display: none;") >= 0, "데스크톱에서 하단 탭을 숨기는 규칙이 없습니다.");
    assertOk(styles.indexOf("position: sticky") >= 0 && styles.indexOf("bottom: 0;") >= 0 && styles.indexOf("backdrop-filter: blur(18px)") >= 0 && styles.indexOf(".app-nav.is-hidden") >= 0, "모바일 앱바 접힘/하단탭 고정 반응형 규칙이 없습니다.");
    assertOk(code.indexOf("settingsSaving") >= 0 && code.indexOf("MySQL 운영 DB") >= 0, "설정 저장 진행 상태가 렌더링되지 않습니다.");
    assertOk(code.indexOf("new window.WebSocket") >= 0, "프론트가 웹소켓 실시간 연결을 생성하지 않습니다.");
    assertOk(code.indexOf("realtime.status") >= 0, "웹소켓 상태 메시지를 처리하지 않습니다.");
    assertOk(code.indexOf("realtimeEventSnackbar") >= 0, "웹소켓 이벤트를 스낵바로 연결하지 않습니다.");
    assertOk(overviewHtml.indexOf("실시간") >= 0, "홈 요약에 실시간 연결 상태가 렌더링되지 않습니다.");
    ["overview", "accounts", "watchlist", "symbols", "notifications", "modeling", "experiments", "feed", "system", "settings"].forEach(function (tab) {
      assertOk(overviewHtml.indexOf('data-tab="' + tab + '"') >= 0, "새 탭이 렌더링되지 않았습니다: " + tab);
    });
    assertOk(overviewHtml.indexOf('data-tab="ontology"') < 0, "관계 분석 독립 탭이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf('data-tab="monitoring"') < 0, "모니터링 독립 탭이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf('data-tab="more"') < 0, "더보기 탭이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf("data-mode=") < 0, "Mock 데이터 전환 버튼이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf(">Mock<") < 0, "Mock 데이터 버튼 라벨이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf("Mock 데이터") < 0, "Mock 데이터 표시 문구가 아직 렌더링됩니다.");
    ["decision", "lab", "alerts", "holdings"].forEach(function (tab) {
      assertOk(overviewHtml.indexOf('data-tab="' + tab + '"') < 0, "기존 탭이 남아 있습니다: " + tab);
    });
    assertOk(feedHtml.indexOf("feed-view feed-view-operations") >= 0 && feedHtml.indexOf("feed-section-tabs") >= 0, "피드 탭이 영향 인박스 중심 섹션 화면으로 렌더링되지 않습니다.");
    assertOk(feedHtml.indexOf("page-mode-switch") >= 0 && feedHtml.indexOf('data-page-mode-page="feed"') >= 0 && feedHtml.indexOf('data-page-mode="results"') >= 0 && feedHtml.indexOf('data-page-mode="settings"') >= 0, "피드 탭에 결과/설정 전환이 없습니다.");
    assertOk(feedHtml.indexOf('data-feed-section="operations"') >= 0 && feedHtml.indexOf('data-feed-section="evidence"') >= 0 && feedHtml.indexOf('data-feed-section="sources"') >= 0 && feedHtml.indexOf('data-feed-section="settings"') < 0, "피드 결과 모드 섹션 탭이 영향/근거/수집원만 제공하지 않습니다.");
    assertOk(feedHtml.indexOf("투자 영향 인박스") >= 0 && feedHtml.indexOf("본문 요약") >= 0 && feedHtml.indexOf("주가 영향") >= 0 && feedHtml.indexOf("feed-impact-article") > feedHtml.indexOf("본문 요약"), "피드 기본 화면이 기사 본문 요약과 주가 영향 판단을 먼저 보여주지 않습니다.");
    assertOk(feedHtml.indexOf("데이터 품질 상태") >= 0 && feedHtml.indexOf("피드 수집 설정") < 0 && feedHtml.indexOf('data-research-evidence-form') < 0, "피드 기본 화면이 설정 폼이나 긴 근거 목록과 분리되지 않았습니다.");
    assertOk(feedEvidenceHtml.indexOf("feed-view feed-view-evidence") >= 0 && feedEvidenceHtml.indexOf("저장 근거 조회·관리") >= 0 && feedEvidenceHtml.indexOf("research-evidence-article") >= 0 && feedEvidenceHtml.indexOf('data-research-evidence-form') >= 0, "피드 근거 DB 섹션에 영향 판단형 저장 근거 조회/관리 폼이 없습니다.");
    assertOk(feedSourcesHtml.indexOf("feed-view feed-view-sources") >= 0 && feedSourcesHtml.indexOf("수집 채널 매트릭스") >= 0 && feedSourcesHtml.indexOf("수집·판단 흐름") >= 0, "피드 수집원 섹션이 채널과 데이터 흐름을 함께 보여주지 않습니다.");
    assertOk(feedSettingsHtml.indexOf("피드 수집 설정") >= 0 && feedSettingsHtml.indexOf("feed-settings-action-grid") >= 0 && feedSettingsHtml.indexOf("뉴스·아카이브") >= 0 && feedSettingsHtml.indexOf("그래프 추론") >= 0 && feedSettingsHtml.indexOf("긴 매핑값") >= 0, "피드 설정 섹션이 요약 카드와 상세 레이어 진입점 구조로 렌더링되지 않습니다.");
    assertOk(code.indexOf("renderFeedSettingsEditorPanel") >= 0 && code.indexOf('newsCollectionRateLimitSeconds') >= 0 && code.indexOf('data-setting="externalSecCompanyCiks"') >= 0, "피드 설정 상세 레이어에 세부 수집 설정 필드가 없습니다.");
    assertOk(feedSettingsHtml.indexOf('data-section-mode="settings"') >= 0 && feedSettingsHtml.indexOf('data-feed-section="settings"') >= 0 && feedSettingsHtml.indexOf('data-feed-section="operations"') < 0, "피드 설정 모드가 수집 설정 섹션으로만 분리되지 않았습니다.");
    assertOk(/\.feed-view-settings \.feed-settings-panel\s*\{[\s\S]*grid-column: 1 \/ -1;/.test(styles) && styles.indexOf(".feed-impact-inbox-panel") >= 0 && styles.indexOf(".feed-settings-sections") >= 0 && styles.indexOf(".feed-evidence-workspace") >= 0 && styles.indexOf(".feed-source-workspace") >= 0, "PC 피드 섹션별 워크스페이스 스타일이 정의되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-responsibility-panel") >= 0 && settingsHtml.indexOf("탭별 결과와 설정 책임") >= 0 && settingsHtml.indexOf("뉴스·근거") >= 0 && settingsHtml.indexOf("피드 설정") >= 0, "운영 설정 탭에 결과/설정 책임 지도가 렌더링되지 않습니다.");
    assertOk(systemHtml.indexOf("<h1>구조·흐름</h1>") >= 0, "구조·흐름 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(systemHtml.indexOf("system-guide-view") >= 0, "시스템 설명 탭이 전용 레이아웃으로 렌더링되지 않습니다.");
    assertOk(systemHtml.indexOf("SYSTEM MANUAL") >= 0 && systemHtml.indexOf("처음 사용하는 순서") >= 0, "시스템 탭에 사용자 매뉴얼이 없습니다.");
    assertOk(systemHtml.indexOf("DATA FLOW") >= 0 && systemHtml.indexOf("system-flow-diagram data-flow") >= 0, "시스템 탭에 데이터 흐름 다이어그램이 없습니다.");
    assertOk(systemHtml.indexOf("EVENT FLOW") >= 0 && systemHtml.indexOf("monitoring.snapshot_collected") >= 0 && systemHtml.indexOf("notification.job_queued") >= 0, "시스템 탭에 이벤트 흐름 설명이 없습니다.");
    assertOk(systemHtml.indexOf("ALERT PIPELINE") >= 0 && systemHtml.indexOf("system-notification-flow") >= 0, "시스템 탭에 알림 생성 흐름 다이어그램이 없습니다.");
    assertOk(systemHtml.indexOf("ONTOLOGY MODEL") >= 0 && systemHtml.indexOf("TBox") >= 0 && systemHtml.indexOf("ABox") >= 0, "시스템 탭에 온톨로지 모델 설명이 없습니다.");
    assertOk(systemHtml.indexOf("ONTOLOGY AUDIT") >= 0 && systemHtml.indexOf("ontology-audit-panel") >= 0, "시스템 탭에 온톨로지 감사 콘솔이 없습니다.");
    assertOk(systemHtml.indexOf("MySQL operational tables") >= 0, "시스템 탭 데이터 흐름이 MySQL 운영 DB 기준으로 렌더링되지 않습니다.");
    assertOk(styles.indexOf(".system-guide-view") >= 0 && styles.indexOf(".system-flow-diagram") >= 0 && styles.indexOf(".system-event-track") >= 0, "시스템 설명 탭 스타일이 없습니다.");
    assertOk(/\.system-guide-view > \.ontology-audit-panel\s*\{[\s\S]*grid-column: 1 \/ -1;[\s\S]*width: 100%;/.test(styles), "온톨로지 감사 콘솔이 구조·흐름 12컬럼 전체 폭을 차지하지 않습니다.");
    assertOk(overviewHtml.indexOf("admin-monitoring-panel") >= 0, "모니터링 상태 패널이 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("account-directory-panel") >= 0, "홈에 DB 계정 패널이 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("account-watchlist-panel") >= 0, "홈에 계정별 관심 종목 패널이 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("DB 저장 계정") >= 0, "DB 계정 제목이 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("home-command-grid") >= 0, "홈 운영 요약 카드가 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("home-action") >= 0, "홈 빠른 이동 카드가 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("토스 실데이터 연결됨") >= 0, "홈에 토스 연결 상태가 표시되지 않습니다.");
    assertOk(accountHtml.indexOf("data-account-form") >= 0, "계정 등록 폼이 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("DB 저장 계정") >= 0, "계정 탭에 DB 계정 목록이 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("account-manager-summary") >= 0, "계정 탭 요약 카드가 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("account-exposure-grid") >= 0, "PC 계좌 노출 지표가 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("계정 노출 상태") >= 0, "계좌 노출 지표 접근성 라벨이 없습니다.");
    assertOk(accountHtml.indexOf("account-credential-grid") >= 0, "계정 보안 상태 요약이 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("Secret 설정됨") >= 0, "토스 secret 설정 상태가 표시되지 않습니다.");
    assertOk(accountHtml.indexOf("저장됨 - 새 값 입력 시 교체") >= 0, "저장된 API 값의 교체 안내가 표시되지 않습니다.");
    assertOk(accountHtml.indexOf("Telegram Bot Token") < 0 && accountHtml.indexOf("Bot token 설정됨") < 0 && accountHtml.indexOf("알림 금지") < 0 && accountHtml.indexOf("메시지 전달 수준") < 0, "계정 탭에 알림 채널/전달 정책 UI가 남아 있습니다.");
    assertOk(accountResultsHtml.indexOf("Telegram Bot Token") < 0 && accountResultsHtml.indexOf("Bot token 설정됨") < 0 && accountResultsHtml.indexOf("알림 금지") < 0 && accountResultsHtml.indexOf("메시지 전달 수준") < 0, "계정 결과 탭에 알림 채널/전달 정책 UI가 남아 있습니다.");
    assertOk(code.indexOf('notifyProvider: String(draft.notifyProvider') < 0 && code.indexOf("if (String(draft.telegramBotToken") < 0, "계정 저장 payload가 알림 채널 secret을 전송합니다.");
    assertOk(code.indexOf("function createNewAccountDraft") >= 0, "새 계정 전용 draft 생성 로직이 없습니다.");
    assertOk(code.indexOf("state.accountDraft = createNewAccountDraft();") >= 0, "새 계정 버튼이 새 draft 생성 로직과 연결되지 않았습니다.");
    assertOk(code.indexOf('"account-" + index') >= 0, "새 계정 ID 중복 방지 로직이 없습니다.");
    assertOk(code.indexOf("draftAccountId: account.id") >= 0, "계정 저장 후 저장한 계정을 계속 선택하지 않습니다.");
    assertOk(newAccountHtml.indexOf('value="account-2"') >= 0, "새 계정 클릭 후 중복 없는 계정 ID가 채워지지 않았습니다.");
    assertOk(newAccountHtml.indexOf('value="추가 계정 2"') >= 0, "새 계정 클릭 후 새 표시 이름이 채워지지 않았습니다.");
    assertOk(newAccountHtml.indexOf("새 계정 등록") >= 0, "새 계정 클릭 후 등록 모드로 전환되지 않았습니다.");
    assertOk(accountHtml.indexOf('value="DB 계정"') >= 0, "로컬 DB 계정 표시 이름이 폼에 채워지지 않았습니다.");
    assertOk(accountHtml.indexOf('value="NVDA,005930"') >= 0, "로컬 DB 관심 종목이 폼에 채워지지 않았습니다.");
    assertOk(accountHtml.indexOf('value="true"') < 0, "마스킹된 boolean 값이 계정 폼에 그대로 표시됩니다.");
    assertOk(watchlistHtml.indexOf("계정별 관심 종목") >= 0, "관심종목 탭에 계정별 관심 종목이 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("account-watchlist-workbench") >= 0, "관심종목 탭에 계정별 편집 워크벤치가 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("data-watch-account-select") >= 0, "관심종목 탭에 계정 선택 버튼이 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("data-watch-account-id=\"main\"") >= 0, "관심종목 추가 폼이 선택 계정에 연결되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("data-watch-symbol-input") >= 0, "관심종목 검색 입력창이 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("data-watch-suggest-list") >= 0, "관심종목 서제스트 영역이 렌더링되지 않았습니다.");
    assertOk(code.indexOf("/api/symbol-universe/suggest?") >= 0 && code.indexOf("팔란티어") >= 0 && code.indexOf("PLTR") >= 0, "관심종목 자동완성이 경량 API와 팔란티어 별칭을 사용하지 않습니다.");
    assertOk(watchlistHtml.indexOf("watch-row-meta") >= 0, "관심종목 알림/시세 상태가 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("시세 알림") >= 0, "관심종목 시세 알림 상태가 표시되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("symbol-result-list") < 0, "관심종목 탭에 전체 종목 결과 리스트가 남아 있습니다.");
    assertOk(watchlistHtml.indexOf("전체 종목 DB") < 0, "관심종목 탭에 전체 종목 DB 안내가 남아 있습니다.");
    assertOk(watchlistHtml.indexOf("NVIDIA") >= 0 && watchlistHtml.indexOf("삼성전자") >= 0, "DB 계정 관심 종목명이 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("NVIDIA · NVDA") < 0 && watchlistHtml.indexOf("삼성전자 · 005930") < 0, "관심 종목 표시 텍스트에 종목코드가 노출됩니다.");
    assertOk(accountHtml.indexOf("관심 NVIDIA, 삼성전자") >= 0, "계정 목록 관심 종목 요약이 표시명만 사용하지 않습니다.");
    assertOk(symbolUniverseHtml.indexOf("<h1>종목 탐색</h1>") >= 0, "종목 탐색 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-result-list") >= 0, "전체종목 탭에 종목 결과 리스트가 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-summary-metric") >= 0, "전체종목 탭에 시장 요약 지표가 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-bulk-bar") >= 0 && symbolUniverseHtml.indexOf('data-action="add-visible-symbols"') >= 0, "전체종목 탭에 페이지 일괄 추가 액션이 없습니다.");
    assertOk(symbolUniverseHtml.indexOf("data-symbol-add-account") >= 0, "전체종목 탭에 관심 추가 대상 계정 선택이 없습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-summary-card") < 0 && symbolUniverseHtml.indexOf("symbol-source-card") < 0, "전체종목 탭에 중첩 카드 클래스가 남아 있습니다.");
    assertOk(code.indexOf("renderSymbolUniverseStarterConsole") >= 0 && styles.indexOf(".symbol-empty-console") >= 0, "전체종목 빈 상태가 원천/카탈로그/다음 행동 콘솔로 정리되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-command-center") >= 0, "알림 운영 command center가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-ops-rail") >= 0, "알림 상태 레일이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-command-panel") < 0, "카드형 알림 관제 패널이 남아 있습니다.");
    assertOk(notificationPolicyHtml.indexOf("notification-command-center") >= 0 && notificationPolicyHtml.indexOf("notification-ops-rail") >= 0, "정책 섹션 상단 운영 요약이 렌더링되지 않았습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-command-center") >= 0 && notificationTemplateHtml.indexOf("notification-ops-rail") >= 0, "템플릿 섹션 상단 운영 요약이 렌더링되지 않았습니다.");
    assertOk(notificationDiagnosticsHtml.indexOf("notification-command-center") >= 0 && notificationDiagnosticsHtml.indexOf("notification-ops-rail") >= 0, "진단 섹션 상단 운영 요약이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-section-bar") >= 0, "알림 내부 섹션 상단 탭 바가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-section-tabs") >= 0, "알림 내부 섹션 탭이 렌더링되지 않았습니다.");
    assertOk(styles.indexOf(".notification-command-center") >= 0 && styles.indexOf("grid-template-columns: repeat(auto-fit, minmax(118px, 1fr))") >= 0, "알림 내부 섹션이 command center 자동 맞춤 탭 스타일로 정의되지 않았습니다.");
    assertOk(notificationHtml.indexOf('data-notification-section="candidates"') >= 0 && notificationHtml.indexOf('data-notification-section="policy"') < 0, "알림 결과 모드가 정책/템플릿/진단 설정과 분리되지 않았습니다.");
    assertOk(notificationPolicyHtml.indexOf('data-page-mode="settings"') >= 0 && notificationPolicyHtml.indexOf('data-notification-section="policy"') >= 0 && notificationPolicyHtml.indexOf('data-notification-section="templates"') >= 0 && notificationPolicyHtml.indexOf('data-notification-section="diagnostics"') >= 0, "알림 설정 모드가 정책/템플릿/진단 섹션을 보여주지 않습니다.");
    assertOk(notificationHtml.indexOf("notification-section-bar") < notificationHtml.indexOf("notification-ops-rail"), "알림 섹션 탭이 상태 레일 위에 배치되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-decision-panel") >= 0, "최근 알림 판단 패널이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-ops-rail") < notificationHtml.indexOf("notification-decision-panel"), "기본 현황에서 상태 레일 다음에 최근 알림 판단이 이어지지 않습니다.");
    assertOk(notificationHtml.indexOf("notification-decision-body") >= 0, "최근 알림 판단 본문 영역이 분리되지 않았습니다.");
    assertOk(code.indexOf("renderNotificationDecisionEmptyConsole") >= 0 && styles.indexOf(".notification-empty-console") >= 0, "최근 알림 판단 빈 상태가 후보/판단/설정 흐름으로 구조화되지 않았습니다.");
    assertOk(
      code.indexOf("renderInvestmentCalendarRailPanel") >= 0 &&
        code.indexOf("investment-calendar-next-time") >= 0 &&
        code.indexOf("investment-calendar-next-title") >= 0 &&
        styles.indexOf(".investment-calendar-rail") >= 0 &&
        styles.indexOf("Calendar next-alert hierarchy") >= 0,
      "투자 캘린더 다음 알림이 시간 우선 요약 카드로 정리되지 않았습니다."
    );
    assertOk(code.indexOf("renderOntologyExperimentStarterPanel") >= 0 && styles.indexOf(".ontology-experiment-starter-grid") >= 0, "전략 검증 빈 상태가 시작 흐름 카드로 정리되지 않았습니다.");
    assertOk(notificationHtml.indexOf("NAVER") >= 0, "최근 알림 판단에서 국내 종목명이 코드 대신 렌더링되지 않습니다.");
    assertOk(notificationHtml.indexOf("035420 판단") < 0 && notificationHtml.indexOf("symbol=035420") < 0 && notificationHtml.indexOf(">035420<") < 0, "최근 알림 판단에 국내 종목코드가 그대로 노출됩니다.");
    assertOk(code.indexOf('indexOf("API를 찾지 못했습니다")') >= 0, "최근 알림 판단 API 미지원 상태를 빈 상태로 처리하지 않습니다.");
    assertOk(code.indexOf("notification-state-message") >= 0, "최근 알림 빈 상태 전용 상태 박스 렌더링 경로가 없습니다.");
    assertOk(notificationHtml.indexOf("admin-message-group-list") < 0, "기본 현황 화면에 정책 목록이 렌더링됩니다.");
    assertOk(notificationHtml.indexOf("notification-template-manager-panel") < 0, "기본 현황 화면에 템플릿 관리 화면이 렌더링됩니다.");
    assertOk(notificationPolicyHtml.indexOf("admin-message-group-list") >= 0, "정책 섹션에 알림 타입 그룹 목록이 렌더링되지 않았습니다.");
    assertOk(notificationPolicyHtml.indexOf("data-message-group-toggle") >= 0, "정책 섹션에 그룹 접기/펼치기 버튼이 없습니다.");
    assertOk(notificationPolicyHtml.indexOf("admin-message-row") < 0, "정책 섹션 기본 화면에 메시지 타입 행이 펼쳐져 있습니다.");
    assertOk(code.indexOf("data-message-select") >= 0, "정책 섹션에 상세 편집 선택 버튼 경로가 없습니다.");
    assertOk(notificationPolicyHtml.indexOf("notification-policy-detail") < 0, "정책 섹션 목록 화면에 상세 편집 패널이 같이 렌더링됩니다.");
    assertOk(code.indexOf("notificationPolicyEditorOpen") >= 0 && code.indexOf("data-notification-editor-close") >= 0, "정책 섹션 상세 편집 레이어 닫기 경로가 없습니다.");
    assertOk(code.indexOf("notification-policy-modal-backdrop") >= 0 && code.indexOf("renderNotificationPolicyDetailPanel()") >= 0, "정책 상세 편집 레이어 렌더링 경로가 없습니다.");
    assertOk(notificationPolicyHtml.indexOf("admin-message-details") < 0, "정책 행 안에 inline 상세 편집기가 남아 있습니다.");
    assertOk(code.indexOf("renderNotificationTemplateRow(template, { policyDetail: true })") >= 0, "알림 타입별 템플릿 상세 렌더링 경로가 없습니다.");
    assertOk(code.indexOf("renderNotificationRuleEditor(rule.key, { inline: true })") >= 0, "정책 상세의 전체 룰 편집 경로가 없습니다.");
    assertOk(notificationDiagnosticsHtml.indexOf("notification-diagnostics-summary-panel") >= 0 && notificationDiagnosticsHtml.indexOf('data-work-detail="notification-rule-diagnostics"') >= 0 && notificationDiagnosticsHtml.indexOf("조건 진단") >= 0, "진단 섹션이 요약 카드와 조건 진단 상세 진입점으로 렌더링되지 않았습니다.");
    assertOk(code.indexOf("renderNotificationAdvancedRulePanel") >= 0 && code.indexOf("renderNotificationRuleEditor(rule.key, { inline: true })") >= 0 && code.indexOf("최소 발송 우선도") >= 0, "진단 섹션의 전체 룰 상세 레이어 렌더링 경로가 없습니다.");
    assertOk(code.indexOf("유사 메시지") >= 0 && code.indexOf("data-notification-rule-similarity-enabled") >= 0, "유사 메시지 억제 설정 경로가 없습니다.");
    assertOk(code.indexOf("data-notification-rule-fields") >= 0, "유사 메시지 fingerprint 필드 입력 경로가 없습니다.");
    assertOk(code.indexOf("장 시간 필터") >= 0 && code.indexOf("data-notification-rule-market-hours-enabled") >= 0, "장 시간 필터 설정 경로가 없습니다.");
    assertOk(code.indexOf("국장") >= 0 && code.indexOf("미장") >= 0, "국장/미장 장 시간 설정 경로가 없습니다.");
    assertOk(code.indexOf("프리마켓") >= 0 && code.indexOf("애프터마켓") >= 0, "프리/애프터마켓 장 시간 설정 경로가 없습니다.");
    assertOk(code.indexOf("data-notification-rule-market-hours-market") >= 0, "장 시간 시장 선택 체크박스 경로가 없습니다.");
    assertOk(code.indexOf("data-notification-rule-condition-value") >= 0, "발송 우선도 조건 값 편집 입력 경로가 없습니다.");
    assertOk(code.indexOf("data-rule-save") >= 0 && code.indexOf("investmentInsight") >= 0, "알림 타입별 룰 저장 경로가 없습니다.");
    assertOk(code.indexOf("investmentInsight") >= 0 && code.indexOf("watchlistOntologySignal") >= 0, "온톨로지 투자 알림 룰 저장 경로가 없습니다.");
    assertOk(notificationHtml.indexOf("최근 알림 판단") >= 0, "최근 알림 판단 제목이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-score-route") >= 0 && notificationHtml.indexOf("85 → 30 (-55)") >= 0, "최근 알림 판단의 점수 변화가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-score-factors") >= 0, "최근 알림 판단의 상승/감점 요인이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("360분 내 7회 · 우선도 -55") >= 0, "최근 알림 판단의 유사 메시지 감점이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("미장 닫힘") >= 0, "최근 알림 판단의 장 시간 외 보류 사유가 렌더링되지 않았습니다.");
    assertOk(
      notificationHtml.indexOf("notification-decision-detail") >= 0 &&
        notificationHtml.indexOf('data-work-detail="notification-job"') >= 0 &&
        code.indexOf("renderNotificationDecisionDetail(activeJob, { compact: true })") >= 0 &&
        code.indexOf("!compact && fingerprint") >= 0,
      "최근 알림 판단 전체 fingerprint가 상세 리포트 레이어로 분리되지 않았습니다.",
    );
    assertOk(notificationHtml.indexOf('data-action="refresh-notification-jobs"') >= 0, "최근 알림 판단 새로고침 버튼이 없습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-template-manager-panel") >= 0, "템플릿 섹션이 렌더링되지 않았습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-template-workbench") >= 0, "템플릿 섹션이 선택형 워크벤치로 렌더링되지 않았습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-template-select-row") >= 0 && notificationTemplateHtml.indexOf("data-template-select") >= 0, "템플릿 섹션에 선택 목록이 없습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-template-detail") < 0, "템플릿 섹션 목록 화면에 상세 편집 패널이 같이 렌더링됩니다.");
    assertOk(code.indexOf("notificationTemplateEditorOpen") >= 0 && code.indexOf("data-notification-template-editor-close") >= 0, "템플릿 상세 편집 레이어 닫기 경로가 없습니다.");
    assertOk(code.indexOf("notification-template-modal-backdrop") >= 0 && code.indexOf("renderNotificationTemplateRow(selected, { templateDetail: true })") >= 0, "템플릿 상세 편집 레이어 렌더링 경로가 없습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-rule-editor") < 0, "템플릿 섹션에 룰 편집기가 섞여 있습니다.");
    assertOk(notificationDiagnosticsHtml.indexOf('data-work-detail="notification-delivery-settings"') >= 0 && code.indexOf("renderAdminDeliveryPanel()") >= 0 && code.indexOf("settings-api-grid") >= 0, "진단 섹션에 전달 설정 상세 레이어 진입점이 없습니다.");
    assertOk(notificationDiagnosticsHtml.indexOf("전달 채널") >= 0 && notificationDiagnosticsHtml.indexOf("Telegram 토큰") >= 0, "진단 섹션에 전달 채널 요약이 표시되지 않습니다.");
    assertOk(notificationDiagnosticsHtml.indexOf('data-work-detail="notification-threshold-settings"') >= 0 && code.indexOf("renderNotificationThresholdPanel") >= 0 && code.indexOf("alert-threshold-grid") >= 0, "진단 섹션에 알림 임계값 상세 레이어 진입점이 없습니다.");
    assertOk(code.indexOf("data-template-test-send") >= 0, "실제 데이터 알림 테스트 발송 경로가 없습니다.");
    assertOk(code.indexOf("모니터링 정상 작동") >= 0, "상태 확인 템플릿 미리보기 샘플 경로가 없습니다.");
    assertOk(code.indexOf("매수 점수") >= 0, "타입별 템플릿 미리보기 샘플 경로가 없습니다.");
    assertOk(code.indexOf("data-notification-template") >= 0 && code.indexOf("investmentInsight") >= 0, "투자 인사이트 템플릿 textarea 경로가 없습니다.");
    assertOk(notificationTemplateHtml.indexOf("{rawLines}") >= 0, "알림 템플릿 변수가 렌더링되지 않았습니다.");
    assertOk(notificationDiagnosticsHtml.indexOf("tab=notifications") >= 0, "알림 링크 기본값이 새 알림 탭을 가리키지 않습니다.");
    assertOk(modelingHtml.indexOf("strategy-section-bar") >= 0, "투자 분석 내부 섹션 탭 바가 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("strategy-section-tabs") >= 0, "투자 분석 내부 섹션 탭이 렌더링되지 않았습니다.");
    assertOk(styles.indexOf(".strategy-section-tabs") >= 0 && styles.indexOf(".strategy-section-bar") >= 0, "투자 분석 내부 섹션 스타일이 정의되지 않았습니다.");
    assertOk(code.indexOf("renderInvestmentTabWorkspace") >= 0 && styles.indexOf(".investment-tab-workspace") >= 0, "투자 분석 탭이 짧은 워크스페이스 구조를 쓰지 않습니다.");
    assertOk(modelingHtml.indexOf('data-strategy-section="evidence"') >= 0 && modelingHtml.indexOf('data-strategy-section="charts"') >= 0 && modelingHtml.indexOf('data-strategy-section="graphs"') >= 0 && modelingHtml.indexOf('data-strategy-section="trace"') >= 0 && modelingHtml.indexOf('data-strategy-section="rules"') < 0, "투자 분석 결과 모드가 전략 룰 설정과 분리되지 않았습니다.");
    assertOk(modelingRulesHtml.indexOf('data-page-mode="settings"') >= 0 && modelingRulesHtml.indexOf('data-strategy-section="rules"') >= 0, "투자 분석 설정 모드가 전략 룰 섹션을 보여주지 않습니다.");
    assertOk(modelingHtml.indexOf("오늘의 판단") >= 0 && modelingHtml.indexOf("투자 근거") >= 0 && modelingHtml.indexOf("통합 차트") >= 0 && modelingHtml.indexOf("온톨로지") >= 0 && modelingHtml.indexOf("검증·리뷰") >= 0, "투자전략 탭이 새 업무 흐름 라벨을 렌더링하지 않습니다.");
    assertOk(modelingHtml.indexOf('data-strategy-section="data"') < 0 && modelingHtml.indexOf('data-strategy-section="policy"') < 0 && modelingHtml.indexOf('data-strategy-section="ontology"') < 0 && modelingHtml.indexOf('data-strategy-section="registry"') < 0 && modelingHtml.indexOf('data-strategy-section="results"') < 0, "이전 전략/관계 섹션 ID가 통합 탭에 남아 있습니다.");
    assertOk(code.indexOf('if (requested === "ontology" || requested === "relations") return "modeling";') >= 0, "기존 관계 분석 URL을 투자 분석 탭으로 매핑하지 않습니다.");
    assertOk(code.indexOf("writeStrategySectionHistory") >= 0 && code.indexOf("strategySectionUrl") >= 0, "투자 분석 내부 탭 URL 동기화 경로가 없습니다.");
    assertOk(modelingHtml.indexOf("<h1>투자 판단</h1>") >= 0, "투자 판단 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(legacyOntologyHtml.indexOf("<h1>투자 판단</h1>") >= 0 && legacyOntologyHtml.indexOf("investment-bridge-panel") >= 0, "기존 관계 분석 URL이 통합 투자 판단 개요로 열리지 않습니다.");
    assertOk(modelingHtml.indexOf("investment-tab-workspace-overview") >= 0, "투자 분석 개요가 워크스페이스 컨테이너로 렌더링되지 않습니다.");
    assertOk(modelingHtml.indexOf("investment-bridge-panel") >= 0 && modelingHtml.indexOf("전략 데이터와 관계 분석을 잇는 추론 구조") >= 0, "투자 분석 개요에 전략-관계-AI 파이프라인이 없습니다.");
    assertOk(modelingHtml.indexOf("AI 추론 입력") >= 0 && modelingHtml.indexOf("investment-ontology-ai-inference-v1") >= 0, "투자 분석 개요에 AI inference packet 계약이 보이지 않습니다.");
    assertOk(modelingHtml.indexOf("investment-evidence-panel") < 0 && modelingHtml.indexOf("투자 근거 카드") < 0, "투자 분석 개요에 근거 카드 미리보기가 중복 렌더링됩니다.");
    assertOk(modelingHtml.indexOf("HOLDS") >= 0 && modelingHtml.indexOf("WATCHES") >= 0, "보유/관심 관계 구분이 투자 분석 개요에 표시되지 않습니다.");
    assertOk(modelingChartHtml.indexOf("macro-signal-panel") >= 0 && modelingChartHtml.indexOf("환율·금리 관계 신호") >= 0, "통합 차트 섹션에 환율·금리 온톨로지 신호 패널이 없습니다.");
    assertOk(modelingChartHtml.indexOf("USD/KRW 환율") >= 0 && modelingChartHtml.indexOf("미국 10년 국채금리") >= 0 && modelingChartHtml.indexOf("10Y-2Y 금리 스프레드") >= 0, "환율·금리 신호 값이 통합 차트 섹션에 표시되지 않습니다.");
    assertOk(styles.indexOf(".macro-signal-grid") >= 0 && styles.indexOf(".macro-relation-row") >= 0, "환율·금리 온톨로지 신호 UI 스타일이 없습니다.");
    assertOk(modelingHtml.indexOf("strategy-process-panel") < 0 && modelingHtml.indexOf("model-guide-panel") < 0, "개요 탭에 긴 운영 가이드 패널이 섞여 있습니다.");
    assertOk(modelingHtml.indexOf("strategy-data-panel") < 0 && modelingHtml.indexOf("admin-modeling-panel") < 0 && modelingHtml.indexOf("model-preview-panel") < 0, "개요 탭에 상세 운영 섹션 패널이 섞여 있습니다.");
    assertOk(modelingEvidenceHtml.indexOf("investment-tab-workspace-evidence") >= 0 && modelingEvidenceHtml.indexOf("investment-evidence-panel") >= 0 && modelingEvidenceHtml.indexOf("strategy-data-panel") >= 0, "근거 카드 섹션에 카드와 데이터 점검이 함께 렌더링되지 않았습니다.");
    assertOk(modelingEvidenceHtml.indexOf("전략 근거") >= 0 && modelingEvidenceHtml.indexOf("관계 근거") >= 0, "근거 카드가 전략 근거와 관계 근거를 분리해서 보여주지 않습니다.");
    assertOk(modelingEvidenceHtml.indexOf("삼성전자") >= 0 && modelingEvidenceHtml.indexOf("005930 가격과") < 0, "근거 카드 본문에 종목코드가 회사명으로 치환되지 않았습니다.");
    assertOk(modelingEvidenceHtml.indexOf("체결강도") >= 0 && modelingEvidenceHtml.indexOf("모델-알림 기준") >= 0, "근거 카드 섹션에 전략 데이터 점검 항목이 없습니다.");
    assertOk(modelingHtml.indexOf("investment-action-panel") >= 0 && modelingHtml.indexOf("model-preview-panel") < 0, "오늘의 판단 섹션은 액션 큐만 짧게 보여줘야 합니다.");
    assertOk(modelingHtml.indexOf("investment-evidence-panel") < 0, "오늘의 판단 섹션에 근거 카드가 중복 렌더링됩니다.");
    assertOk(modelingChartHtml.indexOf("investment-tab-workspace-charts") >= 0 && modelingChartHtml.indexOf("investment-chart-panel") >= 0 && modelingChartHtml.indexOf("investment-chart-periods") >= 0, "통합 차트 섹션이 별도 워크스페이스와 기간 컨트롤로 렌더링되지 않습니다.");
    assertOk(modelingChartHtml.indexOf('data-investment-chart-period="1d"') >= 0 && modelingChartHtml.indexOf('data-investment-chart-period="1w"') >= 0 && modelingChartHtml.indexOf('data-investment-chart-period="1m"') >= 0 && modelingChartHtml.indexOf('data-investment-chart-period="custom"') >= 0, "통합 차트 기간 컨트롤이 일/주/월/사용자 기간을 제공하지 않습니다.");
    assertOk(modelingChartHtml.indexOf("실제 데이터") >= 0 && modelingChartHtml.indexOf("mock") >= 0 && modelingChartHtml.indexOf("API·스냅샷 출처") >= 0, "통합 차트가 실제/mock 데이터와 출처 표시 정책을 보여주지 않습니다.");
    assertOk(modelingChartHtml.indexOf("investment-evidence-panel") < 0 && modelingChartHtml.indexOf("ontology-cytoscape") < 0, "통합 차트 섹션에 근거 카드나 온톨로지 그래프가 섞여 있습니다.");
    assertOk(modelingGraphHtml.indexOf("<h2>온톨로지</h2>") >= 0 && modelingGraphHtml.indexOf("전체 규칙 구조 그래프") >= 0 && modelingGraphHtml.indexOf("핵심 데이터 관계 그래프") >= 0, "온톨로지 섹션이 통합 탭 내부에서 렌더링되지 않았습니다.");
    assertOk(modelingGraphHtml.indexOf("ontology-cytoscape") >= 0 && modelingGraphHtml.indexOf("규칙과 관계 해설") >= 0 && modelingGraphHtml.indexOf("RuleBox 규칙") >= 0, "관계 그래프 섹션에 Cytoscape 그래프와 텍스트 보조 패널이 없습니다.");
    assertOk(legacyOntologyGraphHtml.indexOf("managed-page managed-page-modeling") >= 0 && legacyOntologyGraphHtml.indexOf("<h2>온톨로지</h2>") >= 0, "기존 관계 그래프 URL이 통합 탭 온톨로지 섹션으로 열리지 않습니다.");
    assertOk(/\.ontology-relationship-graphs\s*\{[\s\S]*grid-template-columns: 1fr;/.test(styles), "관계 그래프가 전폭 1열 구조로 정의되지 않았습니다.");
    assertOk(code.indexOf('targetContext + "|RELATES_TO"') < 0 && modelingGraphHtml.indexOf("접은 표시") < 0, "규칙 구조 그래프가 relation type을 접어서 표시합니다.");
    assertOk(code.indexOf("ontologyEntityDisplayLabel") >= 0 && code.indexOf('"의견 " + displayName') >= 0, "현재 데이터 관계 그래프 노드가 회사명 표시명을 거치지 않습니다.");
    assertOk(code.indexOf("properties.symbol || entity.label") < 0 && code.indexOf('"의견 " + symbol') < 0, "현재 데이터 관계 그래프 노드 라벨에 종목코드 우선 경로가 남아 있습니다.");
    assertOk(code.indexOf("HAS_TEMPORAL_WINDOW") >= 0 && code.indexOf("DERIVES_TREND_EPISODE") >= 0 && code.indexOf("AFFECTS_DECISION_EPISODE") >= 0, "기간/히스토리 온톨로지 관계가 웹 그래프 중요 관계로 등록되지 않았습니다.");
    assertOk(code.indexOf(".node-temporal-window") >= 0 && code.indexOf(".node-trend-episode") >= 0, "기간/히스토리 온톨로지 노드 타입이 그래프 스타일에 없습니다.");
    assertOk(modelingRulesHtml.indexOf("investment-ai-packet-panel") >= 0 && modelingRulesHtml.indexOf("AI 추론 입력 계약") >= 0, "전략 룰 섹션에 AI 추론 입력 계약이 없습니다.");
    assertOk(modelingRulesHtml.indexOf("strategy-rules-overview-panel") >= 0 && modelingRulesHtml.indexOf('data-work-detail="strategy-rule-editor"') < 0, "전략 룰 섹션에 레거시 관계 규칙 상세 진입점이 남아 있습니다.");
    assertOk(code.indexOf("renderOntologyRuleEditorPanel") < 0 && code.indexOf('data-model-setting="ontologyRelationRules"') < 0, "웹 편집 UI에 레거시 관계 규칙 편집기가 남아 있습니다.");
    assertOk(modelingRulesHtml.indexOf('data-work-detail="strategy-rulebox-editor"') >= 0 && modelingRulesHtml.indexOf("TypeDB RuleBox") >= 0, "전략 룰 섹션에 TypeDB RuleBox 상세 진입점이 없습니다.");
    assertOk(code.indexOf("renderTypeDBRuleboxPanel") >= 0 && code.indexOf('data-ontology-rulebox-json') >= 0 && code.indexOf('data-action="run-rulebox"') >= 0, "TypeDB RuleBox 상세 레이어에 JSON 편집기나 실행 버튼이 없습니다.");
    assertOk(code.indexOf("최근 버전") >= 0 && code.indexOf("AI 관계 후보 검토") >= 0, "TypeDB RuleBox 상세 레이어에 버전/후보 검토 섹션이 없습니다.");
    assertOk(code.indexOf('data-ontology-rulebox-change-reason') >= 0 && code.indexOf('data-action="append-rulebox-candidate"') >= 0, "RuleBox 변경 이유 입력이나 후보 추가 버튼이 없습니다.");
    assertOk(code.indexOf('data-action="propose-rulebox-candidates"') >= 0 && code.indexOf("AI 후보 생성") >= 0, "RuleBox AI 후보 생성 버튼이 없습니다.");
    assertOk(code.indexOf('data-action="refresh-ontology-diagnostics"') >= 0 && code.indexOf('data-action="seed-ontology-graph"') >= 0 && code.indexOf("renderTypeDBDiagnosticsPanel") >= 0, "RuleBox 상세 레이어에 TypeDB 진단/시드 운영 액션이 없습니다.");
    assertOk(code.indexOf("loadOntologyStrategyDetail") >= 0 && code.indexOf('detail: "full"') >= 0, "온톨로지 상세 탭이 전체 그래프 데이터를 지연 로드하지 않습니다.");
    assertOk(code.indexOf('data-model-setting="ontologyRuleCandidateAiEnabled"') >= 0 && code.indexOf('data-model-setting="ontologyRuleCandidateAiIntervalMinutes"') >= 0, "RuleBox AI 후보 생성 설정이 없습니다.");
    assertOk(modelingRulesHtml.indexOf('data-work-detail="strategy-prompt-editor"') >= 0 && code.indexOf("renderAiPromptRegistryPanel") >= 0 && code.indexOf("Prompt Registry") >= 0, "전략 룰 섹션에 프롬프트 상세 진입점이 없습니다.");
    assertOk(code.indexOf("prompt-registry-list") >= 0 && code.indexOf("prompt-registry-row") >= 0, "프롬프트 레지스트리 목록에 모바일 전용 행 구조가 없습니다.");
    assertOk(/@media \(max-width: 860px\)[\s\S]*\.prompt-registry-row\s*\{[\s\S]*grid-template-columns: 1fr;/.test(styles), "프롬프트 레지스트리 행이 모바일에서 1열 카드로 전환되지 않습니다.");
    assertOk(/@media \(max-width: 860px\)[\s\S]*\.prompt-registry-panel \.prompt-registry-row\s*\{[\s\S]*grid-template-columns: 1fr;/.test(styles), "프롬프트 레지스트리 모바일 1열 전환 규칙의 우선순위가 충분하지 않습니다.");
    assertOk(modelingRulesHtml.indexOf('data-work-detail="strategy-model-policy-editor"') >= 0 && code.indexOf("admin-modeling-panel") >= 0 && modelingRulesHtml.indexOf("model-version-panel") < 0, "전략 룰 섹션은 보조 모델 정책 상세 진입점만 유지하고 로컬 모델 버전 패널은 제거해야 합니다.");
    assertOk(code.indexOf("notificationScoreFormula") >= 0 && code.indexOf("알림 발송 공식") >= 0, "전략 룰 상세 레이어에 알림 발송 공식 편집기가 없습니다.");
    assertOk(modelingTraceHtml.indexOf("<h2>검증·리뷰</h2>") >= 0 && modelingTraceHtml.indexOf("strategy-trace-overview-panel") >= 0 && modelingTraceHtml.indexOf('data-work-detail="strategy-trace-detail"') >= 0 && modelingTraceHtml.indexOf("규칙 추적") >= 0, "검증·리뷰 섹션이 요약 카드와 상세 추적 레이어 진입점으로 렌더링되지 않았습니다.");
    assertOk(code.indexOf("renderOntologyRelationPanel") >= 0 && code.indexOf("ontology-relation-table") >= 0 && code.indexOf("renderOntologyRulePanel") >= 0 && code.indexOf("ontology-rule-list") >= 0, "검증 추적 상세 레이어에 관계/규칙 보조 표 경로가 없습니다.");
    assertOk(code.indexOf("renderOntologyMacroRelationPanel") >= 0 && code.indexOf("HAS_FX_EXPOSURE") >= 0 && code.indexOf("HAS_RATE_SENSITIVITY") >= 0, "검증 추적 상세 레이어에 환율·금리 관계 행 경로가 없습니다.");
    assertOk(code.indexOf("ontologyEntityDisplayLabel") >= 0 && code.indexOf("회사 표시명") >= 0 && code.indexOf("회사명 -> 종목 데이터") >= 0, "검증 추적 상세가 회사명 표시명 기준으로 설명되지 않습니다.");
    assertOk(code.indexOf("종목코드 -> 종목 데이터") < 0, "검증 추적 저장 구조 설명에 종목코드 기준 문구가 남아 있습니다.");
    assertOk(code.indexOf("reasoningCards") >= 0 && code.indexOf("investmentAnalysis") >= 0 && code.indexOf("aiInferencePacket") >= 0, "프론트가 백엔드 reasoning card와 AI inference packet 계약을 소비하지 않습니다.");
    assertOk(modelingHtml.indexOf("model-timing-panel") < 0 && modelingTraceHtml.indexOf("model-timing-panel") < 0, "Mock 시계열 기반 타이밍 패널이 아직 렌더링됩니다.");
    assertOk(code.indexOf("renderModelPreviewPanel") >= 0 && code.indexOf("model-preview-panel") >= 0, "검증·리뷰 탭에 현재 종목 판단 결과 상세 경로가 없습니다.");
    assertOk(code.indexOf("실제 데이터 예시") >= 0, "검증·리뷰 상세에 실제 데이터 예시 설명이 렌더링되지 않습니다.");
    assertOk(code.indexOf("쉬운 해석") >= 0, "검증·리뷰 상세에 종목별 쉬운 해석이 렌더링되지 않습니다.");
    assertOk(code.indexOf("판단을 움직인 항목") >= 0, "검증·리뷰 상세에 판단 영향 항목 블록이 렌더링되지 않습니다.");
    assertOk(code.indexOf("재계산 확인") >= 0, "검증·리뷰 상세에 재계산 확인 블록이 렌더링되지 않습니다.");
    assertOk(code.indexOf("같은 입력 재현됨") >= 0, "검증·리뷰 상세에 같은 입력 재계산 검증 결과가 렌더링되지 않습니다.");
    assertOk(modelingHtml.indexOf("model-timing-panel") < 0 && modelingTraceHtml.indexOf("model-timing-panel") < 0, "Mock 시계열 기반 타이밍 패널이 아직 렌더링됩니다.");
    assertOk(modelingHtml.indexOf("웹에서 운영하는 매수·매도 타이밍 모델") < 0 && modelingTraceHtml.indexOf("웹에서 운영하는 매수·매도 타이밍 모델") < 0, "타이밍 모델 제목이 아직 렌더링됩니다.");
    assertOk(monitoringHtml.indexOf("managed-page-notifications") >= 0 && monitoringHtml.indexOf("<h1>알림 운영</h1>") >= 0, "기존 모니터링 URL이 알림 운영 탭으로 매핑되지 않습니다.");
    assertOk(monitoringHtml.indexOf("notifications-view") >= 0, "알림 후보 섹션에 통합 화면 클래스가 없습니다.");
    assertOk(code.indexOf('if (requested === "monitoring") return "notifications";') >= 0, "기존 모니터링 URL 호환 매핑이 없습니다.");
    assertOk(code.indexOf('return "candidates";') >= 0, "기존 모니터링 URL이 후보 섹션으로 열리지 않습니다.");
    assertOk(styles.indexOf(".notifications-view .monitoring-instrument-panel") >= 0, "알림 후보 섹션 모니터링 패널 레이아웃 CSS가 없습니다.");
    assertOk(styles.indexOf(".monitoring-view .admin-monitoring-panel {\n  grid-area: status;\n  grid-column: auto;") < 0, "모니터링 상태 패널의 PC grid-area가 1컬럼으로 압축될 수 있습니다.");
    assertOk(styles.indexOf(".monitoring-view .alert-panel {\n  grid-area: alerts;\n  grid-column: auto;") < 0, "모니터링 알림 패널의 PC grid-area가 1컬럼으로 압축될 수 있습니다.");
    assertOk(monitoringHtml.indexOf("monitor-status-board") >= 0 && monitoringHtml.indexOf("monitor-runtime-strip") >= 0, "모니터링 실행 상태 패널이 ledger/strip 구조로 렌더링되지 않습니다.");
    assertOk(monitoringHtml.indexOf("monitor-primary-head") >= 0 && monitoringHtml.indexOf("monitor-primary-copy") >= 0, "모니터링 대표 상태 카드가 칩/본문 구조로 분리되지 않았습니다.");
    assertOk(monitoringHtml.indexOf("monitor-runtime-board") >= 0 && monitoringHtml.indexOf("monitor-runtime-timeline") >= 0, "모니터링 런타임 상태가 타임라인 구조로 분리되지 않았습니다.");
    assertOk(monitoringHtml.indexOf("monitor-board") >= 0 && monitoringHtml.indexOf("monitor-board-section") >= 0, "모니터링 실행 상태 패널이 공통 보드 섹션 구조로 렌더링되지 않습니다.");
    assertOk(monitoringHtml.indexOf("현재 실행 상태") >= 0 && monitoringHtml.indexOf("런타임 신호") >= 0, "모니터링 실행 상태 패널의 섹션 헤더가 없습니다.");
    assertOk(monitoringHtml.indexOf("monitor-alert-summary") >= 0, "최근 모니터링 알림이 별도 요약 영역으로 분리되지 않았습니다.");
    assertOk(styles.indexOf(".monitor-runtime-row") >= 0 && styles.indexOf(".monitor-alert-summary") >= 0, "모니터링 런타임/알림 요약 스타일이 없습니다.");
    assertOk(styles.indexOf(".monitor-board-section") >= 0 && styles.indexOf(".monitor-section-head") >= 0, "모니터링 실행 상태 보드 섹션 스타일이 없습니다.");
    assertOk(styles.indexOf('"status status status status status status alerts alerts alerts alerts alerts alerts"') >= 0, "모니터링 탭 PC 상태/알림 영역이 균형형 6:6 배치가 아닙니다.");
    assertOk(styles.indexOf('"status status status alerts alerts alerts alerts alerts"') < 0, "모니터링 상태 패널이 태블릿 폭에서 3컬럼으로 압축될 수 있습니다.");
    assertOk(styles.indexOf(".monitor-ledger-cell") >= 0, "모니터링 실행 상태 ledger 스타일이 없습니다.");
    assertOk(monitoringHtml.indexOf("npm run python:monitor:once") < 0 && monitoringHtml.indexOf("npm run python:service:status") < 0, "모니터링 실행 상태 패널에 CLI 명령어 목록이 노출됩니다.");
    assertOk(monitoringHtml.indexOf("monitoring-instrument-panel") >= 0, "모니터링 탭에 보유·관심 통합 패널이 렌더링되지 않았습니다.");
    assertOk(monitoringHtml.indexOf("보유·관심 종목 통합") >= 0, "모니터링 탭 통합 패널 제목이 렌더링되지 않았습니다.");
    assertOk(monitoringHtml.indexOf("웹소켓 최근 이벤트") >= 0, "모니터링 탭에 웹소켓 이벤트 상태가 없습니다.");
    assertOk(monitoringHtml.indexOf("최근 모니터링 사이클") >= 0, "모니터링 탭에 웹소켓 모니터링 사이클 상태가 없습니다.");
    assertOk(monitoringHtml.indexOf("알림 큐") >= 0, "모니터링 탭에 알림 큐 상태가 없습니다.");
    assertOk(monitoringHtml.indexOf("삼성전자") >= 0 && monitoringHtml.indexOf("NVIDIA") >= 0, "보유 종목과 관심 종목이 함께 렌더링되지 않았습니다.");
    assertOk(monitoringHtml.indexOf(">보유<") >= 0 && monitoringHtml.indexOf(">관심<") >= 0, "보유/관심 상태 라벨이 함께 렌더링되지 않았습니다.");
    assertOk(monitoringHtml.indexOf("data-monitor-instrument-detail") >= 0, "보유·관심 통합 행에 상세 열기 액션이 없습니다.");
    assertOk(monitoringHtml.indexOf("data-monitor-alert-detail") >= 0, "매수·매도 타이밍 알림 행에 상세 열기 액션이 없습니다.");
    assertOk(code.indexOf("renderMonitoringDetailOverlay") >= 0 && code.indexOf("monitoring-detail-drawer") >= 0, "모니터링 상세 드로어 렌더링 경로가 없습니다.");
    assertOk(code.indexOf("Instrument Detail") >= 0 && code.indexOf("Alert Detail") >= 0, "종목/알림 상세 콘텐츠가 분리되어 있지 않습니다.");
    assertOk(styles.indexOf(".monitoring-detail-backdrop") >= 0 && styles.indexOf(".monitoring-detail-drawer") >= 0, "모니터링 상세 드로어 스타일이 없습니다.");
    assertOk(monitoringHtml.indexOf("노출 계산 기준") >= 0, "계좌 노출 패널에 계산 기준이 표시되지 않습니다.");
    assertOk(monitoringHtml.indexOf("총 평가 산식") >= 0 && monitoringHtml.indexOf("보유 원장 합계") >= 0, "계좌 노출 검산 행이 렌더링되지 않았습니다.");
    assertOk(monitoringHtml.indexOf("상세 산식") >= 0 && monitoringHtml.indexOf("계정·연결의 자산 검증") >= 0, "계좌 노출 상세 검산 행이 기본 후보 화면에서 요약 안내로 접히지 않습니다.");
    assertOk(monitoringHtml.indexOf("watchlist-panel") < 0, "모니터링 탭에 관심 종목 관리 패널이 따로 남아 있습니다.");
    assertOk(settingsHtml.indexOf("settings-overview-panel") >= 0, "설정 탭 요약 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-environment-panel") >= 0, "설정 탭 앱 환경 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-delivery-panel") >= 0, "설정 탭 알림 전달 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-external-data-panel") >= 0, "설정 탭 외부 데이터 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-advanced-disclosure") >= 0 && settingsHtml.indexOf("고급 설정") >= 0 && settingsHtml.indexOf("settings-diagnostics-panel") >= 0, "설정 탭이 기본/고급/진단 흐름으로 분리되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-smart-save") >= 0, "설정 탭 스마트 저장 영역이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-save-panel") < 0, "설정 탭에 하단 저장 패널이 남아 있습니다.");
    assertOk(settingsHtml.indexOf("변경사항 저장됨") >= 0, "설정 탭 스마트 저장 상태 문구가 렌더링되지 않았습니다.");
    assertOk((settingsHtml.match(/data-action="save-settings"/g) || []).length >= 1, "설정 저장 버튼이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf('data-action="settings-back"') >= 0, "설정 탭 뒤로가기 버튼이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("Telegram Bot Token") >= 0, "설정 탭에 알림 전달 설정이 없습니다.");
    assertOk(settingsHtml.indexOf("Alpha Vantage API Key") >= 0, "설정 탭에 외부 데이터 API 설정이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="externalAlphaEnabled"') >= 0, "설정 탭에 Alpha Vantage 사용 옵션이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="externalCoinGeckoEnabled"') >= 0, "설정 탭에 CoinGecko 사용 옵션이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="externalFredEnabled"') >= 0, "설정 탭에 FRED 사용 옵션이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="externalDartEnabled"') >= 0, "설정 탭에 OpenDART 사용 옵션이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="externalSecEnabled"') >= 0, "설정 탭에 SEC EDGAR 사용 옵션이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="externalNewsEnabled"') >= 0, "설정 탭에 뉴스 헤드라인 사용 옵션이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="externalNewsProvider"') >= 0, "설정 탭에 뉴스 공급자 옵션이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="dataFreshnessEnabled"') >= 0, "설정 탭에 알림 데이터 신선도 게이트 옵션이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="dataFreshnessQuoteMaxAgeMinutes"') >= 0, "설정 탭에 시세 알림 신선도 기준이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="externalSignalCacheMaxAgeMinutes"') >= 0, "설정 탭에 외부 신호 캐시 TTL 기준이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="marketDataMaxAgeMinutes"') >= 0, "설정 탭에 추천 시세 신선도 기준이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="dartDisclosureAiAnalysisEnabled"') >= 0, "설정 탭에 공시 AI 해석 옵션이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="dartDisclosureAiTimeoutSeconds"') >= 0, "설정 탭에 공시 AI 타임아웃 옵션이 없습니다.");
    assertOk(code.indexOf('kisAppKey: settingValue("kisAppKey")') >= 0 && code.indexOf('kisAppSecret: settingValue("kisAppSecret")') >= 0, "KIS secret 필드가 설정 저장 payload에 포함되지 않습니다.");
    assertOk(code.indexOf('kisMarketSignalCacheMinutes: settingValue("kisMarketSignalCacheMinutes")') >= 0, "KIS 수급 캐시 설정이 저장 payload에 포함되지 않습니다.");
    assertOk(settingsHtml.indexOf('data-setting="tossClientId"') < 0, "설정 탭에 계정 Client ID 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="tossClientSecret"') < 0, "설정 탭에 계정 Secret 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="tossAccountSeq"') < 0, "설정 탭에 계좌 순번 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="watchlistSymbols"') < 0, "설정 탭에 관심 종목 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf("<span>관심 종목</span>") < 0, "설정 탭 앱 환경에 관심 종목 라벨이 남아 있습니다.");
    assertOk(settingsHtml.indexOf("TSLA,AAPL,NVDA,000660") < 0, "설정 탭에 기본 관심 종목 값이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="modelName"') < 0, "설정 탭에 모델 이름 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="customBuyModelFormula"') < 0, "설정 탭에 모델 공식 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="modelDecisionThresholds"') < 0, "설정 탭에 모델 기준 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf("Toss Client") < 0, "설정 탭에 토스 계정 입력 라벨이 남아 있습니다.");
    assertOk(settingsHtml.indexOf("모델 입력과 공식") < 0, "설정 탭에 모델 설정 섹션이 남아 있습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-universe-panel") >= 0, "전체 종목 카탈로그 패널이 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("전체 종목 카탈로그") >= 0 || symbolUniverseHtml.indexOf("전체 종목 정보") >= 0, "전체 종목 카탈로그 제목이 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("AAPL") >= 0, "종목 유니버스 검색 결과가 렌더링되지 않았습니다.");
    assertOk(staticAccountHtml.indexOf('value="Pages DB 계정"') >= 0, "정적 빌드 DB 계정 표시 이름이 폼에 채워지지 않았습니다.");
    assertOk(staticAccountHtml.indexOf('value="MSFT,035720"') >= 0, "정적 빌드 관심 종목이 폼에 채워지지 않았습니다.");
    assertOk(staticAccountHtml.indexOf('value="true"') < 0, "정적 빌드의 마스킹된 boolean 값이 계정 폼에 그대로 표시됩니다.");
  });
}

function withFakeTossApi(callback) {
  const server = http.createServer(function (req, res) {
    if (req.method === "POST" && req.url === "/oauth2/token") {
      req.resume();
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        token_type: "Bearer",
        access_token: "fake-token",
        expires_in: 3600
      }));
      return;
    }

    if (req.method === "GET" && req.url === "/api/v1/accounts") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        result: [
          {
            accountSeq: "1",
            accountNo: "1234567890",
            accountType: "BROKERAGE"
          }
        ]
      }));
      return;
    }

    if (req.method === "GET" && req.url.indexOf("/api/v1/buying-power") === 0) {
      if (req.headers.authorization !== "Bearer fake-token" || req.headers["x-tossinvest-account"] !== "1") {
        res.writeHead(401, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "unauthorized" }));
        return;
      }
      const currency = new URL("http://127.0.0.1" + req.url).searchParams.get("currency");
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        result: {
          currency: currency,
          cashBuyingPower: currency === "USD" ? "100" : "250000"
        }
      }));
      return;
    }

    if (req.method === "GET" && req.url === "/api/v1/holdings") {
      if (req.headers.authorization !== "Bearer fake-token" || req.headers["x-tossinvest-account"] !== "1") {
        res.writeHead(401, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "unauthorized" }));
        return;
      }

      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        result: {
          totalPurchaseAmount: { krw: "130000", usd: "0" },
          marketValue: { amount: { krw: "144000", usd: "0" } },
          profitLoss: { amount: { krw: "14000", usd: "0" }, rate: "10.77" },
          items: [
            {
              symbol: "005930",
              name: "삼성전자",
              marketCountry: "KR",
              currency: "KRW",
              quantity: "2",
              lastPrice: "72000",
              averagePurchasePrice: "65000",
              marketValue: "144000",
              profitLoss: "14000",
              tradeStrength: "118",
              volume: "3521000",
              volumeRatio: "1.8",
              foreignBuyVolume: "420000",
              foreignSellVolume: "275000",
              institutionBuyVolume: "310000",
              institutionSellVolume: "228000"
            }
          ]
        }
      }));
      return;
    }

    if (req.method === "GET" && req.url.indexOf("/api/v1/candles") === 0) {
      if (req.headers.authorization !== "Bearer fake-token") {
        res.writeHead(401, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "unauthorized" }));
        return;
      }
      const candles = [];
      for (let index = 199; index >= 0; index--) {
        const date = new Date(Date.UTC(2026, 0, 1 + index));
        const close = 52000 + index * 100;
        candles.push({
          timestamp: date.toISOString().replace("Z", "+09:00"),
          openPrice: String(close - 100),
          highPrice: String(close + 200),
          lowPrice: String(close - 200),
          closePrice: String(close),
          volume: String(1000000 + index * 1000),
          currency: "KRW"
        });
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ result: { candles: candles, nextBefore: null } }));
      return;
    }

    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "not_found" }));
  });

  return new Promise(function (resolve, reject) {
    server.on("error", reject);
    server.listen(0, "127.0.0.1", async function () {
      const port = server.address().port;
      try {
        await callback("http://127.0.0.1:" + port);
        server.close(function () {
          resolve();
        });
      } catch (error) {
        server.close(function () {
          reject(error);
        });
      }
    });
  });
}

async function withServer(extraEnv, callback) {
  const runId = process.pid + "-" + Date.now() + "-" + Math.random();
  const settingsPath = path.join(os.tmpdir(), "digital-twin-smoke-settings-" + runId + ".json");
  const dataDir = path.join(os.tmpdir(), "digital-twin-smoke-data-" + runId);
  const mysqlDatabase = "orbit_alpha_smoke_" + runId.replace(/[^a-zA-Z0-9_]/g, "_");
  const serverProcess = childProcess.spawn(process.env.PYTHON_BIN || "python3", ["python_service/service.py", "web"], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"],
    env: Object.assign({}, process.env, {
      HOST: "127.0.0.1",
      PORT: String(randomPort()),
      LOCAL_CODEX_ENABLED: "0",
      WATCHLIST_SYMBOLS: "TSLA,AAPL,NVDA,000660",
      MYSQL_HOST: process.env.MYSQL_HOST || "127.0.0.1",
      MYSQL_PORT: process.env.MYSQL_PORT || "3306",
      MYSQL_DATABASE: process.env.MYSQL_SMOKE_DATABASE || mysqlDatabase,
      MYSQL_USER: process.env.MYSQL_USER || "root",
      MYSQL_PASSWORD: process.env.MYSQL_PASSWORD || "",
      MYSQL_UNIX_SOCKET: process.env.MYSQL_UNIX_SOCKET || "",
      KIS_MARKET_SIGNALS_ENABLED: "0",
      EXTERNAL_ALPHA_ENABLED: "0",
      EXTERNAL_COINGECKO_ENABLED: "0",
      EXTERNAL_FRED_ENABLED: "0",
      EXTERNAL_DART_ENABLED: "0",
      EXTERNAL_SEC_ENABLED: "0",
      EXTERNAL_NEWS_ENABLED: "0",
      EXTERNAL_YFINANCE_ENABLED: "0",
      EXTERNAL_CRYPTO_IDS: "",
      SETTINGS_PATH: settingsPath,
      DIGITAL_TWIN_DATA_DIR: dataDir
    }, extraEnv || {})
  });

  try {
    const port = await waitForServer(serverProcess);
    await callback(port, {
      dataDir: dataDir,
      settingsPath: settingsPath
    });
  } finally {
    serverProcess.kill("SIGTERM");
  }
}

async function checkNormalMode(port, context) {
  const home = await request(port, "/");
  assertOk(home.statusCode === 200, "홈 화면 응답 코드가 200이 아닙니다: " + home.statusCode);
  assertOk(home.body.indexOf('id="app"') >= 0, "홈 화면에 앱 루트가 없습니다.");

  const bootstrap = await request(port, "/api/bootstrap");
  assertOk(bootstrap.statusCode === 200, "부트스트랩 API 응답 코드가 200이 아닙니다: " + bootstrap.statusCode);
  const payload = JSON.parse(bootstrap.body);
  assertOk(payload.profile && payload.profile.assistantName, "부트스트랩 API에 프로필 정보가 없습니다.");
  assertOk(Array.isArray(payload.items), "부트스트랩 API items가 배열이 아닙니다.");
  assertOk(Array.isArray(payload.messages), "부트스트랩 API messages가 배열이 아닙니다.");

  const ontologyAudit = await request(port, "/api/ontology/audit?limit=5");
  assertOk(ontologyAudit.statusCode === 200, "온톨로지 감사 API 응답 코드가 200이 아닙니다: " + ontologyAudit.statusCode);
  const ontologyAuditPayload = JSON.parse(ontologyAudit.body);
  assertOk(ontologyAuditPayload.summary && ontologyAuditPayload.sections, "온톨로지 감사 API 응답 형식이 맞지 않습니다.");
  ["tbox", "abox", "rulebox", "inferencebox", "evidence", "sync"].forEach(function (section) {
    assertOk(ontologyAuditPayload.sections[section], "온톨로지 감사 API에 " + section + " 섹션이 없습니다.");
    assertOk(Array.isArray(ontologyAuditPayload.sections[section].rows), "온톨로지 감사 " + section + " rows가 배열이 아닙니다.");
  });

  const settings = await request(port, "/api/settings");
  assertOk(settings.statusCode === 200, "설정 API 응답 코드가 200이 아닙니다: " + settings.statusCode);
  const settingsPayload = JSON.parse(settings.body);
  assertOk(settingsPayload.settings && settingsPayload.configured, "설정 API 응답 형식이 맞지 않습니다.");
  assertOk(settingsPayload.settings.tossClientSecret === "", "설정 API가 secret 원문을 내려주고 있습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "alertRules"), "설정 API에 알림 규칙 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "modelDecisionThresholds"), "설정 API에 모델 판단 기준 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "ontologyRelationRules"), "설정 API에 관계 규칙 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "temporalWindowPeriods"), "설정 API에 기간 판단 구간 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "temporalWindowHistoryLimit"), "설정 API에 기간 히스토리 제한 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "aiPromptTemplates"), "설정 API에 AI 프롬프트 템플릿 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "aiPromptPolicy"), "설정 API에 AI 프롬프트 정책 필드가 없습니다.");
  assertOk(settingsPayload.settings.ontologyRelationRules.indexOf("holding.loss_guard.breakdown.v1") >= 0, "설정 API의 기본 관계 규칙이 비어 있습니다.");
  assertOk(String(settingsPayload.settings.temporalWindowPeriods || "").indexOf("1D=1:2") >= 0, "설정 API의 기간 판단 구간 기본값이 비어 있습니다.");
  assertOk(String(settingsPayload.settings.temporalWindowHistoryLimit || "") === "96", "설정 API의 기간 히스토리 제한 기본값이 맞지 않습니다.");
  assertOk(settingsPayload.settings.modelDecisionThresholds.indexOf("graphSignalAlertScore=78") >= 0, "설정 API의 그래프 신호 기본 판단 기준이 비어 있습니다.");
  assertOk(settingsPayload.settings.alertThresholds.indexOf("graphSignalMinScore=55") >= 0, "설정 API의 그래프 신호 최소 기준이 비어 있습니다.");
  assertOk(settingsPayload.settings.alertThresholds.indexOf("graphSignalConfidenceMin=50") >= 0, "설정 API의 그래프 신호 신뢰도 기준이 비어 있습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "appTheme"), "설정 API에 화면 테마 필드가 없습니다.");
  assertOk(settingsPayload.settings.dartDisclosureAiAnalysisEnabled === "1", "설정 API의 공시 AI 해석 기본값이 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "externalAlphaEnabled"), "설정 API에 Alpha Vantage 사용 옵션이 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "externalCoinGeckoEnabled"), "설정 API에 CoinGecko 사용 옵션이 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "externalFredEnabled"), "설정 API에 FRED 사용 옵션이 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "externalDartEnabled"), "설정 API에 OpenDART 사용 옵션이 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "externalSecEnabled"), "설정 API에 SEC EDGAR 사용 옵션이 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "externalNewsEnabled"), "설정 API에 뉴스 헤드라인 사용 옵션이 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "externalNewsProvider"), "설정 API에 뉴스 공급자 옵션이 없습니다.");
  assertOk(settingsPayload.settings.dataFreshnessEnabled === "1", "설정 API의 데이터 신선도 게이트 기본값이 없습니다.");
  assertOk(settingsPayload.settings.dataFreshnessQuoteMaxAgeMinutes === "10", "설정 API의 시세 신선도 기본값이 없습니다.");
  assertOk(settingsPayload.settings.externalSignalCacheMaxAgeMinutes === "10", "설정 API의 외부 신호 캐시 TTL 기본값이 없습니다.");
  assertOk(settingsPayload.settings.marketDataMaxAgeMinutes === "240", "설정 API의 추천 시세 신선도 기본값이 없습니다.");
  assertOk(settingsPayload.settings.dartDisclosureAiTimeoutSeconds === "90", "설정 API의 공시 AI 타임아웃 기본값이 없습니다.");
  assertOk(settingsPayload.settings.watchlistSymbols.indexOf("TSLA") >= 0, "기본 관심 종목에 TSLA가 없습니다.");
  assertOk(settingsPayload.settings.watchlistSymbols.indexOf("AAPL") >= 0, "기본 관심 종목에 AAPL이 없습니다.");

  const websocketResponse = await websocketHandshake(port);
  assertOk(websocketResponse.indexOf("101 Switching Protocols") >= 0, "웹소켓 업그레이드 응답이 101이 아닙니다.");
  assertOk(websocketResponse.toLowerCase().indexOf("sec-websocket-accept") >= 0, "웹소켓 accept 헤더가 없습니다.");

  const realtimeStatus = await request(port, "/api/realtime/status");
  assertOk(realtimeStatus.statusCode === 200, "실시간 상태 API 응답 코드가 200이 아닙니다: " + realtimeStatus.statusCode);
  const realtimeStatusPayload = JSON.parse(realtimeStatus.body);
  assertOk(Object.prototype.hasOwnProperty.call(realtimeStatusPayload, "connectedClients"), "실시간 상태 API에 연결 수가 없습니다.");
  assertOk(Array.isArray(realtimeStatusPayload.latestEvents), "실시간 상태 API에 최근 이벤트 배열이 없습니다.");
  assertOk(realtimeStatusPayload.monitoring && typeof realtimeStatusPayload.monitoring === "object", "실시간 상태 API에 모니터링 요약이 없습니다.");
  assertOk(realtimeStatusPayload.notificationJobs && typeof realtimeStatusPayload.notificationJobs === "object", "실시간 상태 API에 알림 큐 요약이 없습니다.");

  const universe = await request(port, "/api/symbol-universe?query=AAPL");
  assertOk(universe.statusCode === 200, "종목 유니버스 API 응답 코드가 200이 아닙니다: " + universe.statusCode);
  const universePayload = JSON.parse(universe.body);
  assertOk(Array.isArray(universePayload.items), "종목 유니버스 items가 배열이 아닙니다.");
  assertOk(universePayload.items.some(function (item) { return item.symbol === "AAPL"; }), "종목 유니버스에 AAPL seed가 없습니다.");
  assertOk(universePayload.summary && Array.isArray(universePayload.summary.markets), "종목 유니버스 시장별 신선도 요약이 없습니다.");
  const universeSuggest = await request(port, "/api/symbol-universe/suggest?query=" + encodeURIComponent("팔란티어"));
  assertOk(universeSuggest.statusCode === 200, "종목 자동완성 API 응답 코드가 200이 아닙니다: " + universeSuggest.statusCode);
  const universeSuggestPayload = JSON.parse(universeSuggest.body);
  assertOk(Array.isArray(universeSuggestPayload.items), "종목 자동완성 API items가 배열이 아닙니다.");
  assertOk(universeSuggestPayload.items[0] && universeSuggestPayload.items[0].symbol === "PLTR", "팔란티어 자동완성이 PLTR을 첫 후보로 반환하지 않습니다.");
  assertOk(!Object.prototype.hasOwnProperty.call(universeSuggestPayload, "summary"), "종목 자동완성 API가 무거운 summary payload를 포함합니다.");

  const savedSettings = await request(port, "/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      settings: {
        watchlistSymbols: "TSLA,AAPL,NVDA",
        tossApiBaseUrl: "http://127.0.0.1:1",
        kisBaseUrl: "http://127.0.0.1:2",
        kisMarketSignalsEnabled: "0",
        kisAppKey: "fake-kis-key",
        kisAppSecret: "fake-kis-secret",
        kisMarketSignalCacheMinutes: "7",
        tossClientId: "fake-client",
        tossClientSecret: "fake-secret",
        appTheme: "dark",
        notifyProvider: "telegram",
        telegramBotToken: "fake-telegram-token",
        telegramChatId: "1234",
        dartDisclosureAiAnalysisEnabled: "1",
        dartDisclosureAiUseCodex: "0",
        dartDisclosureAiTimeoutSeconds: "45",
        externalAlphaEnabled: "0",
        externalCoinGeckoEnabled: "0",
        externalFredEnabled: "0",
        externalDartEnabled: "0",
        externalSecEnabled: "0",
        externalNewsEnabled: "0",
        externalNewsProvider: "gdelt",
        dataFreshnessEnabled: "1",
        dataFreshnessQuoteMaxAgeMinutes: "12",
        externalSignalCacheMaxAgeMinutes: "9",
        marketDataMaxAgeMinutes: "180",
        alertRules: "investmentInsight=1\nwatchlistOntologySignal=1",
        modelDecisionThresholds: "graphSignalMinScore=60\ngraphSignalAlertScore=82\ngraphSignalConfidenceMin=55"
      }
    })
  });
  assertOk(savedSettings.statusCode === 200, "설정 저장 API 응답 코드가 200이 아닙니다: " + savedSettings.statusCode);
  const savedSettingsPayload = JSON.parse(savedSettings.body);
  assertOk(savedSettingsPayload.configured.tossClientSecret === true, "저장된 토스 secret 설정 상태가 true가 아닙니다.");
  assertOk(savedSettingsPayload.configured.kisAppKey === true, "저장된 KIS app key 설정 상태가 true가 아닙니다.");
  assertOk(savedSettingsPayload.configured.kisAppSecret === true, "저장된 KIS app secret 설정 상태가 true가 아닙니다.");
  assertOk(savedSettingsPayload.settings.tossClientSecret === "", "저장 응답이 토스 secret을 내려주고 있습니다.");
  assertOk(savedSettingsPayload.settings.kisAppKey === "" && savedSettingsPayload.settings.kisAppSecret === "", "저장 응답이 KIS secret 원문을 내려주고 있습니다.");
  assertOk(savedSettingsPayload.settings.kisBaseUrl === "http://127.0.0.1:2", "저장된 KIS Base URL 값이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.kisMarketSignalCacheMinutes === "7", "저장된 KIS 수급 캐시 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.watchlistSymbols === "TSLA,AAPL,NVDA", "저장된 관심 종목 값이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.alertRules.indexOf("investmentInsight=1") >= 0, "저장된 알림 규칙이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.modelDecisionThresholds.indexOf("graphSignalAlertScore=82") >= 0, "저장된 그래프 신호 기준값이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.alertThresholds.indexOf("graphSignalMinScore=60") >= 0, "그래프 신호 최소 기준이 알림 기준으로 동기화되지 않았습니다.");
  assertOk(savedSettingsPayload.settings.alertThresholds.indexOf("graphSignalAlertScore=82") >= 0, "그래프 신호 알림 기준이 알림 기준으로 동기화되지 않았습니다.");
  assertOk(savedSettingsPayload.settings.alertThresholds.indexOf("graphSignalConfidenceMin=55") >= 0, "그래프 신호 신뢰도 기준이 알림 기준으로 동기화되지 않았습니다.");
  assertOk(savedSettingsPayload.settings.appTheme === "dark", "저장된 화면 테마 값이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.dartDisclosureAiUseCodex === "0", "저장된 공시 AI 엔진 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.dartDisclosureAiTimeoutSeconds === "45", "저장된 공시 AI 타임아웃 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.externalAlphaEnabled === "0", "저장된 Alpha Vantage 사용 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.externalCoinGeckoEnabled === "0", "저장된 CoinGecko 사용 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.externalFredEnabled === "0", "저장된 FRED 사용 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.externalDartEnabled === "0", "저장된 OpenDART 사용 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.externalSecEnabled === "0", "저장된 SEC EDGAR 사용 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.externalNewsEnabled === "0", "저장된 뉴스 헤드라인 사용 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.externalNewsProvider === "gdelt", "저장된 뉴스 공급자 설정이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.dataFreshnessQuoteMaxAgeMinutes === "12", "저장된 시세 신선도 기준이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.externalSignalCacheMaxAgeMinutes === "9", "저장된 외부 신호 캐시 TTL이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.marketDataMaxAgeMinutes === "180", "저장된 추천 시세 신선도 기준이 응답에 없습니다.");
  const eventStatusAfterSettings = JSON.parse((await request(port, "/api/realtime/status")).body);
  assertOk(eventStatusAfterSettings.events["settings.updated"] >= 1, "설정 저장 이벤트가 이벤트 로그에 없습니다.");
  assertOk(eventStatusAfterSettings.latestEvents.some(function (event) { return event.name === "settings.updated"; }), "최근 이벤트에 설정 저장 이벤트가 없습니다.");

  const templates = await request(port, "/api/notification-templates");
  assertOk(templates.statusCode === 200, "알림 템플릿 API 응답 코드가 200이 아닙니다: " + templates.statusCode);
  const templatesPayload = JSON.parse(templates.body);
  assertOk(Array.isArray(templatesPayload.templates), "알림 템플릿 API templates가 배열이 아닙니다.");
  assertOk(templatesPayload.templates.some(function (item) { return item.messageType === "investmentInsight"; }), "투자 인사이트 템플릿이 없습니다.");
  assertOk(templatesPayload.templates.some(function (item) { return item.messageType === "ontologyInferenceMissing"; }), "온톨로지 추론 상태 템플릿이 없습니다.");
  assertOk(templatesPayload.templates.some(function (item) { return item.messageType === "externalDataConnection"; }), "외부 데이터 연결 템플릿이 없습니다.");
  assertOk(!templatesPayload.templates.some(function (item) { return item.messageType === "monitorHeartbeat"; }), "내부 상태 확인 템플릿이 기본 목록에 노출됩니다.");
  assertOk(!templatesPayload.templates.some(function (item) { return item.messageType === "watchlistQuote"; }), "근거 시세 템플릿이 기본 목록에 노출됩니다.");
  assertOk(Array.isArray(templatesPayload.variables) && templatesPayload.variables.indexOf("body") >= 0, "알림 템플릿 변수 목록이 없습니다.");

  const savedTemplate = await request(port, "/api/notification-templates", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messageType: "investmentInsight",
      template: "[{messageType}] {title}\n{rawLines}",
      description: "투자 인사이트 템플릿"
    })
  });
  assertOk(savedTemplate.statusCode === 200, "알림 템플릿 저장 API 응답 코드가 200이 아닙니다: " + savedTemplate.statusCode);
  const savedTemplatePayload = JSON.parse(savedTemplate.body);
  assertOk(savedTemplatePayload.template.template.indexOf("{rawLines}") >= 0, "저장된 알림 템플릿 응답이 맞지 않습니다.");
  const eventStatusAfterTemplate = JSON.parse((await request(port, "/api/realtime/status")).body);
  assertOk(eventStatusAfterTemplate.events["notification_template.updated"] >= 1, "알림 템플릿 저장 이벤트가 이벤트 로그에 없습니다.");
  assertOk(eventStatusAfterTemplate.latestEvents.some(function (event) { return event.name === "notification_template.updated"; }), "최근 이벤트에 알림 템플릿 저장 이벤트가 없습니다.");

  const rules = await request(port, "/api/notification-rules");
  assertOk(rules.statusCode === 200, "알림 룰 API 응답 코드가 200이 아닙니다: " + rules.statusCode);
  const rulesPayload = JSON.parse(rules.body);
  assertOk(Array.isArray(rulesPayload.rules), "알림 룰 API rules가 배열이 아닙니다.");
  assertOk(rulesPayload.rules.some(function (item) { return item.messageType === "investmentInsight"; }), "투자 인사이트 발송 우선도 룰이 없습니다.");
  assertOk(!rulesPayload.rules.some(function (item) { return item.messageType === "externalCryptoMove"; }), "근거 신호 룰이 기본 목록에 노출됩니다.");
  assertOk(Array.isArray(rulesPayload.conditionTypes) && rulesPayload.conditionTypes.length, "알림 룰 조건 타입 목록이 없습니다.");
  assertOk(Array.isArray(rulesPayload.marketHoursSessions) && rulesPayload.marketHoursSessions.length >= 2, "장 시간 세션 목록이 없습니다.");

  const savedRule = await request(port, "/api/notification-rules", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messageType: "investmentInsight",
      enabled: true,
      threshold: 40,
      baseScore: 20,
      lowScoreAction: "suppress",
      similarityEnabled: true,
      similarityWindowMinutes: 90,
      similarityPenalty: -35,
      similarityBypassScoreDelta: 12,
      similarityFields: ["messageType", "accountId", "symbol", "title"],
      marketHoursEnabled: true,
      marketHoursMarkets: ["KR"],
      conditions: [
        { id: "severity_watch", label: "관찰 등급", type: "context_equals", field: "severity", value: "WATCH", terms: [], score: 12, enabled: true }
      ]
    })
  });
  assertOk(savedRule.statusCode === 200, "알림 룰 저장 API 응답 코드가 200이 아닙니다: " + savedRule.statusCode);
  const savedRulePayload = JSON.parse(savedRule.body);
  assertOk(savedRulePayload.rule.threshold === 40, "저장된 알림 룰 기준점이 응답에 없습니다.");
  assertOk(savedRulePayload.rule.conditions[0].score === 12, "저장된 알림 룰 조건 점수가 응답에 없습니다.");
  assertOk(savedRulePayload.rule.similarityWindowMinutes === 90, "저장된 유사 메시지 억제 시간이 응답에 없습니다.");
  assertOk(savedRulePayload.rule.similarityPenalty === -35, "저장된 유사 메시지 반복 감점이 응답에 없습니다.");
  assertOk(savedRulePayload.rule.similarityFields.indexOf("symbol") >= 0, "저장된 fingerprint 필드가 응답에 없습니다.");
  assertOk(savedRulePayload.rule.marketHoursEnabled === true, "저장된 장 시간 필터 토글이 응답에 없습니다.");
  assertOk(savedRulePayload.rule.marketHoursMarkets.indexOf("KR") >= 0, "저장된 장 시간 시장 설정이 응답에 없습니다.");
  const resetRule = await request(port, "/api/notification-rules/investmentInsight", { method: "DELETE" });
  assertOk(resetRule.statusCode === 200, "알림 룰 초기화 API 응답 코드가 200이 아닙니다: " + resetRule.statusCode);
  const eventStatusAfterRule = JSON.parse((await request(port, "/api/realtime/status")).body);
  assertOk(eventStatusAfterRule.events["notification_rule.updated"] >= 2, "알림 룰 저장 이벤트가 이벤트 로그에 없습니다.");

  const notificationJobs = await request(port, "/api/notification-jobs?limit=10");
  assertOk(notificationJobs.statusCode === 200, "최근 알림 판단 API 응답 코드가 200이 아닙니다: " + notificationJobs.statusCode);
  const notificationJobsPayload = JSON.parse(notificationJobs.body);
  assertOk(Array.isArray(notificationJobsPayload.jobs), "최근 알림 판단 API jobs가 배열이 아닙니다.");
  assertOk(notificationJobsPayload.summary && typeof notificationJobsPayload.summary === "object", "최근 알림 판단 API summary가 없습니다.");
  assertOk(notificationJobsPayload.limit === 10, "최근 알림 판단 API limit이 반영되지 않았습니다.");

  const emptyAccounts = await request(port, "/api/service-accounts");
  assertOk(emptyAccounts.statusCode === 200, "계정 DB API 응답 코드가 200이 아닙니다: " + emptyAccounts.statusCode);
  const emptyAccountsPayload = JSON.parse(emptyAccounts.body);
  assertOk(Array.isArray(emptyAccountsPayload.accounts), "계정 DB API accounts가 배열이 아닙니다.");
  assertOk(emptyAccountsPayload.accounts[0].clientSecret !== "fake-secret", "계정 DB API가 secret 원문을 내려주고 있습니다.");

  const savedAccount = await request(port, "/api/service-accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      account: {
        id: "db-test",
        label: "DB 테스트",
        provider: "toss",
        baseUrl: "http://127.0.0.1:1",
        clientId: "db-client",
        clientSecret: "db-secret",
        accountSeq: "7",
        watchlistSymbols: "TSLA,AAPL,NVDA",
        notifyProvider: "telegram",
        telegramBotToken: "telegram-secret",
        telegramChatId: "9876",
        notifyLinkUrl: "http://127.0.0.1:3000"
      }
    })
  });
  assertOk(savedAccount.statusCode === 200, "계정 DB 저장 API 응답 코드가 200이 아닙니다: " + savedAccount.statusCode);
  const savedAccountPayload = JSON.parse(savedAccount.body);
  assertOk(savedAccountPayload.account && savedAccountPayload.account.clientSecret === true, "계정 DB 저장 응답이 토스 secret 설정 상태를 내려주지 않습니다.");
  assertOk(savedAccountPayload.account.telegramBotToken === true, "계정 DB 저장 응답이 텔레그램 토큰 설정 상태를 내려주지 않습니다.");
  const eventStatusAfterAccount = JSON.parse((await request(port, "/api/realtime/status")).body);
  assertOk(eventStatusAfterAccount.events["account.saved"] >= 1, "계정 저장 이벤트가 이벤트 로그에 없습니다.");

  const accountList = await request(port, "/api/service-accounts");
  const accountListPayload = JSON.parse(accountList.body);
  assertOk(accountListPayload.accounts.some(function (account) { return account.id === "db-test"; }), "계정 DB 목록에 저장한 계정이 없습니다.");

  const removedAccount = await request(port, "/api/service-accounts/db-test", { method: "DELETE" });
  assertOk(removedAccount.statusCode === 200, "계정 DB 삭제 API 응답 코드가 200이 아닙니다: " + removedAccount.statusCode);
  assertOk(JSON.parse(removedAccount.body).removed === true, "계정 DB 삭제 응답이 removed=true가 아닙니다.");

  const tossLens = await request(port, "/api/flow-lens?mock=1");
  assertOk(tossLens.statusCode === 200, "토스 판단 API 응답 코드가 200이 아닙니다: " + tossLens.statusCode);
  const tossPayload = JSON.parse(tossLens.body);
  assertOk(tossPayload.toss && Array.isArray(tossPayload.toss.positions), "토스 판단 API에 보유 종목 배열이 없습니다.");
  assertOk(tossPayload.tossDecision && Array.isArray(tossPayload.tossDecision.items), "토스 판단 API에 판단 항목이 없습니다.");
  assertOk(tossPayload.tossDecision.items.some(function (item) { return item.symbol === "AAPL"; }), "토스 판단 항목에 AAPL이 없습니다.");
  assertOk(tossPayload.tossDecision.items.some(function (item) { return item.symbol === "TSLA"; }), "토스 판단 항목에 TSLA 관심 종목이 없습니다.");
  assertOk(tossPayload.tossDecision.investmentAnalysis && tossPayload.tossDecision.investmentAnalysis.contract === "investment-ontology-ai-inference-v1", "토스 판단 API에 투자 분석 AI 추론 계약이 없습니다.");
  assertOk(tossPayload.payloadDetail === "summary", "토스 판단 API 기본 응답이 요약 모드가 아닙니다.");
  assertOk(tossPayload.fullDetailPath === "/api/flow-lens?detail=full", "토스 판단 API가 상세 데이터 경로를 내려주지 않습니다.");
  assertOk(!Array.isArray(tossPayload.tossDecision.investmentAnalysis.reasoningCards), "요약 응답에 reasoning card 배열이 남아 있습니다.");
  assertOk(typeof tossPayload.tossDecision.investmentAnalysis.reasoningCardCount === "number", "요약 응답에 reasoning card 개수 메타가 없습니다.");
  assertOk(tossPayload.tossDecision.ontologyStrategy && tossPayload.tossDecision.ontologyStrategy.detailLevel === "summary", "온톨로지 전략 요약 플래그가 없습니다.");
  assertOk(!Array.isArray(tossPayload.tossDecision.ontologyStrategy.aboxRelations), "요약 응답에 ABox 관계 배열이 남아 있습니다.");
  assertOk(tossPayload.portfolio && Array.isArray(tossPayload.portfolio.markets), "토스 판단 API에 시장별 현금비중 배열이 없습니다.");
  assertOk(tossPayload.portfolio.markets.some(function (market) { return market.key === "KR"; }), "시장별 현금비중에 한국장 항목이 없습니다.");
  assertOk(tossPayload.portfolio.markets.some(function (market) { return market.key === "US"; }), "시장별 현금비중에 미국장 항목이 없습니다.");
  assertOk(tossPayload.portfolio.total > 2700000, "미국장 USD 평가액이 KRW 기준 총 평가액에 환산되지 않았습니다.");
  assertOk(!Array.isArray(tossPayload.news), "토스 전용 판단 API가 뉴스 배열을 내려주고 있습니다.");
  assertOk(!Array.isArray(tossPayload.social), "토스 전용 판단 API가 소셜 배열을 내려주고 있습니다.");

  const tossLensFull = await request(port, "/api/flow-lens?mock=1&detail=full");
  assertOk(tossLensFull.statusCode === 200, "토스 판단 상세 API 응답 코드가 200이 아닙니다: " + tossLensFull.statusCode);
  const tossFullPayload = JSON.parse(tossLensFull.body);
  assertOk(Array.isArray(tossFullPayload.tossDecision.investmentAnalysis.reasoningCards), "토스 판단 상세 API reasoning card 필드가 배열이 아닙니다.");
  assertOk(tossFullPayload.tossDecision.ontologyStrategy && tossFullPayload.tossDecision.ontologyStrategy.aiInferencePacket && tossFullPayload.tossDecision.ontologyStrategy.aiInferencePacket.contract === "investment-ontology-ai-inference-v1", "온톨로지 전략 상세에 AI inference packet이 없습니다.");
  assertOk(tossFullPayload.tossDecision.ontologyStrategy.worldview && tossFullPayload.tossDecision.ontologyStrategy.worldview.runtimeProjectionMode === "abox-facts-only-typedb-native-rules", "토스 판단 상세 API가 TypeDB native rule용 ABox 투영 모드가 아닙니다.");
  assertOk(tossFullPayload.tossDecision.items.some(function (item) { return item.decisionBasis === "ontologyInferenceRequired"; }), "토스 판단 항목이 InferenceBox 없는 판단을 차단하지 않습니다.");

  const scenarios = await request(port, "/api/mock-market/scenarios");
  assertOk(scenarios.statusCode === 200, "mock market 시나리오 API 응답 코드가 200이 아닙니다: " + scenarios.statusCode);
  const scenarioPayload = JSON.parse(scenarios.body);
  assertOk(Array.isArray(scenarioPayload.scenarios), "mock market 시나리오 목록이 배열이 아닙니다.");
  assertOk(scenarioPayload.scenarios.some(function (scenario) { return scenario.id === "semiconductor-boom"; }), "반도체 호황 시나리오가 없습니다.");

  const mockMarket = await request(port, "/api/mock-market/candles?scenario=semiconductor-boom&symbols=NVDA,005930&seed=ci");
  assertOk(mockMarket.statusCode === 200, "mock market candles API 응답 코드가 200이 아닙니다: " + mockMarket.statusCode);
  const mockMarketPayload = JSON.parse(mockMarket.body);
  assertOk(mockMarketPayload.scenario && mockMarketPayload.scenario.id === "semiconductor-boom", "mock market 시나리오 id가 맞지 않습니다.");
  assertOk(mockMarketPayload.series && Array.isArray(mockMarketPayload.series.NVDA.candles), "NVDA mock candle 배열이 없습니다.");
  assertOk(mockMarketPayload.series.NVDA.candles.length >= 200, "NVDA mock candle 수가 부족합니다.");
  assertOk(Array.isArray(mockMarketPayload.signals) && mockMarketPayload.signals.length === 2, "mock market signal 개수가 맞지 않습니다.");

  const staticMockMarket = await request(port, "/mock-data/market/semiconductor-boom.json");
  assertOk(staticMockMarket.statusCode === 200, "정적 mock market JSON 응답 코드가 200이 아닙니다: " + staticMockMarket.statusCode);
  const staticMockMarketPayload = JSON.parse(staticMockMarket.body);
  assertOk(staticMockMarketPayload.request && staticMockMarketPayload.request.staticFile === true, "정적 mock market JSON 표시가 없습니다.");
  assertOk(staticMockMarketPayload.series && Array.isArray(staticMockMarketPayload.series.NVDA.candles), "정적 NVDA mock candle 배열이 없습니다.");

  const adminRedirect = await request(port, "/admin");
  assertOk(adminRedirect.statusCode === 302 && adminRedirect.headers.location === "/admin/", "Python admin preview 디렉터리 리다이렉트가 없습니다.");

  const adminPreview = await request(port, "/admin/");
  assertOk(adminPreview.statusCode === 200, "Python admin preview 응답 코드가 200이 아닙니다: " + adminPreview.statusCode);
  assertOk(adminPreview.body.indexOf("Orbit Alpha Python Admin") >= 0, "Python admin preview 제목이 없습니다.");
  assertOk(adminPreview.body.indexOf("--ds-color-bg: #f3f5f8") >= 0 && adminPreview.body.indexOf("--ds-color-action: #1457a8") >= 0, "Python admin preview에 기관형 금융 팔레트가 적용되지 않았습니다.");

  const adminConfig = await request(port, "/admin/config.json");
  assertOk(adminConfig.statusCode === 200, "Python admin config 응답 코드가 200이 아닙니다: " + adminConfig.statusCode);
  const adminConfigPayload = JSON.parse(adminConfig.body);
  assertOk(adminConfigPayload.mode === "github-pages-readonly-preview", "Python admin config 모드가 정적 미리보기가 아닙니다.");
  assertOk(Array.isArray(adminConfigPayload.pages) && adminConfigPayload.pages.some(function (page) { return page.id === "model-review"; }), "Python admin config에 모델 리뷰 구성이 없습니다.");
  assertOk(adminConfig.body.indexOf("fake-secret") < 0, "Python admin config가 테스트 secret을 포함했습니다.");

  const preflight = await request(port, "/api/data-api/opendart/company", {
    method: "OPTIONS",
    headers: {
      Origin: "https://namsoon00.github.io",
      "Access-Control-Request-Method": "GET",
      "Access-Control-Request-Headers": "accept",
      "Access-Control-Request-Private-Network": "true"
    }
  });
  assertOk(preflight.statusCode === 204, "데이터 API preflight 응답 코드가 204가 아닙니다: " + preflight.statusCode);
  assertOk(preflight.headers["access-control-allow-origin"] === "*", "데이터 API CORS origin 헤더가 없습니다.");
  assertOk(String(preflight.headers["access-control-allow-methods"] || "").indexOf("GET") >= 0, "데이터 API CORS method 헤더에 GET이 없습니다.");
  assertOk(String(preflight.headers["access-control-allow-headers"] || "").toLowerCase().indexOf("accept") >= 0, "데이터 API CORS headers에 Accept가 없습니다.");
  assertOk(preflight.headers["access-control-allow-private-network"] === "true", "데이터 API private network preflight 허용 헤더가 없습니다.");
}

async function checkShareMode(port) {
  const blockedHome = await request(port, "/");
  assertOk(blockedHome.statusCode === 401, "공유 토큰 없는 홈 접근이 차단되지 않았습니다.");

  const blockedApi = await request(port, "/api/bootstrap");
  assertOk(blockedApi.statusCode === 401, "공유 토큰 없는 API 접근이 차단되지 않았습니다.");

  const tokenRedirect = await request(port, "/?share_token=ci-token");
  assertOk(tokenRedirect.statusCode === 302, "공유 토큰 URL이 쿠키 리다이렉트를 만들지 않았습니다.");
  assertOk(String(tokenRedirect.headers["set-cookie"] || "").indexOf("dt_share_token=") >= 0, "공유 토큰 쿠키가 설정되지 않았습니다.");

  const bootstrap = await request(port, "/api/bootstrap", { Cookie: "dt_share_token=ci-token" });
  assertOk(bootstrap.statusCode === 200, "공유 토큰 쿠키로 API 접근이 허용되지 않았습니다.");
}

async function checkLiveTossMode(port) {
  const tossLens = await request(port, "/api/flow-lens");
  assertOk(tossLens.statusCode === 200, "live 토스 판단 API 응답 코드가 200이 아닙니다: " + tossLens.statusCode);
  const payload = JSON.parse(tossLens.body);
  assertOk(payload.toss && payload.toss.mode === "live", "토스 live 모드가 아닙니다.");
  assertOk(Array.isArray(payload.toss.positions), "토스 live 보유 종목 배열이 없습니다.");
  assertOk(payload.toss.positions.length === 1, "토스 live 보유 종목 수가 맞지 않습니다.");
  const position = payload.toss.positions[0];
  assertOk(position.symbol === "005930", "토스 live 보유 종목 코드가 맞지 않습니다.");
  assertOk(position.currentPrice === 72000, "토스 live 현재가 매핑이 맞지 않습니다.");
  assertOk(position.averagePrice === 65000, "토스 live 평균단가 매핑이 맞지 않습니다.");
  assertOk(position.tradeStrength === 118, "토스 live 체결강도 매핑이 맞지 않습니다.");
  assertOk(position.volume === 3521000, "토스 live 거래량 매핑이 맞지 않습니다.");
  assertOk(position.foreignBuyVolume === 420000, "토스 live 외국인 매수량 매핑이 맞지 않습니다.");
  assertOk(position.institutionSellVolume === 228000, "토스 live 기관 매도량 매핑이 맞지 않습니다.");
  assertOk(position.ma20 > 0, "토스 live 캔들 기반 20일 이동평균이 없습니다.");
  assertOk(position.ma60 > 0, "토스 live 캔들 기반 60일 이동평균이 없습니다.");
  assertOk(position.ma20Distance !== 0, "토스 live 이동평균과 현재가 차이가 계산되지 않았습니다.");
  assertOk(payload.toss.account.orderableAmount === 390000, "토스 live 매수 가능 금액이 buying-power API로 계산되지 않았습니다.");
  assertOk(payload.portfolio.cash === 390000, "토스 live 포트폴리오 현금이 buying-power API 값을 반영하지 않았습니다.");
}

async function main() {
  await checkFrontendAdminRender();
  await withServer({}, checkNormalMode);
  await withFakeTossApi(async function (baseUrl) {
    await withServer({
      TOSS_API_BASE_URL: baseUrl,
      TOSS_CLIENT_ID: "fake-client-id",
      TOSS_CLIENT_SECRET: "fake-client-secret"
    }, checkLiveTossMode);
  });
  await withServer({ SHARE_TOKEN: "ci-token" }, checkShareMode);
  console.log("Smoke test passed");
}

main()
  .catch(function (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  });
