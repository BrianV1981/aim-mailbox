# aim-mailbox — Engineering Handoff

> **Updated:** 2026-11-26T01:28:00-05:00
> **Updated by:** `aim-mailbox` (`agy` / Gemini 4 Carbon, session `a82b9f87-627a-4b97-9e27-3f42658abce7`)
> **Priority Mission:** Maintain steady-state `v0.2.2` cross-OS 18-endpoint fleet post office, optionally roll out `<2ms` turn-boundary hooks (`install-hooks`) or retention `gc` as Operator-directed, and ship `#16` (`events.jsonl` write-only feed) **only when** unheld after `aim-office` `#23`–`#30`.
> **Operator:** Brian Vasquez (`@BrianV1981` / `brianv1981@gmail.com`)

---

## 0. COMPLETED WORK (DO NOT REVISIT)

| Issue / PR | Work | Status |
|---|---|---|
| `aim-mailbox#1` (`v0.1.0`) | Core lock-free Maildir engine, 9 drivers, 13 CLI commands, dual-OS CI | ✅ RESOLVED (`28ee239`) |
| `aim-mailbox#2` (`v0.1.1`) | Symlink-safe POSIX `bin/aim-mail` launcher + E2E test | ✅ RESOLVED (`2edaedf`) |
| `aim-mailbox#3`–`#10` | Security (`0700`/`0600`, traversal block), NTFS/WSL2 lock hardening, hop bounds, `wait` vs `watch`, dispatcher isolation, active inbox / async `ack_required=False`, `opencode-http` `parts` schema, `aim-mail.cmd` exit code, fail-closed `--wake`, `operator_approved` gate | ✅ RESOLVED (`1b77c5a`..`8ded2b7`) |
| `aim-mailbox#11` (`v0.1.2`) | Real `tmux` `target-pane` (`=session:` vs `=session`), Windows UTF-8 subprocess capture & NTFS delete-pending lock retry, `agy-cli` opt-in docs, landscape audit (`hcom`, `amux`), `30/30` pytest, Win+Linux CI `45436738254` + `gitleaks` `45436738233` green | ✅ RESOLVED (`26114ff`, tag `v0.1.2`) |
| `aim-mailbox#12` | Advisory path/glob file leases | ✅ CLOSED `wontfix` (worktrees isolate edits) |
| `aim-skill-library#26`, `#27` (`PR #28`–`#30`) | `tmux_send.sh` count-delta + `mktemp` + DND guard + exact `=session:`, Linux+Windows CI `45437345337` + `gitleaks` `45437345260` green, `18/18` unittest | ✅ RESOLVED (`cbe08f2`) |
| Host Symlink & Mailbox | Gitignored `~/aim-connect/.gemini/skills/aim-communicate` symlinked; `~/.aim/mailbox` registrations updated (`aim-mailbox`, `aim-auditor`); roadmap triage logged in `memory-wiki/` | ✅ RESOLVED (`48f7d95`) |
| `aim-mailbox#15`, `#14`, `#13` (`v0.2.0`) | `inbox` filters (`--from`, `--thread-id`, `--priority`, `--query`, `--since`, `--limit`) + `gc` (`--done-days`, `--dead-days`, `--archive-dir`), priority lanes (`urgent\|normal\|low`) + `--coalesce-key` + `--low-batch-window`, `install-hooks` (`agy`, `codex`, `grok`, `opencode`, `generic`) + `<2ms` bash / `<10ms` `-S` empty-inbox `check` fast-path; OSS/paid moat comparison added to `README.md` | ✅ RESOLVED (`45aea39`..`73b09e9`, tag `v0.2.0`) |
| `aim-mailbox#17` (`v0.2.1`) | Dynamic project-scoped `--cwd` session resolution (`resolve_latest_agy_session()` in `drivers.py` filtering `nesting_depth=0, killed=0` across Win11/WSL2/POSIX `conversation_summaries.db`), `--clear-session-id`, `target=auto(cwd)`; `38/38` pytest, Win+Linux CI `45744286831` + `gitleaks` `45744286821` green | ✅ RESOLVED (`306aa92`, tag `v0.2.1`) |
| `aim-skill-library` `PR #34` (`v2.1`) & Live Win11+WSL2 Fleet Bridge | Merged `aim-communicate` `v2.1` (`b1dc653`), repaired Win11 NTFS skill junction (installing all 16 skills), synced all 8 Win11+WSL2 skill roots (`agy`, `codex`, `grok`, `opencode`), installed live Win11 Sidecar (`C:\aim-mailbox`, `C:\Users\kingb\.aim\mailbox` shared via `AIM_MAILBOX_DIR`), and verified live 3-peer round-trip (`aim-connect` `8.8s`, `aim-auditor` `24.6s`, `joshua-win11` `34.1s`) | ✅ RESOLVED (`b1dc653`) |
| `aim-mailbox#18` (`v0.2.2`) | Cross-OS watcher host-affinity guard (`check_driver_host_affinity()` in `drivers.py` + pre-claim skip in `dispatcher.py` + `HOST_MISMATCH` / `SIDECAR_CONTEXT_REQUIRED` diagnostics in `cli.py`); `40/40` pytest + native Win11 Python 3.14 green, Win+Linux CI `45758401701` + `gitleaks` `45758401610` green | ✅ RESOLVED (`7d1a7fc`, tag `v0.2.2`) |
| Live Sidecar Reload & 18-Endpoint Fleet Enrollment | Fast-forwarded `C:\aim-mailbox`, respawned Win11 Sidecar (`PID 10412`), pruned 3 test `dead/` envelopes, verified `6.0s` live watch isolation (`attempts=0`), enrolled all `18` active fleet workspaces (`14` WSL2 `tmux` + `4` Win11 Desktop `auto(cwd)`), and symlinked `~/.aim/mailbox` + `tmux` global `AIM_MAILBOX_DIR` | ✅ RESOLVED (`808a7d2`) |

