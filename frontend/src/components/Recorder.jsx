import { useEffect, useRef, useState } from "react";
import {
  MAX_DURATION_SEC,
  MAX_FILE_SIZE_BYTES,
  MIN_DURATION_SEC,
  extensionForMimeType,
  formatClock,
  formatFileSize,
  pickSupportedMimeType,
} from "../audio/format.js";

const UNSUPPORTED_MESSAGE =
  "当前浏览器不支持 WebM/Opus 录音，请更换 Chrome 或 Edge 后重试";
const MIC_DENIED_MESSAGE = "无法使用麦克风，请在浏览器中允许麦克风权限后重试";
const RECORD_FAIL_MESSAGE = "录音失败，请重试";
const TOO_SHORT_MESSAGE = "录音时长必须在1-60秒之间，请按住按钮重新录制";
const TOO_LARGE_MESSAGE = "音频文件超过5MB限制，请缩短录音后重试";

function stopTracks(stream) {
  stream?.getTracks().forEach((track) => track.stop());
}

function describeMicError(error) {
  const name = error?.name;
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return MIC_DENIED_MESSAGE;
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "未找到可用麦克风，请接入设备后重试";
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return "当前页面无法使用麦克风，请通过 localhost 打开后重试";
  }
  return RECORD_FAIL_MESSAGE;
}

function makeFilename(mime) {
  const stamp = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `recording-${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}-${pad(stamp.getHours())}${pad(stamp.getMinutes())}${pad(stamp.getSeconds())}.${extensionForMimeType(mime)}`;
}

