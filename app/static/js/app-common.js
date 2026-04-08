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