*(Keep this table consolidated. See `memory-wiki/log.md` and closed GitHub issues for full history — do not re-audit.)*

---

## 1. PROJECT IDENTITY

`aim-mailbox` (`aim-mail`, `v0.2.2`, MIT) is a pure-Python 3.10+ stdlib (`0` runtime pip dependencies, `0` daemons, `15` CLI subcommands) local JSON Maildir post office, priority wake dispatcher, and turn-hook installer across Linux, WSL2, macOS, and Windows 11 (`agy-desktop-sidecar`, `agy-cli`, `opencode-http`, `grok-cli`, `codex-cli`, `vscode-chat`, `tmux`, `command`, `none`).

- **Repo & Project Board:** `/home/kingb/aim-mailbox` (`BrianV1981/aim-mailbox`, GitHub Project `#15`: `17 Done`, `1 Todo [ON HOLD]`), `main` @ `808a7d2` (`tag: v0.2.2`), Windows clone synced at `/mnt/c/aim-mailbox` (`C:\aim-mailbox`)
- **Test Baseline:** `python3 -m pytest tests/ -v -W error::DeprecationWarning` (`40 passed` on Linux; `ubuntu-latest` + `windows-latest` CI `45758401701` + `gitleaks` `45758401610` green)
- **Live Shared Mailbox (`AIM_MAILBOX_DIR`):** `/mnt/c/Users/kingb/.aim/mailbox` (`C:\Users\kingb\.aim\mailbox`, also symlinked at `/home/kingb/.aim/mailbox` and exported in `tmux` global env) with **18 registered project-scoped endpoints** (`14` WSL2 `driver=tmux`: `aim-auditor`, `aim-ayrianna`, `aim-calendar`, `aim-connect`, `aim-joshua`, `aim-ld`, `aim-mailbox`, `aim-office`, `aim-skorbane`, `aim-skorbane-content`, `aim-tbsoftwash`, `aim-tbsoftwash-content`, `aim-youtube`, `tbsoftwash-ai-book`; `4` Win11 `driver=agy-desktop-sidecar` `target=auto(cwd)`: `joshua-win11`, `copy-cat`, `aim-social-studio`, `philosophers-after-dark`) and live Windows Sidecar running `C:\Python314\python.exe -u sidecar_runner.py` (`PID 10412`)

