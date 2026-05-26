function wireCreateForm(dependencies) {
  const createForm = document.querySelector("#createForm");
  const emailInput = document.querySelector("#emailInput");
  const daysInput = document.querySelector("#daysInput");
  const cdkInput = document.querySelector("#cdkInput");
  const createButton = document.querySelector("#createButton");

  createForm.addEventListener("submit", (event) => handleCreate(event, {
    ...dependencies,
    emailInput,
    daysInput,
    cdkInput,
    createButton,
  }));
}

async function handleCreate(event, context) {
  event.preventDefault();
  setBusy(context.createButton, true);
  try {
    const payload = buildCreatePayload(context);
    const data = await createCdkRecords(payload, context.adminFetch);
    context.cdkInput.value = "";
    setCreateMessage(data, context.setMessage);
    await context.loadPage(1);
  } catch (error) {
    context.setMessage(error.message, true);
  } finally {
    setBusy(context.createButton, false);
  }
}

function buildCreatePayload(context) {
  const payload = {
    emails: parseEmails(context.emailInput.value),
    valid_days: Number(context.daysInput.value),
  };
  const cdk = context.cdkInput.value.trim();
  if (cdk) {
    payload.cdk = cdk;
  }
  return payload;
}

function parseEmails(value) {
  return value
    .split(/\r?\n/)
    .map((email) => email.trim())
    .filter(Boolean);
}

async function createCdkRecords(payload, adminFetch) {
  if (!payload.emails.length) {
    throw new Error("请输入至少一个邮箱");
  }
  if (payload.emails.length === 1 || payload.cdk) {
    return createSingleCdk(payload, adminFetch);
  }
  return adminFetch("/api/admin/cdks/batch", {
    method: "POST",
    body: JSON.stringify({
      emails: payload.emails,
      valid_days: payload.valid_days,
    }),
  });
}

function createSingleCdk(payload, adminFetch) {
  if (payload.emails.length !== 1) {
    throw new Error("批量导入时不能指定 CDK，请留空随机生成");
  }
  const request = {
    email: payload.emails[0],
    valid_days: payload.valid_days,
  };
  if (payload.cdk) {
    request.cdk = payload.cdk;
  }
  return adminFetch("/api/admin/cdks", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

function setCreateMessage(data, setMessage) {
  if (Array.isArray(data.items)) {
    setMessage(`已新增 ${data.items.length} 条 CDK`);
    return;
  }
  setMessage(`已新增 CDK：${data.cdk}`);
}

function setBusy(createButton, value) {
  createButton.disabled = value;
  createButton.textContent = value ? "新增中" : "新增";
}

window.cdkPlusCreate = { wireCreateForm };
