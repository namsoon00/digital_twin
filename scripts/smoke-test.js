const childProcess = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const net = require("net");
const os = require("os");
const path = require("path");
const vm = require("vm");

const rootDir = path.resolve(__dirname, "..");

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
    socket.setTimeout(5000);
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

function readSqliteSetting(dbPath, key) {
  const script = [
    "import sqlite3, sys",
    "connection = sqlite3.connect(sys.argv[1])",
    "row = connection.execute('SELECT value FROM runtime_settings WHERE key = ?', (sys.argv[2],)).fetchone()",
    "print('' if row is None else row[0])"
  ].join("\n");
  return childProcess.execFileSync(process.env.PYTHON_BIN || "python3", ["-c", script, dbPath, key], {
    cwd: rootDir,
    encoding: "utf8"
  }).trim();
}

function checkFrontendAdminRender() {
  const code = fs.readFileSync(path.join(rootDir, "public", "app.js"), "utf8");
  const styles = fs.readFileSync(path.join(rootDir, "public", "styles.css"), "utf8");
  const indexHtml = fs.readFileSync(path.join(rootDir, "public", "index.html"), "utf8");
  const designSystemDoc = fs.readFileSync(path.join(rootDir, "docs", "design-system.md"), "utf8");
  assertOk(styles.indexOf("--ds-color-bg") >= 0, "전역 디자인 시스템 색상 토큰이 없습니다.");
  assertOk(styles.indexOf("--ds-color-on-action") >= 0, "주요 액션 텍스트 토큰이 없습니다.");
  assertOk(styles.indexOf("--ds-color-bg: #f3f5f8") >= 0, "기관형 금융 콘솔 배경 토큰이 적용되지 않았습니다.");
  assertOk(styles.indexOf("--ds-color-action: #1457a8") >= 0, "기관형 액션 블루 토큰이 적용되지 않았습니다.");
  assertOk(styles.indexOf("--ds-color-orbit-line: #2f6fbb") >= 0, "기관형 신호 라인 토큰이 적용되지 않았습니다.");
  assertOk(styles.indexOf("--ds-color-orbit-signal: #137a63") >= 0, "기관형 시그널 그린 토큰이 적용되지 않았습니다.");
  assertOk(styles.indexOf("--surface: var(--ds-color-panel-soft)") >= 0, "보조 표면 alias가 없습니다.");
  assertOk(styles.indexOf("--ds-control-height-md") >= 0, "전역 컨트롤 높이 토큰이 없습니다.");
  assertOk(styles.indexOf("--ds-page-gap") >= 0 && styles.indexOf("--ds-row-pad-x") >= 0, "전역 레이아웃 간격 토큰이 없습니다.");
  assertOk(styles.indexOf("--ds-card-bg") >= 0 && styles.indexOf("--ds-card-border") >= 0, "금융 카드 토큰이 없습니다.");
  assertOk(styles.indexOf("Institutional finance card layer") >= 0, "금융 카드 레이어 규칙이 없습니다.");
  assertOk(styles.indexOf("Full financial card replacement system") >= 0, "전면 금융 카드 교체 레이어가 없습니다.");
  assertOk(styles.indexOf("Professional form control replacement system") >= 0, "전문 form control 교체 레이어가 없습니다.");
  assertOk(/select\s*\{[\s\S]*appearance: none;[\s\S]*background-image:[\s\S]*data:image\/svg\+xml/.test(styles), "select 박스가 커스텀 chevron 스타일을 쓰지 않습니다.");
  assertOk(/input:not\(\[type="checkbox"\]\):not\(\[type="radio"\]\):focus,[\s\S]*select:focus,[\s\S]*textarea:focus\s*\{[\s\S]*box-shadow: 0 0 0 3px var\(--blue-soft\)/.test(styles), "입력 요소 focus ring이 금융 콘솔 스타일로 통일되지 않았습니다.");
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
  assertOk(styles.indexOf("--ds-shell-width: 1720px") >= 0 && styles.indexOf("--ds-sidebar-width: 256px") >= 0, "PC 중심 shell/sidebar 레이아웃 토큰이 없습니다.");
  assertOk(styles.indexOf("grid-template-columns: var(--ds-sidebar-width) minmax(0, 1fr)") >= 0, "PC shell이 좌측 네비게이션과 작업 영역으로 분리되지 않습니다.");
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
  assertOk(/\.notifications-view \.monitoring-instrument-panel\s*\{\s*grid-column: span 8;/.test(desktopLayoutAuditLayer), "알림 신호 섹션의 통합 종목 패널이 PC 8컬럼으로 고정되지 않습니다.");
  assertOk(/\.notification-decision-list\s*\{\s*grid-template-columns: minmax\(0, 1fr\);/.test(desktopLayoutAuditLayer), "최근 알림 판단 목록이 PC 전폭 원장 행으로 유지되지 않습니다.");
  assertOk(/\.notification-decision-row\s*\{\s*grid-template-columns: minmax\(240px, 0\.34fr\) minmax\(0, 1fr\);/.test(desktopLayoutAuditLayer), "최근 알림 판단 행의 PC 최종 2열 구조가 없습니다.");
  assertOk(/\.strategy-data-grid\s*\{\s*grid-template-columns: repeat\(auto-fit, minmax\(min\(100%, 360px\), 1fr\)\);/.test(desktopLayoutAuditLayer), "전략 데이터 점검 카드가 PC 읽기 폭 제한 그리드로 정리되지 않습니다.");
  assertOk(/:where\([\s\S]*\.symbol-filter-form,[\s\S]*\.settings-body,[\s\S]*\.investment-bridge-flow,[\s\S]*\.formula-ledger,[\s\S]*\.relation-matrix,[\s\S]*\.admin-form-grid[\s\S]*\)\s*\{[\s\S]*gap: var\(--ds-section-gap\);/.test(desktopLayoutAuditLayer), "PC 탭별 내부 작업 영역 gap 감사 규칙이 없습니다.");
  assertOk(/:where\([\s\S]*\.symbol-filter-form,[\s\S]*\.settings-body,[\s\S]*\.investment-bridge-flow,[\s\S]*\.account-credential-grid[\s\S]*\)\s*\{[\s\S]*padding: var\(--ds-space-4\) var\(--ds-panel-pad-x\) var\(--ds-panel-pad-y\);/.test(desktopLayoutAuditLayer), "PC 탭별 내부 작업 영역 padding 감사 규칙이 없습니다.");
  assertOk(/\.notification-section-tabs,[\s\S]*\.ontology-section-tabs[\s\S]*\{[\s\S]*gap: var\(--ds-space-2\);[\s\S]*padding: var\(--ds-space-2\);/.test(desktopLayoutAuditLayer), "PC 섹션 탭 바 간격 감사 규칙이 없습니다.");
  assertOk(/@media \(min-width: 1440px\)[\s\S]*\.notifications-view \.alert-delivery-panel\s*\{[\s\S]*grid-column: span 4;/.test(desktopLayoutAuditLayer), "넓은 PC에서 알림 고급 패널 4:4:4 배치가 없습니다.");
  assertOk(code.indexOf("renderManagedPage") >= 0 && styles.indexOf(".managed-page") >= 0, "전체 탭 공통 관리 페이지 템플릿이 없습니다.");
  assertOk(code.indexOf("renderPageCommandStrip") >= 0 && styles.indexOf(".page-command-strip") >= 0, "페이지 작업 상태 strip 템플릿이 없습니다.");
  assertOk(/@media \(min-width: 861px\)[\s\S]*height: calc\(100dvh - 48px\);/.test(styles), "PC shell이 100dvh 관리 콘솔로 고정되지 않습니다.");
  assertOk(/@media \(min-width: 861px\)[\s\S]*\.workspace-main[\s\S]*overflow: auto;/.test(styles), "PC 본문이 내부 스크롤 작업대로 분리되지 않습니다.");
  assertOk(/@media \(max-width: 1180px\) and \(min-width: 981px\)[\s\S]*grid-template-columns: 224px minmax\(0, 1fr\);/.test(styles), "981px 이상 태블릿이 PC형 좌측 네비게이션으로 유지되지 않습니다.");
  assertOk(/@media \(max-width: 980px\) and \(min-width: 861px\)[\s\S]*\.app-nav[\s\S]*grid-template-columns: auto minmax\(0, 1fr\) auto;/.test(styles), "좁은 태블릿에서 상단 네비게이션으로 전환되지 않습니다.");
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
  assertOk(designSystemDoc.indexOf("12컬럼") >= 0 && designSystemDoc.indexOf("8컬럼") >= 0, "디자인 시스템 문서에 PC/태블릿 컬럼 기준이 없습니다.");
  assertOk(designSystemDoc.indexOf("Button Placement") >= 0, "디자인 시스템 문서에 버튼 위치 정책이 없습니다.");
  assertOk(designSystemDoc.indexOf("aria-current") >= 0, "디자인 시스템 문서에 내비게이션 접근성 기준이 없습니다.");
  assertOk(code.indexOf('appTheme: settingValue("appTheme")') >= 0, "설정 저장 payload에 화면 테마가 포함되지 않았습니다.");
  const payloads = {
    "/api/settings": {
      settings: {
        tossApiBaseUrl: "https://openapi.tossinvest.com",
        notifyProvider: "telegram",
        notifyLinkUrl: "http://127.0.0.1:3000?tab=notifications",
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
      engineVersion: "neo4j-rulebox-graph-reasoner-v1",
      ruleCount: 1,
      conditionCount: 1,
      derivationCount: 1,
      relationTypes: ["HAS_INFERRED_RISK"],
      rules: [
        {
          rule_id: "graph.loss_guard.breakdown.v1",
          label: "손실 방어 추론",
          version: "neo4j-rulebox-graph-reasoner-v1",
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
          messageType: "monitorHeartbeat",
          template: "{readableMessage}",
          description: "상태 확인 템플릿",
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
          messageType: "monitorHeartbeat",
          enabled: true,
          threshold: 45,
          baseScore: 15,
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
    "/api/sqlite/health": {
      generatedAt: "2026-07-01T00:00:00.000Z",
      path: "/tmp/service.db",
      exists: true,
      sizeBytes: 40960,
      walSizeBytes: 0,
      shmSizeBytes: 0,
      recentLockLogCount: 0,
      journalMode: "wal",
      busyTimeoutMs: 30000,
      tables: {
        runtimeSettings: 12,
        domainEvents: 5,
        monitorSnapshots: 1,
        researchEvidence: 4,
        symbolUniverse: 8,
        marketQuoteCache: 3
      },
      outbox: {
        notificationJobs: { pending: 1, done: 2, suppressed: 2 },
        modelReviewJobs: { pending: 1 }
      },
      migrations: [
        { version: "sqlite_operational_schema_20260710", appliedAt: "2026-07-01T00:00:00.000Z" }
      ]
    },
    "/api/notification-schedules": {
      generatedAt: "2026-07-01T00:00:00.000Z",
      schedules: [
        {
          messageType: "monitorHeartbeat",
          label: "실시간 상태",
          enabled: true,
          status: "waiting",
          cadenceMinutes: 10,
          cadenceText: "조건이 다시 충족되면 최소 10분 간격으로 보냅니다.",
          triggerSummary: "실시간 모니터링 워커가 정상 작동 중인지 확인할 때 보냅니다.",
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
      querySelectorAll: function () {
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
        if (key === "/api/economic-feed/rss") {
          return Promise.resolve({
            ok: true,
            json: function () {
              return Promise.resolve({});
            },
            text: function () {
              return Promise.resolve("<rss><channel><item><title>시장 피드 테스트</title><link>https://example.test/feed</link><pubDate>Wed, 08 Jul 2026 00:00:00 GMT</pubDate><description>테스트용 RSS 피드</description></item></channel></rss>");
            }
          });
        }
        if (key === "/api/economic-feed/gdelt") {
          return Promise.resolve({
            ok: true,
            json: function () {
              return Promise.resolve({ articles: [{ title: "글로벌 시장 테스트", url: "https://example.test/gdelt", seendate: "20260708T000000Z", domain: "example.test" }] });
            },
            text: function () {
              return Promise.resolve(JSON.stringify({ articles: [] }));
            }
          });
        }
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
      setTimeout(function () {
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
      }, 80);
    });
  }

  return Promise.all([
    renderForSearch(""),
    renderForSearch("?tab=accounts"),
    renderForSearch("?tab=watchlist"),
    renderForSearch("?tab=symbols"),
    renderForSearch("?tab=notifications"),
    renderForSearch("?tab=notifications&notification=policy"),
    renderForSearch("?tab=notifications&notification=templates"),
    renderForSearch("?tab=notifications&notification=advanced"),
    renderForSearch("?tab=modeling"),
    renderForSearch("?tab=ontology"),
    renderForSearch("?tab=feed"),
    renderForSearch("?tab=modeling&strategy=evidence"),
    renderForSearch("?tab=modeling&strategy=results"),
    renderForSearch("?tab=modeling&strategy=graphs"),
    renderForSearch("?tab=modeling&strategy=registry"),
    renderForSearch("?tab=modeling&strategy=trace"),
    renderForSearch("?tab=ontology&ontology=graphs"),
    renderForSearch("?tab=monitoring"),
    renderForSearch("?tab=system"),
    renderForSearch("?tab=settings"),
    renderForSearch("?tab=accounts", "namsoon00.github.io"),
    renderForSearch("?tab=accounts", null, { captureNewAccountButton: true, clickNewAccount: true })
  ]).then(function (pages) {
    const overviewHtml = pages[0];
    const accountHtml = pages[1];
    const watchlistHtml = pages[2];
    const symbolUniverseHtml = pages[3];
    const notificationHtml = pages[4];
    const notificationPolicyHtml = pages[5];
    const notificationTemplateHtml = pages[6];
    const notificationAdvancedHtml = pages[7];
    const modelingHtml = pages[8];
    const legacyOntologyHtml = pages[9];
    const feedHtml = pages[10];
    const modelingEvidenceHtml = pages[11];
    const modelingResultsHtml = pages[12];
    const modelingGraphHtml = pages[13];
    const modelingRegistryHtml = pages[14];
    const modelingTraceHtml = pages[15];
    const legacyOntologyGraphHtml = pages[16];
    const monitoringHtml = pages[17];
    const systemHtml = pages[18];
    const settingsHtml = pages[19];
    const staticAccountHtml = pages[20];
    const newAccountHtml = pages[21];

    [
      ["overview", overviewHtml],
      ["accounts", accountHtml],
      ["watchlist", watchlistHtml],
      ["symbols", symbolUniverseHtml],
      ["notifications", notificationHtml],
      ["modeling", modelingHtml],
      ["feed", feedHtml],
      ["system", systemHtml],
      ["settings", settingsHtml]
    ].forEach(function (entry) {
      assertOk(entry[1].indexOf("managed-page managed-page-" + entry[0]) >= 0, "탭이 공통 관리 페이지 템플릿을 거치지 않습니다: " + entry[0]);
      assertOk(entry[1].indexOf("page-command-strip") >= 0, "탭에 페이지 작업 상태 strip이 없습니다: " + entry[0]);
    });
    assertOk(legacyOntologyHtml.indexOf("managed-page managed-page-modeling") >= 0, "기존 관계 분석 URL이 투자 분석 탭으로 호환 렌더링되지 않습니다.");
    assertOk(monitoringHtml.indexOf("managed-page managed-page-notifications") >= 0, "기존 모니터링 URL이 알림 공통 페이지로 열리지 않습니다.");

    assertOk(overviewHtml.indexOf("계정·알림·모델 운영 콘솔") < 0, "이전 고정 운영 콘솔 제목이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf("<h1>홈</h1>") >= 0, "홈 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("<h1>계정</h1>") >= 0, "계정 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf('aria-current="page"') >= 0, "활성 탭 접근성 상태가 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("<h1>설정</h1>") >= 0, "설정 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-view") >= 0, "설정 화면이 페이지 구조로 렌더링되지 않았습니다.");
    assertOk(code.indexOf("renderAppNavigation") >= 0 && styles.indexOf(".app-nav") >= 0, "앱 네비게이션 바 구조가 렌더링되지 않습니다.");
    assertOk(overviewHtml.indexOf("app-nav") < overviewHtml.indexOf("topbar"), "앱 네비게이션 바가 topbar 위에 렌더링되지 않습니다.");
    assertOk(overviewHtml.indexOf("top-action-bar") < 0, "기존 상단 버튼 나열 구조가 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf("deskbar deskbar-full") >= 0, "홈 탭은 full deskbar를 사용해야 합니다.");
    assertOk(overviewHtml.indexOf("shell-home") >= 0, "홈 탭 shell이 홈 전용 배치를 사용하지 않습니다.");
    [
      ["계정", accountHtml],
      ["관심종목", watchlistHtml],
      ["전체종목", symbolUniverseHtml],
      ["알림", notificationHtml],
      ["투자 분석", modelingHtml],
      ["피드", feedHtml],
      ["모니터링", monitoringHtml],
      ["시스템", systemHtml],
      ["설정", settingsHtml]
    ].forEach(function (entry) {
      assertOk(entry[1].indexOf("deskbar ") < 0, entry[0] + " 탭에 홈 전용 deskbar가 렌더링됩니다.");
      assertOk(entry[1].indexOf("shell-page") >= 0, entry[0] + " 탭 shell이 본문 우선 배치를 사용하지 않습니다.");
    });
    assertOk(code.indexOf('data-action="open-settings"') < 0, "topbar 설정 버튼이 상단 관리 탭과 중복됩니다.");
    assertOk(code.indexOf("pushState") >= 0 && code.indexOf("popstate") >= 0, "탭 이동이 브라우저 뒤로가기와 동기화되지 않았습니다.");
    assertOk(code.indexOf("restoreTabBarPosition") >= 0 && code.indexOf("tabBarScrollLeft") >= 0, "하단 탭 위치 복원 로직이 없습니다.");
    assertOk(code.indexOf("tabScrollPositions") >= 0 && code.indexOf("restoreRenderedPageScrollPosition") >= 0 && code.indexOf("rememberRenderedPageScrollPosition") >= 0, "탭별 본문 스크롤 복원 로직이 없습니다.");
    assertOk(overviewHtml.indexOf('data-scroll-key="overview"') >= 0, "탭 본문에 스크롤 관리 키가 렌더링되지 않습니다.");
    assertOk(designSystemDoc.indexOf("각 탭은 독립된 스크롤 위치") >= 0, "디자인 시스템 문서에 탭별 스크롤 정책이 없습니다.");
    assertOk(code.indexOf('var bottomTabIds = ["overview", "watchlist", "notifications", "modeling"];') >= 0, "하단 핵심 탭에 알림과 투자 분석이 배치되지 않았습니다.");
    assertOk(code.indexOf('var managementTabIds = ["accounts", "symbols", "feed", "system", "settings"];') >= 0, "상단 운영 메뉴 탭 구성이 역할과 맞지 않습니다.");
    assertOk(styles.indexOf(".app-nav-tab.active") >= 0 && styles.indexOf(".app-nav-menu") >= 0, "앱 네비게이션 활성 탭과 모바일 관리 메뉴 스타일 규칙이 없습니다.");
    assertOk(styles.indexOf("@media (min-width: 861px)") >= 0 && styles.indexOf(".tab-bar {\n    display: none;") >= 0, "데스크톱에서 하단 탭을 숨기는 규칙이 없습니다.");
    assertOk(styles.indexOf("position: sticky") >= 0 && styles.indexOf("bottom: 0;") >= 0 && styles.indexOf("backdrop-filter: blur(18px)") >= 0 && styles.indexOf(".app-nav.is-hidden") >= 0, "모바일 앱바 접힘/하단탭 고정 반응형 규칙이 없습니다.");
    assertOk(code.indexOf("settingsSaving") >= 0 && code.indexOf("로컬 SQLite DB") >= 0, "설정 저장 진행 상태가 렌더링되지 않습니다.");
    assertOk(code.indexOf("new window.WebSocket") >= 0, "프론트가 웹소켓 실시간 연결을 생성하지 않습니다.");
    assertOk(code.indexOf("realtime.status") >= 0, "웹소켓 상태 메시지를 처리하지 않습니다.");
    assertOk(code.indexOf("realtimeEventSnackbar") >= 0, "웹소켓 이벤트를 스낵바로 연결하지 않습니다.");
    assertOk(overviewHtml.indexOf("실시간") >= 0, "홈 요약에 실시간 연결 상태가 렌더링되지 않습니다.");
    ["overview", "accounts", "watchlist", "symbols", "notifications", "modeling", "feed", "system", "settings"].forEach(function (tab) {
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
    assertOk(feedHtml.indexOf("feed-view") >= 0, "피드 탭이 데이터 관리 화면으로 렌더링되지 않습니다.");
    assertOk(feedHtml.indexOf("피드 수집 설정") >= 0 && feedHtml.indexOf("데이터 품질 상태") >= 0, "피드 설정과 데이터 품질 패널이 없습니다.");
    assertOk(feedHtml.indexOf("저장 근거 조회·관리") >= 0 && feedHtml.indexOf('data-research-evidence-form') >= 0, "저장 근거 조회/관리 폼이 없습니다.");
    assertOk(systemHtml.indexOf("<h1>시스템</h1>") >= 0, "시스템 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(systemHtml.indexOf("system-guide-view") >= 0, "시스템 설명 탭이 전용 레이아웃으로 렌더링되지 않습니다.");
    assertOk(systemHtml.indexOf("SYSTEM MANUAL") >= 0 && systemHtml.indexOf("처음 사용하는 순서") >= 0, "시스템 탭에 사용자 매뉴얼이 없습니다.");
    assertOk(systemHtml.indexOf("DATA FLOW") >= 0 && systemHtml.indexOf("system-flow-diagram data-flow") >= 0, "시스템 탭에 데이터 흐름 다이어그램이 없습니다.");
    assertOk(systemHtml.indexOf("EVENT FLOW") >= 0 && systemHtml.indexOf("monitoring.snapshot_collected") >= 0 && systemHtml.indexOf("notification.job_queued") >= 0, "시스템 탭에 이벤트 흐름 설명이 없습니다.");
    assertOk(systemHtml.indexOf("ALERT PIPELINE") >= 0 && systemHtml.indexOf("system-notification-flow") >= 0, "시스템 탭에 알림 생성 흐름 다이어그램이 없습니다.");
    assertOk(systemHtml.indexOf("ONTOLOGY MODEL") >= 0 && systemHtml.indexOf("TBox") >= 0 && systemHtml.indexOf("ABox") >= 0, "시스템 탭에 온톨로지 모델 설명이 없습니다.");
    assertOk(styles.indexOf(".system-guide-view") >= 0 && styles.indexOf(".system-flow-diagram") >= 0 && styles.indexOf(".system-event-track") >= 0, "시스템 설명 탭 스타일이 없습니다.");
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
    assertOk(accountHtml.indexOf("Bot token 설정됨") >= 0, "텔레그램 bot token 설정 상태가 표시되지 않습니다.");
    assertOk(accountHtml.indexOf("Secret 설정됨") >= 0, "토스 secret 설정 상태가 표시되지 않습니다.");
    assertOk(accountHtml.indexOf("저장됨 - 새 값 입력 시 교체") >= 0, "저장된 API 값의 교체 안내가 표시되지 않습니다.");
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
    assertOk(watchlistHtml.indexOf("watch-row-meta") >= 0, "관심종목 알림/시세 상태가 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("시세 알림") >= 0, "관심종목 시세 알림 상태가 표시되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("symbol-result-list") < 0, "관심종목 탭에 전체 종목 결과 리스트가 남아 있습니다.");
    assertOk(watchlistHtml.indexOf("전체 종목 DB") < 0, "관심종목 탭에 전체 종목 DB 안내가 남아 있습니다.");
    assertOk(watchlistHtml.indexOf("NVIDIA") >= 0 && watchlistHtml.indexOf("삼성전자") >= 0, "DB 계정 관심 종목명이 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("NVIDIA · NVDA") < 0 && watchlistHtml.indexOf("삼성전자 · 005930") < 0, "관심 종목 표시 텍스트에 종목코드가 노출됩니다.");
    assertOk(accountHtml.indexOf("관심 NVIDIA, 삼성전자") >= 0, "계정 목록 관심 종목 요약이 표시명만 사용하지 않습니다.");
    assertOk(symbolUniverseHtml.indexOf("<h1>전체종목</h1>") >= 0, "전체종목 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-result-list") >= 0, "전체종목 탭에 종목 결과 리스트가 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-summary-metric") >= 0, "전체종목 탭에 시장 요약 지표가 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-bulk-bar") >= 0 && symbolUniverseHtml.indexOf('data-action="add-visible-symbols"') >= 0, "전체종목 탭에 페이지 일괄 추가 액션이 없습니다.");
    assertOk(symbolUniverseHtml.indexOf("data-symbol-add-account") >= 0, "전체종목 탭에 관심 추가 대상 계정 선택이 없습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-summary-card") < 0 && symbolUniverseHtml.indexOf("symbol-source-card") < 0, "전체종목 탭에 중첩 카드 클래스가 남아 있습니다.");
    assertOk(notificationHtml.indexOf("notification-ops-rail") >= 0, "알림 상태 레일이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-command-panel") < 0, "카드형 알림 관제 패널이 남아 있습니다.");
    assertOk(notificationPolicyHtml.indexOf("notification-ops-rail") < 0, "정책 섹션에 알림 상태 레일이 중복 렌더링됩니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-ops-rail") < 0, "템플릿 섹션에 알림 상태 레일이 중복 렌더링됩니다.");
    assertOk(notificationAdvancedHtml.indexOf("notification-ops-rail") < 0, "고급 섹션에 알림 상태 레일이 중복 렌더링됩니다.");
    assertOk(notificationHtml.indexOf("notification-section-bar") >= 0, "알림 내부 섹션 상단 탭 바가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-section-tabs") >= 0, "알림 내부 섹션 탭이 렌더링되지 않았습니다.");
    assertOk(styles.indexOf(".notification-section-tabs") >= 0 && styles.indexOf("border-bottom: 2px solid transparent") >= 0, "알림 내부 섹션이 탭 스트립 스타일로 정의되지 않았습니다.");
    assertOk(notificationHtml.indexOf('data-notification-section="signals"') >= 0 && notificationHtml.indexOf('data-notification-section="policy"') >= 0 && notificationHtml.indexOf('data-notification-section="templates"') >= 0 && notificationHtml.indexOf('data-notification-section="advanced"') >= 0, "알림 내부 섹션 이동 버튼이 없습니다.");
    assertOk(notificationHtml.indexOf("notification-section-bar") < notificationHtml.indexOf("notification-ops-rail"), "알림 섹션 탭이 상태 레일 위에 배치되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-decision-panel") >= 0, "최근 알림 판단 패널이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-ops-rail") < notificationHtml.indexOf("notification-decision-panel"), "기본 현황에서 상태 레일 다음에 최근 알림 판단이 이어지지 않습니다.");
    assertOk(notificationHtml.indexOf("notification-decision-body") >= 0, "최근 알림 판단 본문 영역이 분리되지 않았습니다.");
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
    assertOk(notificationAdvancedHtml.indexOf("notification-rule-editor") >= 0 && notificationAdvancedHtml.indexOf("최소 발송 우선도") >= 0, "고급 섹션의 전체 룰 상세가 렌더링되지 않았습니다.");
    assertOk(code.indexOf("유사 메시지") >= 0 && code.indexOf("data-notification-rule-similarity-enabled") >= 0, "유사 메시지 억제 설정 경로가 없습니다.");
    assertOk(code.indexOf("data-notification-rule-fields") >= 0, "유사 메시지 fingerprint 필드 입력 경로가 없습니다.");
    assertOk(code.indexOf("장 시간 필터") >= 0 && code.indexOf("data-notification-rule-market-hours-enabled") >= 0, "장 시간 필터 설정 경로가 없습니다.");
    assertOk(code.indexOf("국장") >= 0 && code.indexOf("미장") >= 0, "국장/미장 장 시간 설정 경로가 없습니다.");
    assertOk(code.indexOf("프리마켓") >= 0 && code.indexOf("애프터마켓") >= 0, "프리/애프터마켓 장 시간 설정 경로가 없습니다.");
    assertOk(code.indexOf("data-notification-rule-market-hours-market") >= 0, "장 시간 시장 선택 체크박스 경로가 없습니다.");
    assertOk(code.indexOf("data-notification-rule-condition-value") >= 0, "발송 우선도 조건 값 편집 입력 경로가 없습니다.");
    assertOk(code.indexOf("data-rule-save") >= 0 && code.indexOf("monitorHeartbeat") >= 0, "알림 타입별 룰 저장 경로가 없습니다.");
    assertOk(code.indexOf("externalEquityMove") >= 0 && code.indexOf("externalEquityMove=60") >= 0, "미장 가격/거래량 기본 발송 기준 60점 계약이 없습니다.");
    assertOk(notificationHtml.indexOf("최근 알림 판단") >= 0, "최근 알림 판단 제목이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("발송 우선도 30/45") >= 0, "최근 알림 판단의 발송 우선도가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("360분 내 7회 · 우선도 -55") >= 0, "최근 알림 판단의 유사 메시지 감점이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("미장 닫힘") >= 0, "최근 알림 판단의 장 시간 외 보류 사유가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("messageType=externalcryptomove|symbol=eth") >= 0, "최근 알림 판단 fingerprint가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf('data-action="refresh-notification-jobs"') >= 0, "최근 알림 판단 새로고침 버튼이 없습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-template-manager-panel") >= 0, "템플릿 섹션이 렌더링되지 않았습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-template-workbench") >= 0, "템플릿 섹션이 선택형 워크벤치로 렌더링되지 않았습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-template-select-row") >= 0 && notificationTemplateHtml.indexOf("data-template-select") >= 0, "템플릿 섹션에 선택 목록이 없습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-template-detail") < 0, "템플릿 섹션 목록 화면에 상세 편집 패널이 같이 렌더링됩니다.");
    assertOk(code.indexOf("notificationTemplateEditorOpen") >= 0 && code.indexOf("data-notification-template-editor-close") >= 0, "템플릿 상세 편집 레이어 닫기 경로가 없습니다.");
    assertOk(code.indexOf("notification-template-modal-backdrop") >= 0 && code.indexOf("renderNotificationTemplateRow(selected, { templateDetail: true })") >= 0, "템플릿 상세 편집 레이어 렌더링 경로가 없습니다.");
    assertOk(notificationTemplateHtml.indexOf("notification-rule-editor") < 0, "템플릿 섹션에 룰 편집기가 섞여 있습니다.");
    assertOk(notificationAdvancedHtml.indexOf("settings-api-grid") >= 0, "고급 섹션에 설정 API 상태 요약이 렌더링되지 않았습니다.");
    assertOk(notificationAdvancedHtml.indexOf("Client ID 설정됨") >= 0, "고급 섹션에 토스 Client ID 상태가 표시되지 않습니다.");
    assertOk(notificationAdvancedHtml.indexOf("Bot token 설정됨") >= 0, "고급 섹션에 텔레그램 bot token 상태가 표시되지 않습니다.");
    assertOk(notificationAdvancedHtml.indexOf("notification-threshold-panel") >= 0 && notificationAdvancedHtml.indexOf("alert-threshold-grid") >= 0, "고급 섹션에 알림 임계값 패널이 없습니다.");
    assertOk(code.indexOf("data-template-test-send") >= 0, "실제 데이터 알림 테스트 발송 경로가 없습니다.");
    assertOk(code.indexOf("모니터링 정상 작동") >= 0, "상태 확인 템플릿 미리보기 샘플 경로가 없습니다.");
    assertOk(code.indexOf("매수 점수") >= 0, "타입별 템플릿 미리보기 샘플 경로가 없습니다.");
    assertOk(code.indexOf("data-notification-template") >= 0 && code.indexOf("monitorHeartbeat") >= 0, "상태 확인 템플릿 textarea 경로가 없습니다.");
    assertOk(notificationTemplateHtml.indexOf("{rawLines}") >= 0, "알림 템플릿 변수가 렌더링되지 않았습니다.");
    assertOk(notificationAdvancedHtml.indexOf("tab=notifications") >= 0, "알림 링크 기본값이 새 알림 탭을 가리키지 않습니다.");
    assertOk(modelingHtml.indexOf("strategy-section-bar") >= 0, "투자 분석 내부 섹션 탭 바가 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("strategy-section-tabs") >= 0, "투자 분석 내부 섹션 탭이 렌더링되지 않았습니다.");
    assertOk(styles.indexOf(".strategy-section-tabs") >= 0 && styles.indexOf(".strategy-section-bar") >= 0, "투자 분석 내부 섹션 스타일이 정의되지 않았습니다.");
    assertOk(modelingHtml.indexOf('data-strategy-section="evidence"') >= 0 && modelingHtml.indexOf('data-strategy-section="results"') >= 0 && modelingHtml.indexOf('data-strategy-section="graphs"') >= 0 && modelingHtml.indexOf('data-strategy-section="registry"') >= 0 && modelingHtml.indexOf('data-strategy-section="trace"') >= 0, "투자 분석 내부 섹션 이동 버튼이 없습니다.");
    assertOk(modelingHtml.indexOf('data-strategy-section="data"') < 0 && modelingHtml.indexOf('data-strategy-section="policy"') < 0 && modelingHtml.indexOf('data-strategy-section="ontology"') < 0, "이전 전략/관계 섹션 ID가 통합 탭에 남아 있습니다.");
    assertOk(code.indexOf('if (requested === "ontology" || requested === "relations") return "modeling";') >= 0, "기존 관계 분석 URL을 투자 분석 탭으로 매핑하지 않습니다.");
    assertOk(code.indexOf("writeStrategySectionHistory") >= 0 && code.indexOf("strategySectionUrl") >= 0, "투자 분석 내부 탭 URL 동기화 경로가 없습니다.");
    assertOk(modelingHtml.indexOf("<h1>투자 분석</h1>") >= 0, "투자 분석 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(legacyOntologyHtml.indexOf("<h1>투자 분석</h1>") >= 0 && legacyOntologyHtml.indexOf("investment-bridge-panel") >= 0, "기존 관계 분석 URL이 통합 투자 분석 개요로 열리지 않습니다.");
    assertOk(modelingHtml.indexOf("investment-bridge-panel") >= 0 && modelingHtml.indexOf("전략 데이터와 관계 분석을 잇는 추론 구조") >= 0, "투자 분석 개요에 전략-관계-AI 파이프라인이 없습니다.");
    assertOk(modelingHtml.indexOf("AI 추론 입력") >= 0 && modelingHtml.indexOf("investment-ontology-ai-inference-v1") >= 0, "투자 분석 개요에 AI inference packet 계약이 보이지 않습니다.");
    assertOk(modelingHtml.indexOf("investment-evidence-panel") >= 0 && modelingHtml.indexOf("투자 근거 카드") >= 0, "투자 분석 개요에 reasoning card가 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("HOLDS") >= 0 && modelingHtml.indexOf("WATCHES") >= 0, "보유/관심 관계 구분이 투자 분석 개요에 표시되지 않습니다.");
    assertOk(modelingHtml.indexOf("macro-signal-panel") >= 0 && modelingHtml.indexOf("환율·금리 관계 신호") >= 0, "투자 분석 개요에 환율·금리 온톨로지 신호 패널이 없습니다.");
    assertOk(modelingHtml.indexOf("USD/KRW 환율") >= 0 && modelingHtml.indexOf("미국 10년 국채금리") >= 0 && modelingHtml.indexOf("10Y-2Y 금리 스프레드") >= 0, "환율·금리 신호 값이 투자 분석 개요에 표시되지 않습니다.");
    assertOk(styles.indexOf(".macro-signal-grid") >= 0 && styles.indexOf(".macro-relation-row") >= 0, "환율·금리 온톨로지 신호 UI 스타일이 없습니다.");
    assertOk(modelingHtml.indexOf("strategy-process-panel") >= 0 && modelingHtml.indexOf("Strategy Workflow") >= 0, "투자 분석 개요에 처리 순서 UI가 없습니다.");
    assertOk(modelingHtml.indexOf("model-guide-panel") >= 0 && modelingHtml.indexOf("전략 운영 기준 관리") >= 0, "개요 탭에 모델 운영 가이드가 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("strategy-data-panel") < 0 && modelingHtml.indexOf("admin-modeling-panel") < 0 && modelingHtml.indexOf("model-preview-panel") < 0, "개요 탭에 상세 운영 섹션 패널이 섞여 있습니다.");
    assertOk(modelingEvidenceHtml.indexOf("investment-evidence-panel") >= 0 && modelingEvidenceHtml.indexOf("strategy-data-panel") >= 0, "근거 카드 섹션에 카드와 데이터 점검이 함께 렌더링되지 않았습니다.");
    assertOk(modelingEvidenceHtml.indexOf("전략 근거") >= 0 && modelingEvidenceHtml.indexOf("관계 근거") >= 0, "근거 카드가 전략 근거와 관계 근거를 분리해서 보여주지 않습니다.");
    assertOk(modelingEvidenceHtml.indexOf("삼성전자") >= 0 && modelingEvidenceHtml.indexOf("005930 가격과") < 0, "근거 카드 본문에 종목코드가 회사명으로 치환되지 않았습니다.");
    assertOk(modelingEvidenceHtml.indexOf("체결강도") >= 0 && modelingEvidenceHtml.indexOf("모델-알림 기준") >= 0, "근거 카드 섹션에 전략 데이터 점검 항목이 없습니다.");
    assertOk(modelingResultsHtml.indexOf("investment-evidence-panel") >= 0 && modelingResultsHtml.indexOf("model-preview-panel") >= 0, "종목 판단 섹션이 근거 카드와 모델 판단을 함께 보여주지 않습니다.");
    assertOk(modelingResultsHtml.indexOf("실제 데이터 예시") >= 0 && modelingResultsHtml.indexOf("쉬운 해석") >= 0, "종목 판단 섹션에 기존 모델 해석이 보조 근거로 유지되지 않았습니다.");
    assertOk(modelingGraphHtml.indexOf("TBox·ABox 관계 그래프") >= 0 && modelingGraphHtml.indexOf("규칙 구조 관계 그래프") >= 0 && modelingGraphHtml.indexOf("현재 데이터 관계 그래프") >= 0, "관계 그래프 섹션이 통합 탭 내부에서 렌더링되지 않았습니다.");
    assertOk(modelingGraphHtml.indexOf("ontology-cytoscape") >= 0 && modelingGraphHtml.indexOf("규칙 구조") >= 0 && modelingGraphHtml.indexOf("현재 데이터") >= 0, "관계 그래프 섹션에 Cytoscape 그래프와 보조 패널이 없습니다.");
    assertOk(legacyOntologyGraphHtml.indexOf("managed-page managed-page-modeling") >= 0 && legacyOntologyGraphHtml.indexOf("TBox·ABox 관계 그래프") >= 0, "기존 관계 그래프 URL이 통합 탭 그래프 섹션으로 열리지 않습니다.");
    assertOk(/\.ontology-relationship-graphs\s*\{[\s\S]*grid-template-columns: 1fr;/.test(styles), "관계 그래프가 전폭 1열 구조로 정의되지 않았습니다.");
    assertOk(code.indexOf("ontologyEntityDisplayLabel") >= 0 && code.indexOf('"의견 " + displayName') >= 0, "현재 데이터 관계 그래프 노드가 회사명 표시명을 거치지 않습니다.");
    assertOk(code.indexOf("properties.symbol || entity.label") < 0 && code.indexOf('"의견 " + symbol') < 0, "현재 데이터 관계 그래프 노드 라벨에 종목코드 우선 경로가 남아 있습니다.");
    assertOk(modelingRegistryHtml.indexOf("investment-ai-packet-panel") >= 0 && modelingRegistryHtml.indexOf("AI 추론 입력 계약") >= 0, "규칙·프롬프트 섹션에 AI 추론 입력 계약이 없습니다.");
    assertOk(modelingRegistryHtml.indexOf("ontology-rule-panel") >= 0 && modelingRegistryHtml.indexOf("Relation Rule Registry") >= 0, "규칙·프롬프트 섹션에 관계 규칙 레지스트리가 없습니다.");
    assertOk(modelingRegistryHtml.indexOf('data-model-setting="ontologyRelationRules"') >= 0, "규칙·프롬프트 섹션에 관계 규칙 편집기가 없습니다.");
    assertOk(modelingRegistryHtml.indexOf("neo4j-rulebox-panel") >= 0 && modelingRegistryHtml.indexOf("Neo4j RuleBox") >= 0, "규칙·프롬프트 섹션에 Neo4j RuleBox 관리 콘솔이 없습니다.");
    assertOk(modelingRegistryHtml.indexOf('data-ontology-rulebox-json') >= 0 && modelingRegistryHtml.indexOf('data-action="run-rulebox"') >= 0, "Neo4j RuleBox JSON 편집기나 실행 버튼이 없습니다.");
    assertOk(modelingRegistryHtml.indexOf("prompt-registry-panel") >= 0 && modelingRegistryHtml.indexOf("Prompt Registry") >= 0, "규칙·프롬프트 섹션에 프롬프트 레지스트리가 없습니다.");
    assertOk(modelingRegistryHtml.indexOf("admin-modeling-panel") >= 0 && modelingRegistryHtml.indexOf("model-version-panel") >= 0, "규칙·프롬프트 섹션에 보조 모델 정책과 버전 관리가 유지되지 않았습니다.");
    assertOk(modelingRegistryHtml.indexOf('data-model-setting="notificationScoreFormula"') >= 0, "규칙·프롬프트 섹션에 알림 발송 공식 편집기가 없습니다.");
    assertOk(modelingTraceHtml.indexOf("테이블 저장 구조") >= 0 && modelingTraceHtml.indexOf("규칙 추적") >= 0, "검증 추적 섹션에 관계형 규칙 추적이 없습니다.");
    assertOk(modelingTraceHtml.indexOf("ontology-relation-table") >= 0 && modelingTraceHtml.indexOf("ontology-rule-list") >= 0, "검증 추적 섹션에 관계/규칙 보조 표가 렌더링되지 않았습니다.");
    assertOk(modelingTraceHtml.indexOf("macro-relation-panel") >= 0 && modelingTraceHtml.indexOf("HAS_FX_EXPOSURE") >= 0 && modelingTraceHtml.indexOf("HAS_RATE_SENSITIVITY") >= 0, "검증 추적 섹션에 환율·금리 관계 행이 표시되지 않습니다.");
    assertOk(modelingTraceHtml.indexOf("삼성전자 → 반도체") >= 0 && modelingTraceHtml.indexOf("005930 →") < 0, "검증 추적 샘플 관계가 회사명 대신 종목코드를 노출합니다.");
    assertOk(modelingTraceHtml.indexOf("회사 표시명") >= 0 && modelingTraceHtml.indexOf("종목코드 -> 종목 데이터") < 0, "검증 추적 저장 구조 설명에 종목코드 기준 문구가 남아 있습니다.");
    assertOk(code.indexOf("reasoningCards") >= 0 && code.indexOf("investmentAnalysis") >= 0 && code.indexOf("aiInferencePacket") >= 0, "프론트가 백엔드 reasoning card와 AI inference packet 계약을 소비하지 않습니다.");
    assertOk(modelingHtml.indexOf("model-timing-panel") < 0 && modelingResultsHtml.indexOf("model-timing-panel") < 0, "Mock 시계열 기반 타이밍 패널이 아직 렌더링됩니다.");
    assertOk(modelingResultsHtml.indexOf("model-preview-panel") >= 0, "모델 결과 탭에 현재 종목 판단 결과 패널이 렌더링되지 않았습니다.");
    assertOk(modelingResultsHtml.indexOf("실제 데이터 예시") >= 0, "모델 결과 탭에 실제 데이터 예시 설명이 렌더링되지 않았습니다.");
    assertOk(modelingResultsHtml.indexOf("쉬운 해석") >= 0, "모델 결과 탭에 종목별 쉬운 해석이 렌더링되지 않았습니다.");
    assertOk(modelingResultsHtml.indexOf("판단을 움직인 항목") >= 0, "모델 결과 탭에 판단 영향 항목 블록이 렌더링되지 않았습니다.");
    assertOk(modelingResultsHtml.indexOf("재계산 확인") >= 0, "모델 결과 탭에 재계산 확인 블록이 렌더링되지 않았습니다.");
    assertOk(modelingResultsHtml.indexOf("같은 입력 재현됨") >= 0, "모델 결과 탭에 같은 입력 재계산 검증 결과가 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("model-timing-panel") < 0 && modelingResultsHtml.indexOf("model-timing-panel") < 0, "Mock 시계열 기반 타이밍 패널이 아직 렌더링됩니다.");
    assertOk(modelingHtml.indexOf("웹에서 운영하는 매수·매도 타이밍 모델") < 0 && modelingResultsHtml.indexOf("웹에서 운영하는 매수·매도 타이밍 모델") < 0, "타이밍 모델 제목이 아직 렌더링됩니다.");
    assertOk(monitoringHtml.indexOf("managed-page-notifications") >= 0 && monitoringHtml.indexOf("<h1>알림</h1>") >= 0, "기존 모니터링 URL이 알림 탭으로 매핑되지 않습니다.");
    assertOk(monitoringHtml.indexOf("notifications-view") >= 0, "알림 신호 섹션에 통합 화면 클래스가 없습니다.");
    assertOk(code.indexOf('if (requested === "monitoring") return "notifications";') >= 0, "기존 모니터링 URL 호환 매핑이 없습니다.");
    assertOk(code.indexOf('return "signals";') >= 0, "기존 모니터링 URL이 신호 섹션으로 열리지 않습니다.");
    assertOk(styles.indexOf(".notifications-view .monitoring-instrument-panel") >= 0, "알림 신호 섹션 모니터링 패널 레이아웃 CSS가 없습니다.");
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
    assertOk(monitoringHtml.indexOf("시장 합계 차이") >= 0 && monitoringHtml.indexOf("섹터 합계 차이") >= 0, "계좌 노출 시장/섹터 합계 차이가 표시되지 않습니다.");
    assertOk(monitoringHtml.indexOf("watchlist-panel") < 0, "모니터링 탭에 관심 종목 관리 패널이 따로 남아 있습니다.");
    assertOk(settingsHtml.indexOf("settings-overview-panel") >= 0, "설정 탭 요약 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-environment-panel") >= 0, "설정 탭 앱 환경 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-delivery-panel") >= 0, "설정 탭 알림 전달 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-external-data-panel") >= 0, "설정 탭 외부 데이터 패널이 렌더링되지 않았습니다.");
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
  const serverProcess = childProcess.spawn(process.env.PYTHON_BIN || "python3", ["python_service/service.py", "web"], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"],
    env: Object.assign({}, process.env, {
      HOST: "127.0.0.1",
      PORT: String(randomPort()),
      LOCAL_CODEX_ENABLED: "0",
      WATCHLIST_SYMBOLS: "TSLA,AAPL,NVDA,000660",
      KIS_MARKET_SIGNALS_ENABLED: "0",
      EXTERNAL_ALPHA_ENABLED: "0",
      EXTERNAL_COINGECKO_ENABLED: "0",
      EXTERNAL_FRED_ENABLED: "0",
      EXTERNAL_DART_ENABLED: "0",
      EXTERNAL_SEC_ENABLED: "0",
      EXTERNAL_CRYPTO_IDS: "",
      SETTINGS_PATH: settingsPath,
      DIGITAL_TWIN_DATA_DIR: dataDir
    }, extraEnv || {})
  });

  try {
    const port = await waitForServer(serverProcess);
    await callback(port, {
      dataDir: dataDir,
      serviceDbPath: path.join(dataDir, "service.db"),
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

  const settings = await request(port, "/api/settings");
  assertOk(settings.statusCode === 200, "설정 API 응답 코드가 200이 아닙니다: " + settings.statusCode);
  const settingsPayload = JSON.parse(settings.body);
  assertOk(settingsPayload.settings && settingsPayload.configured, "설정 API 응답 형식이 맞지 않습니다.");
  assertOk(settingsPayload.settings.tossClientSecret === "", "설정 API가 secret 원문을 내려주고 있습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "alertRules"), "설정 API에 알림 규칙 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "modelDecisionThresholds"), "설정 API에 모델 판단 기준 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "ontologyRelationRules"), "설정 API에 관계 규칙 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "aiPromptTemplates"), "설정 API에 AI 프롬프트 템플릿 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "aiPromptPolicy"), "설정 API에 AI 프롬프트 정책 필드가 없습니다.");
  assertOk(settingsPayload.settings.ontologyRelationRules.indexOf("holding.loss_guard.breakdown.v1") >= 0, "설정 API의 기본 관계 규칙이 비어 있습니다.");
  assertOk(settingsPayload.settings.modelDecisionThresholds.indexOf("modelBuy=74") >= 0, "설정 API의 모델 기본 판단 기준이 비어 있습니다.");
  assertOk(settingsPayload.settings.alertThresholds.indexOf("modelBuyScore=74") >= 0, "설정 API의 모델 알림 기준이 비어 있습니다.");
  assertOk(settingsPayload.settings.alertThresholds.indexOf("watchlistBuyScore=74") >= 0, "설정 API의 관심종목 매수 기준이 비어 있습니다.");
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

  const savedSettings = await request(port, "/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      settings: {
        watchlistSymbols: "TSLA,AAPL,NVDA",
        tossApiBaseUrl: "http://127.0.0.1:1",
        kisMarketSignalsEnabled: "0",
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
        alertRules: "priceStop=1\nmodelSell=1",
        modelDecisionThresholds: "modelBuy=75\nmodelSell=70"
      }
    })
  });
  assertOk(savedSettings.statusCode === 200, "설정 저장 API 응답 코드가 200이 아닙니다: " + savedSettings.statusCode);
  const savedSettingsPayload = JSON.parse(savedSettings.body);
  assertOk(savedSettingsPayload.configured.tossClientSecret === true, "저장된 토스 secret 설정 상태가 true가 아닙니다.");
  assertOk(savedSettingsPayload.settings.tossClientSecret === "", "저장 응답이 토스 secret을 내려주고 있습니다.");
  assertOk(savedSettingsPayload.settings.watchlistSymbols === "TSLA,AAPL,NVDA", "저장된 관심 종목 값이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.alertRules.indexOf("priceStop=1") >= 0, "저장된 알림 규칙이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.modelDecisionThresholds.indexOf("modelBuy=75") >= 0, "저장된 모델 기준값이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.alertThresholds.indexOf("modelBuyScore=75") >= 0, "모델 매수 기준이 알림 기준으로 동기화되지 않았습니다.");
  assertOk(savedSettingsPayload.settings.alertThresholds.indexOf("watchlistBuyScore=75") >= 0, "관심종목 매수 기준이 모델 매수 기준과 동기화되지 않았습니다.");
  assertOk(savedSettingsPayload.settings.alertThresholds.indexOf("modelSellScore=70") >= 0, "모델 매도 기준이 알림 기준으로 동기화되지 않았습니다.");
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
  assertOk(readSqliteSetting(context.serviceDbPath, "appTheme") === "dark", "화면 테마 설정이 SQLite DB에 저장되지 않았습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "notifyProvider") === "telegram", "알림 제공자 설정이 SQLite DB에 저장되지 않았습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "telegramChatId") === "1234", "Telegram Chat ID 설정이 SQLite DB에 저장되지 않았습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "dartDisclosureAiUseCodex") === "0", "공시 AI 엔진 설정이 SQLite DB에 저장되지 않았습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "tossClientSecret") === "fake-secret", "Toss secret 설정이 SQLite DB에 저장되지 않았습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "dataFreshnessQuoteMaxAgeMinutes") === "12", "시세 신선도 기준이 SQLite DB에 저장되지 않았습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "externalSignalCacheMaxAgeMinutes") === "9", "외부 신호 캐시 TTL이 SQLite DB에 저장되지 않았습니다.");
  const eventStatusAfterSettings = JSON.parse((await request(port, "/api/realtime/status")).body);
  assertOk(eventStatusAfterSettings.events["settings.updated"] >= 1, "설정 저장 이벤트가 이벤트 로그에 없습니다.");
  assertOk(eventStatusAfterSettings.latestEvents.some(function (event) { return event.name === "settings.updated"; }), "최근 이벤트에 설정 저장 이벤트가 없습니다.");

  const templates = await request(port, "/api/notification-templates");
  assertOk(templates.statusCode === 200, "알림 템플릿 API 응답 코드가 200이 아닙니다: " + templates.statusCode);
  const templatesPayload = JSON.parse(templates.body);
  assertOk(Array.isArray(templatesPayload.templates), "알림 템플릿 API templates가 배열이 아닙니다.");
  assertOk(templatesPayload.templates.some(function (item) { return item.messageType === "monitorHeartbeat"; }), "상태 확인 템플릿이 없습니다.");
  assertOk(templatesPayload.templates.some(function (item) { return item.messageType === "watchlistQuote"; }), "관심종목 시세 템플릿이 없습니다.");
  assertOk(templatesPayload.templates.some(function (item) { return item.messageType === "watchlistBuyCandidate"; }), "관심종목 매수 후보 템플릿이 없습니다.");
  assertOk(Array.isArray(templatesPayload.variables) && templatesPayload.variables.indexOf("body") >= 0, "알림 템플릿 변수 목록이 없습니다.");

  const savedTemplate = await request(port, "/api/notification-templates", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messageType: "monitorHeartbeat",
      template: "[{messageType}] {title}\n{rawLines}",
      description: "상태 확인 템플릿"
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
  assertOk(rulesPayload.rules.some(function (item) { return item.messageType === "monitorHeartbeat"; }), "상태 확인 발송 우선도 룰이 없습니다.");
  assertOk(Array.isArray(rulesPayload.conditionTypes) && rulesPayload.conditionTypes.length, "알림 룰 조건 타입 목록이 없습니다.");
  assertOk(Array.isArray(rulesPayload.marketHoursSessions) && rulesPayload.marketHoursSessions.length >= 2, "장 시간 세션 목록이 없습니다.");

  const savedRule = await request(port, "/api/notification-rules", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messageType: "monitorHeartbeat",
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
  const resetRule = await request(port, "/api/notification-rules/monitorHeartbeat", { method: "DELETE" });
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
  assertOk(Array.isArray(tossPayload.tossDecision.investmentAnalysis.reasoningCards) && tossPayload.tossDecision.investmentAnalysis.reasoningCards.length > 0, "토스 판단 API에 reasoning card가 없습니다.");
  assertOk(tossPayload.tossDecision.ontologyStrategy && tossPayload.tossDecision.ontologyStrategy.aiInferencePacket && tossPayload.tossDecision.ontologyStrategy.aiInferencePacket.contract === "investment-ontology-ai-inference-v1", "온톨로지 전략에 AI inference packet이 없습니다.");
  assertOk(tossPayload.tossDecision.items.some(function (item) { return item.reasoningCard && item.reasoningCard.id; }), "판단 항목이 reasoning card와 연결되지 않았습니다.");
  assertOk(tossPayload.portfolio && Array.isArray(tossPayload.portfolio.markets), "토스 판단 API에 시장별 현금비중 배열이 없습니다.");
  assertOk(tossPayload.portfolio.markets.some(function (market) { return market.key === "KR"; }), "시장별 현금비중에 한국장 항목이 없습니다.");
  assertOk(tossPayload.portfolio.markets.some(function (market) { return market.key === "US"; }), "시장별 현금비중에 미국장 항목이 없습니다.");
  assertOk(tossPayload.portfolio.total > 2700000, "미국장 USD 평가액이 KRW 기준 총 평가액에 환산되지 않았습니다.");
  assertOk(!Array.isArray(tossPayload.news), "토스 전용 판단 API가 뉴스 배열을 내려주고 있습니다.");
  assertOk(!Array.isArray(tossPayload.social), "토스 전용 판단 API가 소셜 배열을 내려주고 있습니다.");

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
