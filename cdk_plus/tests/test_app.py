import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient


ADMIN_HEADERS = {"X-Admin-Password": "akjd345hgi345"}


class AppApiTests(unittest.TestCase):
    def test_admin_create_and_user_resolve_response_shape(self):
        from cdk_plus.app import create_app

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = Path(temp_dir.name) / "cdks.sqlite3"
        app = create_app(
            db_path=db_path,
            cloudmail_client=StaticCloudMailClient("246810"),
            now=fixed_now,
        )
        client = TestClient(app)

        create_response = client.post(
            "/api/admin/cdks",
            headers=ADMIN_HEADERS,
            json={"email": "user@example.com", "valid_days": 2, "cdk": "API12345"},
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["expires_at"], "")

        resolve_response = client.post("/api/codes/resolve", json={"cdks": ["API12345"]})
        payload = resolve_response.json()

        self.assertEqual(resolve_response.status_code, 200)
        self.assertEqual(
            set(payload["items"][0].keys()),
            {"cdk", "email", "code", "status", "expires_at", "updated_at", "error"},
        )
        self.assertEqual(payload["items"][0]["code"], "246810")
        self.assertEqual(payload["items"][0]["expires_at"], "2026-05-27T10:00:00+00:00")

    def test_admin_batch_create_generates_random_cdks(self):
        from cdk_plus.app import create_app

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = create_app(
            db_path=Path(temp_dir.name) / "cdks.sqlite3",
            cloudmail_client=StaticCloudMailClient("246810"),
            now=fixed_now,
        )
        client = TestClient(app)

        response = client.post(
            "/api/admin/cdks/batch",
            headers=ADMIN_HEADERS,
            json={"emails": ["one@example.com", "two@example.com"], "valid_days": 4},
        )
        payload = response.json()

        self.assertEqual(response.status_code, 201)
        self.assertEqual([item["email"] for item in payload["items"]], ["one@example.com", "two@example.com"])
        self.assertEqual([item["valid_days"] for item in payload["items"]], [4, 4])
        self.assertEqual(len({item["cdk"] for item in payload["items"]}), 2)

    def test_admin_list_and_delete_cdks(self):
        from cdk_plus.app import create_app

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = create_app(
            db_path=Path(temp_dir.name) / "cdks.sqlite3",
            cloudmail_client=StaticCloudMailClient("246810"),
            now=fixed_now,
        )
        client = TestClient(app)
        for index in range(3):
            client.post(
                "/api/admin/cdks",
                headers=ADMIN_HEADERS,
                json={
                    "email": f"user{index}@example.com",
                    "valid_days": 2,
                    "cdk": f"PAGE{index}",
                },
            )

        list_response = client.get("/api/admin/cdks?page=1&page_size=2", headers=ADMIN_HEADERS)
        payload = list_response.json()

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 2)
        self.assertEqual(payload["total"], 3)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["cdk"], "PAGE2")
        self.assertIsNone(payload["items"][0]["expires_at"])

        delete_response = client.delete("/api/admin/cdks/PAGE1", headers=ADMIN_HEADERS)
        self.assertEqual(delete_response.status_code, 200)

        after_delete = client.get("/api/admin/cdks?page=1&page_size=10", headers=ADMIN_HEADERS).json()
        self.assertEqual(after_delete["total"], 2)
        self.assertNotIn("PAGE1", [item["cdk"] for item in after_delete["items"]])

    def test_admin_switch_email_updates_target_and_deletes_oldest_inactive_record(self):
        from cdk_plus.app import create_app

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        clock = SequenceClock()
        app = create_app(
            db_path=Path(temp_dir.name) / "cdks.sqlite3",
            cloudmail_client=StaticCloudMailClient("246810"),
            now=clock.now,
        )
        client = TestClient(app)
        for cdk, email in [
            ("TARGET1", "target@example.com"),
            ("OLDEST1", "oldest@example.com"),
            ("NEWEST1", "newest@example.com"),
        ]:
            client.post(
                "/api/admin/cdks",
                headers=ADMIN_HEADERS,
                json={"email": email, "valid_days": 30, "cdk": cdk},
            )

        response = client.post("/api/admin/cdks/TARGET1/switch-email", headers=ADMIN_HEADERS)
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["cdk"], "TARGET1")
        self.assertEqual(payload["email"], "oldest@example.com")
        self.assertEqual(payload["replacement_cdk"], "OLDEST1")
        after_switch = client.get("/api/admin/cdks?page=1&page_size=10", headers=ADMIN_HEADERS).json()
        self.assertEqual(after_switch["total"], 2)
        self.assertNotIn("OLDEST1", [item["cdk"] for item in after_switch["items"]])

    def test_admin_api_requires_password(self):
        from cdk_plus.app import create_app

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = create_app(
            db_path=Path(temp_dir.name) / "cdks.sqlite3",
            cloudmail_client=StaticCloudMailClient("246810"),
            now=fixed_now,
        )
        client = TestClient(app)

        self.assertEqual(client.get("/api/admin/cdks").status_code, 401)
        self.assertEqual(
            client.post(
                "/api/admin/cdks",
                json={"email": "user@example.com", "valid_days": 1, "cdk": "LOCKED1"},
            ).status_code,
            401,
        )
        self.assertEqual(client.delete("/api/admin/cdks/LOCKED1").status_code, 401)

    def test_admin_session_validates_password(self):
        from cdk_plus.app import create_app

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = create_app(
            db_path=Path(temp_dir.name) / "cdks.sqlite3",
            cloudmail_client=StaticCloudMailClient("246810"),
            now=fixed_now,
        )
        client = TestClient(app)

        self.assertEqual(client.post("/api/admin/session", json={"password": "wrong"}).status_code, 401)
        self.assertEqual(client.post("/api/admin/session", json={"password": "akjd345hgi345"}).status_code, 200)


class StaticCloudMailClient:
    def __init__(self, code):
        self.code = code

    def fetch_verification_code(self, email, max_attempts=1):
        return self.code


def fixed_now():
    return datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)


class SequenceClock:
    def __init__(self):
        self.calls = 0

    def now(self):
        current = fixed_now() + timedelta(minutes=self.calls)
        self.calls += 1
        return current


if __name__ == "__main__":
    unittest.main()
