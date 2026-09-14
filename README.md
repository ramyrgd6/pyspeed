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
terminal display live forever. Press `Ctrl+C` when you are done; active network
sessions close, the terminal is restored, and the session log is flushed.

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
pyspeed --refresh 5                  # refresh the UI five times per second
pyspeed --log-file session.log      # write lifecycle and retry logs here
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
  repeating requests and calculating current, average, and peak throughput.
- **Retries** use bounded exponential backoff when a transfer temporarily
  fails, so a transient network error does not kill the session.
- **Statistics** track totals, elapsed time, ping, jitter, and packet loss while
  the UI renders several times per second.

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

## License

MIT. See [LICENSE](LICENSE).
