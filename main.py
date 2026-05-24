import os
import json
import argparse
import sys
import traceback
from typing import List, Set, Tuple
from datetime import datetime
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from api_client import (
    build_mail_api_url,
    fetch_cpa_active_emails,
    fetch_email_credentials,
    normalize_email,
)
from browser_automation import login_chatgpt
from session_converter import convert_to_cpa, convert_to_sub2api, to_email_key
from config import OUTPUT_DIR, BROWSER_PROFILE_DIR, LOG_DIR, PROXY, FLARESOLVERR_URL

console = Console()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve_project_path(path: str) -> str:
    """Resolve relative config paths from the project root."""
    if os.path.isabs(path):
        return path
    return os.path.join(BASE_DIR, path)


class TeeStream:
    """Write runtime output to both the terminal and a log file."""

    def __init__(self, *streams):
        self.streams = streams
        self.encoding = getattr(streams[0], "encoding", "utf-8")

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return any(getattr(stream, "isatty", lambda: False)() for stream in self.streams)

    def fileno(self):
        return self.streams[0].fileno()


def configure_run_logging(log_dir: str):
    """Create a per-run log file and tee stdout/stderr into it."""
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log")
    log_handle = open(log_path, "w", encoding="utf-8")

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = TeeStream(original_stdout, log_handle)
    sys.stderr = TeeStream(original_stderr, log_handle)
    return log_path, log_handle, original_stdout, original_stderr


def restore_run_logging(log_handle, original_stdout, original_stderr) -> None:
    """Restore stdout/stderr after a run completes."""
    sys.stdout = original_stdout
    sys.stderr = original_stderr
    log_handle.close()


def save_json_output(output_path: str, payload: dict, label: str) -> None:
    """Write JSON atomically so partial files are not left behind on failure."""
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)
    temp_path = f"{output_path}.tmp"

    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temp_path, output_path)
    except Exception as error:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise RuntimeError(f"Failed to save {label} to {output_path}: {error}") from error


def filter_items_by_email_set(items: List[dict], excluded_emails: Set[str]) -> Tuple[List[dict], List[dict]]:
    """Split fetched credential items into kept and skipped lists by normalized email."""
    if not excluded_emails:
        return items, []

    kept_items = []
    skipped_items = []
    for item in items:
        normalized = normalize_email(item.get("email"))
        if normalized and normalized in excluded_emails:
            skipped_items.append(item)
            continue
        kept_items.append(item)
    return kept_items, skipped_items


def process_keys(
    codes: List[str],
    output_format: str = "both",
    headless: bool = False,
    proxy: str = None,
    flaresolverr_url: str = FLARESOLVERR_URL,
    skip_active_cpa_emails: bool = False,
    cpa_management_url: str = None,
    cpa_management_key: str = None,
    mail_server_base_url: str = None,
):
    """
    Process key codes and convert to auth JSON files.

    Args:
        codes: List of key codes
        output_format: Output format (cpa, sub2api, both)
        headless: Run browser in headless mode
        proxy: Browser proxy server
        flaresolverr_url: FlareSolverr API endpoint
        skip_active_cpa_emails: Skip emails already active in CLIProxyAPI
        cpa_management_url: CLIProxyAPI base URL or management endpoint
        cpa_management_key: CLIProxyAPI management bearer token
        mail_server_base_url: Optional override for the key pickup server base URL
    """
    output_dir = resolve_project_path(OUTPUT_DIR)
    browser_profile_root = resolve_project_path(BROWSER_PROFILE_DIR)
    log_dir = resolve_project_path(LOG_DIR)
    log_path, log_handle, original_stdout, original_stderr = configure_run_logging(log_dir)
    mail_keys_api = build_mail_api_url(mail_server_base_url, "mail-keys") if mail_server_base_url else None
    mail_code_api = build_mail_api_url(mail_server_base_url, "mail-code") if mail_server_base_url else None

    global console
    console = Console(file=sys.stdout)

    try:
        console.print(f"\n[bold cyan]Processing {len(codes)} key codes...[/bold cyan]\n")
        console.print(f"[dim]Run log: {log_path}[/dim]")
        if mail_server_base_url:
            console.print(f"[dim]Mail pickup server: {mail_server_base_url}[/dim]")

        # Step 1: Fetch email credentials
        console.print("[yellow]Step 1: Fetching email credentials...[/yellow]")
        items = fetch_email_credentials(codes, mail_keys_api=mail_keys_api or None)

        if not items:
            console.print("[red]✗ No valid credentials fetched. Exiting.[/red]")
            return

        console.print(f"[green]✓ Got {len(items)} email/secret pairs[/green]\n")

        skipped_by_cpa_count = 0
        if skip_active_cpa_emails:
            if not cpa_management_url or not cpa_management_key:
                raise ValueError(
                    "CPA active-email filtering requires both cpa_management_url and cpa_management_key"
                )

            console.print("[yellow]Step 1.5: Fetching active emails from CPA...[/yellow]")
            active_cpa_emails = fetch_cpa_active_emails(cpa_management_url, cpa_management_key)
            items, skipped_items = filter_items_by_email_set(items, active_cpa_emails)
            skipped_by_cpa_count = len(skipped_items)

            console.print(
                f"[green]✓ CPA filtering complete: kept {len(items)}, "
                f"skipped {skipped_by_cpa_count} active email(s)[/green]\n"
            )

            if skipped_items:
                preview = ", ".join(
                    item.get("email", "<missing-email>") for item in skipped_items[:5]
                )
                console.print(f"[dim]Skipped CPA-active emails: {preview}[/dim]")
                if len(skipped_items) > 5:
                    console.print(f"[dim]... and {len(skipped_items) - 5} more[/dim]")

            if not items:
                console.print("[yellow]No accounts left after CPA active-email filtering. Exiting.[/yellow]")
                return

        # Create output directory
        os.makedirs(output_dir, exist_ok=True)

        # Step 2: Process each account
        success_count = 0
        fail_count = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Processing accounts...", total=len(items))

            for i, item in enumerate(items):
                email = item.get("email")
                secret = item.get("secret")

                console.print(f"\n[bold]Account {i+1}/{len(items)}: {email}[/bold]")

                try:
                    if not email or not secret:
                        raise ValueError("Credential item is missing email or secret")

                    # Login and extract session
                    console.print("[yellow]→ Logging in to ChatGPT...[/yellow]")
                    profile_dir = os.path.join(browser_profile_root, to_email_key(email))
                    session = login_chatgpt(
                        email,
                        None,
                        profile_dir,
                        headless,
                        secret,
                        proxy,
                        flaresolverr_url,
                        mail_code_api,
                    )

                    if not session:
                        raise RuntimeError("Failed to extract session")

                    # Convert and save
                    email_key = to_email_key(email)

                    if output_format in ["cpa", "both"]:
                        cpa_json = convert_to_cpa(session)
                        cpa_path = os.path.join(output_dir, f"{email_key}_cpa.json")
                        save_json_output(cpa_path, cpa_json, "CPA output")
                        console.print(f"[green]✓ Saved CPA: {cpa_path}[/green]")

                    if output_format in ["sub2api", "both"]:
                        sub2api_json = convert_to_sub2api(session)
                        sub2api_path = os.path.join(output_dir, f"{email_key}_sub2api.json")
                        save_json_output(sub2api_path, sub2api_json, "Sub2API output")
                        console.print(f"[green]✓ Saved Sub2API: {sub2api_path}[/green]")

                    success_count += 1
                    console.print(f"[bold green]✓ Account {i+1} completed successfully[/bold green]")

                except Exception as error:
                    fail_count += 1
                    log_handle.write(f"\n=== Account {i+1} failure traceback ===\n")
                    traceback.print_exc(file=log_handle)
                    log_handle.flush()
                    console.print(f"[bold red]✗ Account {i+1} failed: {error}[/bold red]")
                    console.print(f"[dim]See run log: {log_path}[/dim]")

                progress.update(task, advance=1)

        # Summary
        console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
        console.print("[bold]Summary:[/bold]")
        console.print(f"  Total: {len(items)}")
        if skip_active_cpa_emails:
            console.print(f"  [yellow]↷ Skipped by CPA filter: {skipped_by_cpa_count}[/yellow]")
        console.print(f"  [green]✓ Successful: {success_count}[/green]")
        console.print(f"  [red]✗ Failed: {fail_count}[/red]")
        console.print(f"  Output directory: {output_dir}")
        console.print(f"  Run log: {log_path}")
        console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")
    finally:
        restore_run_logging(log_handle, original_stdout, original_stderr)
        console = Console()


