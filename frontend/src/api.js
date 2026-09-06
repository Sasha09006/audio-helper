import axios from "axios";

const BASE_URL = "http://localhost:8003";

/**
 * 前端超时略长于对应后端接口完整预算，避免前端先超时而切断还在跑的后端任务。
 * 单位 ms。
 */
const TIMEOUTS = {
  health: 5_000,
  upload: 30_000,
  asr: 25_000,    // 后端 20s
  extract: 20_000, // 后端 15s
  search: 35_000,  // 后端 30s
  finalize: 50_000, // 后端 40s
};

export const api = axios.create({ baseURL: BASE_URL });

/**
 * 从 axios 错误中提取面向用户的中文提示。
 * 返回 null 表示请求被主动取消，调用方应静默忽略。
 */
export function parseApiError(err) {
  // AbortController 取消 / axios CancelToken 取消
  if (err.name === "CanceledError" || err.code === "ERR_CANCELED") {
    return null;
  }
  // 后端统一 JSON 错误体
  if (err.response?.data?.error) {
    const { message } = err.response.data.error;
    return message || "请求失败，请稍后重试";
  }
  // axios 超时
  if (err.code === "ECONNABORTED" || err.message?.toLowerCase().includes("timeout")) {
    return "请求超时，请检查网络后重试";
  }
  // 离线
  if (typeof navigator !== "undefined" && !navigator.onLine) {
    return "网络已断开，请检查连接后重试";
  }
  // 无响应（后端未启动 / CORS 拦截等）
  if (!err.response) {
    return "无法连接到服务，请确认后端已在 127.0.0.1:8003 运行";
  }
  return "未知错误，请稍后重试";
}

// ── 各接口封装 ────────────────────────────────────────────────

/** GET /health → { status: "ok" } */
export async function checkHealth(signal) {
  const res = await api.get("/health", { signal, timeout: TIMEOUTS.health });
  return res.data.data;
}

/**
 * POST /upload（multipart/form-data）→ { audio_id }
 * 不手写 Content-Type，由浏览器自动加 boundary。
 */
export async function uploadAudio(blob, filename, signal) {
  const form = new FormData();
  form.append("file", blob, filename);
  const res = await api.post("/upload", form, { signal, timeout: TIMEOUTS.upload });
  return res.data.data;
}

/** POST /asr → { text } */
export async function recognizeAudio(audioId, signal) {
  const res = await api.post(
    "/asr",
    { audio_id: audioId },
    { signal, timeout: TIMEOUTS.asr },
  );
  return res.data.data;
}

/** POST /extract → { city_a, address_a, city_b, address_b, category } */
export async function extractInfo(text, city, signal) {
  const res = await api.post(
    "/extract",
    { text, city },
    { signal, timeout: TIMEOUTS.extract },
  );
  return res.data.data;
}

/** POST /search → { search_id, midpoint, pois } */
export async function searchPois(extractData, signal) {
  const res = await api.post("/search", extractData, { signal, timeout: TIMEOUTS.search });
  return res.data.data;
}

/** POST /finalize → { reply_text, audio_url, warning } */
export async function finalizeRecommendation(searchId, signal) {
  const res = await api.post(
    "/finalize",
    { search_id: searchId },
    { signal, timeout: TIMEOUTS.finalize },
  );
  return res.data.data;
}
