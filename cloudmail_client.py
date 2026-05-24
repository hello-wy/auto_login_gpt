import json
import re
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Union
from urllib.parse import urlparse

import requests


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
CODE_PATTERNS = (
    re.compile(r"(?:代码为|验证码[^0-9]*?)[\s:：]*(\d{6})", re.IGNORECASE),
    re.compile(r"(?:log-?in\s+code|enter\s+this\s+code)[^0-9]{0,24}(\d{6})", re.IGNORECASE),
    re.compile(r"code(?:\s+is|[\s:：])+(\d{6})", re.IGNORECASE),
    re.compile(r"\b(\d{6})\b"),
)


@dataclass(frozen=True)
class CloudMailConfig:
    api_base_url: str
    admin_email: str
    admin_password: str
    domain: str


def normalize_cloudmail_base_url(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    candidate = value if re.match(r"^[a-zA-Z][a-zA-Z\d+\-.]*://", value) else f"https://{value}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if any(char.isspace() for char in parsed.netloc):
        return ""
    path = parsed.path.rstrip("/")
    if path == "/":
        path = ""
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def normalize_cloudmail_domain(raw_value: str) -> str:
    value = str(raw_value or "").strip().lower()
    value = value.removeprefix("@")
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"/.*$", "", value)
    return value if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", value) else ""


def normalize_cloudmail_email(raw_value: str) -> str:
    value = str(raw_value or "").strip().lower()
    return value if EMAIL_PATTERN.match(value) else ""


def normalize_email_lines(raw_text: str, allowed_domain: str = "") -> List[str]:
    domain = normalize_cloudmail_domain(allowed_domain)
    emails = []
    for line_number, raw_line in enumerate(str(raw_text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        email = normalize_cloudmail_email(line)
        if not email:
            raise ValueError(f"Invalid email input on line {line_number}: {line}")
        if domain and not email.endswith(f"@{domain}"):
            raise ValueError(f"Invalid email on line {line_number}: expected domain {domain}")
        emails.append(email)
    if not emails:
        raise ValueError("Email input is empty")
    return emails


def load_cloudmail_config(source: Union[str, Dict]) -> CloudMailConfig:
    payload = _load_config_payload(source)
    config = CloudMailConfig(
        api_base_url=normalize_cloudmail_base_url(payload.get("api_base_url") or payload.get("base_url")),
        admin_email=normalize_cloudmail_email(payload.get("admin_email")),
        admin_password=str(payload.get("admin_password") or ""),
        domain=normalize_cloudmail_domain(payload.get("domain")),
    )
    _validate_cloudmail_config(config)
    return config


def extract_verification_code(subject: str, body: str) -> str:
    source = f"{subject or ''}\n{strip_html(body or '')}"
    for pattern in CODE_PATTERNS:
        match = pattern.search(source)
        if match:
            return match.group(1)
    return ""


def strip_html(value: str) -> str:
    without_blocks = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", str(value or ""), flags=re.IGNORECASE)
    without_tags = re.sub(r"<[^>]+>", " ", without_blocks)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


class CloudMailClient:
    def __init__(
        self,
        config: CloudMailConfig,
        timeout_seconds: int = 30,
        poll_interval_seconds: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.sleep = sleep
        self._token = ""

    def fetch_verification_code(self, email: str, max_attempts: int = 3) -> str:
        target_email = self._normalize_target_email(email)
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                code = self._fetch_code_once(target_email)
                if code:
                    return code
                last_error = RuntimeError(f"No CloudMail verification code for {target_email} on attempt {attempt}")
            except Exception as error:
                last_error = error
            if attempt < max_attempts:
                self.sleep(self.poll_interval_seconds * attempt)
        raise RuntimeError(f"Failed to fetch CloudMail verification code for {target_email}: {last_error}")

    def _fetch_code_once(self, email: str) -> str:
        messages = self.list_messages(email)
        for message in messages:
            code = extract_verification_code(message.get("subject", ""), message.get("body", ""))
            if code:
                return code
        return ""

    def list_messages(self, email: str, limit: int = 20) -> List[Dict[str, str]]:
        payload = {
            "toEmail": email,
            "type": 0,
            "isDel": 0,
            "timeSort": "desc",
            "num": 1,
            "size": limit,
        }
        response = self._post_json("/api/public/emailList", payload, require_token=True)
        return normalize_cloudmail_messages(response, email)

    def ensure_token(self) -> str:
        if self._token:
            return self._token
        response = self._post_json(
            "/api/public/genToken",
            {"email": self.config.admin_email, "password": self.config.admin_password},
            require_token=False,
        )
        token = first_non_empty([response.get("token"), response.get("accessToken"), response.get("data", {}).get("token")])
        if not token:
            raise RuntimeError("CloudMail did not return a token")
        self._token = token
        return token

    def _post_json(self, path: str, payload: Dict, require_token: bool):
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if require_token:
            headers["Authorization"] = self.ensure_token()
        url = f"{self.config.api_base_url}{path}"
        response = requests.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected CloudMail response type: {type(data).__name__}")
        code = data.get("code")
        if code is not None and int(code) != 200:
            message = data.get("message") or data.get("msg") or f"code={code}"
            raise RuntimeError(f"CloudMail business error: {message}")
        return data

    def _normalize_target_email(self, email: str) -> str:
        normalized = normalize_cloudmail_email(email)
        if not normalized:
            raise ValueError(f"Invalid CloudMail target email: {email}")
        if not normalized.endswith(f"@{self.config.domain}"):
            raise ValueError(f"CloudMail target email must use domain {self.config.domain}: {email}")
        return normalized


def normalize_cloudmail_messages(payload, target_email: str) -> List[Dict[str, str]]:
    rows = get_cloudmail_rows(payload)
    messages = [normalize_cloudmail_message(row) for row in rows]
    target = normalize_cloudmail_email(target_email)
    return [message for message in messages if not message["to_email"] or message["to_email"] == target]


def normalize_cloudmail_message(row: Dict) -> Dict[str, str]:
    to_email = first_non_empty([row.get("toEmail"), row.get("to_email"), row.get("recipient"), row.get("email")])
    subject = first_non_empty([row.get("subject"), row.get("title")])
    body = first_non_empty([row.get("content"), row.get("html"), row.get("text"), row.get("plainText")])
    return {"to_email": normalize_cloudmail_email(to_email), "subject": subject, "body": body}


def get_cloudmail_rows(payload) -> Iterable[Dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    candidates = [data]
    if isinstance(data, dict):
        candidates.extend(data.get(key) for key in ("list", "items", "rows", "records"))
    candidates.extend(payload.get(key) for key in ("list", "items", "rows", "records"))
    for candidate in candidates:
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
    return []


def first_non_empty(values: Iterable) -> str:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return ""


def _load_config_payload(source: Union[str, Dict]) -> Dict:
    if isinstance(source, dict):
        return dict(source)
    path = Path(str(source or ""))
    if not path.exists():
        raise FileNotFoundError(f"CloudMail config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("CloudMail config must be a JSON object")
    return payload


def _validate_cloudmail_config(config: CloudMailConfig) -> None:
    if not config.api_base_url:
        raise ValueError("CloudMail api_base_url is required")
    if not config.admin_email:
        raise ValueError("CloudMail admin_email is required")
    if not config.admin_password:
        raise ValueError("CloudMail admin_password is required")
    if not config.domain:
        raise ValueError("CloudMail domain is required")
