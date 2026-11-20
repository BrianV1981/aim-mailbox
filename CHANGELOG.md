# Changelog

All notable changes to `aim-mailbox` are documented in this file.

## [0.1.0] - 2026-11-19

### Added
- **Lock-Free Atomic Maildir Store (`aim_mailbox.store`):** Pure-Python stdlib JSON Maildir engine (`tmp/`, `unread/`, `claimed/`, `done/`, `dead/` via `os.replace()`), safe across POSIX, native Windows 11 NTFS, and WSL2 `/mnt/c` (`9P`) boundaries without SQLite WAL lock contention.
- **Agent Registry & Lease Locks:** Logical agent address book (`agents.json`), strict agent-name path-traversal validation, per-agent TTL dispatch locks (`locks/<agent>.lock.json`), and expired `claimed/` lease recovery.
- **Multi-Harness Wake Drivers (`aim_mailbox.drivers`):** Pluggable zero-LLM wake adapters for `agy-desktop-sidecar` (Antigravity Desktop/IDE Sidecar `agentapi send-message`), `agy-cli` (with read-only `conversation_summaries.db` idle check and automatic `conversation_id` persistence), `opencode-http` (`POST /session/<id>/message`), `grok-cli` (`grok --resume`), `codex-cli` (`codex exec`), `vscode-chat` (`code chat -m agent -r`), `tmux` (`Enter`-only 1-line doorbell + `capture-pane` verify), and `command` (custom script / GUI vision fallback).
- **Zero-Token Dispatcher (`aim_mailbox.dispatcher`):** Single-pass `pulse` (for `cron` / Windows Task Scheduler) and continuous `watch` loop enforcing `max_hops` loop quarantine before waking any LLM.
- **Self-Describing CLI (`aim-mail`) & Launchers:** 13 subcommands (`register`, `agents`, `send`, `inbox`, `read`, `reply`, `ack`, `check`, `wait`, `pulse`, `watch`, `install-agy-sidecar`, `describe`) plus zero-install cross-platform shims (`bin/aim-mail`, `bin/aim-mail.ps1`, `bin/aim-mail.cmd`).
- **Dual-OS CI & Test Suite:** Unit tests and real unmocked subprocess CLI E2E tests running under `-W error::DeprecationWarning` on `ubuntu-latest` and `windows-latest`, plus Gitleaks secret scanning.
