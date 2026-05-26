const PAGE_SIZE = 10;
const PASSWORD_KEY = "cdkPlusAdminPassword";

const loginPanel = document.querySelector("#loginPanel");
const adminPanel = document.querySelector("#adminPanel");
const loginForm = document.querySelector("#loginForm");
const passwordInput = document.querySelector("#passwordInput");
const loginButton = document.querySelector("#loginButton");
const reloadButton = document.querySelector("#reloadButton");
const tableBody = document.querySelector("#tableBody");
const message = document.querySelector("#message");
const totalText = document.querySelector("#totalText");
const pageText = document.querySelector("#pageText");
const prevButton = document.querySelector("#prevButton");
const nextButton = document.querySelector("#nextButton");
const switchModal = document.querySelector("#switchModal");
const switchModalCdk = document.querySelector("#switchModalCdk");
const switchCancelButton = document.querySelector("#switchCancelButton");
const switchConfirmButton = document.querySelector("#switchConfirmButton");

let currentPage = 1;
let totalItems = 0;
let pendingSwitchCdk = "";

init();

function init() {
  loginForm.addEventListener("submit", handleLogin);
  window.cdkPlusCreate.wireCreateForm({ adminFetch, loadPage, setMessage });
  reloadButton.addEventListener("click", () => loadPage(currentPage));
  prevButton.addEventListener("click", () => loadPage(currentPage - 1));
  nextButton.addEventListener("click", () => loadPage(currentPage + 1));
  switchCancelButton.addEventListener("click", closeSwitchModal);
  switchModal.addEventListener("click", closeSwitchModalFromBackdrop);
  switchConfirmButton.addEventListener("click", confirmSwitchEmail);
  restoreSession();
}

async function restoreSession() {
  const password = getPassword();
  if (!password) {
    return;
  }
  try {
    await verifyPassword(password);
    showAdminPanel();
    await loadPage(1);
  } catch (_error) {
    sessionStorage.removeItem(PASSWORD_KEY);
  }
}

async function handleLogin(event) {
  event.preventDefault();
  loginButton.disabled = true;
  loginButton.textContent = "校验中";
  try {
    const password = passwordInput.value;
    await verifyPassword(password);
    sessionStorage.setItem(PASSWORD_KEY, password);
    passwordInput.value = "";
    showAdminPanel();
    setMessage("");
    await loadPage(1);
  } catch (error) {
    setLoginMessage(error.message);
  } finally {
    loginButton.disabled = false;
    loginButton.textContent = "进入管理界面";
  }
}

async function verifyPassword(password) {
  const response = await fetch("/api/admin/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    throw new Error("管理密码错误");
  }
}

