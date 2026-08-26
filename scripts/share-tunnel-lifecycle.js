const DEFAULT_ROTATION_MINUTES = 360;
const DEFAULT_HEALTH_CHECK_SECONDS = 60;
const DEFAULT_ROTATION_GRACE_SECONDS = 120;
const DEFAULT_STARTUP_TIMEOUT_SECONDS = 45;
const DEFAULT_TARGET_PROPAGATION_TIMEOUT_SECONDS = 120;
const DEFAULT_HEALTH_FAILURE_THRESHOLD = 3;

function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, Math.round(parsed)));
}

function lifecycleConfig(environment) {
  const source = environment || {};
  const rotationMinutes = boundedInteger(
    source.SHARE_TUNNEL_ROTATION_MINUTES,
    DEFAULT_ROTATION_MINUTES,
    5,
    7 * 24 * 60
  );
  const healthCheckSeconds = boundedInteger(
    source.SHARE_TUNNEL_HEALTH_CHECK_SECONDS,
    DEFAULT_HEALTH_CHECK_SECONDS,
    10,
    30 * 60
  );
  const rotationGraceSeconds = boundedInteger(
    source.SHARE_TUNNEL_ROTATION_GRACE_SECONDS,
    DEFAULT_ROTATION_GRACE_SECONDS,
    10,
    30 * 60
  );
  const startupTimeoutSeconds = boundedInteger(
    source.SHARE_TUNNEL_STARTUP_TIMEOUT_SECONDS,
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    10,
    5 * 60
  );
  const healthFailureThreshold = boundedInteger(
    source.SHARE_TUNNEL_HEALTH_FAILURE_THRESHOLD,
    DEFAULT_HEALTH_FAILURE_THRESHOLD,
    1,
    10
  );
  const targetPropagationTimeoutSeconds = boundedInteger(
    source.SHARE_TARGET_PROPAGATION_TIMEOUT_SECONDS,
    DEFAULT_TARGET_PROPAGATION_TIMEOUT_SECONDS,
    15,
    10 * 60
  );
  return {
    rotationMinutes,
    rotationIntervalMs: rotationMinutes * 60 * 1000,
    healthCheckSeconds,
    healthCheckIntervalMs: healthCheckSeconds * 1000,
    rotationGraceSeconds,
    rotationGraceMs: rotationGraceSeconds * 1000,
    startupTimeoutSeconds,
    startupTimeoutMs: startupTimeoutSeconds * 1000,
    targetPropagationTimeoutSeconds,
    targetPropagationTimeoutMs: targetPropagationTimeoutSeconds * 1000,
    healthFailureThreshold
  };
}

function nextRotationAt(startedAt, intervalMs) {
  const started = new Date(startedAt || 0).getTime();
  if (!Number.isFinite(started) || started <= 0) return "";
  return new Date(started + Math.max(1, Number(intervalMs || 0))).toISOString();
}

function rotationDue(now, renewAt) {
  const current = new Date(now || Date.now()).getTime();
  const due = new Date(renewAt || 0).getTime();
  return Number.isFinite(current) && Number.isFinite(due) && due > 0 && current >= due;
}

function healthRotationRequired(consecutiveFailures, threshold) {
  return Math.max(0, Number(consecutiveFailures || 0)) >= Math.max(1, Number(threshold || 1));
}

function retryDelayMs(attempt) {
  const count = Math.max(1, Number(attempt || 1));
  return Math.min(5 * 60 * 1000, 5000 * Math.pow(2, Math.min(6, count - 1)));
}

module.exports = {
  DEFAULT_ROTATION_MINUTES,
  healthRotationRequired,
  lifecycleConfig,
  nextRotationAt,
  retryDelayMs,
  rotationDue
};