export default function Recorder() {
  const [phase, setPhase] = useState("idle");
  const [error, setError] = useState("");
  const [elapsedSec, setElapsedSec] = useState(0);
  const [result, setResult] = useState(null);

  const mimeTypeRef = useRef(pickSupportedMimeType());
  const pressingRef = useRef(false);
  const sessionRef = useRef(0);
  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const startedAtRef = useRef(0);
  const stoppingRef = useRef(false);
  const discardedRef = useRef(false);
  const maxTimerRef = useRef(0);
  const tickTimerRef = useRef(0);
  const objectUrlRef = useRef("");
  const unbindWindowRef = useRef(() => {});

  function revokeObjectUrl() {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = "";
    }
  }

  function clearTimers() {
    window.clearTimeout(maxTimerRef.current);
    window.clearInterval(tickTimerRef.current);
    maxTimerRef.current = 0;
    tickTimerRef.current = 0;
  }

  function releaseMicrophone() {
    stopTracks(streamRef.current);
    streamRef.current = null;
  }

  function unbindWindowListeners() {
    unbindWindowRef.current();
    unbindWindowRef.current = () => {};
  }

  function fail(message) {
    clearTimers();
    unbindWindowListeners();
    releaseMicrophone();
    recorderRef.current = null;
    chunksRef.current = [];
    startedAtRef.current = 0;
    stoppingRef.current = false;
    pressingRef.current = false;
    discardedRef.current = false;
    setElapsedSec(0);
    setResult(null);
    revokeObjectUrl();
    setError(message);
    setPhase("idle");
  }

  function handleRecordedBlob(blob, durationSec) {
    if (durationSec < MIN_DURATION_SEC || durationSec > MAX_DURATION_SEC + 0.3) {
      fail(TOO_SHORT_MESSAGE);
      return;
    }
    if (blob.size > MAX_FILE_SIZE_BYTES) {
      fail(TOO_LARGE_MESSAGE);
      return;
    }
    if (blob.size === 0) {
      fail(RECORD_FAIL_MESSAGE);
      return;
    }

    revokeObjectUrl();
    const mime = blob.type || mimeTypeRef.current;
    const objectUrl = URL.createObjectURL(blob);
    objectUrlRef.current = objectUrl;
    setResult({
      blob,
      objectUrl,
      filename: makeFilename(mime),
      mimeType: mime,
      durationSec,
      sizeBytes: blob.size,
    });
    setError("");
    setPhase("ready");
  }

  function finishRecording({ discarded }) {
    discardedRef.current = discardedRef.current || discarded;
    pressingRef.current = false;
    unbindWindowListeners();
    clearTimers();

    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") {
      releaseMicrophone();
      recorderRef.current = null;
      stoppingRef.current = false;
      setElapsedSec(0);
      if (discardedRef.current) {
        setPhase("idle");
      }
      return;
    }
    if (stoppingRef.current) {
      return;
    }
    stoppingRef.current = true;
    try {
      recorder.stop();
    } catch {
      fail(RECORD_FAIL_MESSAGE);
    }
  }

  async function startRecording() {
    const session = sessionRef.current;
    stoppingRef.current = false;
    discardedRef.current = false;
    setError("");
    setPhase("requesting");

    const mimeType = mimeTypeRef.current;
    if (!mimeType) {
      fail(UNSUPPORTED_MESSAGE);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      fail("当前页面无法使用麦克风，请通过 localhost 打开后重试");
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      if (session !== sessionRef.current) {
        return;
      }
      fail(describeMicError(error));
      return;
    }

    if (session !== sessionRef.current || !pressingRef.current || discardedRef.current) {
      stopTracks(stream);
      if (session === sessionRef.current) {
        releaseMicrophone();
        setPhase("idle");
      }
      return;
    }

    const chunks = [];
    chunksRef.current = chunks;
    streamRef.current = stream;

    let recorder;
    try {
      recorder = new MediaRecorder(stream, { mimeType });
    } catch {
      stopTracks(stream);
      streamRef.current = null;
      fail(RECORD_FAIL_MESSAGE);
      return;
    }

    recorderRef.current = recorder;
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) {
        chunks.push(event.data);
      }
    });
    recorder.addEventListener("error", () => {
      if (session !== sessionRef.current) {
        return;
      }
      fail(RECORD_FAIL_MESSAGE);
    });
    recorder.addEventListener("stop", () => {
      const durationSec = startedAtRef.current
        ? (performance.now() - startedAtRef.current) / 1000
        : 0;
      const shouldDiscard = discardedRef.current;
      releaseMicrophone();
      recorderRef.current = null;
      stoppingRef.current = false;
      startedAtRef.current = 0;
      setElapsedSec(0);

      if (session !== sessionRef.current || shouldDiscard) {
        chunksRef.current = [];
        setPhase("idle");
        return;
      }

      const blob = new Blob(chunks, { type: mimeType });
      chunksRef.current = [];
      handleRecordedBlob(blob, durationSec);
    });

    try {
      recorder.start();
    } catch {
      stopTracks(stream);
      streamRef.current = null;
      recorderRef.current = null;
      fail(RECORD_FAIL_MESSAGE);
      return;
    }

    startedAtRef.current = performance.now();
    setElapsedSec(0);
    setPhase("recording");
    tickTimerRef.current = window.setInterval(() => {
      const elapsed = (performance.now() - startedAtRef.current) / 1000;
      setElapsedSec(Math.min(MAX_DURATION_SEC, elapsed));
    }, 200);
    maxTimerRef.current = window.setTimeout(() => {
      finishRecording({ discarded: false });
    }, MAX_DURATION_SEC * 1000);
  }

  function bindWindowListeners() {
    unbindWindowListeners();
    const onUp = () => finishRecording({ discarded: false });
    const onCancel = () => finishRecording({ discarded: false });
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    unbindWindowRef.current = () => {
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
    };
  }

  function beginPress(event) {
    if (event.pointerType === "mouse" && event.button !== 0) {
      return;
    }
    if (pressingRef.current || recorderRef.current || phase === "requesting") {
      return;
    }

    const supported = pickSupportedMimeType();
    mimeTypeRef.current = supported;
    if (!supported) {
      setError(UNSUPPORTED_MESSAGE);
      return;
    }

    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    pressingRef.current = true;
    sessionRef.current += 1;
    revokeObjectUrl();
    setResult(null);
    bindWindowListeners();
    startRecording();
  }

  function cancelRecording() {
    pressingRef.current = false;
    sessionRef.current += 1;
    discardedRef.current = true;
    setError("");
    finishRecording({ discarded: true });
    setPhase("idle");
  }

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === "Escape") {
        cancelRecording();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      pressingRef.current = false;
      sessionRef.current += 1;
      discardedRef.current = true;
      unbindWindowListeners();
      clearTimers();
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        try {
          recorderRef.current.stop();
        } catch {
          /* already stopped */
        }
      }
      releaseMicrophone();
      revokeObjectUrl();
    };
  }, []);

  return (
    <section className="recorder">
      <p className="status" aria-live="polite">
        {phase === "requesting" && "正在获取麦克风…"}
        {phase === "recording" &&
          `录音中 ${formatClock(elapsedSec)} / ${formatClock(MAX_DURATION_SEC)}`}
        {phase === "idle" && !error && "按住按钮录音，松开结束"}
        {phase === "ready" && "录音完成，可试听或下载"}
      </p>

      <button
        type="button"
        className={phase === "recording" ? "record-button is-recording" : "record-button"}
        onPointerDown={beginPress}
        onContextMenu={(event) => event.preventDefault()}
      >
        {phase === "recording" ? "松开结束" : "按住说话"}
      </button>

      {(phase === "recording" || phase === "requesting") && (
        <button type="button" className="cancel-button" onClick={cancelRecording}>
          取消
        </button>
      )}

      {error && <p className="error">{error}</p>}

      {result && (
        <div className="recording-result">
          <audio controls src={result.objectUrl} preload="metadata">
            浏览器无法播放这段录音
          </audio>
          <p className="meta">
            格式 {result.mimeType} · 大小 {formatFileSize(result.sizeBytes)} · 时长约{" "}
            {result.durationSec.toFixed(1)} 秒
          </p>
          <a className="download-link" href={result.objectUrl} download={result.filename}>
            下载录音文件
          </a>
          <p className="hint">下载仅用于后续独立测试上传接口，本轮不会上传。</p>
        </div>
      )}
    </section>
  );
}
