# aim-mailbox — Engineering Handoff

> **Updated:** 2026-11-25T23:41:00-05:00
> **Updated by:** `aim-mailbox` (`agy` / Gemini 4 Carbon, session `7d9bc6ab-eb35-4044-801d-14e1fb8d2c33`)
> **Priority Mission:** Ship `v0.2.2` cross-OS watcher host-affinity guard (preventing Windows Sidecar `watch` from claiming/quarantining WSL2 `tmux` queues), sync `C:\aim-mailbox` + respawn Sidecar, and optionally enroll remaining fleet workspaces (`#16` remains `On Hold`).
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

*(Keep this table consolidated. See `memory-wiki/log.md` and closed GitHub issues for full history — do not re-audit.)*

---

## 1. PROJECT IDENTITY

`aim-mailbox` (`aim-mail`, `v0.2.1`, MIT) is a pure-Python 3.10+ stdlib (`0` runtime pip dependencies, `0` daemons, `15` CLI subcommands) local JSON Maildir post office, priority wake dispatcher, and turn-hook installer across Linux, WSL2, macOS, and Windows 11 (`agy-desktop-sidecar`, `agy-cli`, `opencode-http`, `grok-cli`, `codex-cli`, `vscode-chat`, `tmux`, `command`, `none`).

- **Repo & Project Board:** `/home/kingb/aim-mailbox` (`BrianV1981/aim-mailbox`, GitHub Project `#15`), `main` @ `306aa92` (`tag: v0.2.1`), Windows clone at `/mnt/c/aim-mailbox` (`C:\aim-mailbox`)
- **Test Baseline:** `python3 -m pytest tests/ -v -W error::DeprecationWarning` (`38 passed` on Linux; `ubuntu-latest` + `windows-latest` CI `45744286831` + `gitleaks` `45744286821` green)
- **Live Shared Mailbox (`AIM_MAILBOX_DIR`):** `/mnt/c/Users/kingb/.aim/mailbox` (`C:\Users\kingb\.aim\mailbox`) with 4 registered endpoints (`aim-auditor`, `aim-connect`, `aim-mailbox` on WSL2 `driver=tmux`; `joshua-win11` on Win11 `driver=agy-desktop-sidecar` `target=auto(cwd)` `cwd=C:\joshua`) and live Windows Sidecar running `C:\Python314\python.exe -u sidecar_runner.py`

### Your Knowledge Base
- `/home/kingb/aim-mailbox/memory-wiki/index.md` — Project wiki index
- `/home/kingb/aim-mailbox/memory-wiki/log.md` — Chronological engineering & live fleet test log
- `/home/kingb/aim-mailbox/memory-wiki/pages/open-source-landscape.md` — OSS audit + **§4 Explicit Non-Goals (Do Not Adopt)**
- `/home/kingb/aim-mailbox/memory-wiki/pages/maildir-and-dispatcher-architecture.md` — Atomic JSON Maildir layout & schemas
- `/home/kingb/aim-mailbox/memory-wiki/pages/windows-11-and-cross-os-drivers.md` — Cross-OS wake driver matrix, `auto(cwd)` mechanics & shared watcher notes

---

## 2. YOUR MISSION: SHIP `v0.2.2` CROSS-OS WATCHER HOST-AFFINITY GUARD

Fix the cross-OS shared-mailbox watcher collision discovered during the live 3-peer fleet test so Windows 11 Sidecar `watch` and WSL2 `pulse`/`watch` never claim, fail, or quarantine envelopes belonging to the opposite OS's local drivers. **Leave `#16` (`[ON HOLD]`) untouched.**

### Execution Queue (in order)

