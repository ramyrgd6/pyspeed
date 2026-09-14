"""Live terminal UI, logging, and graceful signal handling for pyspeed."""

from __future__ import annotations

import logging
import signal
import time
from pathlib import Path

import click
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import core

console = Console()


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1000 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"


def _inline_bars(history: tuple[float, ...], color: str, width: int = 18) -> Text:
    samples = history[-width:]
    if not samples:
        return Text("·" * width, style="grey35")
    peak = max(max(samples), 1.0)
    blocks = "▁▂▃▄▅▆▇█"
    output = "".join(blocks[min(len(blocks) - 1, round(value / peak * (len(blocks) - 1)))] for value in samples)
    return Text(output.rjust(width, "·"), style=color)


def _metric_panel(label: str, current: float, average: float, peak: float,
                  history: tuple[float, ...], color: str) -> Panel:
    content = Table.grid(padding=(0, 1))
    content.add_row(Text(label.upper(), style="bold bright_white"))
    content.add_row(Text(f"{current:7.2f} Mbps", style=f"bold {color}"))
    content.add_row(_inline_bars(history, color))
    content.add_row(Text(f"avg {average:.1f} | peak {peak:.1f} Mbps", style="dim"))
    return Panel(content, border_style=color, box=box.ROUNDED, padding=(0, 1))


def _dashboard(snapshot: core.StatsSnapshot, phase: str) -> Group:
    ping = f"{snapshot.ping_ms:.2f} ms" if snapshot.ping_ms is not None else "--"
    jitter = f"{snapshot.jitter_ms:.2f} ms" if snapshot.jitter_ms is not None else "--"
    header = Panel(
        Text.assemble(
            ("PYSPEED", "bold bright_white"),
            ("  /  CONTINUOUS NETWORK MONITOR", "bold cyan"),
            ("\nCloudflare edge  ·  download + upload workers active", "dim"),
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
        _metric_panel("Download", snapshot.current_download_mbps,
                      snapshot.average_download_mbps, snapshot.peak_download_mbps,
                      snapshot.download_history, "spring_green3"),
        _metric_panel("Upload", snapshot.current_upload_mbps,
                      snapshot.average_upload_mbps, snapshot.peak_upload_mbps,
                      snapshot.upload_history, "deep_sky_blue1"),
        Panel(
            _health_table(ping, jitter, snapshot.packet_loss_percent),
            border_style="bright_white", box=box.ROUNDED, padding=(0, 1),
        ),
    )

    details = Table.grid(expand=True, padding=(0, 1))
    details.add_column()
    details.add_column()
    details.add_column()
    details.add_column()
    details.add_row(
        Text(f"DOWNLOADED  {_format_bytes(snapshot.downloaded_bytes)}", style="dim"),
        Text(f"UPLOADED  {_format_bytes(snapshot.uploaded_bytes)}", style="dim"),
        Text(f"ELAPSED  {snapshot.elapsed_seconds:6.1f}s", style="dim"),
        Text(f"LOSS  {snapshot.packet_loss_percent:5.1f}%", style="dim"),
    )
    status = snapshot.last_error or f"{phase.upper()}  ·  Ctrl+C to stop"
    footer = Panel(Text(status, style="yellow" if snapshot.last_error else "dim"),
                   border_style="grey35", box=box.ROUNDED, padding=(0, 1))
    return Group(header, metrics, details, footer)


def _health_table(ping: str, jitter: str, loss: float) -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_row(Text("NETWORK HEALTH", style="bold bright_white"))
    table.add_row(Text(f"ping       {ping}", style="bold white"))
    table.add_row(Text(f"jitter     {jitter}", style="dim"))
    table.add_row(Text(f"packet loss {loss:.1f}%", style="dim"))
    return table


def _configure_logging(path: str) -> tuple[logging.Logger, logging.Handler]:
    handler = logging.FileHandler(Path(path), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("pyspeed")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger, handler


@click.command()
@click.version_option(package_name="pyspeed")
@click.option("--bytes", "download_bytes", default=core.DEFAULT_DOWNLOAD_BYTES,
              show_default=True, type=click.IntRange(min=1),
              help="Payload size for each download request.")
@click.option("--upload-bytes", default=core.DEFAULT_UPLOAD_BYTES,
              show_default=True, type=click.IntRange(min=1),
              help="Payload size for each upload request.")
@click.option("--timeout", default=20.0, show_default=True,
              type=click.FloatRange(min=0.1), help="Network timeout in seconds.")
@click.option("--refresh", default=10.0, show_default=True,
              type=click.FloatRange(min=1.0), help="UI refreshes per second.")
@click.option("--log-file", default="pyspeed.log", show_default=True,
              type=click.Path(dir_okay=False), help="Session log file path.")
@click.option("--no-upload", is_flag=True, help="Disable the upload worker.")
@click.option("--no-ping", is_flag=True, help="Disable the ping worker.")
def main(download_bytes: int, upload_bytes: int, timeout: float,
         refresh: float, log_file: str, no_upload: bool, no_ping: bool) -> None:
    """Continuously measure download and upload until Ctrl+C."""
    logger, handler = _configure_logging(log_file)
    stats = core.SessionStats()
    runner = core.NetworkRunner(
        stats,
        download_bytes,
        upload_bytes,
        timeout,
        enable_upload=not no_upload,
        enable_ping=not no_ping,
    )
    stop_requested = False
    previous_handler = signal.getsignal(signal.SIGINT)

    def request_stop(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        runner.stop()

    signal.signal(signal.SIGINT, request_stop)
    logger.info("session started download_bytes=%s upload_bytes=%s", download_bytes, upload_bytes)
    runner.start()
    try:
        with Live(_dashboard(stats.snapshot(), "starting"), console=console,
                  refresh_per_second=refresh, screen=False) as live:
            while not stop_requested:
                live.update(_dashboard(stats.snapshot(), "running"))
                time.sleep(1 / refresh)
    except KeyboardInterrupt:
        request_stop(signal.SIGINT, None)
    finally:
        runner.stop()
        runner.join(timeout=3.0)
        final = stats.snapshot()
        logger.info(
            "session stopped elapsed=%.1f downloaded=%s uploaded=%s",
            final.elapsed_seconds, final.downloaded_bytes, final.uploaded_bytes,
        )
        handler.flush()
        logger.removeHandler(handler)
        handler.close()
        signal.signal(signal.SIGINT, previous_handler)
        console.print("[yellow]Stopped cleanly. Logs flushed.[/yellow]")


if __name__ == "__main__":
    main()