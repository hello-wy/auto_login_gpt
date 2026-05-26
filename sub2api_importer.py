import json
from datetime import datetime
from urllib.parse import urlparse

import requests

from api_client import normalize_email
from sub2api_constants import DEFAULT_TIMEOUT_SECONDS

DEFAULT_IMPORT_TOTAL = 0


def import_chatgpt_session(session: dict, options: dict) -> dict:
    auth = login_sub2api(options)
    groups = get_groups_by_names(auth["origin"], auth["token"], options)
    payload = build_codex_session_import_payload(session, groups, options)
    result = request_json(
        {
            "method": "POST",
            "url": f"{auth['origin']}/api/v1/admin/accounts/import/codex-session",
            "token": auth["token"],
            "json": payload,
            "timeout_seconds": options.get("timeout_seconds"),
        }
    )
    summary = normalize_import_result(result)
    if summary["failed"] > 0:
        raise RuntimeError(f"Sub2API import failed for {summary['failed']} account(s)")
    if summary["created"] <= 0 and summary["updated"] <= 0:
        raise RuntimeError("Sub2API import did not create or update any account")
    return summary


def login_sub2api(options: dict) -> dict:
    origin = normalize_base_url(options.get("base_url"))
    email = normalize_required_string(options.get("email"), "Sub2API email is required")
    password = normalize_required_string(options.get("password"), "Sub2API password is required")
    data = request_json(
        {
            "method": "POST",
            "url": f"{origin}/api/v1/auth/login",
            "json": {"email": email, "password": password},
            "timeout_seconds": options.get("timeout_seconds"),
        }
    )
    token = normalize_string(data.get("access_token") or data.get("accessToken"))
    if not token:
        raise RuntimeError("Sub2API login response is missing access_token")
    return {"origin": origin, "token": token}


def get_groups_by_names(origin: str, token: str, options: dict) -> list:
    groups = request_json(
        {
            "method": "GET",
            "url": f"{origin}/api/v1/admin/groups/all",
            "token": token,
            "timeout_seconds": options.get("timeout_seconds"),
        }
    )
    if not isinstance(groups, list):
        raise RuntimeError("Sub2API groups response is not a list")
    target_names = normalize_group_names(options.get("group"))
    matched = [group for group in groups if is_target_openai_group(group, target_names)]
    missing = [name for name in target_names if not has_group_name(matched, name)]
    if missing:
        raise RuntimeError(f"Sub2API missing openai group(s): {', '.join(missing)}")
    return matched


def build_codex_session_import_payload(session: dict, groups: list, options: dict) -> dict:
    group_ids = [int(group["id"]) for group in groups if is_positive_int(group.get("id"))]
    if not group_ids:
        raise RuntimeError("Sub2API target group IDs are invalid")
    payload = {
        "content": json.dumps(session, ensure_ascii=False),
        "group_ids": group_ids,
        "priority": normalize_priority(options.get("priority")),
        "auto_pause_on_expired": True,
        "update_existing": True,
    }
    name = normalize_account_name(session)
    if name:
        payload["name"] = name
    expires_at = extract_session_expires_at(session)
    if expires_at:
        payload["expires_at"] = expires_at
    return payload


def request_json(options: dict):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if options.get("token"):
        headers["Authorization"] = f"Bearer {options['token']}"
    try:
        response = requests.request(
            options.get("method", "GET"),
            options["url"],
            json=options.get("json"),
            headers=headers,
            timeout=int(options.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.Timeout as error:
        raise RuntimeError(f"Sub2API request timed out: {options['url']}") from error
    except requests.exceptions.RequestException as error:
        message = extract_request_error_message(error, options["url"])
        raise RuntimeError(message) from error
    if not isinstance(payload, dict):
        return payload
    if "code" in payload:
        if int(payload.get("code") or 0) == 0:
            return payload.get("data")
        raise RuntimeError(extract_error_message(payload, options["url"]))
    return payload


def normalize_base_url(base_url: str) -> str:
    value = normalize_required_string(base_url, "Sub2API base URL is required")
    parsed = urlparse(value if value.startswith(("http://", "https://")) else f"https://{value}")
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Sub2API base URL must include a host")
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_required_string(value, message: str) -> str:
    text = normalize_string(value)
    if not text:
        raise ValueError(message)
    return text


def normalize_string(value) -> str:
    return str(value or "").strip()


def normalize_group_names(value) -> list:
    names = [normalize_string(item) for item in str(value or "vip").replace("，", ",").split(",")]
    return [name for name in names if name]


def is_target_openai_group(group: dict, target_names: list) -> bool:
    if not isinstance(group, dict):
        return False
    name = normalize_string(group.get("name")).lower()
    platform = normalize_string(group.get("platform")).lower()
    return name in {item.lower() for item in target_names} and platform in {"", "openai"}


def has_group_name(groups: list, name: str) -> bool:
    return any(normalize_string(group.get("name")).lower() == name.lower() for group in groups)


def is_positive_int(value) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def normalize_priority(value) -> int:
    priority = int(value or 1)
    if priority < 1:
        raise ValueError("Sub2API account priority must be greater than or equal to 1")
    return priority


def normalize_account_name(session: dict) -> str:
    user = session.get("user") if isinstance(session, dict) else {}
    return normalize_email(user.get("email")) or normalize_string(session.get("email"))


def extract_session_expires_at(session: dict):
    expires = normalize_string(session.get("expires") if isinstance(session, dict) else "")
    if not expires:
        return None
    parsed = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    return int(parsed.timestamp())


def normalize_import_result(result) -> dict:
    payload = result if isinstance(result, dict) else {}
    return {
        "total": max(DEFAULT_IMPORT_TOTAL, int(payload.get("total") or 0)),
        "created": max(0, int(payload.get("created") or 0)),
        "updated": max(0, int(payload.get("updated") or 0)),
        "skipped": max(0, int(payload.get("skipped") or 0)),
        "failed": max(0, int(payload.get("failed") or 0)),
    }


def extract_error_message(payload: dict, url: str) -> str:
    for key in ["message", "detail", "error", "reason"]:
        message = normalize_string(payload.get(key))
        if message:
            return message
    return f"Sub2API request returned error: {url}"


def extract_request_error_message(error: requests.exceptions.RequestException, url: str) -> str:
    response = getattr(error, "response", None)
    if response is None:
        return f"Sub2API request failed: {url}: {error}"
    try:
        payload = response.json()
    except ValueError:
        return f"Sub2API request failed: {url}: {error}"
    if isinstance(payload, dict):
        return extract_error_message(payload, url)
    return f"Sub2API request failed: {url}: {error}"
