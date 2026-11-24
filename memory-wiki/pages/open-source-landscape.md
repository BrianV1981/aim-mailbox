# Open-Source Agent Mailer Audit

## Overview
A late-2026 landscape audit of open-source local inter-agent mail and relay systems shows a sharp split between **lightweight passive mailboxes** (which cannot wake sleeping agents) and **heavyweight daemon orchestrators** (which require POSIX PTY wrappers, Unix sockets, or bloated MCP surfaces). None combine lock-free Windows 11 ↔ WSL2 storage with zero-LLM multi-harness headless wake dispatchers.

---

## 1. Evaluated Open-Source Projects

| Project | Footprint & Storage | Wake-Up Mechanism | License & Limitations |
| :--- | :--- | :--- | :--- |
| **`@juanjofuchs/agent-mail`** (`JuanjoFuchs/agent-mail-cli`) & **`jwcraig/agent-mail-cli`** | Ultra-light CLI (~50 tokens via `--help` / `describe`); local SQLite (`~/.agent-mail/mail.db`); `--ref` file pointers. | **Passive only (`watch` blocks an already-running caller).** Zero cron/scheduler wake runner to boot sleeping CLI or Desktop agents. | Standard OSS, but SQLite WAL locks fail across the Windows 11 NTFS ↔ WSL2 `/mnt/c` (`9P`) mount boundary; no `max_hops` or Operator permission gates. |
| **`mcp_agent_mail`** & **`mcp_agent_mail_rust`** (`Dicklesworthstone`) | Heavy server (FastMCP / Rust + SQLite + Git + 16-screen TUI + ~30 MCP tools loaded at startup). | Relies on active client turn hooks, `fetch_inbox` polling, or advisory file-lease conflict errors (`FILE_RESERVATION_CONFLICT`). | **Non-OSI License** (MIT + custom vendor-restriction rider). Heavy daemon and context-window tool bloat. |
| **`AgentWorkforce/relay`** (`agent-relay`) | Always-on daemon (`~/.relay/relay.sock`), custom REPL shell, repo-local SQLite/state store, managed PTY/`tmux` supervisor. | Routes turns by wrapping interactive CLI processes inside Relay-owned PTYs/`tmux` sessions. | `Apache-2.0`. Forces agents to live inside Relay's PTY wrapper and Unix socket daemon instead of native OS schedulers and standalone Windows 11 harnesses. |
| **`aannoo/hcom`** (`hook-comms`) | PTY wrapper + SQLite (`~/.hcom/hcom.db`) + harness hooks/transcripts; `MIT` license. | Wraps live CLI panes in a PTY/hook supervisor to inject wakeups across terminal sessions. | Requires PTY/hook wrapping; lacks lock-free NTFS ↔ WSL2 `/mnt/c` Maildir storage and minimized Windows 11 Antigravity Desktop Sidecar wakes. |
| **`amux`** (`amux.io`) | Cloud-hosted `tmux` SaaS / managed remote terminal multiplexers. | Remote server pushes keystrokes into cloud-hosted `tmux` sessions. | Cloud SaaS / POSIX `tmux` dependency; not a zero-daemon local-first Windows 11 ↔ WSL2 Maildir post office. |
| **Gas Town Mail** (`gastownhall/gastown` — `gt mail`) | Monolithic workspace orchestrator (`gt mail send`, `gt mail inbox`, `gt mail check --inject`) tied to Beads task IDs. | Prompt-turn `--inject` hooks and Gas Town background polecat daemons. | Tightly coupled to the full Gas Town orchestrator and POSIX shell workflows; not usable as a thin cross-OS drop-in utility. |

---

## 2. Why Existing Tools Missed the Zero-Daemon + Wake-Runner Sweet Spot
1. **The "Interactive Terminal Window" Assumption:** Most authors started from *"How do I type into four open terminal windows?"* — pulling them into PTY virtualization, `tmux` keystroke scraping, and persistent socket daemons instead of treating CLI agents as **stateful-session, stateless-process** workers.
2. **Recent Convergence of Headless Stateful Resume Flags:** Only recently did major CLI harnesses standardize headless stateful session continuation (`agy --conversation <uuid> -p`, `grok --resume <id> -p`, `codex app-server` `thread/resume`, `opencode serve` `/session/<id>/message`), allowing an agent to wake for a single turn, process its inbox, and exit cleanly at `0` idle RAM.
3. **macOS-Centric File-Lock Assumptions:** Builders working exclusively on macOS never hit either the native Windows 11 `tmux` absence or the Windows NTFS ↔ WSL2 `/mnt/c` (`9P`) SQLite WAL locking failure (`database is locked`).

---

## 3. Borrowed Best Practices Integrated into `aim-mailbox`
- **From `@juanjofuchs/agent-mail`:** Keep message bodies concise, pass large specs/diffs by filesystem path (`--ref <path>`), and expose a self-describing JSON/CLI contract (`--help` / `describe`) instead of dozens of MCP tools.
- **From `gt mail`:** Support fast `<15ms` `aim-mail check --inject` turn hooks as an optional supplement for mid-turn awareness.
- **Unique to `aim-mailbox`:** Lock-free Maildir JSON across NTFS + WSL2 (see [Maildir & Dispatcher Architecture](maildir-and-dispatcher-architecture.md)), Operator permission + `max_hops` loop prevention (see [Unified Skill Strategy (`aim-communicate` v2)](aim-communicate-v2-migration.md)), and deterministic Windows 11 Desktop Sidecar / HTTP / CLI wake drivers (see [Windows 11 & Cross-OS Drivers](windows-11-and-cross-os-drivers.md)).

---

## 4. Architectural Anti-Patterns & Explicit Non-Goals (Do Not Adopt)
When borrowing ideas from competing orchestrators (advisory leases, turn hooks, priority batching, thread search/GC, and `events.jsonl` telemetry), contributors **MUST reject** these specific competitor pitfalls that violate `aim-mailbox`'s core invariants:

1. **No SQLite or FTS5 Databases (`hcom`, `mcp_agent_mail`, `agent-mail-cli`):**
   - **Pitfall:** SQLite WAL locks and shared-memory (`-shm`/`-wal`) files break or throw `database is locked` across native Windows 11 NTFS ↔ WSL2 `/mnt/c` (`9P`) shares.
   - **Rule:** Keep state (`boxes/`, `agents.json`, `locks/`, future advisory `leases/`) and queries (`search`/`gc`) 100% pure-stdlib JSON / JSONL Maildir files using atomic `os.replace()`, `O_CREAT | O_EXCL` leases, and NTFS `winerror 5/32` retries.
2. **No Always-On Socket Daemons or Mandatory PTY Wrappers (`AgentWorkforce/relay`, `hcom`):**
   - **Pitfall:** Requiring a resident Unix-socket server (`relay.sock`) or wrapping every interactive agent inside a PTY proxy wastes idle RAM, reintroduces daemon lifecycle failures, breaks native Windows 11 Desktop/GUI workflows, and loses the stateless `pulse` model.
   - **Rule:** Expose observability via passive append-only `events.jsonl` + read-only `tail` streams for external dashboards (`aim-office`), and wire turn-boundary hooks passively (`check --inject`) without wrapping interactive CLI processes.
3. **No Heavy Startup MCP Tool Bloat (`mcp_agent_mail`):**
   - **Pitfall:** Loading 30+ MCP tool schemas into every session consumes thousands of context tokens at startup just to read or send mail.
   - **Rule:** Keep the primary surface as a ~50-token self-describing CLI (`aim-mail --help` / `describe --json`) with `0` runtime pip dependencies.
