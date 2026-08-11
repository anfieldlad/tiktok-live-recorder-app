/**
 * HTTP client for the deployed saver app.
 *
 * Everything the robot does to the app that is not a browser interaction goes
 * through here, so there is one place to add a credential when the API stops
 * being anonymous.
 *
 * Note on paths: the API returns file URLs like "/downloads/<id>/files/0" with
 * no ROOT_PATH prefix, so they are relative to the host, not to the mount point.
 * `resolve()` puts the "/tiktok" back.
 */

import { readFileSync } from "node:fs";

/**
 * Where the robot points. The production host is deliberately not in the repo —
 * same rule SSH.md applies to server details — because this API has no
 * authentication, so its address is worth as much as a credential.
 *
 * Resolution order: ROBOT_BASE_URL env var, then `.env.robot` (gitignored),
 * then a local dev server.
 */
function resolveBaseUrl() {
  if (process.env.ROBOT_BASE_URL) return process.env.ROBOT_BASE_URL;
  try {
    const file = readFileSync(new URL("../../.env.robot", import.meta.url), "utf8");
    const match = file.match(/^\s*ROBOT_BASE_URL\s*=\s*(.+?)\s*$/m);
    if (match) return match[1].replace(/^["']|["']$/g, "");
  } catch {
    // no local target file; fall through
  }
  return "http://127.0.0.1:8000";
}

export const DEFAULT_BASE_URL = resolveBaseUrl();

export class AppClient {
  constructor(baseUrl = DEFAULT_BASE_URL) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    const url = new URL(this.baseUrl);
    this.origin = url.origin;
    this.rootPath = url.pathname.replace(/\/+$/, "");
  }

  /** Turn an API-returned path into an absolute URL. */
  resolve(path) {
    if (/^https?:\/\//i.test(path)) return path;
    if (this.rootPath && path.startsWith(`${this.rootPath}/`)) return `${this.origin}${path}`;
    return `${this.origin}${this.rootPath}${path.startsWith("/") ? path : `/${path}`}`;
  }

  /**
   * Retries transient failures — a DNS hiccup or a dropped connection should
   * not read as "the app is broken". Only safe methods retry by default:
   * re-sending POST /recordings would start a second recording.
   */
  async request(method, path, { body, timeoutMs = 30000, raw = false, retries } = {}) {
    const attempts = (retries ?? (method === "GET" ? 2 : 0)) + 1;
    let lastError = null;

    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try {
        return await this.#send(method, path, { body, timeoutMs, raw });
      } catch (error) {
        // HTTP-level errors are real answers from the app; only network-level
        // failures and gateway errors are worth another go.
        const transient = error.status === undefined || [502, 503, 504].includes(error.status);
        lastError = error;
        if (!transient || attempt === attempts) throw error;
        await new Promise((resolve) => setTimeout(resolve, attempt * 1000));
      }
    }
    throw lastError;
  }

  async #send(method, path, { body, timeoutMs, raw }) {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: body ? { "content-type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (raw) return response;

    const text = await response.text();
    let parsed = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = { detail: text.slice(0, 400) };
    }
    if (!response.ok) {
      const detail = typeof parsed?.detail === "string" ? parsed.detail : JSON.stringify(parsed?.detail ?? text.slice(0, 200));
      const error = new Error(`${method} ${path} -> ${response.status}: ${detail}`);
      error.status = response.status;
      error.payload = parsed;
      throw error;
    }
    return parsed;
  }

  health() {
    return this.request("GET", "/health", { timeoutMs: 15000 });
  }

  healthDetails() {
    return this.request("GET", "/health/details", { timeoutMs: 15000 });
  }

  saveTikTokSession(sessionSs) {
    // Idempotent: writing the same session twice is harmless.
    return this.request("POST", "/auth/tiktok-cookies", { body: { session_ss: sessionSs }, retries: 2 });
  }

  checkLive(source) {
    return this.request("POST", "/recordings/check-live", { body: source, timeoutMs: 120000 });
  }

  getRecording(jobId) {
    return this.request("GET", `/recordings/${jobId}`);
  }

  stopRecording(jobId) {
    return this.request("POST", `/recordings/${jobId}/stop`, { timeoutMs: 60000 });
  }

  deleteRecording(jobId) {
    return this.request("DELETE", `/recordings/${jobId}`);
  }

  listRecordings() {
    return this.request("GET", "/recordings");
  }

  listWatchRecordings() {
    return this.request("GET", "/watch-recordings");
  }

  downloadRecording(jobId) {
    return this.request("GET", `/recordings/${jobId}/download`, { raw: true, timeoutMs: 300000 });
  }

  downloadInstagram(url) {
    return this.request("POST", "/instagram/downloads", { body: { url }, timeoutMs: 600000 });
  }

  /**
   * Pull the first bytes of the live relay, then hang up. Used to prove the
   * relay produces a real MP4 without recording the whole stream.
   */
  async sampleLiveRelay(username, { bytes = 256 * 1024, timeoutMs = 90000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(
        `${this.baseUrl}/live/stream?username=${encodeURIComponent(username)}`,
        { signal: controller.signal },
      );
      if (!response.ok) {
        const detail = await response.text();
        const error = new Error(`GET /live/stream -> ${response.status}: ${detail.slice(0, 300)}`);
        error.status = response.status;
        throw error;
      }

      const chunks = [];
      let total = 0;
      for await (const chunk of response.body) {
        chunks.push(chunk);
        total += chunk.length;
        if (total >= bytes) break;
      }
      controller.abort(); // stop ffmpeg on the server
      return { bytes: total, head: Buffer.concat(chunks.map(Buffer.from)).subarray(0, 32) };
    } finally {
      clearTimeout(timer);
    }
  }
}

/** MP4/fragmented-MP4 files carry an "ftyp" box within the first bytes. */
export function looksLikeMp4(head) {
  return Buffer.from(head).subarray(0, 16).includes(Buffer.from("ftyp"));
}
