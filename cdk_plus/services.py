import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from .cloudmail_client import normalize_cloudmail_email
from .config import CDK_ALPHABET, CDK_LENGTH, CDK_PATTERN, CLOUDMAIL_MAX_ATTEMPTS
from .repository import CdkCreateRecord, CdkRepository


NowProvider = Callable[[], datetime]


@dataclass(frozen=True)
class CreateCdkOptions:
    email: str
    valid_days: int
    cdk: str | None = None


@dataclass(frozen=True)
class CreateBatchCdkOptions:
    emails: list[str]
    valid_days: int


@dataclass(frozen=True)
class CodeResult:
    cdk: str
    email: str
    code: str
    status: str
    expires_at: str
    updated_at: str
    error: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CdkService:
    def __init__(self, repository: CdkRepository, cloudmail_client, now: NowProvider = utc_now):
        self.repository = repository
        self.cloudmail_client = cloudmail_client
        self.now = now

    def create_cdk(self, options: CreateCdkOptions) -> dict:
        normalized_email = validate_email(options.email)
        validated_days = validate_valid_days(options.valid_days)
        value = normalize_cdk(options.cdk) if options.cdk else self._generate_unique_cdk()
        current_time = self.now()
        record = CdkCreateRecord(value, normalized_email, validated_days, current_time)
        record = self.repository.create_cdk(record)
        return serialize_record(record)

    def create_cdks(self, options: CreateBatchCdkOptions) -> list[dict]:
        emails = validate_email_batch(options.emails)
        valid_days = validate_valid_days(options.valid_days)
        return [
            self.create_cdk(CreateCdkOptions(email, valid_days))
            for email in emails
        ]

    def resolve_codes(self, cdks: Iterable[str]) -> list[dict]:
        requested_cdks = [normalize_requested_cdk(cdk) for cdk in cdks]
        records = self.repository.get_cdks(requested_cdks)
        return [self._resolve_one(cdk, records.get(cdk)) for cdk in requested_cdks]

    def list_cdks(self, page: int, page_size: int) -> dict:
        validated_page = validate_positive_int(page, "page")
        validated_size = validate_page_size(page_size)
        return self.repository.list_cdks(validated_page, validated_size)

    def delete_cdk(self, cdk: str) -> dict:
        value = normalize_requested_cdk(cdk)
        if not self.repository.delete_cdk(value):
            raise ValueError(f"CDK not found: {value}")
        return {"deleted": True, "cdk": value}

    def switch_email(self, cdk: str) -> dict:
        value = normalize_requested_cdk(cdk)
        if self.repository.get_cdk(value) is None:
            raise ValueError(f"CDK not found: {value}")
        record = self.repository.switch_email_to_oldest_inactive(value)
        if record is None:
            raise ValueError("No inactive replacement CDK available")
        return serialize_switch_record(record)

    def _resolve_one(self, cdk: str, record: dict | None) -> dict:
        updated_at = self.now().isoformat()
        if record is None:
            return code_result(CodeResult(cdk, "", "", "not_found", "", updated_at, "CDK not found"))
        record = self._activate_if_needed(record)
        if is_expired(record, self.now()):
            return code_result(CodeResult(cdk, record["email"], "", "expired", record["expires_at"], updated_at, "CDK expired"))
        try:
            code = self.cloudmail_client.fetch_verification_code(
                record["email"],
                max_attempts=CLOUDMAIL_MAX_ATTEMPTS,
            )
        except Exception as error:
            return code_result(CodeResult(cdk, record["email"], "", "error", record["expires_at"], updated_at, str(error)))
        status = "ok" if code else "empty"
        error = "" if code else "No verification code found"
        return code_result(CodeResult(cdk, record["email"], code, status, record["expires_at"], updated_at, error))

    def _activate_if_needed(self, record: dict) -> dict:
        if record["expires_at"]:
            return record
        expires_at = self.now() + timedelta(days=record["valid_days"])
        return self.repository.activate_cdk(record["cdk"], expires_at)

    def _generate_unique_cdk(self) -> str:
        for _attempt in range(CDK_LENGTH):
            cdk = "".join(secrets.choice(CDK_ALPHABET) for _ in range(CDK_LENGTH))
            if self.repository.get_cdk(cdk) is None:
                return cdk
        raise RuntimeError("Failed to generate a unique CDK")


def validate_email(email: str) -> str:
    normalized = normalize_cloudmail_email(email)
    if not normalized:
        raise ValueError(f"Invalid email: {email}")
    return normalized


def validate_email_batch(emails: list[str]) -> list[str]:
    if not isinstance(emails, list):
        raise ValueError("emails must be a list")
    normalized = [validate_email(email) for email in emails]
    if not normalized:
        raise ValueError("emails cannot be empty")
    return normalized


def validate_valid_days(valid_days: int) -> int:
    return validate_positive_int(valid_days, "valid_days")


def validate_positive_int(raw_value: int, field_name: str) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a positive integer") from error
    if value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def validate_page_size(page_size: int) -> int:
    value = validate_positive_int(page_size, "page_size")
    if value > 100:
        raise ValueError("page_size must be less than or equal to 100")
    return value


def normalize_cdk(cdk: str) -> str:
    value = str(cdk or "").strip()
    if not re.fullmatch(CDK_PATTERN, value):
        raise ValueError("cdk must be 4-64 characters using letters, numbers, underscore, or hyphen")
    return value


def normalize_requested_cdk(cdk: str) -> str:
    value = str(cdk or "").strip()
    if not value:
        raise ValueError("cdks cannot contain empty values")
    return value


def is_expired(record: dict, now: datetime) -> bool:
    if not record["expires_at"]:
        return False
    expires_at = datetime.fromisoformat(record["expires_at"])
    return expires_at <= now


def serialize_record(record: dict) -> dict:
    return {
        "cdk": record["cdk"],
        "email": record["email"],
        "valid_days": record["valid_days"],
        "expires_at": record["expires_at"] or "",
        "created_at": record["created_at"],
    }


def serialize_switch_record(record: dict) -> dict:
    result = serialize_record(record)
    result["replacement_cdk"] = record["replacement_cdk"]
    result["replacement_email"] = record["replacement_email"]
    return result


def code_result(result: CodeResult) -> dict:
    return {
        "cdk": result.cdk,
        "email": result.email,
        "code": result.code,
        "status": result.status,
        "expires_at": result.expires_at,
        "updated_at": result.updated_at,
        "error": result.error,
    }
