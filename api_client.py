import requests
from typing import Dict, Set
from urllib.parse import urlparse
from config import (
    CPA_MANAGEMENT_TIMEOUT,
)

CPA_INACTIVE_STATUSES = {"error", "expired", "invalid", "revoked", "unavailable"}


def normalize_email(email: str) -> str:
    """Normalize an email for case-insensitive comparisons."""
    if not isinstance(email, str):
        return ""
    return email.strip().lower()


def build_cpa_auth_files_url(base_url: str) -> str:
    """Normalize a CLIProxyAPI management base URL to the auth-files endpoint."""
    if not base_url or not isinstance(base_url, str):
        raise ValueError("CPA management URL is required")

    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(
            "CPA management URL must include scheme and host, "
            "e.g. http://127.0.0.1:8317 or http://127.0.0.1:8317/v0/management"
        )

    if normalized.endswith("/v0/management/auth-files"):
        return normalized
    if normalized.endswith("/v0/management"):
        return f"{normalized}/auth-files"
    return f"{normalized}/v0/management/auth-files"


def is_cpa_auth_file_active(auth_file: Dict) -> bool:
    """Return whether a CLIProxyAPI auth entry is still active and usable."""
    if not isinstance(auth_file, dict):
        return False
    if not normalize_email(auth_file.get("email")):
        return False
    if auth_file.get("unavailable"):
        return False

    status = str(auth_file.get("status", "")).strip().lower()
    if status and status in CPA_INACTIVE_STATUSES:
        return False
    return status in {"", "active"}


def fetch_cpa_active_emails(cpa_management_url: str, cpa_management_key: str) -> Set[str]:
    """
    Fetch active emails from a CLIProxyAPI management endpoint.

    Args:
        cpa_management_url: CLIProxyAPI base URL, management URL, or auth-files URL
        cpa_management_key: Bearer token for the management API

    Returns:
        Set of normalized email addresses considered active in CPA
    """
    if not cpa_management_key:
        raise ValueError("CPA management key is required")

    auth_files_url = build_cpa_auth_files_url(cpa_management_url)
    headers = {"Authorization": f"Bearer {cpa_management_key}"}

    try:
        response = requests.get(auth_files_url, headers=headers, timeout=CPA_MANAGEMENT_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, dict):
            raise ValueError(f"Unexpected CPA payload type: {type(data).__name__}")

        files = data.get("files", [])
        if not isinstance(files, list):
            raise ValueError(f"Unexpected CPA files type: {type(files).__name__}")

        active_emails = {
            normalize_email(auth_file.get("email"))
            for auth_file in files
            if is_cpa_auth_file_active(auth_file)
        }

        active_emails.discard("")
        print(
            f"✓ Fetched {len(files)} CPA auth files from {auth_files_url}; "
            f"{len(active_emails)} active emails will be used for filtering"
        )
        return active_emails
    except requests.exceptions.Timeout as error:
        raise RuntimeError(
            f"CPA management request timed out after {CPA_MANAGEMENT_TIMEOUT}s: {auth_files_url}"
        ) from error
    except requests.exceptions.RequestException as error:
        raise RuntimeError(f"Failed to fetch CPA auth files from {auth_files_url}: {error}") from error
    except ValueError as error:
        raise RuntimeError(f"Failed to parse CPA auth files response: {error}") from error