def main():
    parser = argparse.ArgumentParser(description="Convert key codes to ChatGPT auth JSON files")
    parser.add_argument("--key", type=str, help="Single key code for testing")
    parser.add_argument("--input", type=str, help="Path to file with key codes (one per line)")
    parser.add_argument("--format", type=str, choices=["cpa", "sub2api", "both"], default="both",
                        help="Output format (default: both)")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--proxy", type=str, help="Browser proxy server (e.g., socks5://127.0.0.1:1080)")
    parser.add_argument("--flaresolverr-url", type=str, default=FLARESOLVERR_URL,
                        help=f"FlareSolverr API endpoint (default: {FLARESOLVERR_URL})")
    parser.add_argument(
        "--mail-server-base-url",
        type=str,
        default=os.environ.get("MAIL_SERVER_BASE_URL"),
        help=(
            "Override the key pickup server base URL; supports either the site root or "
            "/api/pickup base path. Defaults to MAIL_SERVER_BASE_URL env var."
        ),
    )
    parser.add_argument(
        "--skip-active-cpa-emails",
        action="store_true",
        help="Skip emails that are already active in a CLIProxyAPI management backend",
    )
    parser.add_argument(
        "--cpa-management-url",
        type=str,
        default=os.environ.get("CPA_MANAGEMENT_URL"),
        help="CLIProxyAPI base URL or management endpoint; defaults to CPA_MANAGEMENT_URL env var",
    )
    parser.add_argument(
        "--cpa-management-key",
        type=str,
        default=os.environ.get("CPA_MANAGEMENT_KEY"),
        help="CLIProxyAPI management bearer token; defaults to CPA_MANAGEMENT_KEY env var",
    )

    args = parser.parse_args()

    codes = []

    if args.key:
        codes = [args.key]
    elif args.input:
        with open(args.input, 'r', encoding='utf-8') as f:
            codes = [line.strip() for line in f if line.strip()]
    else:
        # Interactive mode
        console.print("[bold cyan]Enter key codes (one per line, empty line to finish):[/bold cyan]")
        while True:
            code = input().strip()
            if not code:
                break
            codes.append(code)

    if not codes:
        console.print("[red]No key codes provided. Exiting.[/red]")
        return

    # Use proxy from args or config
    proxy = args.proxy or PROXY

    process_keys(
        codes,
        args.format,
        args.headless,
        proxy,
        args.flaresolverr_url,
        args.skip_active_cpa_emails,
        args.cpa_management_url,
        args.cpa_management_key,
        args.mail_server_base_url,
    )


if __name__ == "__main__":
    main()
