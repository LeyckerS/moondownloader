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
| `--extractors` | integer | `8` | Number of parallel extraction workers. Datanodes uses one shared Chrome instance regardless of this value. |
| `--streams` | integer | `24` | Maximum number of concurrent download streams. |
| `--retries` | integer | `3` | Maximum extraction attempts per URL. Network retries inside one download are separate. |
| `--proxies` | path | `proxies.txt` | Proxy-list file to load. Applies to **downloads only** — extraction always goes direct, see below. A missing file, or one that yields no usable proxies, prints a warning — except the implicit default (`proxies.txt` when `--proxies` is omitted), whose absence stays silent as the normal no-proxy state. |
| `--version` | flag | — | Print the version and exit. Works even without `--urls`/`--output`. |

`--browsers` remains accepted as a deprecated compatibility alias. It is hidden from `--help`; when both flags are supplied, `--extractors` takes precedence and the CLI prints a warning.

`--urls` and `--output` are required. The parser accepts integer values for
`--extractors`, `--streams`, and `--retries`; it does not add further CLI-side range
validation.

The parser's actual default for `--proxies` is unset (`None`); `proxies.txt` is the
effective fallback path applied afterward. That distinction is what lets the CLI tell
"you didn't ask for proxies" (silent) apart from "you asked for a proxy file and it's
missing or empty":

- an explicitly passed path that doesn't exist: `WARNING: proxy file not found at {path}`
- a file that exists but yields no usable proxies: `WARNING: proxy file {path} yielded 0 proxies`
- the implicit default path missing: no warning
- whenever any proxies load or lines get skipped: `[proxies] {n} loaded, {s} skipped`

`--proxies` covers the download half of a run and no more. The pool is read once, in
`download_file`, to build the download session; the extraction layer never receives one,
so the datanodes browser and the fuckingfast `curl_cffi` session both connect from your
own address on every run. `[proxies] 20 loaded` means the bytes are covered — it does not
mean the page loads were.

If a run shows pages opening while every download fails, check the proxies before
suspecting the extractor: that split is exactly what an unusable proxy list looks like
here.

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
  --extractors 8 \
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
  --extractors 8 \
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
  --extractors 12 \
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
| `--extractors` | `Extractors` | Both control parallel extraction workers. |
| `--streams` | `DL streams` | Both limit concurrent downloads. |
| `--retries` | `Retries` | Both control extraction retries. |
| `--proxies` | — | The CLI reads a proxy-list file; it is not a GUI panel setting. |

The CLI does not expose GUI `Pages`, `Captcha s`, `Chrome`, or `API key` controls
as command-line flags. It reads the same extraction-related environment variables
directly; see [Configuration](CONFIGURATION.md#environment-variables) for their
current names and defaults.