### Your Knowledge Base
- `/home/kingb/aim-mailbox/memory-wiki/index.md` — Project wiki index
- `/home/kingb/aim-mailbox/memory-wiki/log.md` — Chronological engineering & live fleet test log
- `/home/kingb/aim-mailbox/memory-wiki/pages/open-source-landscape.md` — OSS audit + **§4 Explicit Non-Goals (Do Not Adopt)**
- `/home/kingb/aim-mailbox/memory-wiki/pages/maildir-and-dispatcher-architecture.md` — Atomic JSON Maildir layout & schemas
- `/home/kingb/aim-mailbox/memory-wiki/pages/windows-11-and-cross-os-drivers.md` — Cross-OS wake driver matrix, `auto(cwd)` mechanics & `v0.2.2` host-affinity guard

---

## 2. YOUR MISSION: STEADY-STATE FLEET OPERATIONS & `#16` READINESS (WHEN UNHELD)

All `v0.1.0`–`v0.2.2` engineering issues (`#1`–`#15`, `#17`, `#18`) and full 18-endpoint cross-OS fleet enrollment are **100% complete and live**. Stand by for Operator directives on fleet operations, optional hook/scheduler rollout, or unholding `aim-mailbox#16` once `aim-office` `#23`–`#30` are ready.

### Execution Queue (Operator-Directed Only)

#### 1️⃣ Optional Turn-Boundary Hook & WSL2 Watcher Rollout (If Operator Requests)
**Problem:** Direct `--wake` dispatches work immediately for WSL2 `tmux` peers, and the Windows 11 Sidecar (`watch --interval 1.5`) auto-delivers to all 4 Windows Desktop workspaces (`joshua-win11`, `copy-cat`, `aim-social-studio`, `philosophers-after-dark`). However, when a Windows 11 agent sends/replies to a WSL2 `tmux` peer **without** a WSL2 watcher running (or omits `--wake` because `tmux` is `HOST_MISMATCH` on Windows), WSL2 delivery relies on `wait`, manual `inbox`, or turn-boundary `check --inject` hooks unless a WSL2 `pulse`/`watch` runner or `install-hooks` is enabled.
**Fix:** Only if directed by the Operator, either (a) install global `<2ms` turn-boundary hooks across WSL2 harnesses (`aim-mail install-hooks --harness agy|grok|codex --agent <id>`) or (b) run a lightweight WSL2 `cron` / `systemd --user` timer executing `aim-mail pulse` (plus weekly `aim-mail gc --done-days 14 --dead-days 30`).
**Key files:** `/home/kingb/aim-mailbox/src/aim_mailbox/hooks.py`, `/home/kingb/aim-mailbox/bin/aim-mail`, `/mnt/c/Users/kingb/.aim/mailbox/agents.json`

#### 2️⃣ `aim-mailbox#16` (`[ON HOLD]` — Unhold Only When `aim-office` `#23`–`#30` Complete)
**Problem:** `aim-office` will eventually need a single bounded audit stream (`$AIM_MAILBOX_DIR/events.jsonl`) to display live message flow, wake outcomes, and `dead/` alerts without polling 18+ `boxes/<agent>/*` subdirectories.
**Fix:** **Keep `#16` untouched until the Operator explicitly unholds it.** When unheld, emit non-fatal append-only JSONL records (`send`, `claim`, `wake_delivered`, `wake_failed`, `wake_deferred`, `ack`, `dead_letter`) to `$AIM_MAILBOX_DIR/events.jsonl` with bounded size rotation (`0o600`), NTFS lock-retry safety, **zero** new CLI subcommands (keep CLI `<= 15`), and **zero** socket/HTTP daemons.
**Key files:** `/home/kingb/aim-mailbox/src/aim_mailbox/store.py`, `/home/kingb/aim-mailbox/src/aim_mailbox/dispatcher.py`, `/home/kingb/aim-mailbox/tests/test_store.py`

---

## 3. DETAILED ANALYSIS / BREAKDOWN

