import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cdk_plus.repository import CdkCreateRecord, CdkRepository
from cdk_plus.services import CdkService, CreateBatchCdkOptions, CreateCdkOptions


class FakeCloudMailClient:
    def __init__(self, codes=None, failures=None):
        self.codes = codes or {}
        self.failures = failures or {}
        self.calls = []

    def fetch_verification_code(self, email, max_attempts=1):
        self.calls.append((email, max_attempts))
        if email in self.failures:
            raise RuntimeError(self.failures[email])
        return self.codes.get(email, "")


class CdkServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "cdks.sqlite3"
        self.repository = CdkRepository(db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_random_cdk_binds_email_without_expiry_until_query(self):
        service = CdkService(self.repository, FakeCloudMailClient(), now=fixed_now)

        record = service.create_cdk(CreateCdkOptions("USER@example.com", 3))
        stored = self.repository.get_cdk(record["cdk"])

        self.assertEqual(record["email"], "user@example.com")
        self.assertEqual(stored["email"], "user@example.com")
        self.assertEqual(record["valid_days"], 3)
        self.assertEqual(record["expires_at"], "")

    def test_create_batch_generates_one_random_cdk_per_email(self):
        service = CdkService(self.repository, FakeCloudMailClient(), now=fixed_now)

        result = service.create_cdks(
            CreateBatchCdkOptions(
                ["One@example.com", "two@example.com", "three@example.com"],
                5,
            )
        )

        self.assertEqual([item["email"] for item in result], ["one@example.com", "two@example.com", "three@example.com"])
        self.assertEqual([item["valid_days"] for item in result], [5, 5, 5])
        self.assertEqual([item["expires_at"] for item in result], ["", "", ""])
        self.assertEqual(len({item["cdk"] for item in result}), 3)

    def test_create_batch_rejects_invalid_email_without_creating_records(self):
        service = CdkService(self.repository, FakeCloudMailClient(), now=fixed_now)

        with self.assertRaisesRegex(ValueError, "Invalid email"):
            service.create_cdks(CreateBatchCdkOptions(["good@example.com", "bad-email"], 5))

        self.assertEqual(self.repository.list_cdks(1, 10)["total"], 0)

    def test_first_query_sets_expiry_from_query_time(self):
        client = FakeCloudMailClient(codes={"user@example.com": "222333"})
        service = CdkService(self.repository, client, now=fixed_now)
        service.create_cdk(CreateCdkOptions("user@example.com", 2, "FRESH1"))

        result = service.resolve_codes(["FRESH1"])[0]
        stored = self.repository.get_cdk("FRESH1")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["code"], "222333")
        self.assertEqual(result["expires_at"], "2026-05-27T10:00:00+00:00")
        self.assertEqual(stored["expires_at"], "2026-05-27T10:00:00+00:00")

    def test_create_rejects_duplicate_custom_cdk(self):
        service = CdkService(self.repository, FakeCloudMailClient(), now=fixed_now)
        service.create_cdk(CreateCdkOptions("first@example.com", 1, "ABC12345"))

        with self.assertRaisesRegex(ValueError, "already exists"):
            service.create_cdk(CreateCdkOptions("second@example.com", 1, "ABC12345"))

    def test_create_rejects_non_positive_valid_days(self):
        service = CdkService(self.repository, FakeCloudMailClient(), now=fixed_now)

        with self.assertRaisesRegex(ValueError, "valid_days"):
            service.create_cdk(CreateCdkOptions("user@example.com", 0))

    def test_expired_cdk_does_not_call_cloudmail(self):
        service = CdkService(self.repository, FakeCloudMailClient(), now=fixed_now)
        expires_at = fixed_now() - timedelta(seconds=1)
        record = CdkCreateRecord("OLD12345", "user@example.com", 1, fixed_now(), expires_at)
        self.repository.create_cdk(record)

        results = service.resolve_codes(["OLD12345"])

        self.assertEqual(results[0]["status"], "expired")
        self.assertEqual(results[0]["email"], "user@example.com")
        self.assertEqual(service.cloudmail_client.calls, [])

    def test_batch_resolve_preserves_order_and_continues_after_error(self):
        client = FakeCloudMailClient(
            codes={"one@example.com": "111111", "three@example.com": "333333"},
            failures={"two@example.com": "CloudMail is down"},
        )
        service = CdkService(self.repository, client, now=fixed_now)
        expires_at = fixed_now() + timedelta(days=1)
        self.repository.create_cdk(CdkCreateRecord("ONE12345", "one@example.com", 1, fixed_now(), expires_at))
        self.repository.create_cdk(CdkCreateRecord("TWO12345", "two@example.com", 1, fixed_now(), expires_at))
        self.repository.create_cdk(CdkCreateRecord("THR12345", "three@example.com", 1, fixed_now(), expires_at))

        results = service.resolve_codes(["ONE12345", "MISSING1", "TWO12345", "THR12345"])

        self.assertEqual([row["cdk"] for row in results], ["ONE12345", "MISSING1", "TWO12345", "THR12345"])
        self.assertEqual([row["status"] for row in results], ["ok", "not_found", "error", "ok"])
        self.assertEqual(results[0]["code"], "111111")
        self.assertEqual(results[2]["error"], "CloudMail is down")
        self.assertEqual(results[3]["code"], "333333")

    def test_resolve_returns_empty_when_cloudmail_has_no_code(self):
        service = CdkService(self.repository, FakeCloudMailClient(), now=fixed_now)
        expires_at = fixed_now() + timedelta(days=1)
        self.repository.create_cdk(CdkCreateRecord("WAIT1234", "wait@example.com", 1, fixed_now(), expires_at))

        result = service.resolve_codes(["WAIT1234"])[0]

        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["code"], "")
        self.assertEqual(result["error"], "No verification code found")

    def test_switch_email_uses_oldest_inactive_record_and_deletes_source(self):
        service = CdkService(self.repository, FakeCloudMailClient(), now=fixed_now)
        self.repository.create_cdk(CdkCreateRecord("TARGET1", "target@example.com", 30, fixed_now()))
        self.repository.create_cdk(CdkCreateRecord("NEWEST1", "newest@example.com", 30, fixed_now()))
        self.repository.create_cdk(CdkCreateRecord("OLDEST1", "oldest@example.com", 30, fixed_now() - timedelta(days=1)))
        active_expiry = fixed_now() + timedelta(days=1)
        self.repository.create_cdk(CdkCreateRecord("ACTIVE1", "active@example.com", 30, fixed_now() - timedelta(days=2), active_expiry))

        result = service.switch_email("TARGET1")

        self.assertEqual(result["cdk"], "TARGET1")
        self.assertEqual(result["email"], "oldest@example.com")
        self.assertEqual(result["replacement_cdk"], "OLDEST1")
        self.assertIsNone(self.repository.get_cdk("OLDEST1"))
        self.assertEqual(self.repository.get_cdk("TARGET1")["email"], "oldest@example.com")
        self.assertEqual(self.repository.get_cdk("NEWEST1")["email"], "newest@example.com")
        self.assertEqual(self.repository.get_cdk("ACTIVE1")["email"], "active@example.com")

    def test_switch_email_rejects_when_no_other_inactive_record_exists(self):
        service = CdkService(self.repository, FakeCloudMailClient(), now=fixed_now)
        self.repository.create_cdk(CdkCreateRecord("TARGET1", "target@example.com", 30, fixed_now()))

        with self.assertRaisesRegex(ValueError, "No inactive replacement CDK available"):
            service.switch_email("TARGET1")


def fixed_now():
    return datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
