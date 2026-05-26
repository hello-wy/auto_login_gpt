import os
from typing import List
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from api_client import normalize_email
from sub2api_constants import DEFAULT_TIMEOUT_SECONDS
from sub2api_importer import import_chatgpt_session, login_sub2api, normalize_string

ERROR_STATUS = "error"
DEFAULT_PAGE_SIZE = 50


def fetch_error_account_emails(options: dict) -> List[str]:
    accounts_url = options.get("accounts_url")
    page_size = int(options.get("page_size") or DEFAULT_PAGE_SIZE)
    timeout_seconds = int(options.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    email_domain = normalize_domain(options.get("email_domain"))
    headers = build_headers(resolve_sub2api_token(options))
    emails = []
    page = 1
    total = None
    while True:
        payload = fetch_accounts_page(
            {
                "accounts_url": accounts_url,
                "page": page,
                "page_size": page_size,
                "headers": headers,
                "timeout_seconds": timeout_seconds,
            }
        )
        items = extract_account_items(payload)
        emails.extend(extract_error_emails(items, email_domain))
        total = extract_total(payload, total)
        if should_stop_paging({"page": page, "page_size": page_size, "item_count": len(items), "total": total}):
            return dedupe_emails(emails)
        page += 1


def build_headers(token: str) -> dict:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def resolve_sub2api_token(options: dict) -> str:
    token = normalize_string(options.get("token"))
    if token:
        return token
    if options.get("email") and options.get("password"):
        return login_sub2api(options)["token"]
    return ""


def fetch_accounts_page(options: dict) -> dict:
    url = build_accounts_page_url(options["accounts_url"], options["page"], options["page_size"])
    try:
        response = requests.get(url, headers=options["headers"], timeout=options["timeout_seconds"])
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.Timeout as error:
        raise RuntimeError(
            f"Sub2API accounts request timed out after {options['timeout_seconds']}s: {url}"
        ) from error
    except requests.exceptions.RequestException as error:
        raise RuntimeError(f"Failed to fetch Sub2API accounts from {url}: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected Sub2API payload type: {type(payload).__name__}")
    return payload


def build_accounts_page_url(accounts_url: str, page: int, page_size: int) -> str:
    if not accounts_url:
        raise ValueError("Sub2API accounts URL is required")
    parsed = urlparse(accounts_url.strip())
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"page": str(page), "page_size": str(page_size), "status": ERROR_STATUS})
    return urlunparse(parsed._replace(query=urlencode(query)))


def extract_account_items(payload: dict) -> list:
    container = payload.get("data", payload)
    if isinstance(container, list):
        return container
    if not isinstance(container, dict):
        raise RuntimeError(f"Unexpected Sub2API data type: {type(container).__name__}")
    items = container.get("items", container.get("accounts", container.get("list")))
    if not isinstance(items, list):
        raise RuntimeError("Unexpected Sub2API accounts payload: missing items list")
    return items


def extract_total(payload: dict, previous_total):
    container = payload.get("data", payload)
    if not isinstance(container, dict):
        return previous_total
    total = container.get("total", previous_total)
    return total if isinstance(total, int) else previous_total


def extract_error_emails(items: list, email_domain: str = "") -> List[str]:
    emails = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("status", "")).strip().lower() != ERROR_STATUS:
            continue
        email = extract_account_email(item)
        if email and has_email_domain(email, email_domain):
            emails.append(email)
    return emails


def extract_account_email(item: dict) -> str:
    credentials = item.get("credentials") if isinstance(item.get("credentials"), dict) else {}
    extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
    for value in [item.get("email"), credentials.get("email"), item.get("name"), item.get("notes"), extra.get("email")]:
        email = normalize_email(value)
        if email:
            return email
    return ""


def normalize_domain(value) -> str:
    return str(value or "").strip().lower().removeprefix("@")


def has_email_domain(email: str, domain: str) -> bool:
    return not domain or email.endswith(f"@{domain}")


def should_stop_paging(options: dict) -> bool:
    page = options["page"]
    page_size = options["page_size"]
    item_count = options["item_count"]
    total = options["total"]
    if item_count < page_size:
        return True
    if isinstance(total, int) and page * page_size >= total:
        return True
    return False


def dedupe_emails(emails: List[str]) -> List[str]:
    seen = set()
    unique = []
    for email in emails:
        if email in seen:
            continue
        seen.add(email)
        unique.append(email)
    return unique


def write_email_lines(output_path: str, emails: List[str]) -> None:
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        for email in emails:
            handle.write(f"{email}\n")