- **How Cross-OS Host-Affinity Works in `v0.2.2` (`#18`):**
  - `check_driver_host_affinity()` (`src/aim_mailbox/drivers.py#L140-L266`) runs inside `pulse_mailbox()` (`src/aim_mailbox/dispatcher.py#L127-L148`) **before** `reclaim_expired_claims()`, `acquire_agent_lock()`, or `claim_envelope()` whenever `dry_run=False`.
  - On Windows (`os.name == "nt"`): skips `driver="tmux"` when no local `binary` override exists and `shutil.which("tmux") is None`, and skips CLI/`command`/`agy-desktop-sidecar` targets whose `cwd` or binary is a pure POSIX path (`/home/...`, `/opt/...`, excluding `/mnt/<drive>`).
  - On POSIX/WSL2 (`os.name != "nt"`): skips `driver="agy-desktop-sidecar"` when `ANTIGRAVITY_LS_ADDRESS` is unset (`SIDECAR_CONTEXT_REQUIRED`), and **even when `ANTIGRAVITY_LS_ADDRESS` IS set inside a WSL2 `agy` CLI session**, skips Windows `<drive>:\` `cwd`/binary targets (`HOST_MISMATCH`) unless an existing local `binary` shim was explicitly passed (preserving mocked cross-OS unit tests!).
  - `_evaluate_wake_exit_code()` (`src/aim_mailbox/cli.py#L106-L163`) fails closed (`rc=2`) on `HOST_MISMATCH` and `SIDECAR_CONTEXT_REQUIRED` unless `--allow-deferred` is passed, while keeping `PASSIVE_DRIVER` (`driver=none`) strictly `rc=2` even under `--allow-deferred`.
- **Design Notes for `#16` (`events.jsonl`) When Unheld:**
  - Because Windows 11 Sidecar and WSL2 agents share `/mnt/c/Users/kingb/.aim/mailbox` over `9P`/NTFS, appending and rotating `events.jsonl` **must** acquire a lightweight lock (`locks/_events.lock.json` via existing atomic lock helpers in `store.py`) and wrap all `open`/`os.replace` operations in `try ... except OSError:` so event-feed contention can **never** abort envelope delivery, claim, or ACK transitions.
  - Do **not** add a `tail` subcommand (`COMMAND_HANDLERS` is already at the `15`-command ceiling!).

---

## 4. IMPLEMENTATION STRATEGY

1. On Inquiries or status checks, obey `AGENTS.md` §2 (YOLO Restraint): answer empirically and **STOP** without modifying files.
2. If directed to unhold and implement `#16` (or any new issue `<N>`):
   - Claim on board: `./aim projects in-progress <N>`
   - Spawn isolated worktree: `./aim fix <N>` and execute all edits/tests inside `workspace/issue-<N>`.
   - Write TDD tests covering Linux + simulated/native Windows NTFS lock contention, run `python3 -m pytest tests/ -v -W error::DeprecationWarning`, surgically stage explicit paths, commit (`Closes #<N>`), promote (`../../aim promote --yes`), prune remote archive (`./aim prune-remote --confirm`), move board item to `Done`, verify green GitHub Actions CI + `gitleaks`, and sync `/mnt/c/aim-mailbox` + respawn Windows Sidecar if runtime Python modules changed.

---

## 5. THE CRITICAL TRAPS & WARNINGS

> **⚠️ EPISTEMIC / OPERATIONAL WARNINGS**
> - **WSL2 → WINDOWS SUBPROCESS STDIN HANG:** Whenever invoking `/mnt/c/Python314/python.exe` or `/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe` from WSL2, **ALWAYS** append `< /dev/null` (and `-NoLogo -NoProfile -NonInteractive` for PowerShell), or the Windows process will hang indefinitely on open WSL stdin!
> - **SIDECAR IN-MEMORY MODULE CACHE:** Updating `/mnt/c/aim-mailbox` via `git pull` does **not** reload Python modules inside the already-running `C:\Python314\python.exe -u sidecar_runner.py` process. Find its PID via `Get-CimInstance Win32_Process` and `Stop-Process -Id <PID> -Force`—`restart_policy: on-failure` respawns it within ~2s (verify new timestamp in `sidecar.log`).
> - **`pathlib.Path` MONKEYPATCH TRAP IN CROSS-OS TESTS:** On Python 3.12+, instantiating `Path(...)` while `os.name` is monkeypatched to the opposite OS raises `NotImplementedError: cannot instantiate 'WindowsPath'/'PosixPath' on your system`. Always use `os.path.isfile()` in cross-OS driver checks and scope `os.name` monkeypatches inside `with monkeypatch.context() as mp:` after `MailboxStore` setup.
> - **REAL `tmux` TEST TRAP & NON-AGENT SESSIONS:** Any test invoking real `tmux` **MUST** unset `TMUX`, set an isolated `TMUX_TMPDIR`, and verify `#{socket_path}` is inside `TMUX_TMPDIR` before `tmux kill-server`. Never enroll service/scratch `tmux` sessions (`aim-connect-workspace`, `aim-facelift-proto`) in `agents.json`.
> - **PUBLIC REPO REDACTION GATE & NON-GOALS:** Never commit `/home/<user>`, `C:\Users\<user>`, or `/mnt/c/Users/<user>` in `src/`, `bin/`, `tests/`, `memory-wiki/`, or `README.md` (`test_no_hardcoded_host_paths_in_repo` enforces this). Keep `0` runtime deps, `0` daemons, `0` SQLite mailbox files, and `<= 15` CLI subcommands.

