import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from api_client import fetch_email_credentials, fetch_verification_code
from main import save_json_output


def make_response(payload, headers=None):
    response = MagicMock()
    response.json.return_value = payload
    response.headers = headers or {"content-type": "application/json"}
    response.raise_for_status.return_value = None
    return response


class RuntimeHelperTests(unittest.TestCase):
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

    def test_fetch_verification_code_requires_secret(self):
        with self.assertRaisesRegex(ValueError, "Missing mail secret"):
            fetch_verification_code("a@example.com", "")


if __name__ == "__main__":
    unittest.main()
