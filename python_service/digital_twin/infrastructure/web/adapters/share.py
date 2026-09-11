"""Web share boundary."""

from digital_twin.infrastructure.runtime_identity import runtime_identity
from digital_twin.infrastructure.share_access import SHARE_ROLE_LOCAL_OWNER
from digital_twin.infrastructure.share_access import SHARE_ROLE_OWNER
from digital_twin.infrastructure.share_access import ShareAccess
from digital_twin.infrastructure.share_access import anonymous_access
from digital_twin.infrastructure.share_access import local_owner_access
from digital_twin.infrastructure.share_access import owner_tokens
from digital_twin.infrastructure.share_access import share_mode_enabled
from digital_twin.infrastructure.share_access import viewer_tokens
from digital_twin.infrastructure.share_runtime import active_share_runtime_state
from digital_twin.infrastructure.share_runtime import fixed_access_url
from digital_twin.infrastructure.share_runtime import fixed_entry_url
from digital_twin.infrastructure.web.telemetry import WEB_PROCESS_STARTED_AT
from typing import Dict


def share_runtime_status_payload(access: ShareAccess = None, settings: Dict[str, object] = None) -> Dict[str, object]:
    settings = settings if isinstance(settings, dict) else {}
    resolved_access = access or (anonymous_access() if share_mode_enabled() else local_owner_access())
    runtime = active_share_runtime_state()
    entry_url = str(runtime.get("fixedEntryUrl") or fixed_entry_url(settings)).strip()
    viewer_token = next(iter(viewer_tokens()), "")
    owner_token = next(iter(owner_tokens()), "")
    viewer_access_url = str(runtime.get("fixedViewerUrl") or fixed_access_url(entry_url, "share_token", viewer_token)).strip()
    owner_access_url = str(runtime.get("fixedOwnerUrl") or fixed_access_url(entry_url, "owner_token", owner_token)).strip()
    current_viewer_url = str(runtime.get("viewerUrl") or "").strip()
    current_owner_url = str(runtime.get("ownerUrl") or "").strip()
    privileged = resolved_access.role in {SHARE_ROLE_LOCAL_OWNER, SHARE_ROLE_OWNER}
    selected_access_url = owner_access_url if resolved_access.role == SHARE_ROLE_OWNER else viewer_access_url
    selected_current_url = current_owner_url if resolved_access.role == SHARE_ROLE_OWNER else current_viewer_url
    identity = dict(runtime_identity())
    identity["startedAt"] = WEB_PROCESS_STARTED_AT
    return {
        "enabled": share_mode_enabled(),
        "active": bool(runtime),
        "provider": str(runtime.get("provider") or "cloudflared"),
        "baseUrl": str(runtime.get("baseUrl") or "") if runtime else "",
        "fixedEntryUrl": entry_url,
        "fixedAccessUrl": selected_access_url if privileged else "",
        "currentAccessUrl": selected_current_url if privileged else "",
        "updatedAt": str(runtime.get("updatedAt") or ""),
        "targetPublishStatus": str(runtime.get("targetPublishStatus") or ("waiting" if runtime else "inactive")),
        "targetPublishedAt": str(runtime.get("targetPublishedAt") or ""),
        "targetPublishError": str(runtime.get("targetPublishError") or "")[:500] if privileged else "",
        "rotationStatus": str(runtime.get("rotationStatus") or ("active" if runtime else "inactive")),
        "rotationCount": int(runtime.get("rotationCount") or 0),
        "rotationMinutes": int(runtime.get("rotationMinutes") or 0),
        "rotationGraceSeconds": int(runtime.get("rotationGraceSeconds") or 0),
        "tunnelStartedAt": str(runtime.get("tunnelStartedAt") or ""),
        "renewAt": str(runtime.get("renewAt") or ""),
        "lastRotationAt": str(runtime.get("lastRotationAt") or ""),
        "lastRotationReason": str(runtime.get("lastRotationReason") or ""),
        "lastRotationStatus": str(runtime.get("lastRotationStatus") or ""),
        "lastRotationError": str(runtime.get("lastRotationError") or "")[:500] if privileged else "",
        "lastHealthCheckAt": str(runtime.get("lastHealthCheckAt") or ""),
        "lastHealthStatus": str(runtime.get("lastHealthStatus") or "unknown"),
        "lastHealthError": str(runtime.get("lastHealthError") or "")[:500] if privileged else "",
        "consecutiveHealthFailures": int(runtime.get("consecutiveHealthFailures") or 0),
        "runtimeIdentity": identity,
        "accessLinkPolicy": "fragment-only",
    }
