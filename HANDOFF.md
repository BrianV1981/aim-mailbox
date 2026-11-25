# aim-mailbox — Engineering Handoff

> **Updated:** 2026-11-24T21:05:00-05:00
> **Updated by:** `aim-mailbox` (`agy` / Gemini 4 Hydrogen, session `8cb8be17-77c6-41b8-ab9f-d9a1693bde7d`)
> **Priority Mission:** Implement `v0.2.0` roadmap (`#15`, `#14`, `#13`) in isolated `./aim fix` worktrees while honoring explicit non-goals (`#16` remains `On Hold`).
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

*(Keep this table consolidated. See `memory-wiki/log.md` and closed GitHub issues for full history — do not re-audit.)*

---

## 1. PROJECT IDENTITY

`aim-mailbox` (`aim-mail`, `v0.1.2`, MIT) is a pure-Python 3.10+ stdlib (`0` runtime pip dependencies, `0` daemons) local JSON Maildir post office and cross-OS wake dispatcher across Linux, WSL2, macOS, and Windows 11 (`agy-desktop-sidecar`, `agy-cli`, `opencode-http`, `grok-cli`, `codex-cli`, `vscode-chat`, `tmux`, `command`, `none`).

- **Repo & Project Board:** `BrianV1981/aim-mailbox` (GitHub Project `#15`), `main` @ `48f7d95` (ahead of `v0.1.2` tag `26114ff` with wiki triage docs)
- **Test Baseline:** `python3 -m pytest tests/ -v -W error::DeprecationWarning` (`30 passed` on Linux; `ubuntu-latest` + `windows-latest` CI green)
- **Live Host Mailbox:** `~/.aim/mailbox` (`aim-mailbox`, `aim-auditor` registered with `driver=tmux`)

### Your Knowledge Base
- `memory-wiki/index.md` — Project wiki index
- `memory-wiki/log.md` — Chronological engineering & audit log
- `memory-wiki/pages/open-source-landscape.md` — OSS audit + **§4 Explicit Non-Goals (Do Not Adopt)**
- `memory-wiki/pages/maildir-and-dispatcher-architecture.md` — Atomic JSON Maildir layout & schemas
- `memory-wiki/pages/windows-11-and-cross-os-drivers.md` — Cross-OS wake driver matrix

---

## 2. YOUR MISSION: EXECUTE `v0.2.0` ROADMAP (`#15`, `#14`, `#13`)

Implement the three approved `v0.2.0` enhancements in isolated `./aim fix <id>` worktrees while keeping total CLI subcommands `<= 15` (currently `13`), `0` runtime pip dependencies, and strict cross-OS (`ubuntu-latest` + `windows-latest`) parity. **Leave `#16` (`[ON HOLD]`) untouched.**

### Execution Queue (in order)

#### 1️⃣ `aim-mailbox#15` — Inbox Query Filters & Maildir Retention `gc`
**Problem:** `_lookup_thread_bounds()` (`src/aim_mailbox/store.py`) and `inbox --state all` scan all JSON files across `done/`/`dead/`; long-lived boxes grow unbounded.
**Fix:** Extend `aim-mail inbox <agent>` with `--from`, `--thread-id`, `--query`, `--since`, `--limit` (no separate `search` subcommand, zero SQLite/FTS5) and add `aim-mail gc [--agent A] [--done-days 14] [--dead-days 30] [--dry-run] [--archive-dir DIR] [--json]` pruning/archiving only `done/` and `dead/`.
**Key files:** `src/aim_mailbox/store.py`, `src/aim_mailbox/cli.py`, `tests/test_store.py`, `tests/test_cli_e2e.py`

#### 2️⃣ `aim-mailbox#14` — Priority Lanes & `--coalesce-key` Batching
**Problem:** Routine updates wake active sessions individually while urgent stops share the same FIFO queue.
**Fix:** Add backward-compatible `priority` (`urgent|normal|low`, default `normal`) and `coalesce_key` fields to envelopes (`send`, `reply`, `inbox`), dispatch `urgent` first, batch matching `coalesce_key` unread envelopes cleanly without stranding `ack_required=True` IDs, and defer `low` into bounded batch windows while enforcing `operator_approved` and `max_hops` on all priorities.
**Key files:** `src/aim_mailbox/store.py`, `src/aim_mailbox/dispatcher.py`, `src/aim_mailbox/cli.py`, `tests/test_drivers_and_dispatcher.py`

#### 3️⃣ `aim-mailbox#13` — Cross-Harness Turn-Boundary Hook Installer (`install-hooks`) & `v0.2.0` Release
**Problem:** `aim-mail check --inject <agent>` exists, but harnesses require manual hook wiring.
**Fix:** Add `aim-mail install-hooks --harness agy|opencode|codex|grok|generic` (`--uninstall`, `--dry-run`, `--json`) writing non-destructive hooks (no PTY proxy). Verify exact native hook schemas per harness and benchmark `<15ms` empty-inbox `check --inject --quiet` path. Then bump version to `0.2.0` (`VERSION`, `pyproject.toml`, `src/aim_mailbox/__init__.py`, `CHANGELOG.md`), confirm green `CI` + `gitleaks`, and tag `v0.2.0`.
**Key files:** `src/aim_mailbox/drivers.py` (or new `hooks.py`), `src/aim_mailbox/cli.py`, `README.md`, `tests/test_cli_e2e.py`

---

## 3. DETAILED ANALYSIS / BREAKDOWN

