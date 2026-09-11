import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { formatClock } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { settingsState } from "../state/settings.mjs";

function refreshShareRuntimeStatus() {
  return requestJson("/api/share/status", { force: true, silent: true, key: "share-runtime-status" }).then(function (payload) {
    settingsState.shareRuntime = payload && typeof payload === "object" ? payload : {};
    render();
    return settingsState.shareRuntime;
  });
}

function pollShareTunnelRotation(previousRotationAt, attempt) {
  var count = Number(attempt || 0);
  if (!settingsState.shareRotationRequesting || count >= 60) {
    settingsState.shareRotationRequesting = false;
    render();
    return;
  }
  window.setTimeout(function () {
    refreshShareRuntimeStatus().then(function (share) {
      var failed = String(share.lastRotationStatus || "") === "failed"
        && String(share.rotationStatus || "") === "degraded";
      var completed = String(share.lastRotationStatus || "") === "ok"
        && String(share.lastRotationAt || "")
        && String(share.lastRotationAt || "") !== String(previousRotationAt || "")
        && String(share.rotationStatus || "") === "active";
      if (completed) {
        settingsState.shareRotationRequesting = false;
        showSnackbar("고정 링크를 새 Cloudflare 주소로 전환했습니다.", "success");
        render();
        return;
      }
      if (failed) {
        settingsState.shareRotationRequesting = false;
        showSnackbar(share.lastRotationError || "새 주소 준비에 실패해 기존 연결을 유지합니다.", "danger");
        render();
        return;
      }
      pollShareTunnelRotation(previousRotationAt, count + 1);
    }).catch(function () {
      pollShareTunnelRotation(previousRotationAt, count + 1);
    });
  }, 3000);
}

function requestShareTunnelRotation() {
  if (settingsState.shareRotationRequesting) return;
  var previousRotationAt = String((settingsState.shareRuntime || {}).lastRotationAt || "");
  settingsState.shareRotationRequesting = true;
  render();
  sendJson("/api/share/rotate", "POST", { reason: "owner-request" }, { timeoutMs: 15000 })
    .then(function () {
      showSnackbar("기존 연결을 유지한 채 새 Cloudflare 주소를 준비합니다.");
      pollShareTunnelRotation(previousRotationAt, 0);
    })
    .catch(function (error) {
      settingsState.shareRotationRequesting = false;
      showSnackbar(error.message || "터널 갱신을 요청하지 못했습니다.", "danger");
      render();
    });
}

