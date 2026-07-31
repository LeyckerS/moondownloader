# CLI

`moon_cli.py` is the headless front-end. It uses the same extraction layer as the
GUI, so a batch containing only `fuckingfast.co` links does not start a browser;
the first `datanodes.to` link starts one shared Chrome instance when needed.

```bash
python moon_cli.py --urls links.txt --output ./downloads
```

The URL file contains one URL per line. Empty lines and lines beginning with `#`
are ignored.

## Arguments

| Argument | Type | Default | What it changes |
|:--|:--|:--|:--|
| `--urls` | path | required | Text file containing one URL per line. The CLI exits before a run if the file is missing or has no usable URLs. |
| `--output` | path | required | Directory where downloaded files are written. It is created if needed. |
| `--browsers` | integer | `8` | Number of parallel extraction workers. Despite the name, it does not start that many browsers: datanodes uses one shared Chrome instance. |
| `--streams` | integer | `24` | Maximum number of concurrent download streams. |
| `--retries` | integer | `3` | Maximum extraction attempts per URL. Network retries inside one download are separate. |
| `--proxies` | path | `proxies.txt` | Proxy-list file to load. Missing files simply result in no proxies being loaded. |
| `--version` | flag | — | Print the version and exit. Works even without `--urls`/`--output`. |

`--urls` and `--output` are required. The parser accepts integer values for
`--browsers`, `--streams`, and `--retries`; it does not add further CLI-side range
validation.

## Examples

### fuckingfast.co batch

```text
# fuckingfast-links.txt
https://fuckingfast.co/example-a#example-a.zip
https://fuckingfast.co/example-b#example-b.zip
```

```bash
python moon_cli.py \
  --urls fuckingfast-links.txt \
  --output ./downloads/fuckingfast \
  --browsers 8 \
  --streams 24
```

A batch made only of `fuckingfast.co` links uses direct HTTP extraction and does
not launch Chrome.

### datanodes.to batch

```text
# datanodes-links.txt
https://datanodes.to/example-a
https://datanodes.to/example-b
```

```bash
python moon_cli.py \
  --urls datanodes-links.txt \
  --output ./downloads/datanodes \
  --browsers 8 \
  --streams 24
```

The first `datanodes.to` link starts one shared Chrome instance. If Turnstile
requires a manual solve, wait for it in that browser window; the shared browser
and its clearance are reused for the rest of the run.

### Mixed batch

```text
# mixed-links.txt
https://fuckingfast.co/example-a#example-a.zip
https://datanodes.to/example-b
https://fuckingfast.co/example-c#example-c.zip
```

```bash
python moon_cli.py \
  --urls mixed-links.txt \
  --output ./downloads/mixed \
  --browsers 12 \
  --streams 32 \
  --retries 3 \
  --proxies ./proxies.txt
```

The `fuckingfast.co` entries are extracted without a browser. The datanodes entry
causes the shared Chrome instance to start; it is not one browser per worker.

## Exit codes

| Code | Current behavior |
|:--:|:--|
| `0` | The CLI completes normally, including a run in which individual URLs fail, or is interrupted with `Ctrl+C`. Inspect the final `ok` and `fail` counts and `failed_links.txt` when it is written. |
| `1` | The URL file is missing or has no usable URLs, or an unhandled exception reaches `main()`. In the latter case the CLI writes `crash_log.txt` beside `moon_cli.py`. |
| `2` | `argparse` rejects command-line usage, such as missing required arguments or a non-integer value for an integer argument. |

After a normal run, the CLI writes `moontech_cli_*.log` and `moontech_cli_*.json`
beside `moon_cli.py`, not inside `--output`. If any URL exhausts its attempts, it
also writes `failed_links.txt` there.

## Relationship to GUI settings

| CLI argument | GUI setting | Notes |
|:--|:--|:--|
| `--urls` | link editor | Both supply the input links. |
| `--output` | output-folder picker | Both select where downloaded files are written. |
| `--browsers` | `Extractors` | Both control parallel extraction workers. |
| `--streams` | `DL streams` | Both limit concurrent downloads. |
| `--retries` | `Retries` | Both control extraction retries. |
| `--proxies` | — | The CLI reads a proxy-list file; it is not a GUI panel setting. |

The CLI does not expose GUI `Pages`, `Captcha s`, `Chrome`, or `API key` controls
as command-line flags. It reads the same extraction-related environment variables
directly; see [Configuration](CONFIGURATION.md#environment-variables) for their
current names and defaults.
