export const MIN_DURATION_SEC = 1;
export const MAX_DURATION_SEC = 60;
export const MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024;

const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm; codecs=opus"];

export function pickSupportedMimeType() {
  if (
    typeof MediaRecorder === "undefined" ||
    typeof MediaRecorder.isTypeSupported !== "function"
  ) {
    return null;
  }

  return MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type)) ?? null;
}

export function extensionForMimeType(mimeType) {
  if (mimeType?.includes("webm")) {
    return "webm";
  }
  return "webm";
}

export function formatFileSize(bytes) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function formatClock(seconds) {
  const clamped = Math.max(0, seconds);
  const whole = Math.min(MAX_DURATION_SEC, Math.floor(clamped));
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}
