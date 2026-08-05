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
| [@AdvaitVarhade](https://github.com/AdvaitVarhade) | made `--proxies` report missing files and skipped lines instead of failing silently (#48); narrowed `native_dialog`'s exception handling and documented four deliberate swallows (#54); narrowed and documented the eight exception handlers in the first slice of `moon_extract.py` without breaking the deferred playwright import (#82), then closed the issue by doing the last slice too (#130) |
| [@Moferanoluwa](https://github.com/Moferanoluwa) | added `--version` and made both report flavours record which build wrote them (#61); corrected the `--proxies` documentation that #48 had made stale (#63); brought `docs/ARCHITECTURE.md` in line with the shared download engine (#68); put CI on a 3.10/3.11/3.12 matrix with a `ruff` baseline (#69); made documentation-only pull requests verify their `moon_cli.py` examples against the real parser (#70); annotated the exception handlers in `moon_engine.py` and narrowed the report-save swallow (#71) |
| [@RubenSanosh](https://github.com/RubenSanosh) | froze the elapsed-time clock when a run finishes, so the GUI stops counting after the last file lands (#73) |
| [@darlenepolek](https://github.com/darlenepolek) | derived the CI byte-compile list from `git ls-files` so it can no longer drift, and made the lint workflow watch its own file (#77) |
| [@XEDAB](https://github.com/XEDAB) | made a discarded partial download say so, instead of silently restarting a multi-gigabyte transfer when the server ignores a resume request (#98); gave `download_file` a way to reach the live log, so mid-transfer failures stop being invisible until the run ends — and fixed an `UnboundLocalError` reachable whenever a connection timed out before the first byte (#120); made the live speed divide by the observation window instead of the span of the buffered burst, ending a ninefold overstatement (#142) |
| [@tomatotomata](https://github.com/tomatotomata) | corrected the headless CLI example in `README.md`, which documented positional URLs and a `-o` flag the parser never accepted (#53) |
| [@kocaemre](https://github.com/kocaemre) | taught the documentation guard to check single-dash flags, so it now catches the line it was written for (#104) |
| [@Vam-si-krish](https://github.com/Vam-si-krish) | made an unsupported host fail on sight instead of being retried like a network error, and collapsed the host names to one definition shared by both front-ends (#108); extended the CI matrix to Python 3.13 and 3.14, closing the gap over the versions the project promises but never tested (#129) |
| [@felix-windsor](https://github.com/felix-windsor) | stopped two links with the same filename from sharing one `.tmp` and corrupting each other, by reserving destination names at registration (#119) |
| [@AashishGupta2007](https://github.com/AashishGupta2007) | narrowed and documented the eight exception handlers in the Chrome-lifecycle slice of `moon_extract.py` (#118) |
| [@Guflly](https://github.com/Guflly) | made `Engine.stop()` actually close Chrome before returning, so shutting down mid-run no longer leaves an orphaned browser holding its profile lock (#122) |
| [@basisworks](https://github.com/basisworks) | documented that proxies cover downloads only — establishing that the fuckingfast `curl_cffi` session goes direct too, not just the datanodes browser (#128) |
| [@PomPomSaturin](https://github.com/PomPomSaturin) | restored the dependency upper bounds without moving the floors, added a tested `constraints.txt`, and wrote the CI job that fails when a requirement has no upper bound — so they cannot be lost a third time (#136); made both worker gathers collect their failures instead of aborting on the first one, and name the worker that failed (#143) |
| [@8nt0n](https://github.com/8nt0n) | made the proxy chip tell the truth before a run starts, distinguishing "no file" from "file with nothing usable in it", and extracted the line parser so the status check and the real load cannot disagree (#132) |

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
