import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cloudmail_client import (
    CloudMailClient,
    extract_verification_code,
    load_cloudmail_config,
    normalize_cloudmail_messages,
    normalize_cloudmail_base_url,
    normalize_email_lines,
)


def make_response(payload):
    response = MagicMock()
    response.ok = True
    response.status_code = 200
    response.text = json.dumps(payload)
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class CloudMailClientTests(unittest.TestCase):
    def test_normalize_email_lines_accepts_one_email_per_line(self):
        emails = normalize_email_lines(
            "\n"
            " S45N0SG8RV@edu.arrangework.dpdns.org \n"
            "2enho3s56x@edu.arrangework.dpdns.org\n",
            allowed_domain="edu.arrangework.dpdns.org",
        )

        self.assertEqual(
            emails,
            [
                "s45n0sg8rv@edu.arrangework.dpdns.org",
                "2enho3s56x@edu.arrangework.dpdns.org",
            ],
        )

    def test_normalize_email_lines_rejects_non_email_rows(self):
        with self.assertRaisesRegex(ValueError, "line 2"):
            normalize_email_lines(
                "s45n0sg8rv@edu.arrangework.dpdns.org\nAAAA-BBBB-CCCC",
                allowed_domain="edu.arrangework.dpdns.org",
            )

    def test_normalize_email_lines_rejects_unexpected_domain(self):
        with self.assertRaisesRegex(ValueError, "expected domain"):
            normalize_email_lines(
                "user@example.com",
                allowed_domain="edu.arrangework.dpdns.org",
            )

    def test_load_cloudmail_config_reads_json_and_normalizes_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "cloudmail.json"
            config_path.write_text(
                json.dumps(
                    {
                        "api_base_url": "edu.arrangework.dpdns.org/",
                        "admin_email": " admin@edu.arrangework.dpdns.org ",
                        "admin_password": "secret",
                        "domain": "@EDU.ARRANGEWORK.DPDNS.ORG",
                    }
                ),
                encoding="utf-8",
            )

            config = load_cloudmail_config(str(config_path))

            self.assertEqual(config.api_base_url, "https://edu.arrangework.dpdns.org")
            self.assertEqual(config.admin_email, "admin@edu.arrangework.dpdns.org")
            self.assertEqual(config.admin_password, "secret")
            self.assertEqual(config.domain, "edu.arrangework.dpdns.org")

    def test_extract_verification_code_supports_subject_and_html_body(self):
        self.assertEqual(extract_verification_code("Your code is 123456", ""), "123456")
        self.assertEqual(extract_verification_code("", "<b>验证码：</b> 654321"), "654321")

    def test_normalize_cloudmail_messages_supports_data_list_payload(self):
        messages = normalize_cloudmail_messages(
            {
                "code": 200,
                "data": [
                    {
                        "toEmail": "user@edu.arrangework.dpdns.org",
                        "subject": "OpenAI",
                        "content": "Your code is 112233",
                    }
                ],
            },
            "user@edu.arrangework.dpdns.org",
        )

        self.assertEqual(messages[0]["body"], "Your code is 112233")

    @patch("cloudmail_client.requests.post")
    def test_fetch_verification_code_uses_token_and_email_list(self, mock_post):
        mock_post.side_effect = [
            make_response({"code": 200, "data": {"token": "token-1"}}),
            make_response(
                {
                    "code": 200,
                    "data": {
                        "list": [
                            {
                                "toEmail": "user@edu.arrangework.dpdns.org",
                                "subject": "OpenAI code",
                                "content": "Your code is 135790",
                                "createTime": "2026-05-24 12:00:00",
                            }
                        ]
                    },
                }
            ),
        ]
        config = load_cloudmail_config(
            {
                "api_base_url": "https://edu.arrangework.dpdns.org",
                "admin_email": "admin@edu.arrangework.dpdns.org",
                "admin_password": "secret",
                "domain": "edu.arrangework.dpdns.org",
            }
        )
        client = CloudMailClient(config, sleep=lambda _seconds: None)

        code = client.fetch_verification_code(
            "user@edu.arrangework.dpdns.org",
            max_attempts=1,
        )

        self.assertEqual(code, "135790")
        self.assertEqual(mock_post.call_args_list[0].args[0], "https://edu.arrangework.dpdns.org/api/public/genToken")
        self.assertEqual(mock_post.call_args_list[1].args[0], "https://edu.arrangework.dpdns.org/api/public/emailList")
        self.assertEqual(mock_post.call_args_list[1].kwargs["headers"]["Authorization"], "token-1")
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["toEmail"], "user@edu.arrangework.dpdns.org")

    def test_normalize_cloudmail_base_url_rejects_invalid_values(self):
        self.assertEqual(normalize_cloudmail_base_url("not a url with spaces"), "")


if __name__ == "__main__":
    unittest.main()
