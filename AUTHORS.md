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
| [@AdvaitVarhade](https://github.com/AdvaitVarhade) | made `--proxies` report missing files and skipped lines instead of failing silently (#48); narrowed `native_dialog`'s exception handling and documented four deliberate swallows (#54); narrowed and documented the eight exception handlers in the first slice of `moon_extract.py` without breaking the deferred playwright import (#82), then closed the issue by doing the last slice too (#130); gave `moon_cli.py` structured exit codes so a script can tell success from partial failure from total failure, aligning pre-flight errors with argparse's own code 2 (#153) |
| [@Moferanoluwa](https://github.com/Moferanoluwa) | added `--version` and made both report flavours record which build wrote them (#61); corrected the `--proxies` documentation that #48 had made stale (#63); brought `docs/ARCHITECTURE.md` in line with the shared download engine (#68); put CI on a 3.10/3.11/3.12 matrix with a `ruff` baseline (#69); made documentation-only pull requests verify their `moon_cli.py` examples against the real parser (#70); annotated the exception handlers in `moon_engine.py` and narrowed the report-save swallow (#71) |
| [@RubenSanosh](https://github.com/RubenSanosh) | froze the elapsed-time clock when a run finishes, so the GUI stops counting after the last file lands (#73) |
| [@darlenepolek](https://github.com/darlenepolek) | derived the CI byte-compile list from `git ls-files` so it can no longer drift, and made the lint workflow watch its own file (#77) |
| [@XEDAB](https://github.com/XEDAB) | made a discarded partial download say so, instead of silently restarting a multi-gigabyte transfer when the server ignores a resume request (#98); gave `download_file` a way to reach the live log, so mid-transfer failures stop being invisible until the run ends — and fixed an `UnboundLocalError` reachable whenever a connection timed out before the first byte (#120); made the live speed divide by the observation window instead of the span of the buffered burst, ending a ninefold overstatement (#142); found that an assertion shared by four tests in `test_no_chrome.py` was comparing a fabricated count against itself, and removed it from the two callers where it was invented while keeping it where the engine's own counter makes it real (#155, #157); moved the `download_file` stub inside the loop that patches both front-ends, so the engine can no longer reach the real downloader during tests (#160, #162) |
| [@tomatotomata](https://github.com/tomatotomata) | corrected the headless CLI example in `README.md`, which documented positional URLs and a `-o` flag the parser never accepted (#53) |
| [@kocaemre](https://github.com/kocaemre) | taught the documentation guard to check single-dash flags, so it now catches the line it was written for (#104) |
| [@Vam-si-krish](https://github.com/Vam-si-krish) | made an unsupported host fail on sight instead of being retried like a network error, and collapsed the host names to one definition shared by both front-ends (#108); extended the CI matrix to Python 3.13 and 3.14, closing the gap over the versions the project promises but never tested (#129) |
| [@felix-windsor](https://github.com/felix-windsor) | stopped two links with the same filename from sharing one `.tmp` and corrupting each other, by reserving destination names at registration (#119) |
| [@AashishGupta2007](https://github.com/AashishGupta2007) | narrowed and documented the eight exception handlers in the Chrome-lifecycle slice of `moon_extract.py` (#118); removed the dead THEME palette from `moon_engine.py`, having first checked that not one of its fifteen hex values appears in `web/styles.css` (#145, #159) |
| [@Guflly](https://github.com/Guflly) | made `Engine.stop()` actually close Chrome before returning, so shutting down mid-run no longer leaves an orphaned browser holding its profile lock (#122) |
| [@basisworks](https://github.com/basisworks) | documented that proxies cover downloads only — establishing that the fuckingfast `curl_cffi` session goes direct too, not just the datanodes browser (#128) |
| [@PomPomSaturin](https://github.com/PomPomSaturin) | restored the dependency upper bounds without moving the floors, added a tested `constraints.txt`, and wrote the CI job that fails when a requirement has no upper bound — so they cannot be lost a third time (#136); made both worker gathers collect their failures instead of aborting on the first one, and name the worker that failed (#143) |
| [@8nt0n](https://github.com/8nt0n) | made the proxy chip tell the truth before a run starts, distinguishing "no file" from "file with nothing usable in it", and extracted the line parser so the status check and the real load cannot disagree (#132) |
| [@Allen58562](https://github.com/Allen58562) | made Stop interrupt transfers already in flight instead of waiting for them to finish, via a registry of per-download kill events signalled with `call_soon_threadsafe`, and gave a user-initiated stop its own `stopped` status so it is no longer counted as a stall kill and re-queued (#149) |
| [@shard872](https://github.com/shard872) | made a full destination disk a run-level fatal condition instead of a per-file error — errno-based `ENOSPC` detection, a shared abort both front-ends observe, the folder and shortfall named in the live log, `.tmp` files preserved for resume, and a `memoryview` write loop so a short write on a nearly-full disk cannot truncate silently (#150) |
| [@nightcityblade](https://github.com/nightcityblade) | took `ruff` out of the Python version matrix so it runs once per pull request instead of five times over identical work — keeping the `ruff==0.16.1` pin, so upstream adding a rule still cannot turn an unrelated pull request red (#81, #158); put `ruff.toml` and `pytest.ini` into the workflow's paths filter, so the two files that decide what CI enforces can no longer be changed without CI running (#164, #165) |
| [@Divesh-Kshirsagar](https://github.com/Divesh-Kshirsagar) | added the project's first `pytest.ini`, silencing the `aiohttp.BasicAuth` deprecation by exact message rather than by category — so the call that cannot yet be changed stops adding noise while a genuine future deprecation still surfaces (#151, #161) |
| [@mazi-eth](https://github.com/mazi-eth) | removed `Engine._LOG_MAX_LINES`, a constant left over from when `moon_engine.py` was generated from a tkinter GUI — the log ring has been bounded by a `deque` maxlen instead (#80, #167) |
| [@StefStrg](https://github.com/StefStrg) | made `docs-cli-check.yml` watch its own file on pull requests as it already did on push, so an edit to that workflow can no longer arrive with an empty check list (#93, #168) |
| [@yhuikzdtguioaert](https://github.com/yhuikzdtguioaert) | made the shared `Extractors` slider state the concurrency datanodes actually gets — `min(Extractors, Pages)`, live on both sliders and relabelled in both languages — instead of implying every worker can open a datanodes page (#83, #169) |
| [@harshvardhan60792](https://github.com/harshvardhan60792) | added the regression test that fails when the engine reaches the real downloader instead of the stub, and gave `run_engine` a `mode` parameter so the no-Chrome suite can exercise download mode at all — it had only ever run link extraction, which is why the missing stub went unseen (#160, #170) |
| [@FlaggedATX](https://github.com/FlaggedATX) | corrected the `ruff.toml` comment that claimed line length was handled elsewhere — nothing enforces it, and the file now says so — on their first pull request (#79, #171) |
| [@snowyukitty](https://github.com/snowyukitty) | removed the double-count of the final write buffer in `bytes_acc`, which made every small file's bytes count twice in the totals, and pinned the accounting with nine regression tests whose expectations are read from the chunks the fake session actually delivered rather than from a model of the loop (#172, #175) |
| [@tunglambk](https://github.com/tunglambk) | gave `web/`-only pull requests their first CI check — `node --check web/app.js` behind a `web/**` paths filter, closing the hole where a syntax error in the GUI could merge with an empty check list (#174, #176) |

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
