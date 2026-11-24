# Changelog

All notable changes to `aim-mailbox` are documented in this file.

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