async function loadPage(page) {
  const targetPage = Math.max(1, page);
  try {
    const data = await adminFetch(`/api/admin/cdks?page=${targetPage}&page_size=${PAGE_SIZE}`);
    currentPage = data.page;
    totalItems = data.total;
    renderTable(data.items || []);
    renderPagination();
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function adminFetch(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Password": getPassword(),
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (response.status === 401) {
    sessionStorage.removeItem(PASSWORD_KEY);
    showLoginPanel();
    throw new Error("管理密码已失效，请重新登录");
  }
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
}

function renderTable(items) {
  tableBody.innerHTML = "";
  if (items.length === 0) {
    tableBody.appendChild(emptyRow());
    return;
  }
  items.forEach((item) => {
    tableBody.appendChild(recordRow(item));
  });
}

function recordRow(item) {
  const row = document.createElement("tr");
  row.className = "hover:bg-slate-50";
  row.append(
    cell(item.cdk, "font-mono font-bold text-slate-900"),
    cell(item.email, "font-medium text-slate-700"),
    cell(`${item.valid_days} 天`, "text-slate-600"),
    cell(formatTime(item.created_at), "text-slate-600"),
    cell(formatTime(item.expires_at), "text-slate-600"),
    actionCell(item.cdk),
  );
  return row;
}

function cell(text, className) {
  const node = document.createElement("td");
  node.className = `whitespace-nowrap px-4 py-3 ${className}`;
  node.textContent = text;
  return node;
}

function actionCell(cdk) {
  const node = document.createElement("td");
  node.className = "whitespace-nowrap px-4 py-3 text-right";
  const actions = document.createElement("div");
  actions.className = "inline-flex items-center gap-2";
  actions.append(switchButton(cdk), deleteButton(cdk));
  node.appendChild(actions);
  return node;
}

function switchButton(cdk) {
  const button = document.createElement("button");
  button.className = "rounded-lg bg-blue-50 px-3 py-2 text-sm font-semibold text-blue-700 transition hover:bg-blue-100";
  button.type = "button";
  button.textContent = "切换邮箱";
  button.addEventListener("click", () => switchEmail(cdk));
  return button;
}

function deleteButton(cdk) {
  const button = document.createElement("button");
  button.className = "rounded-lg bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-700 transition hover:bg-rose-100";
  button.type = "button";
  button.textContent = "删除";
  button.addEventListener("click", () => deleteCdk(cdk));
  return button;
}

function emptyRow() {
  const row = document.createElement("tr");
  const node = document.createElement("td");
  node.className = "px-4 py-8 text-center text-sm text-slate-500";
  node.colSpan = 6;
  node.textContent = "暂无 CDK";
  row.appendChild(node);
  return row;
}

async function deleteCdk(cdk) {
  if (!confirm(`确认删除 CDK：${cdk}？`)) {
    return;
  }
  try {
    const data = await adminFetch(`/api/admin/cdks/${encodeURIComponent(cdk)}`, { method: "DELETE" });
    setMessage(`已删除 CDK：${data.cdk}`);
    await loadPage(currentPage);
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function switchEmail(cdk) {
  pendingSwitchCdk = cdk;
  switchModalCdk.textContent = cdk;
  switchModal.classList.remove("hidden");
  switchModal.classList.add("flex");
  switchConfirmButton.focus();
}

function closeSwitchModal() {
  pendingSwitchCdk = "";
  switchModal.classList.add("hidden");
  switchModal.classList.remove("flex");
  switchConfirmButton.disabled = false;
  switchConfirmButton.textContent = "确认切换";
}

function closeSwitchModalFromBackdrop(event) {
  if (event.target === switchModal) {
    closeSwitchModal();
  }
}

async function confirmSwitchEmail() {
  const cdk = pendingSwitchCdk;
  if (!cdk) {
    return;
  }
  switchConfirmButton.disabled = true;
  switchConfirmButton.textContent = "切换中";
  try {
    const data = await adminFetch(`/api/admin/cdks/${encodeURIComponent(cdk)}/switch-email`, { method: "POST" });
    closeSwitchModal();
    setMessage(`已切换为 ${data.email}，并删除来源 CDK：${data.replacement_cdk}`);
    await loadPage(currentPage);
  } catch (error) {
    setMessage(error.message, true);
    closeSwitchModal();
  }
}

function renderPagination() {
  const totalPages = Math.max(1, Math.ceil(totalItems / PAGE_SIZE));
  if (currentPage > totalPages) {
    loadPage(totalPages);
    return;
  }
  totalText.textContent = `共 ${totalItems} 条记录`;
  pageText.textContent = `第 ${currentPage} / ${totalPages} 页`;
  prevButton.disabled = currentPage <= 1;
  nextButton.disabled = currentPage >= totalPages;
}

function formatTime(value) {
  if (!value) {
    return "未激活";
  }
  return new Date(value).toLocaleString();
}

function getPassword() {
  return sessionStorage.getItem(PASSWORD_KEY) || "";
}

function showAdminPanel() {
  loginPanel.classList.add("hidden");
  adminPanel.classList.remove("hidden");
}

function showLoginPanel() {
  adminPanel.classList.add("hidden");
  loginPanel.classList.remove("hidden");
}

function setLoginMessage(text) {
  const node = loginForm.querySelector("[data-login-error]") || document.createElement("p");
  node.dataset.loginError = "true";
  node.className = "min-h-5 text-sm font-medium text-rose-600";
  node.textContent = text;
  loginForm.appendChild(node);
}

function setMessage(text, isError = false) {
  message.textContent = text;
  message.className = isError ? "min-h-5 text-sm font-medium text-rose-600" : "min-h-5 text-sm text-slate-500";
}
