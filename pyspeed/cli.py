"""Live terminal UI, logging, and graceful signal handling for pyspeed."""

from __future__ import annotations

import logging
import csv
import json
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
NETWORK_INFO = core.network_info()


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1000 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"


def _format_rate(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.2f} Gbps"
    if value >= 1:
        return f"{value:.2f} Mbps"
    return f"{value * 1000:.0f} Kbps"


def _inline_bars(history: tuple[float, ...], color: str, width: int = 18) -> Text:
    samples = history[-width:]
    if not samples:
        return Text("·" * width, style="grey35")
    peak = max(max(samples), 1.0)
    blocks = "▁▂▃▄▅▆▇█"
    output = "".join(blocks[min(len(blocks) - 1, round(value / peak * (len(blocks) - 1)))] for value in samples)
    return Text(output.rjust(width, "·"), style=color)


def _metric_panel(label: str, current: float, average: float, peak: float,
                  history: tuple[float, ...], color: str,
                  show_graph: bool = True) -> Panel:
    content = Table.grid(padding=(0, 1))
    content.add_row(Text(label.upper(), style="bold bright_white"))
    content.add_row(Text(f"{_format_rate(current):>11}", style=f"bold {color}"))
    if show_graph:
        content.add_row(_inline_bars(history, color))
    content.add_row(Text(f"avg {_format_rate(average)} | peak {_format_rate(peak)}", style="dim"))
    return Panel(content, border_style=color, box=box.ROUNDED, padding=(0, 1))


def _dashboard(snapshot: core.StatsSnapshot, phase: str,
               show_graph: bool = True) -> Group:
    ping = f"{snapshot.ping_ms:.2f} ms" if snapshot.ping_ms is not None else "--"
    jitter = f"{snapshot.jitter_ms:.2f} ms" if snapshot.jitter_ms is not None else "--"
    header = Panel(
        Text.assemble(
            ("PYSPEED", "bold bright_white"),
            ("  /  CONTINUOUS NETWORK MONITOR", "bold cyan"),
            (f"\nCloudflare edge  ·  {NETWORK_INFO['hostname']} ({NETWORK_INFO['local_ip']})", "dim"),
        ),
        border_style="bright_cyan",
        box=box.ROUNDED,
        padding=(0, 1),
    )

    download_panel = _metric_panel(
        "Download", snapshot.current_download_mbps,
        snapshot.average_download_mbps, snapshot.peak_download_mbps,
        snapshot.download_history, "spring_green3", show_graph,
    )
    upload_panel = _metric_panel(
        "Upload", snapshot.current_upload_mbps,
        snapshot.average_upload_mbps, snapshot.peak_upload_mbps,
        snapshot.upload_history, "deep_sky_blue1", show_graph,
    )
    health_panel = Panel(
        _health_table(ping, jitter, snapshot.packet_loss_percent,
                      snapshot.latency_history if show_graph else ()),
        border_style="bright_white", box=box.ROUNDED, padding=(0, 1),
    )
    metrics = Table.grid(expand=True, padding=(0, 1))
    if console.size.width < 100:
        metrics.add_column(ratio=1)
        metrics.add_column(ratio=1)
        metrics.add_row(download_panel, upload_panel)
        metrics.add_row(health_panel, "")
    else:
        metrics.add_column(ratio=1)
        metrics.add_column(ratio=1)
        metrics.add_column(ratio=1)
        metrics.add_row(download_panel, upload_panel, health_panel)

    windows = Table.grid(expand=True, padding=(0, 1))
    windows.add_column()
    windows.add_column()
    windows.add_column()
    windows.add_row(
        Text(f"1s  ↓ {_format_rate(snapshot.download_1s_mbps)}  ↑ {_format_rate(snapshot.upload_1s_mbps)}", style="dim"),
        Text(f"10s  ↓ {_format_rate(snapshot.download_10s_mbps)}  ↑ {_format_rate(snapshot.upload_10s_mbps)}", style="dim"),
        Text(f"60s  ↓ {_format_rate(snapshot.download_60s_mbps)}  ↑ {_format_rate(snapshot.upload_60s_mbps)}", style="dim"),
    )

    details = Table.grid(expand=True, padding=(0, 1))
    details.add_column()
    details.add_column()
    details.add_row(
        Text(f"DOWNLOADED  {_format_bytes(snapshot.downloaded_bytes)}", style="dim"),
        Text(f"UPLOADED  {_format_bytes(snapshot.uploaded_bytes)}", style="dim"),
    )
    details.add_row(
        Text(f"ELAPSED  {snapshot.elapsed_seconds:6.1f}s", style="dim"),
        Text(f"RECONNECTS  {snapshot.reconnects}", style="dim"),
    )
    latency = Table.grid(expand=True, padding=(0, 1))
    latency.add_column()
    latency.add_column()
    latency.add_column()
    latency.add_row(
        Text(f"IDLE  {snapshot.idle_ping_ms:.1f} ms" if snapshot.idle_ping_ms is not None else "IDLE  --", style="dim"),
        Text(f"LOADED  {snapshot.loaded_ping_ms:.1f} ms" if snapshot.loaded_ping_ms is not None else "LOADED  --", style="dim"),
        Text(f"INCREASE  +{snapshot.latency_increase_ms:.1f} ms" if snapshot.latency_increase_ms is not None else "INCREASE  --", style="dim"),
    )
    status = snapshot.last_error or f"{phase.upper()}  ·  Ctrl+C to stop"
    footer = Panel(Text(status, style="yellow" if snapshot.last_error else "dim"),
                   border_style="grey35", box=box.ROUNDED, padding=(0, 1))
    event_panel = _event_panel(snapshot.events)
    return Group(header, metrics, windows, details, latency, event_panel, footer)


