const DEFAULT_REFRESH_SECONDS = 30;
const SECOND_MS = 1000;

const input = document.querySelector("#cdkInput");
const results = document.querySelector("#results");
const resultsPanel = document.querySelector("#resultsPanel");
const message = document.querySelector("#message");
const refreshText = document.querySelector("#refreshText");
const progressBar = document.querySelector("#progressBar");
const refreshButton = document.querySelector("#refreshButton");
const queryButton = document.querySelector("#queryButton");
const clock = document.querySelector("#clock");

let refreshSeconds = DEFAULT_REFRESH_SECONDS;
let remainingSeconds = DEFAULT_REFRESH_SECONDS;
let hasQueried = false;
let isLoading = false;

init();

async function init() {
  await loadConfig();
  queryButton.addEventListener("click", queryNow);
  refreshButton.addEventListener("click", refreshNow);
  input.addEventListener("input", handleInputChange);
  updateClock();
  updateProgress();
  setInterval(tick, SECOND_MS);
  setInterval(updateClock, SECOND_MS);
}

async function loadConfig() {
  const response = await fetch("/api/config");
  if (!response.ok) {
    throw new Error("Failed to load app config");
  }
  const payload = await response.json();
  refreshSeconds = Number(payload.refresh_seconds) || DEFAULT_REFRESH_SECONDS;
  remainingSeconds = refreshSeconds;
}

async function tick() {
  if (!hasQueried || isLoading) {
    return;
  }
  remainingSeconds -= 1;
  if (remainingSeconds <= 0) {
    await refreshNow();
    return;
  }
  updateProgress();
}

async function queryNow() {
  if (readCdks().length === 0) {
    hasQueried = false;
    hideResultsPanel();
    renderEmpty();
    setMessage("请输入 CDK，获取邮箱验证码", true);
    return;
  }
  hasQueried = true;
  showResultsPanel();
  await refreshNow();
}

async function refreshNow() {
  const cdks = readCdks();
  remainingSeconds = refreshSeconds;
  updateProgress();
  if (cdks.length === 0) {
    renderEmpty();
    hideResultsPanel();
    hasQueried = false;
    setMessage("请输入 CDK，获取邮箱验证码", true);
    return;
  }
  await resolveCodes(cdks);
}

async function resolveCodes(cdks) {
  setLoading(true);
  try {
    const response = await fetch("/api/codes/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cdks }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "查询失败");
    }
    renderResults(payload.items || []);
    setMessage(`已查询 ${cdks.length} 个 CDK`);
  } catch (error) {
    renderEmpty();
    setMessage(error.message, true);
  } finally {
    setLoading(false);
  }
}

function readCdks() {
  return input.value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function renderResults(items) {
  results.innerHTML = "";
  if (items.length === 0) {
    renderEmpty();
    return;
  }
  items.forEach((item, index) => {
    results.appendChild(renderRow(item, index));
  });
}

function renderRow(item, index) {
  const row = document.createElement("div");
  row.className =
    "grid min-h-20 grid-cols-[2rem_minmax(0,1fr)] items-center gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm shadow-slate-200/70 sm:grid-cols-[2rem_minmax(0,1fr)_minmax(7rem,auto)_5.5rem]";
  row.appendChild(makeIndex(index));
  row.appendChild(makeIdentity(item));
  row.appendChild(makeCode(item));
  row.appendChild(makeCopyButton(item));
  return row;
}

function makeIndex(index) {
  const node = document.createElement("span");
  node.className = "grid h-6 w-6 place-items-center rounded-full bg-emerald-500 text-xs font-extrabold text-white";
  node.textContent = String(index + 1);
  return node;
}

function makeIdentity(item) {
  const wrap = document.createElement("div");
  wrap.className = "min-w-0";
  const email = document.createElement("div");
  const status = document.createElement("div");
  email.className = "truncate text-sm font-bold text-slate-700";
  status.className = "mt-1 line-clamp-2 text-xs leading-5 text-slate-500";
  email.textContent = item.email || item.cdk;
  status.textContent = displayMessage(item);
  wrap.append(email, status);
  return wrap;
}

function makeCode(item) {
  const node = document.createElement("div");
  node.className = codeClasses(item);
  node.textContent = item.code || statusText(item.status);
  return node;
}

function makeCopyButton(item) {
  const button = document.createElement("button");
  button.className =
    "col-start-2 inline-flex min-h-10 items-center justify-center rounded-lg bg-emerald-50 px-4 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100 focus:outline-none focus:ring-4 focus:ring-emerald-100 disabled:cursor-not-allowed disabled:opacity-50 sm:col-start-auto";
  button.type = "button";
  button.textContent = "复制";
  button.disabled = !item.code;
  button.addEventListener("click", () => copyCode(item.code, button));
  return button;
}

async function copyCode(code, button) {
  if (!code) {
    return;
  }
  await navigator.clipboard.writeText(code);
  button.textContent = "已复制";
  setTimeout(() => {
    button.textContent = "复制";
  }, 1200);
}

function codeClasses(item) {
  const base =
    "col-start-2 min-w-28 rounded-lg border px-4 py-3 text-center text-sm font-bold sm:col-start-auto";
  if (item.code) {
    return `${base} border-emerald-200 bg-emerald-50 font-mono text-lg tracking-[0.28em] text-slate-950`;
  }
  if (item.status === "error" || item.status === "not_found" || item.status === "expired") {
    return `${base} border-rose-200 bg-rose-50 text-rose-700`;
  }
  return `${base} border-amber-200 bg-amber-50 text-amber-700`;
}

function statusText(status) {
  const labels = {
    ok: "已获取",
    empty: "暂无验证码",
    error: "查询错误",
    not_found: "CDK 不存在",
    expired: "CDK 已过期",
  };
  return labels[status] || status || "未知状态";
}

function displayMessage(item) {
  if (item.status === "expired") {
    return "CDK 已过期";
  }
  if (item.status === "not_found") {
    return "CDK 不存在";
  }
  if (item.status === "empty") {
    return "暂无验证码";
  }
  return item.error || statusText(item.status);
}

function renderEmpty() {
  results.innerHTML = "";
}

function showResultsPanel() {
  resultsPanel.classList.remove("hidden");
}

function hideResultsPanel() {
  resultsPanel.classList.add("hidden");
}

function setLoading(value) {
  isLoading = value;
  refreshButton.disabled = value;
  queryButton.disabled = value;
  refreshButton.textContent = value ? "查询中" : "刷新";
  queryButton.textContent = value ? "查询中" : "查询验证码";
}

function handleInputChange() {
  remainingSeconds = refreshSeconds;
  renderEmpty();
  hideResultsPanel();
  hasQueried = false;
  setMessage("");
  updateProgress();
}

function updateProgress() {
  const width = Math.max(0, Math.min(100, (remainingSeconds / refreshSeconds) * 100));
  refreshText.textContent = `${remainingSeconds} 秒后自动刷新`;
  progressBar.style.width = `${width}%`;
}

function updateClock() {
  clock.textContent = new Date().toLocaleString();
}

function setMessage(text, isError = false) {
  message.textContent = text;
  message.className = isError ? "min-h-5 text-sm font-medium text-rose-600" : "min-h-5 text-sm text-slate-500";
}
