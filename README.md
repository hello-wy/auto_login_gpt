# ChatGPT Key to Auth Converter

将 `key code` 批量转换为 ChatGPT Web 会话 JSON，输出 `CPA` 和 `Sub2API` 两种格式。

## 前置条件

运行前必须满足以下条件：

1. 安装 Python 3.9+
2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 安装 Patchright Chrome

```bash
patchright install chrome
```

4. 启动本地 FlareSolverr

默认地址为 `http://127.0.0.1:8191/v1`。程序会在使用浏览器代理时调用它预解 Cloudflare。

5. 如果使用代理

- 代理只作用于浏览器和 FlareSolverr 链路，不作用于邮件接口请求
- 推荐格式：`http://...`、`https://...`、`socks4://...`、`socks5://...`
- 代理较慢时，可能需要调大 `config.py` 里的 `EMAIL_FORM_STABILIZE_SECONDS` 和 `EMAIL_POST_SUBMIT_TIMEOUT_SECONDS`
- 邮件接口请求超时由 `MAIL_API_TIMEOUT` 控制
- 如果 key 取件服务器不是默认地址，可以用 `--mail-server-base-url` 覆盖
- `--mail-server-base-url` 只影响 `mail-keys` / `mail-code` 两个接口，不影响浏览器代理或 FlareSolverr

6. 如果要启用 CPA 存活邮箱过滤（可选）

- 需要一个可访问的 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 管理接口
- 需要能访问 `/v0/management/auth-files` 的 Bearer Token
- 程序会先取回 CPA 里仍可用的邮箱，再和 key 接口返回的邮箱比对，跳过已存在且仍存活的账号

## 使用方法

准备你自己的输入文件，例如 `keys.txt`。仓库只保留脱敏样例 `keys.example.txt`。

### 单个 key

```bash
python main.py --key "AAAA-BBBB-CCCC"
```

### 批量处理

```bash
python main.py --input keys.txt
```

### 覆盖 key 取件服务器地址

```bash
python main.py --input keys.txt --mail-server-base-url "https://plus.keria.cc.cd"
```

也支持直接传到 `/api/pickup`：

```bash
python main.py --input keys.txt --mail-server-base-url "https://plus.keria.cc.cd/api/pickup"
```

也可以通过环境变量提供：

```bash
set MAIL_SERVER_BASE_URL=https://plus.keria.cc.cd
python main.py --input keys.txt
```

### 使用浏览器代理

```bash
python main.py --input keys.txt --proxy "socks5://127.0.0.1:1080"
```

### 跳过 CPA 已存活邮箱

```bash
python main.py --input keys.txt --skip-active-cpa-emails --cpa-management-url "http://127.0.0.1:8317" --cpa-management-key "<MANAGEMENT_TOKEN>"
```

也可以通过环境变量提供：

```bash
set CPA_MANAGEMENT_URL=http://127.0.0.1:8317
set CPA_MANAGEMENT_KEY=<MANAGEMENT_TOKEN>
python main.py --input keys.txt --skip-active-cpa-emails
```

### 常用参数

```bash
python main.py [选项]

选项:
  --key KEY
  --input FILE
  --format {cpa,sub2api,both}
  --headless
  --proxy PROXY
  --flaresolverr-url FLARESOLVERR_URL
  --mail-server-base-url MAIL_SERVER_BASE_URL
  --skip-active-cpa-emails
  --cpa-management-url CPA_MANAGEMENT_URL
  --cpa-management-key CPA_MANAGEMENT_KEY
```

## 当前已验证流程

程序会按下面的页面状态推进，并在关键节点检查当前页面是否符合预期：

1. `email`
   填邮箱前会额外等待一段时间，避免代理环境下页面尚未稳定导致空提交
2. `password`（可选）
   某些账号会先落到密码页；程序会自动点击“使用一次性验证码登录”
3. `one_time_code`
   获取最新验证码并提交
4. `logged_in`
   提取 `https://chatgpt.com/api/auth/session`

成功后会立即：

