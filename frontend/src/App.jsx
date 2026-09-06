import { useEffect, useRef, useState } from "react";
import CitySelect from "./components/CitySelect.jsx";
import Recorder from "./components/Recorder.jsx";
import {
  checkHealth,
  extractInfo,
  finalizeRecommendation,
  parseApiError,
  recognizeAudio,
  searchPois,
  uploadAudio,
} from "./api.js";

// ── 各阶段状态文案 ────────────────────────────────────────────
const PHASE_STATUS = {
  idle: "",
  checking: "检查服务状态…",
  uploading: "上传录音中…",
  recognizing: "识别语音中…",
  extracting: "提取地点信息中…",
  searching: "查找中间地点中…",
  finalizing: "生成推荐中…",
  done: "",
  error: "",
};

// 正在进行中的阶段集合
const ACTIVE_PHASES = new Set([
  "checking",
  "uploading",
  "recognizing",
  "extracting",
  "searching",
  "finalizing",
]);

export default function App() {
  const [city, setCity] = useState("杭州");

  // 流程阶段
  const [phase, setPhase] = useState("idle");
  // 错误信息（失败时保留已完成的部分结果）
  const [pipelineError, setPipelineError] = useState(null);

  // 累积结果
  const [recognizedText, setRecognizedText] = useState(null);
  const [extractedInfo, setExtractedInfo] = useState(null);
  const [searchResult, setSearchResult] = useState(null);
  const [finalResult, setFinalResult] = useState(null);

  // 音频播放
  const [showPlayButton, setShowPlayButton] = useState(false);
  const [audioLoadError, setAudioLoadError] = useState(false);

  const abortRef = useRef(null);          // 当前 pipeline 的 AbortController
  const audioRef = useRef(null);          // <audio> DOM 引用
  const playAttempted = useRef(false);    // 自动播放是否已尝试

  // 组件卸载时释放资源
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // audio_url 变化时重置播放状态
  useEffect(() => {
    playAttempted.current = false;
    setShowPlayButton(false);
    setAudioLoadError(false);
  }, [finalResult?.audio_url]);

  // ── 清空本轮所有结果 ──────────────────────────────────────
  function clearResults() {
    setPipelineError(null);
    setRecognizedText(null);
    setExtractedInfo(null);
    setSearchResult(null);
    setFinalResult(null);
    setShowPlayButton(false);
    setAudioLoadError(false);
    // 停止旧音频
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
    }
  }

  // ── 用户开始新一轮录音（Recorder 回调）────────────────────
  function handleRecordingStart() {
    abortRef.current?.abort();
    abortRef.current = null;
    clearResults();
    setPhase("idle");
  }

  // ── 录音完成后自动启动 pipeline（Recorder 回调）──────────
  async function handleRecorded(blob, filename) {
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;

    clearResults();

    try {
      // 1. 健康检查
      setPhase("checking");
      await checkHealth(signal);

      // 2. 上传录音
      setPhase("uploading");
      const { audio_id } = await uploadAudio(blob, filename, signal);

      // 3. 语音识别
      setPhase("recognizing");
      const { text } = await recognizeAudio(audio_id, signal);
      setRecognizedText(text);

      // 4. 信息提取
      setPhase("extracting");
      const info = await extractInfo(text, city, signal);
      setExtractedInfo(info);

      // 5. 中点搜店
      setPhase("searching");
      const search = await searchPois(info, signal);
      setSearchResult(search);

      // 6. 生成推荐语 + TTS
      setPhase("finalizing");
      const final = await finalizeRecommendation(search.search_id, signal);
      setFinalResult(final);

      setPhase("done");
    } catch (err) {
      const msg = parseApiError(err);
      if (msg === null) {
        // 主动取消，静默退出
        return;
      }
      const apiError = err.response?.data?.error;
      setPipelineError({
        message: msg,
        code: apiError?.code ?? "UNKNOWN",
        stage: apiError?.stage ?? "network",
      });
      setPhase("error");
    }
  }

  // ── 音频自动播放 ──────────────────────────────────────────
  function handleAudioCanPlay() {
    if (playAttempted.current) return;
    playAttempted.current = true;
    audioRef.current
      ?.play()
      .catch((err) => {
        if (err.name === "NotAllowedError") {
          // 浏览器拦截自动播放，显示手动播放按钮
          setShowPlayButton(true);
        }
        // 其他错误（如已销毁）静默忽略
      });
  }

  const statusMsg = PHASE_STATUS[phase] || "";
  const isActive = ACTIVE_PHASES.has(phase);
  const hasAnyResult =
    phase !== "idle" &&
    (pipelineError ||
      recognizedText ||
      extractedInfo ||
      searchResult ||
      finalResult ||
      statusMsg);

  return (
    <main className="page">
      <h1>语音约碰面地点</h1>
      <p>按住录音，说出两人所在地点和想找的店。当前仅支持同城两人。</p>

      <CitySelect value={city} onChange={setCity} />

      <Recorder
        onRecordingStart={handleRecordingStart}
        onRecorded={handleRecorded}
      />

      {/* ── Pipeline 结果区域 ── */}
      {hasAnyResult && (
        <div className="pipeline">
          {/* 进行中状态 */}
          {statusMsg && (
            <p className="pipeline-status" aria-live="polite" aria-busy={isActive}>
              <span className="spinner" aria-hidden="true" />
              {statusMsg}
            </p>
          )}

          {/* 错误提示（保留已完成部分） */}
          {pipelineError && (
            <div className="pipeline-error" role="alert">
              <p className="error-message">⚠ {pipelineError.message}</p>
              <p className="error-detail">
                [{pipelineError.stage} · {pipelineError.code}]
              </p>
            </div>
          )}

          {/* 识别文字 */}
          {recognizedText && (
            <section className="result-section">
              <h2 className="result-heading">识别文字</h2>
              <p className="recognized-text">{recognizedText}</p>
            </section>
          )}

          {/* 提取信息 */}
          {extractedInfo && (
            <section className="result-section">
              <h2 className="result-heading">提取信息</h2>
              <dl className="info-grid">
                <dt>甲方</dt>
                <dd>
                  {extractedInfo.city_a} · {extractedInfo.address_a}
                </dd>
                <dt>乙方</dt>
                <dd>
                  {extractedInfo.city_b} · {extractedInfo.address_b}
                </dd>
                <dt>类别</dt>
                <dd>{extractedInfo.category}</dd>
              </dl>
            </section>
          )}

          {/* 候选地点（后端顺序，最多3家） */}
          {searchResult?.pois?.length > 0 && (
            <section className="result-section">
              <h2 className="result-heading">候选地点</h2>
              <ol className="poi-list">
                {searchResult.pois.map((poi, i) => (
                  <li key={i} className="poi-item">
                    <span className="poi-name">{poi.name}</span>
                    <span className="poi-address">{poi.address}</span>
                    <span className="poi-distance">
                      距中点约 {poi.distance_to_midpoint_m} 米
                    </span>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {/* 推荐语 + 音频播放 */}
          {finalResult && (
            <section className="result-section result-final">
              <h2 className="result-heading">推荐语</h2>
              <p className="recommendation-text">{finalResult.reply_text}</p>

              {/* TTS 降级警告 */}
              {finalResult.warning && (
                <p className="degraded-warning">⚠ {finalResult.warning}</p>
              )}

              {/* 音频播放器（仅当 audio_url 不为 null 且未加载失败） */}
              {finalResult.audio_url && !audioLoadError && (
                <div className="audio-player">
                  <audio
                    ref={audioRef}
                    src={finalResult.audio_url}
                    controls
                    onCanPlay={handleAudioCanPlay}
                    onError={() => setAudioLoadError(true)}
                  />
                  {showPlayButton && (
                    <button
                      type="button"
                      className="play-button"
                      onClick={() => {
                        setShowPlayButton(false);
                        audioRef.current?.play();
                      }}
                    >
                      ▶ 点击播放推荐语
                    </button>
                  )}
                </div>
              )}

              {/* 音频加载失败提示（保留文字结果） */}
              {audioLoadError && (
                <p className="audio-load-error">音频加载失败，请查看上方文字推荐</p>
              )}
            </section>
          )}
        </div>
      )}
    </main>
  );
}
