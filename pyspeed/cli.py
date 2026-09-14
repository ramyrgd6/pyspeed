"""
pyspeed — a live, terminal speed test in the spirit of Ookla's speedtest CLI.

Usage:
    pyspeed                 # run ping + download + upload
    pyspeed --no-upload      # skip the upload phase
    pyspeed --bytes 50000000 # use a 50MB download payload instead of 100MB
"""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich import box

from . import core

console = Console()


def _speed_table(ping: core.PingResult | None, download_mbps: float | None,
                  upload_mbps: float | None, phase: str) -> Table:
    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()

    table.add_row("Server", "Cloudflare (speed.cloudflare.com)")

    if ping is not None:
        table.add_row(
            "Ping",
            f"[bold]{ping.latency_ms:6.2f} ms[/bold]  "
            f"(jitter {ping.jitter_ms:.2f} ms)",
        )
    else:
        marker = "[yellow]measuring…[/yellow]" if phase == "ping" else "-"
        table.add_row("Ping", marker)

    if download_mbps is not None:
        table.add_row("Download", f"[bold green]{download_mbps:7.2f} Mbps[/bold green]")
    elif phase == "download":
        table.add_row("Download", "[yellow]measuring…[/yellow]")
    else:
        table.add_row("Download", "-")

    if upload_mbps is not None:
        table.add_row("Upload", f"[bold magenta]{upload_mbps:7.2f} Mbps[/bold magenta]")
    elif phase == "upload":
        table.add_row("Upload", "[yellow]measuring…[/yellow]")
    else:
        table.add_row("Upload", "-")

    return table


def _panel(ping, download_mbps, upload_mbps, phase, note=""):
    table = _speed_table(ping, download_mbps, upload_mbps, phase)
    title = "[bold white]pyspeed[/bold white]"
    subtitle = note if note else None
    return Panel(table, title=title, subtitle=subtitle, border_style="cyan")


@click.command()
@click.version_option(package_name="pyspeed")
@click.option("--bytes", "download_bytes", default=core.DEFAULT_DOWNLOAD_BYTES,
              show_default=True, type=click.IntRange(min=1),
              help="Download payload size in bytes.")
@click.option("--upload-bytes", default=core.DEFAULT_UPLOAD_BYTES,
              show_default=True, type=click.IntRange(min=1),
              help="Upload payload size in bytes.")
@click.option("--timeout", default=20.0, show_default=True,
              type=click.FloatRange(min=0.1),
              help="Network timeout in seconds for each request.")
@click.option("--no-upload", is_flag=True, help="Skip the upload phase.")
@click.option("--no-ping", is_flag=True, help="Skip the ping phase.")
def main(download_bytes: int, upload_bytes: int, timeout: float,
         no_upload: bool, no_ping: bool):
    """Run a live download/upload/ping speed test in your terminal."""
    ping_result: core.PingResult | None = None
    download_mbps: float | None = None
    upload_mbps: float | None = None

    try:
        with Live(_panel(None, None, None, "ping"), console=console,
                  refresh_per_second=10) as live:

            # --- Ping phase ---
            if not no_ping:
                live.update(_panel(None, None, None, "ping"))
                ping_result = core.measure_ping(timeout=timeout)
                live.update(_panel(ping_result, None, None, "download"))

            # --- Download phase ---
            live.update(_panel(ping_result, None, None, "download"))
            last_speed = 0.0
            for elapsed, total in core.measure_download(download_bytes, timeout=timeout):
                if elapsed > 0:
                    last_speed = core.bytes_to_mbps(total, elapsed)
                live.update(_panel(ping_result, last_speed, None, "download"))
            download_mbps = last_speed

            # --- Upload phase ---
            if not no_upload:
                live.update(_panel(ping_result, download_mbps, None, "upload"))
                last_speed = 0.0
                for elapsed, total in core.measure_upload(upload_bytes, timeout=timeout):
                    if elapsed > 0:
                        last_speed = core.bytes_to_mbps(total, elapsed)
                    live.update(_panel(ping_result, download_mbps, last_speed, "upload"))
                upload_mbps = last_speed

            live.update(_panel(ping_result, download_mbps, upload_mbps, "done",
                                note="[dim]done[/dim]"))

    except ConnectionError as exc:
        console.print(f"[bold red]Connection error:[/bold red] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[bold red]Unexpected error:[/bold red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
