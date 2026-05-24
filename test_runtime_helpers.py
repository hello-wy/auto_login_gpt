import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from api_client import (
    build_cpa_auth_files_url,
    build_mail_api_url,
    fetch_cpa_active_emails,
    fetch_email_credentials,
    fetch_verification_code,
)
from main import filter_items_by_email_set, save_json_output


def make_response(payload, headers=None):
    response = MagicMock()
    response.json.return_value = payload
    response.headers = headers or {"content-type": "application/json"}
    response.raise_for_status.return_value = None
    return response


class RuntimeHelperTests(unittest.TestCase):
    def test_build_cpa_auth_files_url_supports_base_and_management_paths(self):
        self.assertEqual(
            build_cpa_auth_files_url("http://127.0.0.1:8317"),
            "http://127.0.0.1:8317/v0/management/auth-files",
        )
        self.assertEqual(
            build_cpa_auth_files_url("http://127.0.0.1:8317/v0/management"),
            "http://127.0.0.1:8317/v0/management/auth-files",
        )
        self.assertEqual(
            build_cpa_auth_files_url("http://127.0.0.1:8317/v0/management/auth-files"),
            "http://127.0.0.1:8317/v0/management/auth-files",
        )

    def test_build_mail_api_url_supports_root_and_pickup_paths(self):
        self.assertEqual(
            build_mail_api_url("https://plus.keria.cc.cd", "mail-keys"),
            "https://plus.keria.cc.cd/api/pickup/mail-keys",
        )
        self.assertEqual(
            build_mail_api_url("https://plus.keria.cc.cd/api/pickup", "mail-code"),
            "https://plus.keria.cc.cd/api/pickup/mail-code",
        )
        self.assertEqual(
            build_mail_api_url("https://plus.keria.cc.cd/api/pickup/mail-keys", "mail-keys"),
            "https://plus.keria.cc.cd/api/pickup/mail-keys",
        )

    def test_save_json_output_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "nested" / "account.json"

            save_json_output(str(output_path), {"ok": True}, "test output")

            self.assertTrue(output_path.exists())
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), {"ok": True})

    def test_save_json_output_does_not_clear_existing_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()
            existing_file = output_dir / "keep_me.json"
            existing_file.write_text('{"keep": true}', encoding="utf-8")

            save_json_output(str(output_dir / "new_account.json"), {"ok": True}, "test output")

            self.assertTrue(existing_file.exists())
            self.assertEqual(json.loads(existing_file.read_text(encoding="utf-8")), {"keep": True})
            self.assertEqual(
                json.loads((output_dir / "new_account.json").read_text(encoding="utf-8")),
                {"ok": True},
            )

    def test_filter_items_by_email_set_skips_normalized_matches(self):
        kept_items, skipped_items = filter_items_by_email_set(
            [
                {"email": "Alive@example.com", "secret": "a"},
                {"email": "keep@example.com", "secret": "b"},
            ],
            {"alive@example.com"},
        )

        self.assertEqual([item["email"] for item in kept_items], ["keep@example.com"])
        self.assertEqual([item["email"] for item in skipped_items], ["Alive@example.com"])

    @patch("api_client.requests.post")
    def test_fetch_email_credentials_skips_rows_missing_email_or_secret(self, mock_post):
        mock_post.return_value = make_response(
            {
                "ok": True,
                "items": [
                    {"ok": True, "email": "a@example.com", "secret": "secret-a"},
                    {"ok": True, "email": "missing-secret@example.com"},
                    {"ok": True, "secret": "missing-email"},
                    {"ok": False, "email": "ignored@example.com", "secret": "ignored"},
                ],
            }
        )

        items = fetch_email_credentials(["AAAA-BBBB-CCCC"])

        self.assertEqual(items, [{"ok": True, "email": "a@example.com", "secret": "secret-a"}])

    @patch("api_client.requests.post")
    def test_fetch_email_credentials_uses_overridden_mail_keys_api(self, mock_post):
        mock_post.return_value = make_response({"ok": True, "items": []})

        fetch_email_credentials(["AAAA-BBBB-CCCC"], mail_keys_api="https://example.com/api/pickup/mail-keys")

        self.assertEqual(
            mock_post.call_args.kwargs["url"] if "url" in mock_post.call_args.kwargs else mock_post.call_args.args[0],
            "https://example.com/api/pickup/mail-keys",
        )

    @patch("api_client.requests.get")
    def test_fetch_cpa_active_emails_only_keeps_active_entries(self, mock_get):
        mock_get.return_value = make_response(
            {
                "files": [
                    {
                        "email": "alive@example.com",
                        "status": "active",
                        "disabled": False,
                        "unavailable": False,
                    },
                    {
                        "email": "error@example.com",
                        "status": "error",
                        "disabled": False,
                        "unavailable": True,
                    },
                    {
                        "email": "disabled@example.com",
                        "status": "active",
                        "disabled": True,
                        "unavailable": False,
                    },
                    {
                        "email": "nostatus@example.com",
                        "disabled": False,
                        "unavailable": False,
                    },
                ]
            }
        )

        active_emails = fetch_cpa_active_emails("http://127.0.0.1:8317", "token-1")

        self.assertEqual(
            active_emails,
            {"alive@example.com", "disabled@example.com", "nostatus@example.com"},
        )

    def test_fetch_verification_code_requires_secret(self):
        with self.assertRaisesRegex(ValueError, "Missing mail secret"):
            fetch_verification_code("a@example.com", "")

    @patch("api_client.requests.post")
    def test_fetch_verification_code_uses_overridden_mail_code_api(self, mock_post):
        mock_post.return_value = make_response({"ok": True, "code": "123456"})

        code = fetch_verification_code(
            "a@example.com",
            "secret-1",
            mail_code_api="https://example.com/api/pickup/mail-code",
        )

        self.assertEqual(code, "123456")
        self.assertEqual(
            mock_post.call_args.kwargs["url"] if "url" in mock_post.call_args.kwargs else mock_post.call_args.args[0],
            "https://example.com/api/pickup/mail-code",
        )


if __name__ == "__main__":
    unittest.main()