#### 1️⃣ `aim-mailbox#18` (Open & Resolve) — Cross-OS Host-Affinity Gate in `pulse_mailbox()` (`v0.2.2`)
**Problem:** The live Windows 11 Sidecar runs `aim-mail --mailbox-dir C:\Users\kingb\.aim\mailbox watch --interval 1.5` across the shared `agents.json`. When WSL2 peers (`aim-connect`, `aim-auditor`, `joshua-win11`) replied to `aim-mailbox` (`driver=tmux`, `cwd=/home/kingb/aim-mailbox`), the Windows Sidecar swept `aim-mailbox`, claimed the unread replies on Windows (`os.name == "nt"`), failed 3 times with `FileNotFoundError: [WinError 2]` (because `tmux.exe` does not exist on Windows), and moved all 3 WSL2 replies into `boxes/aim-mailbox/dead/`. The symmetric problem occurs if a global WSL2 `pulse` sweeps `joshua-win11` (`driver=agy-desktop-sidecar` without `ANTIGRAVITY_LS_ADDRESS` or Windows `C:\...` CLI `cwd`).
**Fix:** Add a pre-claim host-affinity check in `src/aim_mailbox/dispatcher.py` (and/or `drivers.py`) before acquiring the agent lock or claiming unread files:
- On Windows (`os.name == "nt"`): skip/defer `driver == "tmux"` when no explicit existing `binary` and `shutil.which("tmux") is None`, and skip local CLI drivers (`agy-cli`, `grok-cli`, `codex-cli`, `vscode-chat`) whose `cwd` is a pure POSIX path starting with `/` (not `/mnt/<drive>`).
- On POSIX/WSL2 (`os.name != "nt"`): skip/defer `driver == "agy-desktop-sidecar"` when `ANTIGRAVITY_LS_ADDRESS` is unset (on global sweeps, and fail closed without burning `attempts` on targeted `send --wake`!), and skip local CLI drivers whose `cwd` is a Windows `<drive>:\` path when the target binary is Windows-only or unreachable.
- Never claim envelopes or increment `attempts` on host-mismatched targets (`summary["skipped"]` or `summary["deferred"]` with reason `"HOST_MISMATCH"` / `"SIDECAR_CONTEXT_REQUIRED"`).
**Key files:** `/home/kingb/aim-mailbox/src/aim_mailbox/dispatcher.py`, `/home/kingb/aim-mailbox/src/aim_mailbox/drivers.py`, `/home/kingb/aim-mailbox/src/aim_mailbox/cli.py`, `/home/kingb/aim-mailbox/tests/test_drivers_and_dispatcher.py`, `/home/kingb/aim-mailbox/tests/test_cli_e2e.py`

#### 2️⃣ Sync Live Windows 11 Sidecar & Re-Verify Multi-OS Round-Trip
**Problem:** Running `sidecar_runner.py` on Windows keeps the old module imported in RAM until respawned, and the 3 test replies (`20261126T042447386987Z_d4b457b9`, `20261126T042501605165Z_c2daade1`, `20261126T042514181032Z_93ece532`) are sitting in `boxes/aim-mailbox/dead/`.
**Fix:** After `#18` (`v0.2.2`) passes Linux + Windows CI, run `git -C /mnt/c/aim-mailbox pull --ff-only`, terminate the running `sidecar_runner.py` `python.exe` PID so Antigravity's `on-failure` supervisor respawns it on `v0.2.2`, clean/gc the 3 test `dead/` envelopes if desired, and verify a WSL2 reply stays untouched in `boxes/aim-mailbox/unread/` while the Windows Sidecar watches!
**Key files:** `/mnt/c/aim-mailbox`, `/mnt/c/Users/kingb/.gemini/antigravity/sidecar_data/aim-mailbox/logs/sidecar.log`, `/mnt/c/Users/kingb/.aim/mailbox/agents.json`

#### 3️⃣ Optional Fleet Workspace Enrollment (Operator-Directed)
**Problem:** Only 4 of ~19 active WSL2 `tmux` + Windows 11 Desktop workspaces are registered in `agents.json` so far.
**Fix:** If requested by the Operator, batch-register additional WSL2 `tmux` sessions (`aim-office`, `aim-joshua`, `aim-ld`, etc.) and Windows 11 Desktop projects (`copy-cat`, `aim-social-studio`) using project-scoped `--cwd` / `--tmux-session` (zero pinned `session_id`s).
**Key files:** `/mnt/c/Users/kingb/.aim/mailbox/agents.json`

---

## 3. DETAILED ANALYSIS / BREAKDOWN

- **Targeted `--wake` vs. Global `pulse` / `watch` Semantics on Host Mismatch:**
  - When `watch` or `pulse` sweeps all agents (`agent_id=None`), host-mismatched agents (e.g., WSL2 `tmux` seen by Windows Sidecar, or Windows `agy-desktop-sidecar` seen by WSL2 `cron`) should append to `summary["skipped"]` (`reason="HOST_MISMATCH"`) and leave `boxes/<aid>/unread/` **100% untouched** (`attempts` stays `0`).
  - When a user explicitly runs `aim-mail send --to joshua-win11 ... --wake` from WSL2 (`agent_id="joshua-win11"`), `_evaluate_wake_exit_code()` in `src/aim_mailbox/cli.py` already fails closed (`rc=2`) on `skipped`/`deferred`! Just make sure `_evaluate_wake_exit_code()` prints an accurate message for `HOST_MISMATCH` / `SIDECAR_CONTEXT_REQUIRED` (instead of saying *"has passive driver 'none'"*—note line 119 of `cli.py` currently assumes any `summary["skipped"]` entry means passive driver `'none'`! Check `s.get("reason")` in `_evaluate_wake_exit_code()`!).
