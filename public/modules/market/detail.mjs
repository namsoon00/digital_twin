import { feedSettingsEditorLabel } from "./feed.mjs";
import { renderFeedSettingsEditorPanel } from "./settings.mjs";
import { editorWorkDetailPayload } from "../navigation/detail.mjs";

function feedSettingsWorkDetailPayload(key) {
  var label = feedSettingsEditorLabel(key);
  return editorWorkDetailPayload(
    "Feed Operations",
    label + " 상세 설정",
    "기본 화면은 상태만 보고, 실제 입력은 이 레이어에서 수정합니다.",
    renderFeedSettingsEditorPanel(key)
  );
}

export { feedSettingsWorkDetailPayload };
