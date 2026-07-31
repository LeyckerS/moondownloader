# CLI Reference

MoonDownloader CLI — headless downloader for server deployment.

This document covers all command-line arguments for `moon_cli.py`, intended for users running MoonDownloader on a server or in any environment without a graphical interface.

## Basic Usage

```bash
python moon_cli.py --urls urls.txt --output ./downloads
```

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--urls` | Yes | — | Path to a text file containing one URL per line |
| `--output` | Yes | — | Output folder where downloaded files will be saved |
| `--browsers` | No | `8` | Number of parallel extraction workers |
| `--streams` | No | `24` | Number of concurrent download streams |
| `--retries` | No | `3` | Maximum number of retries per failed link |
| `--proxies` | No | `proxies.txt` | Path to a proxy list file |

## Examples

### Minimal run

```bash
python moon_cli.py --urls urls.txt --output ./downloads
```

### Custom concurrency

Increase parallel workers and download streams for faster throughput on a powerful server:

```bash
python moon_cli.py --urls urls.txt --output ./downloads --browsers 16 --streams 48
```

### Custom retry and proxy settings

```bash
python moon_cli.py --urls urls.txt --output ./downloads --retries 5 --proxies my_proxies.txt
```

## Notes

- `--urls` file format: one URL per line, no additional formatting required.
- `--browsers` controls how many headless browser instances run in parallel to extract download links. Higher values speed up extraction but use more memory and CPU.
- `--streams` controls how many files can be downloaded concurrently. Higher values increase throughput but may trigger rate limits on some providers.
- If `--proxies` is not specified, MoonDownloader looks for `proxies.txt` in the current working directory. If the file does not exist, requests are made without a proxy.

## See Also

- [QUICKSTART.md](./QUICKSTART.md)
- [CONFIGURATION.md](./CONFIGURATION.md)
- [PROVIDERS.md](./PROVIDERS.md)