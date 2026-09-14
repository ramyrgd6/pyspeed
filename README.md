# pyspeed

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-2ea44f)

**A live internet speed test for your terminal, built in Python.**

```text
╭──────────────────────────────────────────────────────────────────────────────╮
│ PYSPEED  /  LIVE NETWORK DIAGNOSTICS                                         │
│ Cloudflare edge  ·  speed.cloudflare.com                                     │
╰──────────────────────────────────────────────────────────────────────────────╯
╭────────────────────────╮ ╭────────────────────────╮ ╭────────────────────────╮
│ PING                   │ │ DOWNLOAD               │ │ UPLOAD                 │
│ 12.84                  │ │ 241.37                 │ │ 38.92                  │
│ ms  ·  jitter 1.10 ms  │ │ Mbps                   │ │ Mbps                   │
╰────────────────────────╯ ╰────────────────────────╯ ╰────────────────────────╯
╭────────────────────────── Download frequency bars ───────────────────────────╮
│     265.1       ▇▇▇                                                           │
│              ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ │
│     0.0       time  →                                                         │
╰────────────────────────── Mbps over time ────────────────────────────────────╯
╭──────────────────────────────────────────────────────────────────────────────╮
│ ●  COMPLETE                                                    results ready │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  38.92 Mbps │
╰──────────────────────────────────────────────────────────────────────────────╯
```

`pyspeed` starts independent ping, download, and upload workers and keeps the
terminal display live forever in a fullscreen alternate screen, like `btop`.
Press `Ctrl+C` when you are done; active network
sessions close, the terminal is restored, a final summary is shown, and logs
are flushed.

## Install

```bash
git clone https://github.com/ramyrgd6/pyspeed.git
cd pyspeed
python -m pip install -e .
```

## Usage

```bash
pyspeed                              # ping + download + upload
pyspeed --no-upload                  # skip upload
pyspeed --no-ping                    # skip ping
pyspeed --bytes 50000000             # download 50 MB
pyspeed --upload-bytes 10000000      # upload 10 MB
pyspeed --timeout 10                 # allow 10 seconds per network request
pyspeed --refresh 250                # refresh every 250 ms
pyspeed --log results.csv            # periodic CSV snapshots
pyspeed --log results.json           # JSON-lines snapshots
pyspeed --download-limit 80M         # throttle real download traffic
pyspeed --upload-limit 30Mbps        # throttle real upload traffic
pyspeed --connections 2              # two parallel workers per direction
pyspeed --history 120                # retain a longer scrolling history
pyspeed --no-graph                   # hide history bars on small terminals
pyspeed --no-upload                  # run without the upload worker
pyspeed --version                    # print the installed version
```

Run a small transfer while developing:

```bash
pyspeed --bytes 10000000 --upload-bytes 5000000
```

## How it works

- **Ping** sends several tiny requests and reports average round-trip latency
  plus jitter between consecutive samples.
- **Download and upload** run in independent worker threads, continuously
  repeating requests and calculating current, 1/10/60-second, average, and
  peak throughput.
- **Retries** use bounded exponential backoff when a transfer temporarily
  fails, so a transient network error does not kill the session. Reconnects and
  failures are counted.
- **Latency** records an idle baseline and loaded latency while both workers
  are active. The displayed increase is a bufferbloat estimate, not an
  official standardized grade.
- **Statistics** track totals, elapsed time, jitter, packet loss, variance,
  stability, and bounded scrolling histories while the UI redraws in place.
- **Events** surface sustained download/upload drops, loaded-latency spikes,
  and connection failures in a bounded timeline. Alerts require repeated
  abnormal samples and use cooldowns to avoid noisy false-positive storms.

The project deliberately has a small surface area: `pyspeed.core` contains the
measurement logic, while `pyspeed.cli` owns the live terminal presentation.

## Development

```bash
python -m pip install -e '.[dev]'
pytest -q
python -m compileall -q pyspeed
```

## Notes

Results vary with your connection, route to Cloudflare's edge, and current
network conditions. This is a personal educational project and is not
affiliated with Ookla or Cloudflare.

Continuous mode creates sustained traffic. Use it only on connections and
endpoints you control or are authorized to load. Cloudflare's public endpoint
is suitable for short personal tests; stop the monitor rather than leaving a
public service under load indefinitely.

The stability label is a diagnostic estimate based on measured throughput
variation, latency behavior, and packet loss. It is not an official ISP score.

## License

MIT. See [LICENSE](LICENSE).