- **`#15` (`inbox` + `gc`):** Age terminal envelopes by `acked_at` (`done/`) and `quarantined_at` (`dead/`), falling back to `created_at` / `stat().st_mtime`. Never touch `unread/` or `claimed/`. Confine `--archive-dir` safely (`write_json_atomic` + `_unlink_with_retry`, `0o700`/`0o600`).
- **`#14` (`priority` + `coalesce_key`):** Coalescing must preserve or cleanly retire superseded IDs (e.g., claim all matching `coalesce_key` unread envelopes in one wake batch, or transition superseded items to `done/` with `coalesced_into`) so `ack_required=True` senders do not stall. `low` batch-window deferral needs a max-age floor so low-priority items eventually dispatch; `send --priority low --wake` without `--allow-deferred` should fail closed clearly.
- **`#13` (`install-hooks`):** Antigravity `hooks.json` schema is verified (`SessionStart`, `PreInvocation`, `Stop` arrays with `"type": "command"`); verify `opencode`, `codex`, and `grok` hook surfaces empirically before writing adapters (use `generic` fallback if any harness lacks native command hooks).

---

## 4. IMPLEMENTATION STRATEGY

1. Execute one issue per `./aim fix <id>` worktree (`./aim projects in-progress <id>` -> TDD + code -> `python3 -m pytest tests/ -v -W error::DeprecationWarning` -> `../../aim promote --yes` -> `./aim projects done <id>` -> `./aim prune-remote --confirm`).
2. Wait for GitHub Actions `CI` (`ubuntu-latest` + `windows-latest`) and `gitleaks` to pass green after each promotion before starting the next issue.
3. After `#13` passes CI, bump `VERSION` + `pyproject.toml` + `src/aim_mailbox/__init__.py` + `CHANGELOG.md` to `0.2.0`, update `memory-wiki/`, push, and tag `v0.2.0`.

---

## 5. THE CRITICAL TRAPS & WARNINGS

> **⚠️ EPISTEMIC / OPERATIONAL WARNINGS**
> - **REAL `tmux` TEST TRAP:** Any test invoking real `tmux` **MUST** unset `TMUX` (`monkeypatch.delenv("TMUX", raising=False)`), set an isolated `TMUX_TMPDIR`, and verify `#{socket_path}` is inside `TMUX_TMPDIR` before calling `tmux kill-server`. Inherited `$TMUX` overrides `TMUX_TMPDIR` and kills the live host `tmux` server!
> - **PUBLIC REPO REDACTION GATE:** Never commit machine-local `/home/<user>` paths or private brand names in any tracked file (`test_no_hardcoded_host_paths_in_repo` enforces `src/`, `bin/`, `tests/`, `memory-wiki/`, `README.md`).
> - **WINDOWS `cp1252` & NTFS LOCKING:** Always pass `encoding="utf-8", errors="replace"` in subprocess captures, `-X utf8` in `.cmd` test shims, and keep `PermissionError`/`EBUSY` retry handling around lock/state operations.
> - **NO COMPETITOR ANTI-PATTERNS:** Zero SQLite/FTS5, zero resident daemons/PTY wrappers, keep subcommands `<= 15`, and keep `#16` `On Hold`.

---

## 6. KEY PATHS

- `src/aim_mailbox/store.py` — Atomic Maildir JSON store, validation, locks, thread bounds
- `src/aim_mailbox/drivers.py` — 9 wake drivers, `is_agy_session_idle`, sidecar install/uninstall
- `src/aim_mailbox/dispatcher.py` — `pulse_mailbox` / `watch_mailbox`, isolation & policy gates
- `src/aim_mailbox/cli.py` — 13-subcommand CLI parser (`COMMAND_HANDLERS`) & fail-closed `--wake`
- `tests/test_store.py`, `tests/test_drivers_and_dispatcher.py`, `tests/test_cli_e2e.py` — 30 unit + E2E tests
- `.github/workflows/ci.yml`, `.github/workflows/gitleaks.yml` — Dual-OS CI (`ubuntu-latest`, `windows-latest`) + secret scan
- `memory-wiki/pages/open-source-landscape.md` — Non-goals & OSS guardrails (`§4`)

---

## 7. THE FULL PICTURE / WHAT COMES AFTER

| Phase | Target | Gate |
|---|---|---|
| `v0.2.0` | `#15` (`inbox` filters + `gc`), `#14` (`priority` + `coalesce_key`), `#13` (`install-hooks`) | `<= 15` CLI verbs, `0` runtime deps, Linux+Windows CI green |
| Deferred | `#16` (`events.jsonl` write-only feed, no daemon/`tail`) | Blocked until `aim-office` `#23`–`#30` are ready |

---

## 8. OPERATOR PREFERENCES

- Follow `AGENTS.md`: strict `./aim fix` worktrees for code, `./aim projects` board sync, TDD with empirical proof, surgical `git add <paths>`, and final `🔊` plain-English spoken summary on every reply.
- Keep `aim-mailbox` zero-daemon, pure-stdlib, and Windows 11 NTFS ↔ WSL2 `/mnt/c` safe.

---

## 9. IMMEDIATE NEXT STEPS

1. Verify clean baseline: `python3 -m pytest tests/ -v -W error::DeprecationWarning && gh issue list --state open && ./aim projects board`
2. Confirm with Operator (or proceed if authorized) on `#15`: `./aim fix 15 && ./aim projects in-progress 15`
3. Implement `#15` (`inbox` filters + `aim-mail gc`), verify Linux + Windows CI, then proceed to `#14` and `#13`.
