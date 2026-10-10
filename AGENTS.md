# Agent instructions

Fork of [petergridge/Irrigation-V5](https://github.com/petergridge/Irrigation-V5) with intentional local behaviour (low-power, resume after reboot, zone/valve cache, Tuya close-on-lag, `freq_start_date`, Créneau rotation). See [`docs/upstream-alignment.md`](docs/upstream-alignment.md) and [`docs/upstream-lag.md`](docs/upstream-lag.md): **no blind upstream merge**, port fixes deliberately.

## Upstream sync — record git ancestry (day 1)

The owner's HA (`ForkUpstreamMonitor`) uses GitHub compare `upstream-tag...main`, which counts commits by **ancestry, not content**. A port that does not make the upstream tag an ancestor of `main` keeps alerting "N commit(s) behind" (happened: `4cfa3b8` ported V2026.08.04 without merge → 19 behind until #20).

1. Port the upstream fixes on a branch (cherry-pick / manual reconcile, preserving the local behaviour above).
2. In the same PR, finish with `git fetch upstream --tags && git merge -s ours <upstream-tag>` — no file change, records ancestry.
3. Merge the PR with **Create a merge commit** (`gh pr merge --merge`) or a fast-forward push — **never squash or rebase-merge** (drops the upstream parent).
4. Done only when `git rev-list --left-right --count <upstream-tag>...origin/main` prints `0 <ahead>`.

## Tests

`pip install -r requirements-test.txt`, then from the repo root: `PYTHONPATH=. pytest custom_components/irrigationprogram/tests -o asyncio_mode=auto` (CI: `.github/workflows/pytest.yml`).
