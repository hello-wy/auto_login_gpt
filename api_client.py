import requests
import time
from typing import List, Dict
from config import (
    MAIL_KEYS_API,
    MAIL_CODE_API,
    API_HEADERS,
    CODE_FETCH_MAX_RETRIES,
    CODE_FETCH_RETRY_DELAY,
    MAIL_API_TIMEOUT,
)


def fetch_email_credentials(codes: List[str]) -> List[Dict]:
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

    try:
        response = requests.post(MAIL_KEYS_API, files=files, headers=API_HEADERS, timeout=MAIL_API_TIMEOUT)
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


def fetch_verification_code(email: str, secret: str, max_retries: int = CODE_FETCH_MAX_RETRIES) -> str:
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

    for attempt in range(max_retries):
        # Use files parameter for proper multipart/form-data encoding
        files = {
            'email_or_url': (None, email),
            'mail_secret': (None, secret)
        }

        try:
            response = requests.post(MAIL_CODE_API, files=files, headers=API_HEADERS, timeout=MAIL_API_TIMEOUT)

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