function renderShareRuntimePanel() {
  var share = settingsState.shareRuntime && typeof settingsState.shareRuntime === "object" ? settingsState.shareRuntime : {};
  var identity = share.runtimeIdentity && typeof share.runtimeIdentity === "object" ? share.runtimeIdentity : (settingsState.runtimeIdentity || {});
  var active = Boolean(share.active);
  var enabled = Boolean(share.enabled);
  var publishStatus = String(share.targetPublishStatus || (active ? "waiting" : "inactive"));
  var publishLabel = ({ published: "Pages 반영됨", publishing: "주소 반영 중", disabled: "자동 반영 꺼짐", failed: "반영 실패", waiting: "반영 대기" })[publishStatus] || "연결 없음";
  var publishTone = publishStatus === "published" ? "watch" : (publishStatus === "failed" ? "danger" : "caution");
  var accessUrl = String(share.fixedAccessUrl || "");
  var currentUrl = String(share.currentAccessUrl || "");
  var publicUrl = accessUrl || String(share.fixedEntryUrl || "");
  var revision = String(identity.revision || "unknown");
  var startedAt = String(identity.startedAt || "");
  var rotationStatus = String(share.rotationStatus || (active ? "active" : "inactive"));
  var rotationBusy = settingsState.shareRotationRequesting || ["preparing", "verifying", "publishing", "confirming", "recovering"].indexOf(rotationStatus) >= 0;
  var rotationLabel = ({ active: "자동 갱신 대기", preparing: "새 터널 준비 중", verifying: "새 터널 검증 중", publishing: "고정 주소 반영 중", confirming: "고정 주소 전환 확인", recovering: "연결 복구 중", degraded: "기존 터널 유지" })[rotationStatus] || "상태 확인 필요";
  var healthStatus = String(share.lastHealthStatus || "unknown");
  var healthLabel = healthStatus === "healthy" ? "정상" : (healthStatus === "degraded" ? "재확인 중" : "확인 대기");
  var healthTone = healthStatus === "healthy" ? "watch" : (healthStatus === "degraded" ? "caution" : "muted");
  var renewalText = share.renewAt ? formatClock(share.renewAt) : "예정 시각 확인 중";
  return [
    '<section class="share-runtime-panel"' + cardTypeAttrs("diagnostic-card", active ? "watch" : "caution") + '>',
    '<div class="share-runtime-head">',
    '<div><p class="label">Stable Web Entry</p><strong>웹 공유 연결</strong><span>앱 배포와 터널을 분리하고 고정 진입 주소가 현재 실행 중인 앱을 찾습니다.</span></div>',
    '<span class="tone-chip ' + (active ? "watch" : "caution") + '">' + escapeHtml(active ? "터널 연결됨" : (enabled ? "터널 확인 필요" : "공유 꺼짐")) + '</span>',
    '</div>',
    '<div class="share-runtime-status-grid">',
    '<div><span>터널</span><strong>' + escapeHtml(active ? String(share.provider || "cloudflared") : "연결 없음") + '</strong><small>' + escapeHtml(String(share.updatedAt || "상태 기록 없음")) + '</small></div>',
    '<div><span>고정 주소</span><strong class="' + escapeHtml(publishTone) + '">' + escapeHtml(publishLabel) + '</strong><small>' + escapeHtml(String(share.targetPublishedAt || "반영 기록 없음")) + '</small></div>',
    '<div><span>실행 코드</span><strong>' + escapeHtml(revision) + '</strong><small>' + escapeHtml(startedAt || "시작 시각 확인 불가") + '</small></div>',
    '<div><span>자동 갱신</span><strong class="' + escapeHtml(rotationBusy ? "caution" : "watch") + '">' + escapeHtml(rotationLabel) + '</strong><small>' + escapeHtml("다음 " + renewalText + " · " + Number(share.rotationMinutes || 0) + "분 주기") + '</small></div>',
    '<div><span>터널 건강</span><strong class="' + escapeHtml(healthTone) + '">' + escapeHtml(healthLabel) + '</strong><small>' + escapeHtml(share.lastHealthCheckAt ? "확인 " + formatClock(share.lastHealthCheckAt) : "상태 확인 전") + '</small></div>',
    '</div>',
    '<div class="share-runtime-link">',
    '<span>고정 접속 주소</span>',
    '<code>' + escapeHtml(publicUrl || "고정 진입 주소를 확인할 수 없습니다.") + '</code>',
    accessUrl ? '<p>접근 키는 `#` 뒤 fragment에만 있으며 GitHub Pages 서버에는 전달되지 않습니다.</p>' : '<p>토큰이 포함된 전체 링크는 로컬 소유자에게만 표시됩니다.</p>',
    '<div class="share-runtime-actions">',
    accessUrl ? '<button class="text-button primary" type="button" data-copy-share-url="' + escapeHtml(accessUrl) + '">전체 링크 복사</button>' : '',
    accessUrl ? '<button class="text-button" type="button" data-action="rotate-share-tunnel"' + (rotationBusy ? ' disabled aria-busy="true"' : '') + '>' + escapeHtml(rotationBusy ? "새 주소 준비 중" : "지금 주소 갱신") + '</button>' : '',
    publicUrl ? '<a class="text-button" href="' + escapeHtml(publicUrl) + '" target="_blank" rel="noreferrer">고정 주소 열기</a>' : '',
    currentUrl ? '<a class="text-button" href="' + escapeHtml(currentUrl) + '" target="_blank" rel="noreferrer">현재 터널 확인</a>' : '',
    '</div>',
    '</div>',
    publishStatus === "failed" && share.targetPublishError ? '<p class="form-error">' + escapeHtml(share.targetPublishError) + '</p>' : '',
    share.lastRotationStatus === "failed" && share.lastRotationError ? '<p class="form-error">' + escapeHtml(share.lastRotationError) + '</p>' : '',
    '</section>'
  ].join("");
}

export { renderShareRuntimePanel, requestShareTunnelRotation };
