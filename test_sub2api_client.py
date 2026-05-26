import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sub2api_client import (
    fetch_error_account_emails,
    import_chatgpt_session,
    write_email_lines,
)
from sub2api_importer import login_sub2api


def make_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.text = json_dumps(payload)
    response.raise_for_status.return_value = None
    return response


def make_http_error_response(payload):
    import requests

    response = make_response(payload)
    error = requests.exceptions.HTTPError("401 Client Error: Unauthorized", response=response)
    response.raise_for_status.side_effect = error
    return response


def json_dumps(payload):
    import json

    return json.dumps(payload)


class Sub2ApiClientTests(unittest.TestCase):
    @patch("sub2api_client.requests.get")
    def test_fetch_error_account_emails_reads_all_error_pages(self, mock_get):
        mock_get.side_effect = [
            make_response(
                {
                    "data": {
                        "items": [
                            {
                                "name": "A@Example.com",
                                "status": "error",
                                "credentials": {"email": "A@Example.com"},
                            },
                            {
                                "name": "outside@proton.me",
                                "status": "error",
                                "credentials": {"email": "outside@proton.me"},
                            },
                        ],
                        "total": 3,
                    }
                }
            ),
            make_response(
                {
                    "data": {
                        "items": [
                            {"email": "b@example.com", "status": "error"},
                        ],
                        "total": 3,
                    }
                }
            ),
        ]

        emails = fetch_error_account_emails(
            {
                "accounts_url": "https://solidapi.top/api/v1/admin/accounts?page=1&page_size=2&status=",
                "page_size": 2,
                "token": "token-1",
                "timeout_seconds": 5,
                "email_domain": "example.com",
            }
        )

        self.assertEqual(emails, ["a@example.com", "b@example.com"])
        first_call = mock_get.call_args_list[0]
        self.assertEqual(first_call.kwargs["headers"]["Authorization"], "Bearer token-1")
        self.assertIn("status=error", first_call.args[0])
        self.assertIn("page=1", first_call.args[0])
        self.assertIn("page_size=2", first_call.args[0])

    def test_write_email_lines_writes_one_email_per_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "nested" / "emails.txt"

            write_email_lines(str(output_path), ["a@example.com", "b@example.com"])

            self.assertEqual(output_path.read_text(encoding="utf-8"), "a@example.com\nb@example.com\n")

    @patch("sub2api_client.requests.request")
    def test_import_chatgpt_session_logs_in_resolves_group_and_imports(self, mock_request):
        mock_request.side_effect = [
            make_response({"code": 0, "data": {"access_token": "admin-token"}}),
            make_response({"code": 0, "data": [{"id": 7, "name": "vip", "platform": "openai"}]}),
            make_response({"code": 0, "data": {"total": 1, "created": 1, "updated": 0, "failed": 0}}),
        ]
        session = {
            "accessToken": "access-token-1",
            "sessionToken": "session-token-1",
            "expires": "2026-06-01T00:00:00.000Z",
            "user": {"email": "a@example.com"},
        }

        result = import_chatgpt_session(
            session,
            {
                "base_url": "https://solidapi.top/",
                "email": "admin@example.com",
                "password": "password-1",
                "group": "vip",
                "priority": 1,
                "timeout_seconds": 5,
            },
        )

        self.assertEqual(result["created"], 1)
        self.assertEqual(mock_request.call_args_list[0].args[1], "https://solidapi.top/api/v1/auth/login")
        self.assertEqual(mock_request.call_args_list[1].args[1], "https://solidapi.top/api/v1/admin/groups/all")
        import_call = mock_request.call_args_list[2]
        self.assertEqual(import_call.args[1], "https://solidapi.top/api/v1/admin/accounts/import/codex-session")
        self.assertEqual(import_call.kwargs["headers"]["Authorization"], "Bearer admin-token")
        payload = import_call.kwargs["json"]
        self.assertEqual(payload["group_ids"], [7])
        self.assertEqual(payload["name"], "a@example.com")
        self.assertEqual(payload["priority"], 1)
        self.assertTrue(payload["update_existing"])
        self.assertIn('"accessToken": "access-token-1"', payload["content"])

    @patch("sub2api_client.requests.request")
    def test_login_error_includes_sub2api_response_message(self, mock_request):
        mock_request.return_value = make_http_error_response(
            {"code": 401, "message": "invalid email or password", "reason": "INVALID_CREDENTIALS"}
        )

        with self.assertRaisesRegex(RuntimeError, "invalid email or password"):
            login_sub2api(
                {
                    "base_url": "https://solidapi.top/",
                    "email": "admin@example.com",
                    "password": "bad-password",
                    "timeout_seconds": 5,
                }
            )


if __name__ == "__main__":
    unittest.main()
