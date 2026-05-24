import requests
import time
from typing import Dict, List, Set
from urllib.parse import urlparse
from config import (
    CPA_MANAGEMENT_TIMEOUT,
    MAIL_KEYS_API,
    MAIL_CODE_API,
    API_HEADERS,
    CODE_FETCH_MAX_RETRIES,
    CODE_FETCH_RETRY_DELAY,
    MAIL_API_TIMEOUT,
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


def build_mail_api_url(base_url: str, endpoint_name: str) -> str:
    """Normalize a pickup server base URL to a concrete mail API endpoint."""
    if not base_url or not isinstance(base_url, str):
        raise ValueError("Mail API base URL is required")

    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(
            "Mail API base URL must include scheme and host, "
            "e.g. https://plus.keria.cc.cd or https://plus.keria.cc.cd/api/pickup"
        )

    if normalized.endswith(f"/api/pickup/{endpoint_name}"):
        return normalized
    if normalized.endswith("/api/pickup"):
        return f"{normalized}/{endpoint_name}"
    return f"{normalized}/api/pickup/{endpoint_name}"


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


def fetch_email_credentials(codes: List[str], mail_keys_api: str = MAIL_KEYS_API) -> List[Dict]:
    """
    Fetch email and secret pairs from key codes.

    Args:
        codes: List of key codes (e.g., ["AAAA-BBBB-CCCC", "DDDD-EEEE-FFFF"])

    Returns:
        List of dicts with email, secret, code fields
    """
    # Use files parameter for proper multipart/form-data encoding
    files = {
        'codes': (None, '\n'.join(codes))
    }
    mail_keys_api = mail_keys_api or MAIL_KEYS_API

    try:
        response = requests.post(mail_keys_api, files=files, headers=API_HEADERS, timeout=MAIL_API_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, dict):
            print(f"✗ Unexpected credentials API payload type: {type(data).__name__}")
            return []

        if data.get("ok"):
            items = data.get("items", [])
            if not isinstance(items, list):
                print(f"✗ Unexpected credentials API items type: {type(items).__name__}")
                return []

            successful_items = []
            skipped_items = 0
            for item in items:
                if not isinstance(item, dict) or not item.get("ok"):
                    continue
                if not item.get("email") or not item.get("secret"):
                    skipped_items += 1
                    continue
                successful_items.append(item)

            print(f"✓ Successfully fetched {len(successful_items)}/{len(codes)} email credentials")
            if skipped_items:
                print(f"⚠ Skipped {skipped_items} credential rows missing email or secret")
            return successful_items
        else:
            print(f"✗ API returned ok=false: {data.get('message', 'Unknown error')}")
            return []
    except requests.exceptions.Timeout:
        print(f"✗ Error fetching email credentials: request timed out after {MAIL_API_TIMEOUT}s")
        return []
    except requests.exceptions.RequestException as e:
        print(f"✗ Error fetching email credentials: {e}")
        return []
    except ValueError as e:
        print(f"✗ Error decoding email credentials response: {e}")
        return []
    except Exception as e:
        print(f"✗ Unexpected error fetching email credentials: {e}")
        return []


def fetch_verification_code(
    email: str,
    secret: str,
    max_retries: int = CODE_FETCH_MAX_RETRIES,
    mail_code_api: str = MAIL_CODE_API,
) -> str:
    """
    Fetch verification code for an email.

    Args:
        email: Email address
        secret: Secret key for the email
        max_retries: Maximum number of retry attempts

    Returns:
        6-digit verification code

    Raises:
        Exception if code retrieval fails after all retries
    """
    if not email:
        raise ValueError("Email is required to fetch a verification code")
    if not secret:
        raise ValueError(f"Missing mail secret for verification code fetch: {email}")
    mail_code_api = mail_code_api or MAIL_CODE_API

    for attempt in range(max_retries):
        # Use files parameter for proper multipart/form-data encoding
        files = {
            'email_or_url': (None, email),
            'mail_secret': (None, secret)
        }

        try:
            response = requests.post(mail_code_api, files=files, headers=API_HEADERS, timeout=MAIL_API_TIMEOUT)

            # Check if response is JSON
            content_type = response.headers.get('content-type', '')
            if 'application/json' not in content_type:
                print(f"⚠ Attempt {attempt + 1}/{max_retries}: Got HTML response instead of JSON (status {response.status_code})")
                if attempt < max_retries - 1:
                    delay = CODE_FETCH_RETRY_DELAY * (attempt + 1)
                    print(f"  Waiting {delay}s before retry...")
                    time.sleep(delay)
                continue

            response.raise_for_status()
            data = response.json()

            if not isinstance(data, dict):
                print(f"⚠ Attempt {attempt + 1}/{max_retries}: Unexpected response payload type {type(data).__name__}")
                data = {}

            if data.get("ok") and data.get("code"):
                print(f"✓ Got verification code: {data['code']}")
                return data["code"]
            else:
                print(f"⚠ Attempt {attempt + 1}/{max_retries}: {data.get('message', 'No code received')}")
        except requests.exceptions.Timeout:
            print(
                f"⚠ Attempt {attempt + 1}/{max_retries} failed: "
                f"request timed out after {MAIL_API_TIMEOUT}s"
            )
        except requests.exceptions.RequestException as e:
            print(f"⚠ Attempt {attempt + 1}/{max_retries} failed: {e}")
        except ValueError as e:
            print(f"⚠ Attempt {attempt + 1}/{max_retries} failed to decode JSON: {e}")
        except Exception as e:
            print(f"⚠ Attempt {attempt + 1}/{max_retries} error: {e}")

        if attempt < max_retries - 1:
            delay = CODE_FETCH_RETRY_DELAY * (attempt + 1)
            print(f"  Waiting {delay}s before retry...")
            time.sleep(delay)

    raise Exception(f"Failed to fetch verification code after {max_retries} attempts")