---

## 6. KEY PATHS

- `/home/kingb/aim-mailbox/src/aim_mailbox/store.py` — Atomic Maildir JSON store, filters, `gc`, `--clear-session-id`
- `/home/kingb/aim-mailbox/src/aim_mailbox/drivers.py` — 9 wake drivers, `resolve_latest_agy_session()`, `check_driver_host_affinity()`, sidecar installer
- `/home/kingb/aim-mailbox/src/aim_mailbox/dispatcher.py` — `pulse_mailbox()` / `watch_mailbox()`, pre-claim host-affinity skip, priority/coalesce & safety gates
- `/home/kingb/aim-mailbox/src/aim_mailbox/hooks.py` & `_fast_check.py` — Cross-harness `install-hooks` & `<10ms` empty check
- `/home/kingb/aim-mailbox/src/aim_mailbox/cli.py` & `/home/kingb/aim-mailbox/bin/aim-mail` — 15-verb CLI, `_evaluate_wake_exit_code()`, & `<2ms` bash check fast-path
- `/home/kingb/aim-mailbox/tests/` — 40 unit + E2E pytest tests (`test_store.py`, `test_drivers_and_dispatcher.py`, `test_cli_e2e.py`)
- `/mnt/c/aim-mailbox` — Synced Windows 11 clone used by `C:\Python314\python.exe` Sidecar
- `/mnt/c/Users/kingb/.aim/mailbox` (`/home/kingb/.aim/mailbox` symlink) — Shared Windows 11 + WSL2 live Maildir (`18` endpoints in `agents.json`)
- `/mnt/c/Users/kingb/.gemini/config/sidecars/aim-mailbox/` & `/mnt/c/Users/kingb/.gemini/antigravity/sidecar_data/aim-mailbox/logs/sidecar.log` — Live Windows Sidecar config & runtime log

---

## 7. THE FULL PICTURE / WHAT COMES AFTER

| Phase | Target | Gate |
|---|---|---|
| Live Fleet (`v0.2.2`, Active) | 18 cross-OS endpoints (`14` WSL2 `tmux` + `4` Win11 Sidecar `auto(cwd)`) | ✅ Live (`40/40` pytest, CI `45758401701` green, Sidecar `PID 10412` active) |
| Optional WSL2 Wake/Hook Polish | Operator-directed `install-hooks` or WSL2 `cron` `pulse` + weekly `gc` | Zero idle RAM, `<2ms` empty-inbox hook latency |
| Deferred (`On Hold`) | `#16` (`events.jsonl` bounded write-only feed, `<= 15` CLI verbs, no daemon/`tail`) | Blocked until `aim-office` `#23`–`#30` are ready |

---

## 8. OPERATOR PREFERENCES

- Follow `AGENTS.md`: strict `./aim fix` worktrees for code, `./aim projects` board sync, TDD with empirical proof, surgical `git add <paths>`, Inquiry restraint (never modify files unprompted on questions), and final `🔊` plain-English spoken summary on every reply.
- Keep `aim-mailbox` zero-daemon, pure-stdlib, `<= 15` subcommands, and Windows 11 NTFS ↔ WSL2 `/mnt/c` safe.

---

## 9. IMMEDIATE NEXT STEPS

1. Verify clean baseline & fleet health: `python3 -m pytest tests/ -v -W error::DeprecationWarning && aim-mail agents && ./aim projects board`
2. Await Operator directive (e.g., cross-OS fleet dispatches, WSL2 `install-hooks` / `pulse` timer setup, or unholding `#16` once `aim-office` `#23`–`#30` land).
