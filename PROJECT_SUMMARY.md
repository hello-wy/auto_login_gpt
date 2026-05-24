# 项目总结

## 当前状态

项目已完成基础可用版本，并完成真实代理环境下的端到端验证。

当前已验证能力：

- key 批量换取邮箱和 secret
- 邮件验证码获取与重试
- Patchright 浏览器登录
- 代理 + 本地 FlareSolverr 协同
- 页面状态检查与分支处理
- ChatGPT session 提取
- Sub2API 默认导出，CPA 可通过 `--format cpa` 或 `--format both` 显式启用

## 登录流程结论

实际运行中，登录页不总是固定路径，当前实现已覆盖以下分支：

1. `email`
   首次进入邮箱页，等待页面稳定后输入邮箱
2. `password`（可选）
   某些账号会先落到密码页
3. `one_time_code`
   自动点击“使用一次性验证码登录”后回到一次性验证码流程
4. `logged_in`
   提取 `https://chatgpt.com/api/auth/session`

关键点：

- 代理环境下，邮箱页如果太早输入，容易出现“继续后仍停留当前页”
- 解决方式不是额外堆提交策略，而是给页面足够的稳定时间，并校验页面状态是否符合预期

## 关键配置

见 `config.py`：

- `PROXY`
- `FLARESOLVERR_URL`
- `FLARESOLVERR_MAX_TIMEOUT`
- `FLARESOLVERR_WAIT_SECONDS`
- `MAIL_API_TIMEOUT`
- `MAIL_SERVER_BASE_URL`（CLI / env override）
- `EMAIL_FORM_STABILIZE_SECONDS`
- `EMAIL_POST_SUBMIT_TIMEOUT_SECONDS`
- `CPA_MANAGEMENT_TIMEOUT`

其中后两项是当前代理场景最关键的调参点。

## 可选 CPA 过滤

当前支持一个可选前置过滤：

- 从 `CLIProxyAPI` 管理接口 `/v0/management/auth-files` 拉取账号列表
- 读取其中的 `email`
- 将 key 接口返回的邮箱与 CPA 中仍存活的邮箱比较
- 跳过已存在且仍存活的邮箱，再进入浏览器登录流程

当前“仍存活”的判定：

- `email` 非空
- `unavailable != true`
- `status` 不在 `error / expired / invalid / revoked / unavailable`

`disabled` 不参与过滤判定。

## 可选取件服务覆盖

当前支持一个可选前置覆盖：

- 用 `--mail-server-base-url` 或 `MAIL_SERVER_BASE_URL` 指向新的取件服务
- 支持传站点根地址，也支持直接传到 `/api/pickup`
- 程序会自动派生 `mail-keys` 和 `mail-code` 两个 endpoint
- 这个覆盖只作用于 key / 验证码取件接口，不影响浏览器代理和 FlareSolverr

## 清理策略

为避免提交敏感数据，仓库只保留代码、测试和脱敏样例。

不应保留或提交：

- 真实 key 输入文件
- 导出的 session JSON
- 运行日志
- 页面失败产物
- 浏览器 profile
- 调试截图
- 临时抓取的前端 bundle

样例输入保留为 `keys.example.txt`。

## 验证方式

静态和自动化校验：

```bash
ruff check api_client.py browser_automation.py config.py main.py test_browser_automation_helpers.py test_runtime_helpers.py
python -m unittest test_browser_automation_helpers.py test_runtime_helpers.py
```

真实链路校验：

```bash
python main.py --key "<YOUR_KEY>" --proxy "socks5://<HOST>:<PORT>"
```

成功标志：

- 日志出现 `Session extracted successfully`
- `output/` 里默认生成 Sub2API 文件

失败排查入口：

- `logs/run-*.log`
- `artifacts/*.png`
- `artifacts/*.html`
- `artifacts/*.txt`

## 后续可选项

- 增加 `--keep-open`，便于成功后保留浏览器窗口人工确认
- 将更多等待时间和分支策略外露为 CLI 参数
- 补充更完整的无代理 / 代理回归测试
