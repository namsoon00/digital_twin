import { readPersistentPayload, writePersistentPayload } from "../state/storage.mjs";

function notificationRecipientId() {
  var stored = String(readPersistentPayload("orbitAlphaNotificationRecipientId", "") || "").trim();
  if (stored && stored !== "local-owner") return stored;
  var randomId = window.crypto && typeof window.crypto.randomUUID === "function"
    ? window.crypto.randomUUID()
    : [Date.now().toString(36), Math.random().toString(36).slice(2)].join("-");
  var recipientId = "browser-" + randomId;
  writePersistentPayload("orbitAlphaNotificationRecipientId", recipientId);
  return recipientId;
}

export { notificationRecipientId };
