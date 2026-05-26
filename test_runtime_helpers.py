import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from api_client import (
    build_cpa_auth_files_url,
    fetch_cpa_active_emails,
)
from main import build_parser, fetch_sub2api_error_emails_to_file, read_emails_from_args
from runner import build_email_items, filter_items_by_email_set, save_json_output, save_session_outputs


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

    def test_build_email_items_wraps_emails_without_secrets(self):
        items = build_email_items(["a@example.com", "b@example.com"])

        self.assertEqual(items, [{"email": "a@example.com"}, {"email": "b@example.com"}])

    def test_cli_defaults_to_sub2api_output_only(self):
        args = build_parser().parse_args([])

        self.assertEqual(args.format, "sub2api")

    @patch("runner.import_chatgpt_session")
    def test_sub2api_output_imports_session_without_writing_json(self, mock_import):
        mock_import.return_value = {"created": 1, "updated": 0, "failed": 0}
        console = MagicMock()
        session = {"accessToken": "token-1", "user": {"email": "a@example.com"}}

        with tempfile.TemporaryDirectory() as temp_dir:
            save_session_outputs(
                "a@example.com",
                session,
                {
                    "options": {
                        "output_format": "sub2api",
                        "sub2api": {
                            "base_url": "https://solidapi.top/",
                            "email": "admin@example.com",
                            "password": "password-1",
                            "group": "vip",
                            "priority": 1,
                        },
                    },
                    "summary": {"output_dir": temp_dir},
                    "console": console,
                },
            )

            self.assertEqual(list(Path(temp_dir).iterdir()), [])

        mock_import.assert_called_once()

    @patch("main.fetch_sub2api_error_emails_to_file")
    @patch("main.load_cloudmail_config")
    def test_sub2api_error_input_source_writes_and_reads_email_file(self, mock_config, mock_fetch):
        mock_config.return_value.domain = "example.com"
        mock_fetch.return_value = "error_accounts.txt"
        args = build_parser().parse_args(
            [
                "--input-source",
                "sub2api-errors",
                "--sub2api-error-output",
                "error_accounts.txt",
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "error_accounts.txt"

            def write_error_accounts(options):
                output_path.write_text("a@example.com\nb@example.com\n", encoding="utf-8")
                return str(output_path)

            mock_fetch.side_effect = write_error_accounts

            emails = read_emails_from_args(args)

        self.assertEqual(emails, ["a@example.com", "b@example.com"])
        self.assertEqual(mock_fetch.call_args.args[0]["email_domain"], "example.com")

    @patch("main.fetch_error_account_emails")
    def test_sub2api_error_input_source_rejects_empty_error_email_list(self, mock_fetch):
        mock_fetch.return_value = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "error_accounts.txt"
            with self.assertRaisesRegex(RuntimeError, "No Sub2API error account emails found"):
                fetch_sub2api_error_emails_to_file({"output_path": str(output_path)})

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

if __name__ == "__main__":
    unittest.main()
