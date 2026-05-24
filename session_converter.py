import json
import base64
import time
import re
from typing import Dict
from datetime import datetime, timezone


def parse_jwt(token: str) -> Dict:
    """Parse JWT token without verification."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}

        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding

        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        print(f"⚠ JWT parsing error: {e}")
        return {}


def to_email_key(email: str) -> str:
    """Convert email to safe filename key."""
    return re.sub(r'[^a-z0-9]+', '_', email.lower()).strip('_')


def strip_unavailable(obj):
    """Recursively remove None, empty string, and undefined values."""
    if isinstance(obj, dict):
        return {k: strip_unavailable(v) for k, v in obj.items()
                if v is not None and v != ''}
    elif isinstance(obj, list):
        return [strip_unavailable(item) for item in obj]
    return obj


def build_synthetic_id_token(account_id: str, plan_type: str, user_id: str, email: str, expires_at: int) -> str:
    """Build synthetic JWT id_token for CPA format."""
    header = {
        "alg": "none",
        "typ": "JWT",
        "cpa_synthetic": True
    }

    payload = {
        "iat": int(time.time()),
        "exp": expires_at,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan_type,
            "chatgpt_user_id": user_id,
            "user_id": user_id
        },
        "email": email
    }

    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip('=')
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    signature_b64 = base64.urlsafe_b64encode(b"synthetic").decode().rstrip('=')

    return f"{header_b64}.{payload_b64}.{signature_b64}"


def convert_to_cpa(session: Dict) -> Dict:
    """Convert ChatGPT session to CPA format."""
    access_token = session.get("accessToken", "")
    jwt_payload = parse_jwt(access_token)

    account_id = session.get("account", {}).get("id", "")
    plan_type = session.get("account", {}).get("planType", "")
    user_id = session.get("user", {}).get("id", "")
    email = session.get("user", {}).get("email", "")

    jwt_exp = jwt_payload.get("exp", int(time.time()) + 90 * 24 * 3600)
    expires_iso = datetime.fromtimestamp(jwt_exp, tz=timezone.utc).isoformat()

    id_token = build_synthetic_id_token(account_id, plan_type, user_id, email, jwt_exp)

    cpa = {
        "type": "codex",
        "account_id": account_id,
        "chatgpt_account_id": account_id,
        "email": email,
        "name": session.get("user", {}).get("name", email),
        "plan_type": plan_type,
        "chatgpt_plan_type": plan_type,
        "id_token": id_token,
        "id_token_synthetic": True,
        "access_token": access_token,
        "refresh_token": "",
        "session_token": session.get("sessionToken", ""),
        "last_refresh": datetime.now(timezone.utc).isoformat(),
        "expired": expires_iso
    }

    return strip_unavailable(cpa)


def convert_to_sub2api(session: Dict) -> Dict:
    """Convert ChatGPT session to Sub2API format."""
    access_token = session.get("accessToken", "")
    jwt_payload = parse_jwt(access_token)

    account_id = session.get("account", {}).get("id", "")
    plan_type = session.get("account", {}).get("planType", "")
    user_id = session.get("user", {}).get("id", "")
    email = session.get("user", {}).get("email", "")
    name = session.get("user", {}).get("name", email)

    jwt_exp = jwt_payload.get("exp", int(time.time()) + 90 * 24 * 3600)
    expires_iso = datetime.fromtimestamp(jwt_exp, tz=timezone.utc).isoformat()
    expires_in = max(0, jwt_exp - int(time.time()))

    account = {
        "name": name,
        "platform": "openai",
        "type": "oauth",
        "expires_at": jwt_exp,
        "auto_pause_on_expired": True,
        "concurrency": 10,
        "priority": 1,
        "credentials": {
            "access_token": access_token,
            "chatgpt_account_id": account_id,
            "chatgpt_user_id": user_id,
            "email": email,
            "expires_at": expires_iso,
            "expires_in": expires_in,
            "plan_type": plan_type
        },
        "extra": {
            "email": email,
            "email_key": to_email_key(email),
            "name": name,
            "auth_provider": session.get("authProvider", ""),
            "source": "chatgpt_web_session",
            "last_refresh": datetime.now(timezone.utc).isoformat()
        }
    }

    document = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "proxies": [],
        "accounts": [strip_unavailable(account)]
    }

    return document
