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
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from . import core

console = Console()


def _metric_panel(label: str, value: str, detail: str, color: str) -> Panel:
    content = Table.grid(padding=(0, 1))
    content.add_row(Text(label.upper(), style="bold bright_white"))
    content.add_row(Text(value, style=f"bold {color}"))
    content.add_row(Text(detail, style="dim"))
    return Panel(content, border_style=color, box=box.ROUNDED, padding=(0, 1))


def _progress_bar(current: int, total: int, width: int = 42) -> Text:
    if total <= 0:
        return Text("·" * width, style="grey50")
    filled = min(width, round(width * current / total))
    return Text("━" * filled, style="bright_cyan") + Text(
        "━" * (width - filled), style="grey30"
    )


def _dashboard(
    ping: core.PingResult | None,
    download_mbps: float | None,
    upload_mbps: float | None,
    phase: str,
    progress_current: int = 0,
    progress_total: int = 0,
    current_speed: float = 0.0,
    note: str = "",
) -> Group:
    ping_value = f"{ping.latency_ms:.2f}" if ping else "--"
    ping_detail = f"jitter {ping.jitter_ms:.2f} ms" if ping else "latency"
    download_value = f"{download_mbps:.2f}" if download_mbps is not None else "--"
    upload_value = f"{upload_mbps:.2f}" if upload_mbps is not None else "--"

    header = Panel(
        Text.assemble(
            ("PYSPEED", "bold bright_white"),
            ("  /  LIVE NETWORK DIAGNOSTICS", "bold cyan"),
            ("\nCloudflare edge  ·  speed.cloudflare.com", "dim"),
        ),
        border_style="bright_cyan",
        box=box.ROUNDED,
        padding=(0, 1),
    )

    metrics = Table.grid(expand=True, padding=(0, 1))
    metrics.add_column(ratio=1)
    metrics.add_column(ratio=1)
    metrics.add_column(ratio=1)
    metrics.add_row(
        _metric_panel("Ping", ping_value, f"ms  ·  {ping_detail}", "bright_white"),
        _metric_panel("Download", download_value, "Mbps", "spring_green3"),
        _metric_panel("Upload", upload_value, "Mbps", "deep_sky_blue1"),
    )

    phase_title = phase.upper() if phase != "done" else "COMPLETE"
    phase_color = "green" if phase == "done" else "bright_cyan"
    phase_table = Table.grid(expand=True, padding=(0, 1))
    phase_table.add_column()
    phase_table.add_column(justify="right")
    phase_table.add_row(
        Text(f"●  {phase_title}", style=f"bold {phase_color}"),
        Text(note or "measuring", style="dim"),
    )
    phase_table.add_row(
        _progress_bar(progress_current, progress_total),
        Text(
            f"{current_speed:.2f} Mbps" if current_speed else "warming up",
            style="bold white",
        ),
    )
    activity = Panel(phase_table, border_style="grey35", box=box.ROUNDED, padding=(0, 1))

    return Group(header, metrics, activity)


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
        with Live(_dashboard(None, None, None, "ping"), console=console,
                  refresh_per_second=10) as live:

            # --- Ping phase ---
            if not no_ping:
                live.update(_dashboard(None, None, None, "ping", note="8 probes"))
                ping_result = core.measure_ping(timeout=timeout)
                live.update(_dashboard(ping_result, None, None, "download"))

            # --- Download phase ---
            live.update(_dashboard(
                ping_result, None, None, "download", progress_total=download_bytes
            ))
            last_speed = 0.0
            for elapsed, total in core.measure_download(download_bytes, timeout=timeout):
                if elapsed > 0:
                    last_speed = core.bytes_to_mbps(total, elapsed)
                live.update(_dashboard(
                    ping_result,
                    last_speed,
                    None,
                    "download",
                    progress_current=total,
                    progress_total=download_bytes,
                    current_speed=last_speed,
                    note=f"{total / download_bytes:.0%} transferred",
                ))
            download_mbps = last_speed

            # --- Upload phase ---
            if not no_upload:
                live.update(_dashboard(
                    ping_result,
                    download_mbps,
                    None,
                    "upload",
                    progress_total=upload_bytes,
                ))
                last_speed = 0.0
                for elapsed, total in core.measure_upload(upload_bytes, timeout=timeout):
                    if elapsed > 0:
                        last_speed = core.bytes_to_mbps(total, elapsed)
                    live.update(_dashboard(
                        ping_result,
                        download_mbps,
                        last_speed,
                        "upload",
                        progress_current=total,
                        progress_total=upload_bytes,
                        current_speed=last_speed,
                        note=f"{total / upload_bytes:.0%} transferred",
                    ))
                upload_mbps = last_speed

            live.update(_dashboard(
                ping_result,
                download_mbps,
                upload_mbps,
                "done",
                progress_current=1,
                progress_total=1,
                current_speed=upload_mbps or download_mbps or 0.0,
                note="results ready",
            ))

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
