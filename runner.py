import json
import os
import sys
import traceback
from datetime import datetime
from typing import List, Set, Tuple

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from api_client import fetch_cpa_active_emails, normalize_email
from browser_automation import login_chatgpt
from cloudmail_client import CloudMailClient, load_cloudmail_config
from config import (
    BROWSER_PROFILE_DIR,
    CLOUDMAIL_API_TIMEOUT,
    CLOUDMAIL_CODE_FETCH_RETRY_DELAY,
    CLOUDMAIL_CONFIG_PATH,
    FLARESOLVERR_URL,
    LOG_DIR,
    OUTPUT_DIR,
)
from session_converter import convert_to_cpa, convert_to_sub2api, to_email_key

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
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log")
    log_handle = open(log_path, "w", encoding="utf-8")
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = TeeStream(original_stdout, log_handle)
    sys.stderr = TeeStream(original_stderr, log_handle)
    return log_path, log_handle, original_stdout, original_stderr


def restore_run_logging(log_handle, original_stdout, original_stderr) -> None:
    sys.stdout = original_stdout
    sys.stderr = original_stderr
    log_handle.close()


def save_json_output(output_path: str, payload: dict, label: str) -> None:
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)
    temp_path = f"{output_path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temp_path, output_path)
    except Exception as error:
        remove_temp_file(temp_path)
        raise RuntimeError(f"Failed to save {label} to {output_path}: {error}") from error


def remove_temp_file(temp_path: str) -> None:
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except OSError:
        pass


def filter_items_by_email_set(items: List[dict], excluded_emails: Set[str]) -> Tuple[List[dict], List[dict]]:
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


def build_email_items(emails: List[str]) -> List[dict]:
    return [{"email": email} for email in emails]


def create_cloudmail_client(config_path: str) -> CloudMailClient:
    config = load_cloudmail_config(resolve_project_path(config_path))
    return CloudMailClient(
        config,
        timeout_seconds=CLOUDMAIL_API_TIMEOUT,
        poll_interval_seconds=CLOUDMAIL_CODE_FETCH_RETRY_DELAY,
    )


def apply_cpa_filter(items: List[dict], options: dict, console: Console) -> Tuple[List[dict], int]:
    if not options.get("skip_active_cpa_emails"):
        return items, 0
    cpa_url = options.get("cpa_management_url")
    cpa_key = options.get("cpa_management_key")
    if not cpa_url or not cpa_key:
        raise ValueError("CPA active-email filtering requires both cpa_management_url and cpa_management_key")
    console.print("[yellow]Step 1.5: Fetching active emails from CPA...[/yellow]")
    active_emails = fetch_cpa_active_emails(cpa_url, cpa_key)
    kept_items, skipped_items = filter_items_by_email_set(items, active_emails)
    report_cpa_filter(skipped_items, kept_items, console)
    return kept_items, len(skipped_items)


def report_cpa_filter(skipped_items: List[dict], kept_items: List[dict], console: Console) -> None:
    console.print(
        f"[green]✓ CPA filtering complete: kept {len(kept_items)}, "
        f"skipped {len(skipped_items)} active email(s)[/green]\n"
    )
    if not skipped_items:
        return
    preview = ", ".join(item.get("email", "<missing-email>") for item in skipped_items[:5])
    console.print(f"[dim]Skipped CPA-active emails: {preview}[/dim]")
    if len(skipped_items) > 5:
        console.print(f"[dim]... and {len(skipped_items) - 5} more[/dim]")


def process_email_accounts(emails: List[str], options: dict) -> None:
    log_dir = resolve_project_path(LOG_DIR)
    log_path, log_handle, original_stdout, original_stderr = configure_run_logging(log_dir)
    console = Console(file=sys.stdout)
    try:
        run_accounts_with_logging(emails, options, log_path, log_handle, console)
    finally:
        restore_run_logging(log_handle, original_stdout, original_stderr)


