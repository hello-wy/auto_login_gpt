import requests
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import unquote, urlparse, urlunparse
from patchright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from session_converter import to_email_key
from config import (
    CHATGPT_LOGIN_URL,
    CHATGPT_SESSION_API,
    CODE_INPUT_TIMEOUT,
    DIAGNOSTICS_DIR,
    LOGIN_MAX_RETRIES,
    EMAIL_FORM_STABILIZE_SECONDS,
    EMAIL_POST_SUBMIT_TIMEOUT_SECONDS,
    FLARESOLVERR_URL,
    FLARESOLVERR_MAX_TIMEOUT,
    FLARESOLVERR_WAIT_SECONDS,
)

EMAIL_INPUT_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[id*="email"]',
    'input[placeholder*="email" i]',
    'input[autocomplete="username"]',
    'input[autocomplete="email"]',
]

CODE_INPUT_SELECTORS = [
    'input[name="code"]',
    'input[type="text"][inputmode="numeric"]',
    'input[placeholder*="code" i]',
    'input[autocomplete="one-time-code"]',
    'input[id*="code"]',
]

CONTINUE_BUTTON_SELECTORS = [
    'button[type="submit"]',
    'button:has-text("Continue")',
    'button:has-text("继续")',
    'button[name="action"]',
]

OTP_FALLBACK_SELECTORS = [
    'button:has-text("使用一次性验证码登录")',
    'button:has-text("Log in with one-time code")',
    'a:has-text("使用一次性验证码登录")',
    'a:has-text("Log in with one-time code")',
]

VERIFY_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button:has-text("Continue")',
    'button:has-text("继续")',
    'button:has-text("Verify")',
    'button:has-text("验证")',
]


def build_browser_proxy_settings(proxy: Optional[str]) -> Optional[Dict[str, str]]:
    """Normalize a proxy string for Chromium and FlareSolverr."""
    if not proxy:
        return None

    parsed = urlparse(proxy)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        raise ValueError("Proxy must include scheme, host, and port, e.g. socks5://127.0.0.1:1080")

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "socks4", "socks5", "socks5h"}:
        raise ValueError(f"Unsupported proxy scheme: {parsed.scheme}")

    if scheme == "socks5h":
        print("  ⚠ Chromium and FlareSolverr use socks5://, normalizing socks5h:// to socks5://")
        scheme = "socks5"

    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    server = urlunparse((scheme, f"{hostname}:{parsed.port}", "", "", "", ""))
    settings = {"server": server}

    if parsed.username:
        settings["username"] = unquote(parsed.username)
    if parsed.password:
        settings["password"] = unquote(parsed.password)

    return settings


def resolve_project_path(path: str) -> Path:
    """Resolve project-relative runtime directories from this repository root."""
    path_obj = Path(path)
    if path_obj.is_absolute():
        return path_obj
    return Path(__file__).resolve().parent / path_obj


