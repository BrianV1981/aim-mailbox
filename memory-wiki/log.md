# Memory Log

## [2026-11-19] ingest | Shipped `aim-mailbox` `v0.1.0` Core Engine, 8 Wake Drivers, CLI & Dual-OS CI (`#1`)
- Published `BrianV1981/aim-mailbox` (`MIT`) and linked GitHub Project `#15` (`@BrianV1981's aim-mailbox`).
- Implemented `src/aim_mailbox/` (`store.py`, `drivers.py`, `dispatcher.py`, `cli.py`) in pure Python 3.10+ stdlib (`0` runtime pip dependencies) with 13 CLI subcommands (`register`, `agents`, `send`, `inbox`, `read`, `reply`, `ack`, `check`, `wait`, `pulse`, `watch`, `install-agy-sidecar`, `describe`) and cross-platform launchers (`bin/aim-mail`, `bin/aim-mail.ps1`, `bin/aim-mail.cmd`).
- Hardened Windows + cross-OS edge cases caught during E2E testing: explicit literal `{token}` expansion in `drivers.py` (preventing `str.format()` `KeyError` on JSON/dict braces inside custom commands), `defer=True` claim release so busy sessions never burn retry attempts, UTF-8 stdio reconfiguration for Windows `cp1252` consoles, and `os.replace()` retry backoff for transient NTFS file-scanner locks.
- Verified `15/15` unit + unmocked subprocess CLI E2E pytest tests (`0` warnings) locally on Linux and real Windows 11 PowerShell/CMD shims.

## [2026-11-19] ingest | Bootstrap `aim-mailbox` Architecture, OSS Audit & Windows 11 Harness Matrix
- Initialized `aim-mailbox` J.O.S.H.U.A. workspace (`v1.1.3` clean scaffold) and synthesized full architectural research into `memory-wiki/`.
- Created `pages/vision-and-positioning.md`: documented the Windows 11 `tmux` boundary, One-Way GUI Diode, zero-LLM router philosophy, and ecosystem positioning (`aim-mailbox` superseding `aim-chalkboard` while keeping UI cockpits in `aim-connect`/`aim-office`).
- Created `pages/open-source-landscape.md`: audited `@juanjofuchs/agent-mail`, `mcp_agent_mail` / `mcp_agent_mail_rust` (daemon bloat + non-OSI license rider), `AgentWorkforce/relay` (`relay.sock` + PTY daemon), and Gas Town `gt mail`.
- Created `pages/maildir-and-dispatcher-architecture.md`: specified the lock-free atomic JSON Maildir storage model across Windows NTFS and WSL2 `/mnt/c` (`9P`), envelope & `agents.json` schemas, and zero-token cron/Task-Scheduler `pulse` guardrails.
- Created `pages/windows-11-and-cross-os-drivers.md`: documented deterministic session-targeting mechanics across Antigravity Desktop Sidecars (`agentapi send-message` via `language_server.exe`, empirically proven E2E from WSL2 into a minimized Windows 11 Desktop session with zero app restart), `agy` CLI (`--conversation <uuid> -p` + `conversation_summaries.db` idle check), OpenCode `serve` HTTP API, VS Code `v1.145+` (`code chat` & `code agent`), Grok Build (`--resume`), Codex `app-server` JSON-RPC, POSIX `tmux`, and optional Lars vision fallback for closed GUIs.
- Created `pages/aim-communicate-v2-migration.md`: documented the single-skill `aim-communicate` `v2.0` upgrade path in `aim-skill-library` (resolving `#17` and `#19` while preserving Operator permission and `max_hops` loop gates).