def run_accounts_with_logging(
    emails: List[str],
    options: dict,
    log_path: str,
    log_handle,
    console: Console,
) -> None:
    console.print(f"\n[bold cyan]Processing {len(emails)} email account(s)...[/bold cyan]\n")
    console.print(f"[dim]Run log: {log_path}[/dim]")
    console.print("[yellow]Step 1: Loading CloudMail config...[/yellow]")
    cloudmail_client = create_cloudmail_client(options.get("cloudmail_config_path", CLOUDMAIL_CONFIG_PATH))
    console.print(f"[green]✓ Loaded CloudMail config for {cloudmail_client.config.domain}[/green]\n")
    items, skipped_count = apply_cpa_filter(build_email_items(emails), options, console)
    if not items:
        console.print("[yellow]No accounts left after CPA active-email filtering. Exiting.[/yellow]")
        return
    summary = process_account_items(items, options, cloudmail_client, log_path, log_handle, console)
    print_summary(summary, skipped_count, log_path, console)


def process_account_items(
    items: List[dict],
    options: dict,
    cloudmail_client: CloudMailClient,
    log_path: str,
    log_handle,
    console: Console,
) -> dict:
    output_dir = resolve_project_path(OUTPUT_DIR)
    os.makedirs(output_dir, exist_ok=True)
    summary = {"total": len(items), "success": 0, "fail": 0, "output_dir": output_dir}
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), console=console) as progress:
        task = progress.add_task("[cyan]Processing accounts...", total=len(items))
        for index, item in enumerate(items):
            handle_account(index, item, options, cloudmail_client, summary, log_path, log_handle, console)
            progress.update(task, advance=1)
    return summary


def handle_account(index: int, item: dict, options: dict, cloudmail_client: CloudMailClient, summary: dict, log_path: str, log_handle, console: Console) -> None:
    email = item.get("email")
    console.print(f"\n[bold]Account {index + 1}/{summary['total']}: {email}[/bold]")
    try:
        if not email:
            raise ValueError("Account item is missing email")
        session = login_account(email, options, cloudmail_client, console)
        save_session_outputs(email, session, options.get("output_format", "both"), summary["output_dir"], console)
        summary["success"] += 1
        console.print(f"[bold green]✓ Account {index + 1} completed successfully[/bold green]")
    except Exception as error:
        summary["fail"] += 1
        log_handle.write(f"\n=== Account {index + 1} failure traceback ===\n")
        traceback.print_exc(file=log_handle)
        log_handle.flush()
        console.print(f"[bold red]✗ Account {index + 1} failed: {error}[/bold red]")
        console.print(f"[dim]See run log: {log_path}[/dim]")


def login_account(email: str, options: dict, cloudmail_client: CloudMailClient, console: Console) -> dict:
    console.print("[yellow]→ Logging in to ChatGPT...[/yellow]")
    profile_root = resolve_project_path(BROWSER_PROFILE_DIR)
    profile_dir = os.path.join(profile_root, to_email_key(email))
    session = login_chatgpt(
        email,
        None,
        profile_dir,
        options.get("headless", False),
        cloudmail_client,
        options.get("proxy"),
        options.get("flaresolverr_url", FLARESOLVERR_URL),
    )
    if not session:
        raise RuntimeError("Failed to extract session")
    return session


def save_session_outputs(email: str, session: dict, output_format: str, output_dir: str, console: Console) -> None:
    email_key = to_email_key(email)
    if output_format in ["cpa", "both"]:
        cpa_path = os.path.join(output_dir, f"{email_key}_cpa.json")
        save_json_output(cpa_path, convert_to_cpa(session), "CPA output")
        console.print(f"[green]✓ Saved CPA: {cpa_path}[/green]")
    if output_format in ["sub2api", "both"]:
        sub2api_path = os.path.join(output_dir, f"{email_key}_sub2api.json")
        save_json_output(sub2api_path, convert_to_sub2api(session), "Sub2API output")
        console.print(f"[green]✓ Saved Sub2API: {sub2api_path}[/green]")


def print_summary(summary: dict, skipped_count: int, log_path: str, console: Console) -> None:
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print("[bold]Summary:[/bold]")
    console.print(f"  Total: {summary['total']}")
    if skipped_count:
        console.print(f"  [yellow]↷ Skipped by CPA filter: {skipped_count}[/yellow]")
    console.print(f"  [green]✓ Successful: {summary['success']}[/green]")
    console.print(f"  [red]✗ Failed: {summary['fail']}[/red]")
    console.print(f"  Output directory: {summary['output_dir']}")
    console.print(f"  Run log: {log_path}")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")
