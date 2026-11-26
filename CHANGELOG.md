# Changelog

All notable changes to `aim-mailbox` are documented in this file.

## [0.3.0] - 2026-11-26

### Added
- **First-Class `amail` CLI Alias & Canonical `aim-mail` Skill (`#19`, `aim-joshua#181`):** Added `amail` alongside `aim-mail` in `pyproject.toml` `[project.scripts]` and cross-OS zero-install launchers (`bin/amail`, `bin/amail.cmd`, `bin/amail.ps1`, including the `<2ms` POSIX empty-inbox `check` fast-path and symlink-chain resolution). Updated `describe` (`brand: "A-Mail"`, `aliases: ["amail", "aim-mail"]`, `skill: "aim-mail"`), `--version` / `--help`, `hooks.py` (`amail` + `aim-mail` hook recognition), `README.md`, and `AGENTS.md` (`§8c. THE A-MAIL PROTOCOL`), and bundled canonical `skills/aim-mail/SKILL.md` superseding `aim-communicate` and retiring standalone `tmux_send.sh` scripts.

## [0.2.2] - 2026-11-25

### Fixed
- **Cross-OS Shared-Mailbox Watcher Host-Affinity Guard (`#18`):** Added `check_driver_host_affinity()` in `src/aim_mailbox/drivers.py` and pre-claim skipping in `pulse_mailbox()` (`src/aim_mailbox/dispatcher.py`) so Windows 11 Sidecar `watch` and POSIX/WSL2 `pulse`/`watch` sharing one `AIM_MAILBOX_DIR` (`agents.json`) never claim, fail, or quarantine envelopes belonging to opposite-OS drivers (`tmux` and pure-POSIX `cwd`/binaries on Windows; `agy-desktop-sidecar` without `ANTIGRAVITY_LS_ADDRESS` and Windows `<drive>:\` `cwd`/binaries on POSIX). Updated `_evaluate_wake_exit_code()` in `src/aim_mailbox/cli.py` to report `HOST_MISMATCH` and `SIDECAR_CONTEXT_REQUIRED` distinctly from `PASSIVE_DRIVER` (`driver=none`) while leaving `unread/` `attempts` at `0`.

## [0.2.1] - 2026-11-25

### Added
- **Project-Scoped `--cwd` Auto-Session Resolution Across Handoffs (`#17`):** `agy-desktop-sidecar` and `agy-cli` now dynamically resolve the most recently active top-level (`nesting_depth = 0`, `killed = 0`) `conversation_id` matching `--cwd` (`workspace_uris`) from Antigravity's read-only `conversation_summaries.db` across Windows (`file:///c%3A/...`, `C:\...`), WSL2 (`/mnt/c/...`), and POSIX (`file:///...`) without pinning `session_id` in `agents.json`, eliminating manual mailbox updates during `/aim-handoff`. Added `--clear-session-id` on `aim-mail register` and `target=auto(cwd)` display in `aim-mail agents`.

## [0.2.0] - 2026-11-24

### Added
- **Inbox Query Filters & Maildir Retention GC (`#15`):** Extended `aim-mail inbox <agent>` with pure-stdlib `--from`, `--thread-id`, `--priority`, `--query`, `--since`, and `--limit` filters (zero SQLite/FTS5), and added `aim-mail gc [--agent A] [--done-days 14] [--dead-days 30] [--dry-run] [--archive-dir DIR] [--json]` to prune or atomically archive aged terminal envelopes from `done/` and `dead/` while never touching `unread/` or `claimed/`.
- **Priority Lanes & Coalesce-Key Batching (`#14`):** Added backward-compatible `priority` (`urgent|normal|low`, default `normal`) and `coalesce_key` fields across envelopes (`send`, `reply`, `inbox`, `read`). `pulse_mailbox()` / `watch_mailbox()` dispatch `urgent` first, coalesce matching `coalesce_key` unread envelopes into one wake batch (atomically retiring superseded IDs to `done/` with `coalesced_into` only on `DELIVERED`), defer `low` envelopes into bounded `--low-batch-window` windows (with fail-closed `send --priority low --wake` unless `--allow-deferred`), and preserve `operator_approved` and `max_hops` gates across all priorities.
- **Cross-Harness Turn-Boundary Hook Installer & `<15ms` Fast Check (`#13`):** Added `aim-mail install-hooks --harness agy|opencode|codex|grok|generic` (`--uninstall`, `--dry-run`, `--json`) to non-destructively install turn-boundary `check --inject --quiet` hooks (using Antigravity `hooks.json` `SessionStart`/`PreInvocation`/`Stop`, Codex `hooks.json` `Stop`, Grok `hooks/aim-mailbox.json`, and generic shell fallback for OpenCode/`generic`), added `--format agy-inject|agy-stop` (with `continuationActive` loop guard) on `check`, and added a `<2ms` pure-shell / `<10ms` zero-import empty-inbox fast-path keeping empty `check --inject --quiet` well under `15ms`.

## [0.1.2] - 2026-11-23

### Security & Reliability
- **Path Confinement, Permissions & Validation (`#3`, `#6`, `#8`):** Blocked `../` traversal on `message_id`, envelope `id`, and `--sidecar-id`, enforced `0o700`/`0o600` file permissions, validated `session_id`/`http_url`, normalized `agent_id` to lowercase (disallowing trailing `.`), locked `agents.json` RMW, eliminated `acquire_agent_lock()` stale-unlink TOCTOU, and retried `EBUSY`/`PermissionError` atomic moves.
- **Thread & Dispatcher Integrity (`#4`, `#5`, `#7`, `#9`):** Propagated thread `hop_count` on `send --thread-id`, rejected over-hop/`dead/` replies, added `wait --claim` and `inbox --state active`, kept async `ack_required=False` wakes readable until read, isolated driver OS/subprocess/HTTP failures in `pulse`/`watch`, and enforced fail-closed `--wake` exits plus initial `operator_approved` gating.
- **Real `tmux` & Windows CI Fixes (`#6`, `#11`):** Separated `tmux` `session_target` (`=name`) from `pane_target` (`=name:`), updated `opencode-http` to OpenCode `parts` schema, fixed `bin/aim-mail.cmd` exit-code propagation, fixed Windows wrapper/UTF-8 test shim handling, and made `--dangerously-skip-permissions` opt-in for `agy-cli`.

## [0.1.1] - 2026-11-20

### Fixed
- **Symlink-Safe POSIX Launcher (`bin/aim-mail`, `#2`):** Resolved relative and absolute symlink chains (`~/.local/bin/aim-mail -> .../bin/aim-mail`) portably across Linux, WSL2, and macOS before computing `REPO_ROOT`, and added an E2E symlink invocation regression test.

## [0.1.0] - 2026-11-19

### Added
- **Lock-Free Atomic Maildir Store (`aim_mailbox.store`):** Pure-Python stdlib JSON Maildir engine (`tmp/`, `unread/`, `claimed/`, `done/`, `dead/` via `os.replace()`), safe across POSIX, native Windows 11 NTFS, and WSL2 `/mnt/c` (`9P`) boundaries without SQLite WAL lock contention.
- **Agent Registry & Lease Locks:** Logical agent address book (`agents.json`), strict agent-name path-traversal validation, per-agent TTL dispatch locks (`locks/<agent>.lock.json`), and expired `claimed/` lease recovery.
- **Multi-Harness Wake Drivers (`aim_mailbox.drivers`):** Pluggable zero-LLM wake adapters for `agy-desktop-sidecar` (Antigravity Desktop/IDE Sidecar `agentapi send-message`), `agy-cli` (with read-only `conversation_summaries.db` idle check and automatic `conversation_id` persistence), `opencode-http` (`POST /session/<id>/message`), `grok-cli` (`grok --resume`), `codex-cli` (`codex exec`), `vscode-chat` (`code chat -m agent -r`), `tmux` (`Enter`-only 1-line doorbell + `capture-pane` verify), and `command` (custom script / GUI vision fallback).
- **Zero-Token Dispatcher (`aim_mailbox.dispatcher`):** Single-pass `pulse` (for `cron` / Windows Task Scheduler) and continuous `watch` loop enforcing `max_hops` loop quarantine before waking any LLM.
- **Self-Describing CLI (`aim-mail`) & Launchers:** 13 subcommands (`register`, `agents`, `send`, `inbox`, `read`, `reply`, `ack`, `check`, `wait`, `pulse`, `watch`, `install-agy-sidecar`, `describe`) plus zero-install cross-platform shims (`bin/aim-mail`, `bin/aim-mail.ps1`, `bin/aim-mail.cmd`).
- **Dual-OS CI & Test Suite:** Unit tests and real unmocked subprocess CLI E2E tests running under `-W error::DeprecationWarning` on `ubuntu-latest` and `windows-latest`, plus Gitleaks secret scanning.