def _health_table(ping: str, jitter: str, loss: float,
                  latency_history: tuple[float, ...]) -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_row(Text("NETWORK HEALTH", style="bold bright_white"))
    table.add_row(Text(f"ping       {ping}", style="bold white"))
    table.add_row(Text(f"jitter     {jitter}", style="dim"))
    table.add_row(Text(f"packet loss {loss:.1f}%", style="dim"))
    table.add_row(_inline_bars(latency_history, "yellow", width=18))
    return table


def _event_panel(events: tuple[core.DiagnosticEvent, ...]) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="dim")
    table.add_column()
    recent = events[-3:]
    if not recent:
        table.add_row("EVENTS", "No anomalies detected")
    else:
        for event in recent:
            stamp = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
            style = "red" if event.severity == "critical" else "yellow" if event.severity == "warning" else "dim"
            table.add_row(Text(stamp, style="dim"), Text(event.message, style=style))
    return Panel(table, title="[bold bright_white]EVENT TIMELINE[/bold bright_white]",
                 border_style="grey35", box=box.ROUNDED, padding=(0, 1))


def _configure_logging(path: str) -> tuple[logging.Logger, logging.Handler]:
    handler = logging.FileHandler(Path(path), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("pyspeed")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger, handler


class _SnapshotWriter:
    """Periodic CSV or JSON-lines writer with bounded in-memory state."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.file = self.path.open("a", newline="", encoding="utf-8")
        self.csv_writer = csv.writer(self.file) if self.path.suffix.lower() == ".csv" else None
        if self.csv_writer and self.file.tell() == 0:
            self.csv_writer.writerow(self.fields)
            self.file.flush()

    fields = (
        "timestamp", "download_mbps", "upload_mbps", "download_1s_mbps",
        "upload_1s_mbps", "ping_ms", "jitter_ms", "packet_loss_percent",
        "downloaded_bytes", "uploaded_bytes", "elapsed_seconds", "reconnects",
    )

    def write(self, snapshot: core.StatsSnapshot) -> None:
        record = {
            "timestamp": time.time(),
            "download_mbps": snapshot.current_download_mbps,
            "upload_mbps": snapshot.current_upload_mbps,
            "download_1s_mbps": snapshot.download_1s_mbps,
            "upload_1s_mbps": snapshot.upload_1s_mbps,
            "ping_ms": snapshot.loaded_ping_ms,
            "jitter_ms": snapshot.jitter_ms,
            "packet_loss_percent": snapshot.packet_loss_percent,
            "downloaded_bytes": snapshot.downloaded_bytes,
            "uploaded_bytes": snapshot.uploaded_bytes,
            "elapsed_seconds": snapshot.elapsed_seconds,
            "reconnects": snapshot.reconnects,
        }
        if self.csv_writer:
            self.csv_writer.writerow([record[field] for field in self.fields])
        else:
            self.file.write(json.dumps(record) + "\n")
        self.file.flush()

    def close(self) -> None:
        self.file.flush()
        self.file.close()


def _summary(snapshot: core.StatsSnapshot) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold bright_white")
    table.add_column()
    table.add_row("SESSION SUMMARY", "")
    table.add_row("Runtime", f"{snapshot.elapsed_seconds:.1f}s")
    table.add_row("Download", f"avg {_format_rate(snapshot.average_download_mbps)} | peak {_format_rate(snapshot.peak_download_mbps)} | min {_format_rate(snapshot.min_sustained_download_mbps)}")
    table.add_row("Upload", f"avg {_format_rate(snapshot.average_upload_mbps)} | peak {_format_rate(snapshot.peak_upload_mbps)} | min {_format_rate(snapshot.min_sustained_upload_mbps)}")
    table.add_row("Latency", f"idle {snapshot.idle_ping_ms or 0:.1f} ms | loaded {snapshot.loaded_ping_ms or 0:.1f} ms | jitter {snapshot.jitter_ms or 0:.1f} ms")
    table.add_row("Traffic", f"down {_format_bytes(snapshot.downloaded_bytes)} | up {_format_bytes(snapshot.uploaded_bytes)}")
    table.add_row("Health", f"loss {snapshot.packet_loss_percent:.1f}% | reconnects {snapshot.reconnects} | stability {snapshot.stability}")
    return Panel(table, title="[bold bright_cyan]pyspeed[/bold bright_cyan]", border_style="bright_cyan", box=box.ROUNDED)


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
@click.option("--refresh", default=250.0, show_default=True,
              type=click.FloatRange(min=50.0), help="Dashboard refresh interval in milliseconds.")
@click.option("--log", "--log-file", "log_file", default="pyspeed.csv", show_default=True,
              type=click.Path(dir_okay=False), help="CSV or JSON-lines session log path.")
@click.option("--download-limit", type=str, help="Real download limit, e.g. 80M or 500Kbps.")
@click.option("--upload-limit", type=str, help="Real upload limit, e.g. 30M or 10Mbps.")
@click.option("--connections", default=1, show_default=True,
              type=click.IntRange(1, 4), help="Parallel connections per direction.")
@click.option("--history", default=64, show_default=True,
              type=click.IntRange(16, 120), help="Samples retained for scrolling history.")
@click.option("--no-graph", is_flag=True, help="Hide scrolling history bars.")
@click.option("--no-upload", is_flag=True, help="Disable the upload worker.")
@click.option("--no-ping", is_flag=True, help="Disable the ping worker.")
def main(download_bytes: int, upload_bytes: int, timeout: float,
         refresh: float, log_file: str, download_limit: str | None,
         upload_limit: str | None, connections: int, history: int,
         no_graph: bool, no_upload: bool, no_ping: bool) -> None:
    """Continuously measure download and upload until Ctrl+C."""
    logger, handler = _configure_logging(log_file + ".events")
    stats = core.SessionStats(history_limit=history)
    writer = _SnapshotWriter(log_file)
    if not no_ping:
        try:
            idle = core.measure_ping(samples=3, timeout=min(timeout, 5.0))
            stats.set_idle_ping(idle.latency_ms)
        except (ConnectionError, OSError) as exc:
            logger.warning("idle ping unavailable: %s", exc)
    runner = core.NetworkRunner(
        stats,
        download_bytes,
        upload_bytes,
        timeout,
        enable_upload=not no_upload,
        enable_ping=not no_ping,
        download_limit=core.parse_rate(download_limit),
        upload_limit=core.parse_rate(upload_limit),
        connections=connections,
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
        with Live(_dashboard(stats.snapshot(), "starting", show_graph=not no_graph), console=console,
              refresh_per_second=1000 / refresh, screen=True) as live:
            next_log = 0.0
            while not stop_requested:
                snapshot = stats.snapshot()
                stats.detect_events()
                snapshot = stats.snapshot()
                live.update(_dashboard(snapshot, "running", show_graph=not no_graph))
                if snapshot.elapsed_seconds >= next_log:
                    writer.write(snapshot)
                    next_log = snapshot.elapsed_seconds + 1.0
                time.sleep(refresh / 1000)
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
        writer.write(final)
        writer.close()
        handler.flush()
        logger.removeHandler(handler)
        handler.close()
        signal.signal(signal.SIGINT, previous_handler)
        console.print(_summary(final))
        console.print("[yellow]Stopped cleanly. Logs flushed.[/yellow]")


if __name__ == "__main__":
    main()