- **Preserving Existing Unit Tests on Mocked Drivers:**
  - In `tests/test_drivers_and_dispatcher.py`, tests register `driver="tmux"` with `binary=str(fake_tmux)` (which runs on `windows-latest` CI via `fake_tmux.cmd`!) and `driver="agy-desktop-sidecar"` without `ANTIGRAVITY_LS_ADDRESS` (`test_agy_desktop_sidecar_driver_and_installer` line 173 asserts `len(s_fail["failed"]) == 1` and `len(store.list_inbox("win-desktop", state="unread")) == 1`!).
  - **CRITICAL:** Check `test_agy_desktop_sidecar_driver_and_installer` (`tests/test_drivers_and_dispatcher.py#L170-L175`) before changing `agy-desktop-sidecar` behavior when `ANTIGRAVITY_LS_ADDRESS` is unset! And for `driver="tmux"` on Windows (`os.name == "nt"`), if `cfg.get("binary")` is set (like `fake_tmux.cmd` in CI!) or `shutil.which("tmux")` is found, DO NOT skip—only skip `driver="tmux"` on `os.name == "nt"` when `not cfg.get("binary") and shutil.which("tmux") is None`! That keeps `windows-latest` `fake_tmux.cmd` unit tests 100% green while blocking real Windows Sidecar `[WinError 2]` on WSL2 `tmux` agents!

---

## 4. IMPLEMENTATION STRATEGY

1. Open GitHub issue `#18`, claim on board (`./aim projects in-progress 18`), and spawn worktree (`./aim fix 18`).
2. Write TDD regression test simulating Windows (`monkeypatch.setattr(os, "name", "nt")`, `monkeypatch.setattr(shutil, "which", lambda _: None)`) running `pulse_mailbox(store)` over a `driver="tmux"` agent with unread mail—asserting `dispatched == []`, `failed == []`, `skipped` has `reason="HOST_MISMATCH"`, and the envelope remains in `unread/` with `attempts == 0` across 5 consecutive `pulse_mailbox` cycles! Also update `_evaluate_wake_exit_code()` in `cli.py` to distinguish `PASSIVE_DRIVER` from `HOST_MISMATCH`.
3. Bump `VERSION` + `pyproject.toml` + `src/aim_mailbox/__init__.py` + `README.md` + `CHANGELOG.md` + `memory-wiki/log.md` to `0.2.2`, run `python3 -m pytest tests/ -v -W error::DeprecationWarning`, promote (`../../aim promote --yes`), mark `#18` `Done`, prune remote branch, tag `v0.2.2`, and verify green `ubuntu-latest` + `windows-latest` CI + `gitleaks`.
4. Pull `/mnt/c/aim-mailbox` (`git -C /mnt/c/aim-mailbox pull --ff-only`), kill the old `sidecar_runner.py` PID via PowerShell (closing `< /dev/null`!) so Antigravity respawns it on `v0.2.2`, and verify live that an unread message in `boxes/aim-mailbox/unread/` is NOT touched by the Windows Sidecar.

---

## 5. THE CRITICAL TRAPS & WARNINGS