def build_flaresolverr_proxy(proxy_settings: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Convert Chromium proxy settings to FlareSolverr session payload."""
    if not proxy_settings:
        return None

    flaresolverr_proxy = {"url": proxy_settings["server"]}
    if proxy_settings.get("username"):
        flaresolverr_proxy["username"] = proxy_settings["username"]
    if proxy_settings.get("password"):
        flaresolverr_proxy["password"] = proxy_settings["password"]
    return flaresolverr_proxy


def post_flaresolverr_command(flaresolverr_url: str, payload: Dict, timeout_seconds: float) -> Dict:
    """Send a command to FlareSolverr and validate the response."""
    response = requests.post(flaresolverr_url, json=payload, timeout=timeout_seconds)
    response.raise_for_status()

    data = response.json()
    if data.get("status") != "ok":
        raise Exception(data.get("message", "FlareSolverr returned an unknown error"))

    return data


def solve_cloudflare_with_flaresolverr(
    url: str,
    flaresolverr_url: str,
    proxy_settings: Optional[Dict[str, str]] = None,
) -> Dict:
    """Use FlareSolverr to pre-solve Cloudflare and fetch cookies/user-agent."""
    session_id = None

    try:
        if proxy_settings:
            session_id = f"keytoauth-{uuid.uuid4().hex}"
            post_flaresolverr_command(
                flaresolverr_url,
                {
                    "cmd": "sessions.create",
                    "session": session_id,
                    "proxy": build_flaresolverr_proxy(proxy_settings),
                },
                timeout_seconds=15,
            )

        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": FLARESOLVERR_MAX_TIMEOUT,
            "waitInSeconds": FLARESOLVERR_WAIT_SECONDS,
        }
        if session_id:
            payload["session"] = session_id

        response = post_flaresolverr_command(
            flaresolverr_url,
            payload,
            timeout_seconds=(FLARESOLVERR_MAX_TIMEOUT / 1000) + 15,
        )
        return response.get("solution", {})
    finally:
        if session_id:
            try:
                post_flaresolverr_command(
                    flaresolverr_url,
                    {"cmd": "sessions.destroy", "session": session_id},
                    timeout_seconds=15,
                )
            except Exception as destroy_error:
                print(f"  ⚠ Failed to destroy FlareSolverr session: {destroy_error}")


def apply_flaresolverr_solution(context, solution: Dict) -> int:
    """Import FlareSolverr cookies into the browser context."""
    cookies = []
    for raw_cookie in solution.get("cookies", []):
        cookie = {
            key: raw_cookie[key]
            for key in ("name", "value", "domain", "path", "httpOnly", "secure")
            if key in raw_cookie
        }

        if raw_cookie.get("sameSite") in {"Strict", "Lax", "None"}:
            cookie["sameSite"] = raw_cookie["sameSite"]

        expires = raw_cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = expires

        if all(key in cookie for key in ("name", "value", "domain", "path")):
            cookies.append(cookie)

    if cookies:
        context.add_cookies(cookies)

    return len(cookies)


def reset_browser_profile(profile_dir: str) -> None:
    """Ensure each run starts from a clean persistent browser profile."""
    profile_path = Path(profile_dir).resolve()

    if profile_path.parent == profile_path:
        raise ValueError(f"Refusing to reset invalid browser profile path: {profile_path}")

    profile_path.parent.mkdir(parents=True, exist_ok=True)

    if profile_path.exists():
        print(f"  🧹 Resetting browser profile: {profile_path}")
        shutil.rmtree(profile_path)

    profile_path.mkdir(parents=True, exist_ok=True)


def capture_debug_artifacts(page, email: str, stage: str) -> Dict[str, str]:
    """Persist screenshot, DOM, and compact state to aid post-failure debugging."""
    diagnostics_root = resolve_project_path(DIAGNOSTICS_DIR)
    diagnostics_root.mkdir(parents=True, exist_ok=True)

    artifact_prefix = diagnostics_root / f"{time.strftime('%Y%m%d-%H%M%S')}_{to_email_key(email)}_{stage}"
    artifacts = {}

    try:
        screenshot_path = artifact_prefix.with_suffix(".png")
        page.screenshot(path=str(screenshot_path), full_page=True)
        artifacts["screenshot"] = str(screenshot_path)
    except Exception as error:
        artifacts["screenshot_error"] = str(error)

    try:
        html_path = artifact_prefix.with_suffix(".html")
        html_path.write_text(page.content(), encoding="utf-8")
        artifacts["html"] = str(html_path)
    except Exception as error:
        artifacts["html_error"] = str(error)

    try:
        snapshot = get_login_flow_snapshot(page)
        state_path = artifact_prefix.with_suffix(".txt")
        state_path.write_text(
            f"state={snapshot['state']}\nurl={snapshot['url']}\nbody={snapshot['text']}\n",
            encoding="utf-8",
        )
        artifacts["state"] = str(state_path)
    except Exception as error:
        artifacts["state_error"] = str(error)

    return artifacts


def format_artifact_summary(artifacts: Dict[str, str]) -> str:
    """Format artifact paths for human-readable error messages."""
    if not artifacts:
        return "no artifacts captured"

    parts = []
    for label, value in artifacts.items():
        parts.append(f"{label}={value}")
    return ", ".join(parts)


def wait_for_first_visible_selector(page, selectors, timeout_ms: int) -> Optional[str]:
    """Return the first visible selector from a candidate list."""
    deadline = time.time() + (timeout_ms / 1000)

    while time.time() < deadline:
        for selector in selectors:
            try:
                page.locator(selector).first.wait_for(state="visible", timeout=500)
                return selector
            except PlaywrightTimeoutError:
                continue
        time.sleep(0.2)

    return None


def collect_email_step_diagnostics(page, email_input_selector: str) -> str:
    """Capture compact diagnostics when the email step does not advance."""
    try:
        diagnostics = page.evaluate(
            """(emailSelector) => {
                const emailInput = document.querySelector(emailSelector);
                const visibleButtons = Array.from(document.querySelectorAll("button"))
                    .filter((button) => {
                        const style = window.getComputedStyle(button);
                        const rect = button.getBoundingClientRect();
                        return style.visibility !== "hidden" &&
                            style.display !== "none" &&
                            rect.width > 0 &&
                            rect.height > 0;
                    })
                    .slice(0, 8)
                    .map((button) => ({
                        text: (button.innerText || button.textContent || "").trim(),
                        type: button.getAttribute("type"),
                        disabled: button.disabled,
                        ariaDisabled: button.getAttribute("aria-disabled"),
                    }));

                return {
                    url: location.href,
                    emailValue: emailInput ? emailInput.value : null,
                    activeElement: document.activeElement ? document.activeElement.tagName : null,
                    emailInputs: Array.from(document.querySelectorAll('input[type="email"], input[name="email"], input[autocomplete="username"], input[autocomplete="email"]'))
                        .slice(0, 8)
                        .map((input) => ({
                            value: input.value,
                            name: input.getAttribute("name"),
                            type: input.getAttribute("type"),
                            autocomplete: input.getAttribute("autocomplete"),
                        })),
                    buttons: visibleButtons,
                    bodyText: (document.body.innerText || "").slice(0, 500),
                };
            }""",
            email_input_selector,
        )
        return str(diagnostics)
    except Exception as diagnostics_error:
        return f"Failed to collect diagnostics: {diagnostics_error}"


def get_login_flow_snapshot(page) -> Dict[str, str]:
    """Return a compact snapshot of the current login flow state."""
    return page.evaluate(
        """() => {
            const text = document.body && document.body.innerText ? document.body.innerText : "";
            const has = (selector) => !!document.querySelector(selector);
            const url = location.href;

            let state = "unknown";
            if (
                text.includes("正在验证") ||
                text.includes("Verifying") ||
                text.includes("请稍候") ||
                text.includes("Cloudflare") ||
                text.includes("Checking your browser")
            ) {
                state = "cloudflare";
            } else if (
                url.includes("auth.openai.com/log-in/password") ||
                has('input[type="password"]') ||
                text.includes("输入密码") ||
                text.includes("Enter your password")
            ) {
                state = "password";
            } else if (
                has('input[name="code"]') ||
                has('input[autocomplete="one-time-code"]') ||
                text.includes("一次性验证码") ||
                text.includes("one-time code") ||
                text.includes("验证码")
            ) {
                state = "one_time_code";
            } else if (
                has('input[type="email"]') ||
                has('input[name="email"]')
            ) {
                state = "email";
            } else if (
                url.includes("chatgpt.com") &&
                !url.includes("/auth/")
            ) {
                state = "logged_in";
            }

            return {
                state,
                url,
                text: text.slice(0, 500),
            };
        }"""
    )


def assert_login_flow_state(page, expected_states, step_name: str) -> Dict[str, str]:
    """Ensure the current page is in one of the expected login-flow states."""
    snapshot = get_login_flow_snapshot(page)
    if snapshot["state"] not in expected_states:
        raise Exception(
            f"Unexpected page state during {step_name}: "
            f"expected {expected_states}, got {snapshot['state']} @ {snapshot['url']} "
            f"body={snapshot['text']!r}"
        )
    return snapshot


def populate_input_with_verification(page, input_locator, value: str, label: str) -> None:
    """Populate an input and verify the DOM keeps the expected value."""
    print(f"  → Populating {label}...")
    input_locator.click()
    input_locator.press("Control+A")
    input_locator.press("Backspace")
    input_locator.type(value, delay=80)
    input_locator.evaluate("(input) => input.blur()")

    current_value = input_locator.input_value(timeout=5000)
    print(f"  ↳ {label} input now: {current_value!r}")
    if current_value == value:
        return

    input_locator.fill(value)
    current_value = input_locator.input_value(timeout=5000)
    print(f"  ↳ {label} input after fill fallback: {current_value!r}")
    if current_value == value:
        return

    raise Exception(f"Failed to populate {label} input with the expected value")


def submit_email_and_wait_for_code(page, email_input_selector: str, email: str) -> str:
    """Submit the email step and wait until the verification code input is visible."""
    continue_selector = wait_for_first_visible_selector(page, CONTINUE_BUTTON_SELECTORS, timeout_ms=10000)
    if not continue_selector:
        raise Exception("Could not find continue button on email step")

    continue_locator = page.locator(continue_selector).first
    button_text = continue_locator.inner_text(timeout=5000).strip()
    is_enabled = continue_locator.is_enabled(timeout=5000)
    print(f"  ✓ Found continue button: {continue_selector} (text={button_text!r}, enabled={is_enabled})")

    form_locator = continue_locator.locator("xpath=ancestor::form[1]")
    email_locator = form_locator.locator('input[type="email"]').first
    try:
        email_locator.wait_for(state="visible", timeout=3000)
        print("  ✓ Using form-scoped email input")
    except PlaywrightTimeoutError:
        email_locator = page.locator(email_input_selector).first
        print(f"  ⚠ Falling back to page-scoped email input: {email_input_selector}")

    snapshot = assert_login_flow_state(page, {"email"}, "email step before filling")
    print(f"  📍 Email step confirmed: {snapshot['state']} @ {snapshot['url']}")

    print(f"  → Waiting {EMAIL_FORM_STABILIZE_SECONDS} seconds for login form to stabilize...")
    time.sleep(EMAIL_FORM_STABILIZE_SECONDS)

    print(f"  → Filling email: {email}")
    populate_input_with_verification(page, email_locator, email, "email")
    time.sleep(0.5)

    print("  → Clicking continue button...")
    continue_locator.click(timeout=5000, no_wait_after=True)
    otp_fallback_clicked = False
    wait_deadline = time.time() + EMAIL_POST_SUBMIT_TIMEOUT_SECONDS
    last_state = None

    while time.time() < wait_deadline:
        snapshot = get_login_flow_snapshot(page)
        if snapshot["state"] != last_state:
            print(f"  📍 Flow state after email submit: {snapshot['state']} @ {snapshot['url']}")
            last_state = snapshot["state"]

        code_input_selector = wait_for_first_visible_selector(page, CODE_INPUT_SELECTORS, timeout_ms=1000)
        if code_input_selector:
            print(f"  ✓ Found code input: {code_input_selector}")
            return code_input_selector

        if snapshot["state"] == "password" and not otp_fallback_clicked:
            otp_fallback_selector = wait_for_first_visible_selector(page, OTP_FALLBACK_SELECTORS, timeout_ms=1000)
            if otp_fallback_selector:
                print(f"  → Password page detected, clicking OTP fallback: {otp_fallback_selector}")
                page.locator(otp_fallback_selector).first.click(timeout=5000, no_wait_after=True)
                otp_fallback_clicked = True
                continue

        time.sleep(1)

    diagnostics = collect_email_step_diagnostics(page, email_input_selector)
    artifacts = capture_debug_artifacts(page, email, "email-submit-timeout")
    raise Exception(
        "Email submit did not reach the verification-code page within "
        f"{EMAIL_POST_SUBMIT_TIMEOUT_SECONDS}s. "
        "If you are using a slow proxy, increase EMAIL_FORM_STABILIZE_SECONDS or "
        "EMAIL_POST_SUBMIT_TIMEOUT_SECONDS in config.py. "
        f"Diagnostics: {diagnostics}. "
        f"Artifacts: {format_artifact_summary(artifacts)}"
    )


def get_fresh_verification_code(cloudmail_client, email: str, max_attempts: int) -> str:
    if cloudmail_client is None:
        raise ValueError("CloudMail client is required to fetch verification codes")
    return cloudmail_client.fetch_verification_code(email, max_attempts=max_attempts)


def login_chatgpt(
    email: str,
    verification_code: str,
    profile_dir: str,
    headless: bool = False,
    cloudmail_client=None,
    proxy: str = None,
    flaresolverr_url: str = FLARESOLVERR_URL,
) -> Dict:
    """
    Automate ChatGPT login and extract session.

    Args:
        email: Email address
        verification_code: 6-digit verification code
        profile_dir: Browser profile directory
        headless: Run in headless mode
        cloudmail_client: CloudMail client for fetching fresh one-time codes
        proxy: Browser proxy server (e.g., socks5://127.0.0.1:1080)
        flaresolverr_url: FlareSolverr API endpoint

    Returns:
        Session JSON dict

    Raises:
        Exception if login fails
    """
    print(f"🌐 Starting browser automation for {email}")
    proxy_settings = build_browser_proxy_settings(proxy)
    flaresolverr_solution = None

    if proxy_settings and flaresolverr_url:
        try:
            print(f"  → Solving Cloudflare with FlareSolverr: {flaresolverr_url}")
            flaresolverr_solution = solve_cloudflare_with_flaresolverr(
                CHATGPT_LOGIN_URL,
                flaresolverr_url,
                proxy_settings,
            )
            print(f"  ✓ FlareSolverr returned {len(flaresolverr_solution.get('cookies', []))} cookies")
        except Exception as flaresolverr_error:
            print(f"  ⚠ FlareSolverr pre-solve failed: {flaresolverr_error}")

    reset_browser_profile(profile_dir)

    with sync_playwright() as p:
        launch_options = {
            "user_data_dir": profile_dir,
            "channel": "chrome",
            "headless": headless,
            "no_viewport": True
        }

        # Add proxy if provided
        if proxy_settings:
            launch_options["proxy"] = proxy_settings
            print(f"  🔒 Using proxy: {proxy_settings['server']}")

        if flaresolverr_solution and flaresolverr_solution.get("userAgent"):
            launch_options["user_agent"] = flaresolverr_solution["userAgent"]
            print(f"  🪪 Using FlareSolverr user-agent: {launch_options['user_agent']}")

        context = p.chromium.launch_persistent_context(**launch_options)

        if flaresolverr_solution:
            imported_cookie_count = apply_flaresolverr_solution(context, flaresolverr_solution)
            if imported_cookie_count:
                print(f"  🍪 Imported {imported_cookie_count} FlareSolverr cookies into the browser context")

        page = context.pages[0] if context.pages else context.new_page()

        try:
            # Navigate to login page
            print(f"  → Navigating to {CHATGPT_LOGIN_URL}")
            page.goto(CHATGPT_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

            # Wait for Cloudflare verification to complete
            print("  → Waiting for Cloudflare verification (this may take 10-30 seconds)...")

            # Wait for Cloudflare challenge to disappear
            max_wait = 45  # Increased from 30
            for i in range(max_wait):
                # Check if Cloudflare is still present
                cloudflare_present = page.evaluate("""() => {
                    const text = document.body.innerText;
                    return text.includes('正在验证') ||
                           text.includes('Verifying') ||
                           text.includes('请稍候') ||
                           text.includes('Cloudflare') ||
                           text.includes('Checking your browser');
                }""")

                if not cloudflare_present:
                    print("  ✓ Cloudflare verification completed")
                    break

                if i % 5 == 0 and i > 0:
                    print(f"  ⏳ Still waiting... ({i}s)")
                time.sleep(1)

            # Additional wait for page to stabilize
            time.sleep(5)  # Increased from 3

            # Check if already logged in (redirected to main page)
            current_url = page.url
            print(f"  📍 Current URL: {current_url}")
            snapshot = assert_login_flow_state(page, {"email", "logged_in"}, "post-cloudflare landing")
            print(f"  📍 Landing page confirmed: {snapshot['state']} @ {snapshot['url']}")

            if snapshot["state"] == "logged_in":
                print("  ✓ Already logged in! Redirected to main page")
                session = extract_session(page)
                if session:
                    print("  ✓ Login successful!")
                    return session
                else:
                    raise Exception("Redirected to main page but could not extract session")

            # Try multiple selectors for email input
            email_input = None
            print("  → Looking for email input field...")
            for selector in EMAIL_INPUT_SELECTORS:
                try:
                    page.wait_for_selector(selector, timeout=10000, state="visible")
                    email_input = selector
                    print(f"  ✓ Found email input: {selector}")
                    break
                except PlaywrightTimeoutError:
                    continue

            if not email_input:
                artifacts = capture_debug_artifacts(page, email, "email-input-missing")
                raise Exception(
                    "Could not find email input field after Cloudflare verification. "
                    f"Artifacts: {format_artifact_summary(artifacts)}"
                )

            code_input = submit_email_and_wait_for_code(page, email_input, email)

            # IMPORTANT: Wait 5 seconds for page to stabilize, then fetch fresh code
            print("  → Waiting 5 seconds for page to stabilize...")
            time.sleep(5)

            # Now fetch the verification code (this will be the latest one)
            print("  → Fetching fresh verification code...")
            verification_code = get_fresh_verification_code(
                cloudmail_client,
                email,
                max_attempts=3,
            )
            print(f"  ✓ Got fresh code: {verification_code}")

            # Login with retry logic (user's "重复2" requirement)
            session = login_with_retry(
                page,
                code_input,
                verification_code,
                email,
                cloudmail_client,
            )

            print("  ✓ Login successful!")
            return session

        except Exception as e:
            artifacts = capture_debug_artifacts(page, email, "login-failed")
            raise Exception(f"Login failed: {e}. Artifacts: {format_artifact_summary(artifacts)}")
        finally:
            context.close()


def login_with_retry(
    page,
    code_input_selector: str,
    verification_code: str,
    email: str,
    cloudmail_client,
) -> Dict:
    """
    Attempt login with retry logic.
    Important: Need to wait 3 seconds for code input page to load, then refresh to get new code and re-enter.

    Args:
        page: Playwright page object
        code_input_selector: CSS selector for code input
        verification_code: 6-digit code
        email: Email address
        cloudmail_client: CloudMail client for fetching new codes

    Returns:
        Session JSON dict
    """
    for attempt in range(LOGIN_MAX_RETRIES + 1):
        try:
            if attempt > 0:
                print(f"  ⟳ Retry attempt {attempt}/{LOGIN_MAX_RETRIES}")

                # Wait 3 seconds for the code input page to fully load
                print("  → Waiting 3 seconds for page to stabilize...")
                time.sleep(3)

                # Now fetch fresh verification code (this triggers a new email)
                print("  → Fetching fresh verification code...")
                verification_code = get_fresh_verification_code(
                    cloudmail_client,
                    email,
                    max_attempts=2,
                )
                print(f"  ✓ Got new code: {verification_code}")

                if page.is_closed():
                    raise Exception("Verification page was closed before retrying the code submission")

                print("  → Re-checking verification page...")
                refreshed_code_input = wait_for_first_visible_selector(page, CODE_INPUT_SELECTORS, timeout_ms=CODE_INPUT_TIMEOUT)
                if not refreshed_code_input:
                    snapshot = get_login_flow_snapshot(page)
                    raise Exception(
                        "Could not find verification code input for retry "
                        f"(state={snapshot['state']} @ {snapshot['url']})"
                    )
                code_input_selector = refreshed_code_input

            snapshot = assert_login_flow_state(page, {"one_time_code"}, "verification code entry")
            print(f"  📍 Verification page confirmed: {snapshot['state']} @ {snapshot['url']}")

            # Fill verification code
            print(f"  → Entering verification code: {verification_code}")
            populate_input_with_verification(page, page.locator(code_input_selector).first, verification_code, "verification code")

            # Submit
            submit_selector = wait_for_first_visible_selector(page, VERIFY_SUBMIT_SELECTORS, timeout_ms=5000)
            if not submit_selector:
                raise Exception("Could not find verification submit button")
            page.click(submit_selector, timeout=5000, no_wait_after=True)
            print(f"  ✓ Clicked submit: {submit_selector}")

            # Wait for successful login - check for redirect to main page
            print("  → Waiting for login to complete...")

            # Wait for redirect away from auth page
            max_redirect_wait = 20
            for i in range(max_redirect_wait):
                current_url = page.url
                if i == 0:
                    print(f"  📍 Current URL after submit: {current_url}")

                # Check if redirected to main page (login success)
                if "chatgpt.com" in current_url and "/auth/" not in current_url:
                    print("  ✓ Login successful! Redirected to main page")
                    break

                if i > 0 and i % 5 == 0:
                    print(f"  ⏳ Still waiting for redirect... ({i}s)")

                time.sleep(1)

            # Additional wait for page to stabilize
            time.sleep(3)

            # Try to extract session
            session = extract_session(page)
            if session:
                return session

            # If no session yet, wait a bit more
            print("  → Still waiting for session...")
            time.sleep(5)
            session = extract_session(page)
            if session:
                return session

            raise Exception("Login appeared to succeed but could not extract session")

        except Exception as e:
            if attempt < LOGIN_MAX_RETRIES:
                print(f"  ⚠ Attempt {attempt + 1} failed: {e}")
                continue
            else:
                raise


def extract_session(page) -> Dict:
    """
    Extract session JSON from ChatGPT.

    Args:
        page: Playwright page object

    Returns:
        Session JSON dict
    """
    try:
        # Try to fetch session API
        print(f"  → Extracting session from {CHATGPT_SESSION_API}")

        session_json = page.evaluate("""async () => {
            try {
                const response = await fetch('https://chatgpt.com/api/auth/session', {
                    credentials: 'include'
                });
                return await response.json();
            } catch (e) {
                return null;
            }
        }""")

        if session_json and session_json.get("user"):
            print("  ✓ Session extracted successfully")
            return session_json
        else:
            # Try navigating directly
            page.goto(CHATGPT_SESSION_API, timeout=30000)
            time.sleep(2)

            # Get page content as JSON
            content = page.content()
            if "accessToken" in content or "user" in content:
                import json
                # Extract JSON from page
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    session_json = json.loads(content[start:end])
                    if session_json.get("user"):
                        print("  ✓ Session extracted from page content")
                        return session_json

            return None

    except Exception as e:
        print(f"  ⚠ Session extraction error: {e}")
        return None
