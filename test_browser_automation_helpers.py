import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from browser_automation import (
    apply_flaresolverr_solution,
    build_browser_proxy_settings,
    capture_debug_artifacts,
    format_artifact_summary,
    reset_browser_profile,
    solve_cloudflare_with_flaresolverr,
)


def make_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class BrowserAutomationHelperTests(unittest.TestCase):
    def test_build_browser_proxy_settings_normalizes_socks5h(self):
        settings = build_browser_proxy_settings("socks5h://user:pass@127.0.0.1:1080")

        self.assertEqual(settings["server"], "socks5://127.0.0.1:1080")
        self.assertEqual(settings["username"], "user")
        self.assertEqual(settings["password"], "pass")

    def test_apply_flaresolverr_solution_filters_cookie_fields(self):
        context = MagicMock()

        count = apply_flaresolverr_solution(
            context,
            {
                "cookies": [
                    {
                        "name": "cf_clearance",
                        "value": "cookie-value",
                        "domain": ".chatgpt.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax",
                        "expires": -1,
                        "size": 123,
                        "session": False,
                    }
                ]
            },
        )

        self.assertEqual(count, 1)
        context.add_cookies.assert_called_once_with(
            [
                {
                    "name": "cf_clearance",
                    "value": "cookie-value",
                    "domain": ".chatgpt.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ]
        )

    def test_reset_browser_profile_removes_old_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "browser_profile" / "account_a"
            profile_dir.mkdir(parents=True)
            stale_file = profile_dir / "Cookies"
            stale_file.write_text("stale-session", encoding="utf-8")

            reset_browser_profile(str(profile_dir))

            self.assertTrue(profile_dir.exists())
            self.assertEqual(list(profile_dir.iterdir()), [])

    @patch("browser_automation.time.strftime", return_value="20260524-123456")
    @patch("browser_automation.resolve_project_path")
    def test_capture_debug_artifacts_writes_debug_files(self, mock_resolve_project_path, _mock_strftime):
        with tempfile.TemporaryDirectory() as temp_dir:
            diagnostics_root = Path(temp_dir) / "artifacts"
            mock_resolve_project_path.return_value = diagnostics_root

            class FakePage:
                def screenshot(self, path, full_page):
                    Path(path).write_bytes(b"png")

                def content(self):
                    return "<html>debug</html>"

                def evaluate(self, _script):
                    return {
                        "state": "email",
                        "url": "https://chatgpt.com/auth/login",
                        "text": "email step",
                    }

            artifacts = capture_debug_artifacts(FakePage(), "User+1@example.com", "login-failed")

            self.assertTrue(Path(artifacts["screenshot"]).exists())
            self.assertTrue(Path(artifacts["html"]).exists())
            self.assertTrue(Path(artifacts["state"]).exists())
            self.assertIn("screenshot=", format_artifact_summary(artifacts))

    @patch("browser_automation.requests.post")
    def test_solve_cloudflare_with_proxy_uses_session_lifecycle(self, mock_post):
        mock_post.side_effect = [
            make_response({"status": "ok", "session": "keytoauth-session"}),
            make_response(
                {
                    "status": "ok",
                    "solution": {
                        "cookies": [{"name": "cf_clearance", "value": "x", "domain": ".chatgpt.com", "path": "/"}],
                        "userAgent": "Mozilla/5.0",
                    },
                }
            ),
            make_response({"status": "ok"}),
        ]

        solution = solve_cloudflare_with_flaresolverr(
            "https://chatgpt.com/auth/login?next=%2F",
            "http://127.0.0.1:8191/v1",
            {
                "server": "socks5://127.0.0.1:1080",
                "username": "user",
                "password": "pass",
            },
        )

        self.assertEqual(solution["userAgent"], "Mozilla/5.0")
        self.assertEqual(mock_post.call_count, 3)

        create_payload = mock_post.call_args_list[0].kwargs["json"]
        request_payload = mock_post.call_args_list[1].kwargs["json"]
        destroy_payload = mock_post.call_args_list[2].kwargs["json"]

        self.assertEqual(create_payload["cmd"], "sessions.create")
        self.assertEqual(
            create_payload["proxy"],
            {
                "url": "socks5://127.0.0.1:1080",
                "username": "user",
                "password": "pass",
            },
        )
        self.assertEqual(request_payload["cmd"], "request.get")
        self.assertIn("session", request_payload)
        self.assertEqual(destroy_payload["cmd"], "sessions.destroy")
        self.assertEqual(destroy_payload["session"], request_payload["session"])


if __name__ == "__main__":
    unittest.main()
