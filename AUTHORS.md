# Authors

Moon Downloader is created and maintained by:

- **LeyckerS** — [github.com/LeyckerS](https://github.com/LeyckerS)

## Contributors

People outside the maintainer who have shipped merged changes, in order of
first contribution:

| Contributor | What they did |
|:--|:--|
| [@kushin25](https://github.com/kushin25) | `docs/CLI.md`, the CLI reference (#37) |
| [@pollychen-lab](https://github.com/pollychen-lab) | moved the no-Chrome regression into a pytest suite (#38), then moved the download engine into a shared `moon_download.py` (#41) |
| NanoRisk6 — *account no longer on GitHub* | pointed the documented verification commands at `pytest tests/` across six files after the test move (issue #43, commit `f9f8f74`) |
| [@AdvaitVarhade](https://github.com/AdvaitVarhade) | made `--proxies` report missing files and skipped lines instead of failing silently (#48); narrowed `native_dialog`'s exception handling and documented four deliberate swallows (#54) |
| [@Moferanoluwa](https://github.com/Moferanoluwa) | added `--version` and made both report flavours record which build wrote them (#61); corrected the `--proxies` documentation that #48 had made stale (#63); brought `docs/ARCHITECTURE.md` in line with the shared download engine (#68); put CI on a 3.10/3.11/3.12 matrix with a `ruff` baseline (#69); made documentation-only pull requests verify their `moon_cli.py` examples against the real parser (#70); annotated the exception handlers in `moon_engine.py` and narrowed the report-save swallow (#71) |

Dependabot handles the dependency and action bumps.

**@pollychen-lab's #41 is the largest single contribution to the project so far.**
`download_file`, `Telemetry` and `ProxyPool` existed as two copies, one in
`moon_engine.py` and one in `moon_cli.py`, so every fix in the download path was
a two-file change and the two copies were free to drift. They now live once in
`moon_download.py`.

The full commit history, including everyone not listed above, is on the
[contributors graph](https://github.com/LeyckerS/moondownloader/graphs/contributors).

If you have contributed and want your entry to say something different — a
different description, a link, a bio — open a PR editing this file.
