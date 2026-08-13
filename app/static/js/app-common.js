window.appConfig = window.appConfig || {};

function appPath(path) {
  return `${window.appConfig.basePath || ""}${path}`;
}

function setNotice(el, message, type = "") {
  el.textContent = message;
  el.className = `notice ${type}`.trim();
}

function formatApiDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const location = Array.isArray(item.loc) ? item.loc.slice(1).join(" -> ") : "";
        const message = item.msg || JSON.stringify(item);
        return location ? `${location}: ${message}` : message;
      }
      return String(item);
    }).join("\n");
  }
  if (typeof detail === "object") return detail.message || JSON.stringify(detail);
  return String(detail);
}

async function readApiError(response, fallbackMessage) {
  let body = null;
  try {
    body = await response.json();
  } catch {
    return fallbackMessage;
  }
  return formatApiDetail(body?.detail) || fallbackMessage;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

async function refreshStorageNote() {
  const el = document.getElementById("storage-note");
  if (!el) return;
  try {
    const response = await fetch(appPath("/health/details"));
    if (!response.ok) return;
    const { storage } = await response.json();
    if (!storage) return;
    // "0.0 GB used" tells you nothing; drop to MB below a gigabyte.
    const size = (bytes) =>
      bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
    el.textContent = `Storage: ${size(storage.used_bytes)} used, ${size(storage.free_bytes)} free`;
    // Keep .sp: it is what pushes the storage figure to the end of the footer
    // rule. Writing className wholesale used to drop it on the first poll.
    el.className = storage.over_soft_limit ? "sp footer-item warn" : "sp footer-item";
  } catch {
    // The footer is decoration; a failed poll must never surface as an error.
  }
}

document.addEventListener("DOMContentLoaded", refreshStorageNote);