> **⚠️ EPISTEMIC / OPERATIONAL WARNINGS**
> - **WSL2 → WINDOWS SUBPROCESS STDIN HANG:** Whenever invoking `/mnt/c/Python314/python.exe` or `/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe` from WSL2, **ALWAYS** append `< /dev/null` (and `-NoLogo -NoProfile -NonInteractive` for PowerShell), or the Windows process will hang indefinitely on open WSL stdin!
> - **SIDECAR IN-MEMORY MODULE CACHE:** Updating `/mnt/c/aim-mailbox` via `git pull` does **not** reload Python modules inside the already-running `C:\Python314\python.exe -u sidecar_runner.py` process. Find its PID via `Get-CimInstance Win32_Process` and `Stop-Process -Id <PID> -Force`—`restart_policy: on-failure` respawns it within ~2s (verify new timestamp in `sidecar.log`).
> - **REAL `tmux` TEST TRAP:** Any test invoking real `tmux` **MUST** unset `TMUX` (`monkeypatch.delenv("TMUX", raising=False)`), set an isolated `TMUX_TMPDIR`, and verify `#{socket_path}` is inside `TMUX_TMPDIR` before calling `tmux kill-server`.
> - **PUBLIC REPO REDACTION GATE:** Never commit `/home/<user>`, `C:\Users\<user>`, or `/mnt/c/Users/<user>` in `src/`, `bin/`, `tests/`, `memory-wiki/`, or `README.md` (`test_no_hardcoded_host_paths_in_repo` enforces this!).
> - **NO COMPETITOR ANTI-PATTERNS:** Zero SQLite/FTS5 for mailbox storage, zero resident daemons/PTY wrappers, keep subcommands `<= 15` (currently `15`), and keep `#16` `On Hold`.

---

## 6. KEY PATHS

- `/home/kingb/aim-mailbox/src/aim_mailbox/store.py` — Atomic Maildir JSON store, filters, `gc`, `--clear-session-id`
- `/home/kingb/aim-mailbox/src/aim_mailbox/drivers.py` — 9 wake drivers, `resolve_latest_agy_session()`, sidecar installer
- `/home/kingb/aim-mailbox/src/aim_mailbox/dispatcher.py` — `pulse_mailbox()` / `watch_mailbox()`, priority/coalesce & safety gates
- `/home/kingb/aim-mailbox/src/aim_mailbox/hooks.py` & `_fast_check.py` — Cross-harness `install-hooks` & `<10ms` empty check
- `/home/kingb/aim-mailbox/src/aim_mailbox/cli.py` & `/home/kingb/aim-mailbox/bin/aim-mail` — 15-verb CLI & `<2ms` bash check fast-path
- `/home/kingb/aim-mailbox/tests/` — 38 unit + E2E pytest tests (`test_store.py`, `test_drivers_and_dispatcher.py`, `test_cli_e2e.py`)
- `/mnt/c/aim-mailbox` — Editable Windows 11 clone used by `C:\Python314\python.exe` Sidecar
- `/mnt/c/Users/kingb/.aim/mailbox` — Shared Windows 11 + WSL2 (`AIM_MAILBOX_DIR`) live Maildir
- `/mnt/c/Users/kingb/.gemini/config/sidecars/aim-mailbox/` & `/mnt/c/Users/kingb/.gemini/antigravity/sidecar_data/aim-mailbox/logs/sidecar.log` — Live Windows Sidecar config & runtime log

---

## 7. THE FULL PICTURE / WHAT COMES AFTER

| Phase | Target | Gate |
|---|---|---|
| `v0.2.2` (Immediate) | `#18` Cross-OS watcher host-affinity guard + live Sidecar reload | `39+` pytest green, Linux+Windows CI green, WSL2 `unread/` untouched by Win11 Sidecar |
| Fleet Onboarding | Optional registration of remaining WSL2 `tmux` & Win11 Desktop workspaces | Project-scoped `--cwd` / `--tmux-session` (zero pinned `session_id`s) |
| Deferred | `#16` (`events.jsonl` write-only feed, no daemon/`tail`) | Blocked until `aim-office` `#23`–`#30` are ready |

---

## 8. OPERATOR PREFERENCES

- Follow `AGENTS.md`: strict `./aim fix` worktrees for code, `./aim projects` board sync, TDD with empirical proof, surgical `git add <paths>`, Inquiry restraint (never modify files unprompted on questions), and final `🔊` plain-English spoken summary on every reply.
- Keep `aim-mailbox` zero-daemon, pure-stdlib, `<= 15` subcommands, and Windows 11 NTFS ↔ WSL2 `/mnt/c` safe.

---

## 9. IMMEDIATE NEXT STEPS

1. Verify clean baseline: `python3 -m pytest tests/ -v -W error::DeprecationWarning && gh issue list --state open && ./aim projects board`
2. Open `#18` for the cross-OS watcher host-affinity guard (if Operator confirms or directs execution), run `./aim fix 18 && ./aim projects in-progress 18`, implement + test (`v0.2.2`), promote, verify Linux+Windows CI, and reload the Windows Sidecar (`/mnt/c/aim-mailbox`).
