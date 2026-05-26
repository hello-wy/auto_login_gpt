import argparse
import os

from rich.console import Console

from cloudmail_client import load_cloudmail_config, normalize_email_lines
from config import (
    CLOUDMAIL_CONFIG_PATH,
    FLARESOLVERR_URL,
    PROXY,
    SUB2API_ACCOUNTS_URL,
    SUB2API_ACCOUNT_PRIORITY,
    SUB2API_BASE_URL,
    SUB2API_ERROR_EMAIL_OUTPUT_PATH,
    SUB2API_GROUP_NAME,
)
from runner import process_email_accounts, resolve_project_path
from sub2api_client import DEFAULT_PAGE_SIZE, fetch_error_account_emails, write_email_lines

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert email accounts to ChatGPT auth JSON files")
    parser.add_argument("--input-source", choices=["file", "sub2api-errors"], default="file")
    parser.add_argument("--email", type=str, help="Single email address for testing")
    parser.add_argument("--input", type=str, help="Path to file with email addresses (one per line)")
    parser.add_argument("--format", type=str, choices=["cpa", "sub2api", "both"], default="sub2api")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--proxy", type=str, help="Browser proxy server, e.g. socks5://127.0.0.1:1080")
    parser.add_argument("--flaresolverr-url", type=str, default=FLARESOLVERR_URL)
    parser.add_argument("--key", type=str, help=argparse.SUPPRESS)
    parser.add_argument(
        "--cloudmail-config",
        type=str,
        default=os.environ.get("CLOUDMAIL_CONFIG_PATH", CLOUDMAIL_CONFIG_PATH),
        help=f"CloudMail JSON config path (default: {CLOUDMAIL_CONFIG_PATH})",
    )
    parser.add_argument("--skip-active-cpa-emails", action="store_true")
    parser.add_argument("--cpa-management-url", type=str, default=os.environ.get("CPA_MANAGEMENT_URL"))
    parser.add_argument("--cpa-management-key", type=str, default=os.environ.get("CPA_MANAGEMENT_KEY"))
    parser.add_argument(
        "--sub2api-accounts-url",
        type=str,
        default=os.environ.get("SUB2API_ACCOUNTS_URL", SUB2API_ACCOUNTS_URL),
    )
    parser.add_argument("--sub2api-admin-token", type=str, default=os.environ.get("SUB2API_ADMIN_TOKEN"))
    parser.add_argument("--sub2api-url", type=str, default=os.environ.get("SUB2API_URL", SUB2API_BASE_URL))
    parser.add_argument("--sub2api-email", type=str, default=os.environ.get("SUB2API_EMAIL"))
    parser.add_argument("--sub2api-password", type=str, default=os.environ.get("SUB2API_PASSWORD"))
    parser.add_argument("--sub2api-group", type=str, default=os.environ.get("SUB2API_GROUP", SUB2API_GROUP_NAME))
    parser.add_argument(
        "--sub2api-priority",
        type=int,
        default=int(os.environ.get("SUB2API_PRIORITY", SUB2API_ACCOUNT_PRIORITY)),
    )
    parser.add_argument("--sub2api-page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--sub2api-error-output", type=str, default=SUB2API_ERROR_EMAIL_OUTPUT_PATH)
    return parser


def read_emails_from_args(args) -> list:
    if args.key:
        raise ValueError("Key-code input is no longer supported. Use --email or --input with one email per line.")
    config = load_cloudmail_config(resolve_project_path(args.cloudmail_config))
    if args.input_source == "sub2api-errors":
        options = build_sub2api_options(args)
        options["email_domain"] = config.domain
        input_path = fetch_sub2api_error_emails_to_file(options)
        with open(input_path, "r", encoding="utf-8") as handle:
            return normalize_email_lines(handle.read(), allowed_domain=config.domain)
    if args.email:
        return normalize_email_lines(args.email, allowed_domain=config.domain)
    if args.input:
        with open(args.input, "r", encoding="utf-8") as handle:
            return normalize_email_lines(handle.read(), allowed_domain=config.domain)
    return read_emails_interactively(config.domain)


def build_sub2api_options(args) -> dict:
    return {
        "accounts_url": args.sub2api_accounts_url,
        "token": args.sub2api_admin_token,
        "base_url": args.sub2api_url,
        "email": args.sub2api_email,
        "password": args.sub2api_password,
        "group": args.sub2api_group,
        "priority": args.sub2api_priority,
        "page_size": args.sub2api_page_size,
        "output_path": resolve_project_path(args.sub2api_error_output),
    }


def fetch_sub2api_error_emails_to_file(options: dict) -> str:
    emails = fetch_error_account_emails(options)
    if not emails:
        raise RuntimeError("No Sub2API error account emails found")
    output_path = options["output_path"]
    write_email_lines(output_path, emails)
    console.print(f"[green]✓ Saved Sub2API error emails: {output_path}[/green]")
    return output_path


def read_emails_interactively(domain: str) -> list:
    console.print("[bold cyan]Enter email addresses (one per line, empty line to finish):[/bold cyan]")
    lines = []
    while True:
        line = input().strip()
        if not line:
            break
        lines.append(line)
    return normalize_email_lines("\n".join(lines), allowed_domain=domain)


def build_run_options(args) -> dict:
    return {
        "output_format": args.format,
        "headless": args.headless,
        "proxy": args.proxy or PROXY,
        "flaresolverr_url": args.flaresolverr_url,
        "skip_active_cpa_emails": args.skip_active_cpa_emails,
        "cpa_management_url": args.cpa_management_url,
        "cpa_management_key": args.cpa_management_key,
        "cloudmail_config_path": args.cloudmail_config,
        "sub2api": build_sub2api_options(args),
    }


def main() -> None:
    args = build_parser().parse_args()
    emails = read_emails_from_args(args)
    process_email_accounts(emails, build_run_options(args))


if __name__ == "__main__":
    main()