1. 导出 session
2. 写入 `output/`
3. 关闭浏览器窗口

这属于当前设计，不是异常。

## 输出文件

生成的文件位于 `./output/`：

- `{email_key}_cpa.json`
- `{email_key}_sub2api.json`

程序默认不会清空整个 `output/` 目录，只会创建目录并覆盖同名账号文件。

这些文件包含真实会话信息，默认不应提交到仓库。

## CPA 过滤规则

启用 `--skip-active-cpa-emails` 后，程序会请求 `CLIProxyAPI` 的管理接口 `/v0/management/auth-files`，读取其中的邮箱，并按下面规则认定为“仍存活”：

- 有有效 `email`
- `unavailable` 不为 `true`
- `status` 不属于 `error`、`expired`、`invalid`、`revoked`、`unavailable`

`disabled` 状态不会被额外处理。

## 环境变量

如果不想在命令行里重复输入，可以使用下面几个环境变量：

- `MAIL_SERVER_BASE_URL`
- `CPA_MANAGEMENT_URL`
- `CPA_MANAGEMENT_KEY`

## 调试产物

每次运行都会生成：

- `logs/run-YYYYMMDD-HHMMSS.log`
- 失败时的 `artifacts/*.png`
- 失败时的 `artifacts/*.html`
- 失败时的 `artifacts/*.txt`

建议排障顺序：

1. 先看控制台里的失败摘要
2. 再看对应的 `logs/run-*.log`
3. 如果是页面问题，再打开 `artifacts/` 下的截图和 HTML

## 敏感信息与清理规则

仓库默认忽略以下内容：

- `keys.txt`
- `output/`
- `logs/`
- `artifacts/`
- `browser_profile/`
- `browser_profile_probe*/`
- 调试截图和临时 bundle

如果需要分享仓库，请只保留代码、文档和脱敏样例，不要保留：

- key 输入文件
- 导出的 session JSON
- 浏览器 profile
- 调试截图

## 常见问题

### 1. 看起来登录成功了，但窗口直接关闭

这是正常行为。程序已经提取 session 并写入 `output/`，随后自动关闭浏览器。

### 2. 邮箱页点了“继续”但没有进入下一步

先看日志里的页面状态输出：

- 如果还停在 `email`，通常是代理环境下页面接管较慢
- 如果跳到 `password`，程序会自动回退到“一次性验证码登录”
- 如果长时间未到 `one_time_code`，优先调大 `config.py` 中的等待时间

### 3. FlareSolverr 预解失败

- 先确认 `http://127.0.0.1:8191/` 返回 `FlareSolverr is ready!`
- 程序会打印错误并继续尝试原始浏览器流程

### 4. 代理环境很慢

优先调整：

- `EMAIL_FORM_STABILIZE_SECONDS`
- `EMAIL_POST_SUBMIT_TIMEOUT_SECONDS`
- `FLARESOLVERR_MAX_TIMEOUT`

### 5. 登录后没有保存文件

检查控制台是否出现：

- `Session extracted successfully`
- `Saved CPA`
- `Saved Sub2API`

如果已经看到 `Session extracted successfully`，但最后仍然失败，优先看：

- `output/` 是否可写
- 运行日志里是否出现 `Failed to save ...`
- 控制台打印的绝对输出路径是否符合预期

### 6. 需要更详细的错误上下文

现在失败信息会附带：

- 当前阶段的页面状态
- 失败时保存下来的截图 / HTML / 文本状态文件路径
- 本次运行的日志文件路径

## 项目结构

```text
keytoauth/
├── api_client.py
├── browser_automation.py
├── config.py
├── keys.example.txt
├── main.py
├── README.md
├── requirements.txt
├── session_converter.py
├── test_browser_automation_helpers.py
└── test_runtime_helpers.py
```

## 验证命令

```bash
ruff check api_client.py browser_automation.py config.py main.py test_browser_automation_helpers.py test_runtime_helpers.py
python -m unittest test_browser_automation_helpers.py test_runtime_helpers.py
```

## 友情链接

- [linux.do](https://linux.do)
