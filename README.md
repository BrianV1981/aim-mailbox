# aim-mailbox (`aim-mail`)

**Zero-daemon, zero-LLM local Maildir post office, priority wake router, and cross-OS turn-hook installer for AI coding agents.**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)
[![Dependencies: 0](https://img.shields.io/badge/Runtime%20Deps-0%20(pure%20stdlib)-brightgreen.svg)](pyproject.toml)
[![Platforms: Win11 | WSL2 | Linux | macOS](https://img.shields.io/badge/Platforms-Win11%20%7C%20WSL2%20%7C%20Linux%20%7C%20macOS-blue.svg)](#sharing-one-mailbox-between-native-windows-11-and-wsl2)
[![A.I.M. Ecosystem](https://img.shields.io/badge/A.I.M.-Ecosystem-00d4ff.svg)](https://github.com/BrianV1981/aim-ecosystem)

`aim-mailbox` (`v0.2.2`) lets autonomous AI coding agents communicate asynchronously across **Linux, WSL2, macOS, and Native Windows 11** and across heterogeneous harnesses (**Antigravity Desktop/IDE, Antigravity CLI, OpenCode, Grok Build, Codex CLI, VS Code Agent Mode, `tmux`, and custom GUI/CLI tools**) using **pure Python 3.10+ stdlib (`0` pip runtime dependencies, `0` resident database daemons, and `0` idle LLM tokens)**.

---

## Why `aim-mailbox` Exists

Multi-agent coding workflows break down at three physical boundaries that most terminal-only or cloud-only orchestrators ignore:

1. **The `tmux` Wall (No Native Windows 11 Support):** Traditional agent relays paste keystrokes into POSIX `tmux` panes. Because `tmux` requires Unix PTYs and `fork()`, native Windows 11 PowerShell (`pwsh`) and `cmd.exe` hosts cannot participate without forcing every agent inside WSL2.
2. **The One-Way GUI Diode:** Desktop IDE/Electron apps (Antigravity Desktop/IDE, VS Code, Cursor) can run outbound shell commands (`aim-mail send`), but external terminal scripts cannot `tmux paste-buffer` inbound into a graphical chat window. Reaching a minimized or background GUI session requires deterministic harness-native sidecars/HTTP APIs (`agentapi`, `opencode serve`, `code chat`).
3. **The Windows 11 NTFS ↔ WSL2 `/mnt/c` (`9P`) SQLite Trap:** Lightweight SQLite-based mail CLIs rely on WAL (`-wal`/`-shm`) shared-memory locking, which throws `sqlite3.OperationalError: database is locked` or corrupts state when native Windows 11 processes and WSL2 Linux processes access the same shared folder across the `9P` filesystem bridge.

`aim-mailbox` solves all three by decoupling **lock-free atomic Maildir storage** (Layer 1) from **pluggable last-mile wake dispatchers and `<2ms` turn-boundary hooks** (Layer 2).

---

## Comparison: Open-Source & Paid Alternatives vs. `aim-mailbox`

### Landscape Matrix

| Tool / Platform | License / Pricing | Storage & Runtime | Wake / Delivery Mechanism | Native Win11 + WSL2 `9P` Safe? | Cross-Harness? | Idle Overhead | Key Tradeoff vs. `aim-mailbox` |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`aim-mailbox` (`aim-mail`)** | **MIT (Free, OSS)** | **Pure-stdlib atomic JSON Maildir (`0` deps, `0` daemons)** | **9 deterministic wake drivers + `<2ms` `check --inject` hooks** | **Yes** (`os.replace` + NTFS retries) | **Yes** (Antigravity, OpenCode, Grok, Codex, VS Code, `tmux`, custom) | **`0` idle RAM, `0` idle tokens** | Built specifically for zero-daemon local multi-OS / cross-harness fleets |
| **`@juanjofuchs/agent-mail`** / `jwcraig/agent-mail-cli` | OSS (MIT) | SQLite (`mail.db`) + Node/CLI | Passive only (`watch` blocks already-running caller) | **No** (SQLite WAL fails over `9P`) | Partial (CLI callers only) | Low | Cannot wake sleeping Desktop/CLI sessions; no `max_hops` or approval gates |
| **`mcp_agent_mail`** / `_rust` | **Non-OSI** (MIT + vendor rider) | SQLite + Git + FastMCP/Rust server | Active client turn polling + file-lease conflict errors | **No** (SQLite WAL + server) | MCP clients only | Heavy (server + 16-screen TUI + ~30 MCP tools) | Startup MCP tool bloat burns context tokens; non-OSI license rider |
| **`AgentWorkforce/relay`** (`agent-relay`) | Apache-2.0 | Resident daemon (`relay.sock`) + SQLite + PTY wrapper | Wraps interactive CLIs inside managed PTY/`tmux` | **No** (POSIX socket + PTY) | CLI/PTY only | Resident daemon + PTY wrappers | Forces agents inside PTY proxy; cannot wake minimized Windows Desktop GUIs |
| **`aannoo/hcom`** (`hook-comms`) | MIT | SQLite (`hcom.db`) + PTY wrapper + hooks | PTY/hook supervisor injects turn wakeups | **No** (SQLite + PTY wrapper) | Hooked CLIs | PTY supervisor + SQLite | Requires PTY wrapping & SQLite; no Windows Desktop Sidecar wake |
| **Gas Town Mail (`gt mail`)** | OSS | Beads / workspace state + POSIX polecat daemons | Turn `--inject` hooks + background orchestrator | **No** (POSIX-centric) | Tied to Gas Town | Background daemons | Monolithic orchestrator; not a thin cross-OS standalone post office |
| **`amux` / `amux Cloud`** | Source-avail (MIT + Commons Clause) / **$39–$129/mo** + API keys | Server + `tmux 3.2+` sessions (local Mac/Linux or cloud VM) | Server pushes into managed `tmux` sessions | **No** (macOS/Linux `tmux` only) | Terminal agents in `tmux` | Resident server / cloud VM | Commercial resale restriction; no native Windows 11 or Desktop GUI sidecars |
| **Claude Code Agent Teams** (`TeammateTool`) | Paid Claude plan (**$20–$200/mo** or API billing) | Anthropic harness-internal task/team state | Spawns parallel Claude teammate context windows | CLI-bound | **No** (Claude Code only) | **~7x token multiplier** across parallel teammates | Single-vendor lock-in; cannot message Codex, Grok, Antigravity, or OpenCode |
| **Cursor Cloud / Background Agents** | Paid subscription (**$30–$200+/mo** + credits) | Cloud VMs managed by Cursor | Asynchronous cloud VM agent runs | Cloud-isolated | **No** (Cursor only) | Paid cloud compute & credit pool | Locked to Cursor SaaS; cannot coordinate heterogeneous local tools on your repo |
| **Cognition Devin** | Paid (**$20–$500/mo** or Enterprise ACUs) | Proprietary cloud sandbox | Autonomous cloud SWE sessions | Cloud-isolated | **No** (Devin only) | Metered ACU / subscription cost | Closed SaaS platform, not a local inter-agent message bus |
| **OpenAI Codex Cloud / Agents API** | Paid OpenAI plan / API usage | Managed OpenAI microVMs | Cloud task execution + API thread state | Cloud-isolated | **No** (Codex ecosystem) | Cloud/API billing | Single-vendor cloud harness rather than local multi-harness router |
| **Conductor** *(Melty Labs)* | Free local app (acquired by Meta; Cloud wound down) | Local macOS app + git worktrees | Native macOS UI thread orchestration | **No** (**macOS only**) | Selected local CLIs | Desktop Electron/macOS app | No Windows 11, WSL2, or Linux support; UI-coupled rather than CLI post office |

---

### Our Moat: 6 Structural Advantages

1. **Zero-Daemon, Cross-Boundary Atomic Storage (`Windows 11 NTFS ↔ WSL2 9P`):** Every message is an immutable-stage JSON file written to `tmp/` and atomically moved via `os.replace()` with NTFS `PermissionError`/`EBUSY` retries. Windows 11 native tools and WSL2 Linux processes share one `~/.aim/mailbox` (`AIM_MAILBOX_DIR`) concurrently with zero SQLite `database is locked` failures.
2. **True Sleeping-Agent Wake-Up Without PTY Wrapping:** Rather than trapping interactive CLIs inside a fragile PTY proxy (`relay`, `hcom`) or restricting you to POSIX `tmux` (`amux`), `aim-mailbox` wakes **9 surfaces natively**—including minimized Windows 11 Antigravity Desktop windows via Sidecar `agentapi`, headless stateful CLI resumes (`agy --conversation`, `grok --resume`, `codex exec resume`), OpenCode HTTP sessions, VS Code Agent Mode, and `Enter`-only `tmux` doorbells.
3. **`<2ms` Empty-Inbox Turn Hooks + `0` Idle Overhead:** `aim-mail check <agent> --inject --quiet` short-circuits via a pure-shell / zero-import fast-path in `<2ms` on POSIX (`<10ms` in pure Python `-S`) when `unread/` is empty—spending **zero RAM** and **zero LLM tokens** between turns.
4. **Priority Lanes & `coalesce_key` Noise Suppression (`v0.2.0`):** Emergency `urgent` stops wake ahead of `normal` traffic, duplicate status updates sharing `--coalesce-key` collapse cleanly into one wake batch (retiring superseded `ack_required=True` envelopes to `done/` with `coalesced_into` only after `DELIVERED`), and `low` traffic batches inside bounded `--low-batch-window` windows.
5. **Loop, Security & Cost Guardrails Built Into the Store:** Enforces `max_hops` thread propagation (quarantining loops to `dead/` *before* waking any LLM), fail-closed initial `--operator-approved` gating (`rc=2`), `0o700`/`0o600` permissions, strict `../` path-traversal rejection, and `defer=True` busy handling so active sessions never burn retry attempts.
6. **Zero Dependencies & ~50-Token Self-Describing Surface:** `0` runtime pip dependencies, `<= 15` CLI subcommands (`aim-mail describe` / `--help`), and MIT licensing—drop it into any repo via zero-install launchers (`bin/aim-mail`, `bin/aim-mail.cmd`, `bin/aim-mail.ps1`) with no vendor lock-in or MCP context bloat.

---

## Architecture & Maildir Lifecycle

```text
~/.aim/mailbox/                 # Or %USERPROFILE%\.aim\mailbox via AIM_MAILBOX_DIR
├── agents.json                 # Locked RMW address book: agent_id -> driver, cwd, session_id
├── locks/
│   ├── _agents.lock.json       # Atomic O_CREAT|O_EXCL lock for agents.json updates
│   └── <agent_id>.lock.json    # Per-agent dispatch lease lock (PID + TTL stale recovery)
└── boxes/
    └── <agent_id>/
        ├── tmp/                # 0600 staged temp file + fsync before atomic os.replace()
        ├── unread/             # Delivered envelopes awaiting wake/read/check
        ├── claimed/            # Leased to an active wake or `read --claim`
        ├── done/               # ACKed, replied-to, or coalesced-retired envelopes + receipts
        └── dead/               # Quarantined envelopes (max_hops exceeded or max_attempts failed)
```

### Envelope Schema (`v0.2.0`)

```json
{
  "id": "20261124T210000000000Z_a1b2c3d4",
  "thread_id": "thr_9f8e7d6c5b4a",
  "in_reply_to": null,
  "from": "aim-joshua",
  "to": "copy-cat",
  "reply_to": "aim-joshua",
  "subject": "Schema check",
  "body": "Validate ep02 manifest structure; see attached ref.",
  "refs": ["specs/ep02.json"],
  "priority": "normal",
  "coalesce_key": "ep02:schema",
  "coalesced_ids": [],
  "hop_count": 0,
  "max_hops": 1,
  "ack_required": true,
  "operator_approved": true,
  "attempts": 0,
  "max_attempts": 3,
  "created_at": "2026-11-24T21:00:00.000000Z"
}
```

### Reliability & Security Invariants

- **Atomic Moves & NTFS Retry:** `write_json_atomic()` writes with `O_CREAT | O_EXCL` (`0o600`), calls `os.fsync()`, and swaps via `_replace_with_retry()` (6 attempts with backoff on `PermissionError` / `EACCES` / `EBUSY` for Windows Defender/indexer delete-pending locks).
- **Path & Input Validation:** Agent IDs normalize to lowercase (`@Worker` → `worker`), disallow `..`, trailing `.`, path separators, and Windows reserved device names (`CON`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`). Message IDs, session IDs, HTTP URLs (`http://`/`https://` only), and `--archive-dir` targets are strictly confined.
- **Busy Defer vs. Failure Quarantine:** If a target session is mid-turn (`BUSY_DEFERRED`), `release_claim(..., defer=True)` returns the envelope to `unread/` **without** decrementing `max_attempts`. Actual crashes/timeouts increment `attempts` and quarantine to `dead/` once `attempts >= max_attempts`.
- **Cross-OS Watcher Host-Affinity Isolation (`v0.2.2`):** When Windows 11 Sidecar `watch` and WSL2/POSIX `pulse` share one `agents.json`, `pulse_mailbox()` skips opposite-OS drivers (`tmux` / POSIX `cwd` on Windows; `agy-desktop-sidecar` without `ANTIGRAVITY_LS_ADDRESS` or Windows `<drive>:\` `cwd` on POSIX) *before* acquiring agent locks or claiming unread files—never burning `attempts` or quarantining cross-OS mail to `dead/`.
- **Fail-Closed Wake Policy:** `send --wake` and `reply --wake` exit `2` if the target is unregistered, passive (`driver=none`), missing `--operator-approved` (unless the target agent has `--allow-unapproved`), or deferred/remote-skipped (`BUSY_DEFERRED`, `AGENT_LOCK_HELD`, `LOW_PRIORITY_BATCH_WINDOW`, `HOST_MISMATCH`, `SIDECAR_CONTEXT_REQUIRED`) without `--allow-deferred`.

---

## Installation

### Option A: `pip` / `pipx` (Editable or Wheel)
```bash
pip install -e .
aim-mail --version
```

### Option B: Zero-Install Shims (Clone & Run Immediately)
- **Linux / macOS / WSL2:** `./bin/aim-mail --help` *(symlink-safe for `~/.local/bin/aim-mail`)*
- **Windows CMD:** `.\bin\aim-mail.cmd --help`
- **Windows PowerShell:** `.\bin\aim-mail.ps1 --help`

### Sharing One Mailbox Between Native Windows 11 and WSL2
Point WSL2 at your Windows user mailbox directory so native Windows apps and WSL2 CLI agents read and write the exact same Maildir:
```bash
# Inside WSL2 (~/.bashrc)
export AIM_MAILBOX_DIR="/mnt/c/Users/<YourWindowsUser>/.aim/mailbox"
```

---

## Quickstart & `v0.2.0` Workflows

### 1. Core Messaging & Wake Loop
```bash
# Register logical agents by project folder (zero session-id updates needed across handoffs!)
aim-mail register aim-auditor --driver agy-cli --cwd ~/aim-auditor
aim-mail register copy-cat --driver agy-desktop-sidecar --cwd "C:\Projects\copy-cat"
aim-mail register backend-dev --driver opencode-http --http-url http://127.0.0.1:4096

# Send an operator-approved message with a file ref and immediate wake
aim-mail send \
  --from aim-joshua \
  --to copy-cat \
  --subject "Schema check" \
  --body "Please validate ep02 manifest structure." \
  --ref specs/ep02.json \
  --operator-approved \
  --wake

# Recipient reads, replies (auto-incrementing hop_count & ACKing parent), or ACKs
aim-mail inbox copy-cat --state active
aim-mail read copy-cat --claim
aim-mail reply copy-cat <msg_id> --body "Verified: schema passes validation."

# Or run a single-pass / continuous zero-LLM dispatcher sweep
aim-mail pulse
aim-mail watch --interval 1.5
```

### 2. Priority Lanes & `--coalesce-key` Batching (`v0.2.0`)
```bash
# Urgent alerts jump ahead of normal/low mail (still requires --operator-approved & obeys max_hops)
aim-mail send --from lead --to worker --body "STOP: rollback migration" --priority urgent --operator-approved --wake

# Repeated CI/status updates with the same --coalesce-key collapse into one wake batch
aim-mail send --from ci --to worker --body "Build #41 green" --coalesce-key ci:main --operator-approved
aim-mail send --from ci --to worker --body "Build #42 green" --coalesce-key ci:main --operator-approved
aim-mail pulse --agent worker
# -> Wakes once with Build #42; Build #41 is cleanly retired to done/ with coalesced_into=<Build #42 id>

# Low-priority mail defers inside --low-batch-window (default 30s) until the oldest waits >= window
aim-mail send --from metrics --to worker --body "Nightly digest" --priority low --operator-approved --wake --allow-deferred
aim-mail pulse --low-batch-window 30
```

### 3. Inbox Filtering & Maildir Retention `gc` (`v0.2.0`)
```bash
# Pure-stdlib envelope queries without SQLite/FTS5
aim-mail inbox worker --state all --from aim-joshua --priority urgent --query "migration" --since 2026-11-24T00:00:00Z --limit 10 --json

# Preview retention cleanup (ages done/ by acked_at, dead/ by quarantined_at; never touches unread/ or claimed/)
aim-mail gc --done-days 14 --dead-days 30 --archive-dir ~/.aim/mailbox-archive --dry-run --json

# Execute atomic archive + unlink
aim-mail gc --done-days 14 --dead-days 30 --archive-dir ~/.aim/mailbox-archive
```

### 4. One-Command Turn-Boundary Hooks (`v0.2.0`)
Install non-destructive turn-boundary hooks calling `aim-mail check <agent> --inject --quiet` (`<2ms` empty-inbox overhead via `bin/aim-mail`):
```bash
# Antigravity (writes "aim-mailbox" into ~/.gemini/config/hooks.json or <workspace>/.agents/hooks.json)
aim-mail install-hooks --harness agy --agent aim-auditor

# Codex CLI (non-destructively merges into ~/.codex/hooks.json Stop array)
aim-mail install-hooks --harness codex --agent codex-worker

# Grok Build (writes standalone ~/.grok/hooks/aim-mailbox.json)
aim-mail install-hooks --harness grok --agent grok-worker

# OpenCode / Generic (writes standalone shell/.cmd hook without mutating opencode.json)
aim-mail install-hooks --harness opencode --agent oc-worker
aim-mail install-hooks --harness generic --agent my-agent

# Preview or cleanly remove anytime
aim-mail install-hooks --harness agy --agent aim-auditor --dry-run --json
aim-mail install-hooks --harness agy --agent aim-auditor --uninstall
```

---

## Pluggable Wake Drivers

| Driver | Target Surface | Session Targeting & Wake Mechanics |
| :--- | :--- | :--- |
| `agy-desktop-sidecar` | **Antigravity Desktop / IDE** (Win11, macOS, Linux) | Runs inside an official Antigravity Sidecar (`aim-mail install-agy-sidecar`), auto-resolves the latest top-level `conversation_id` for `--cwd` from `conversation_summaries.db` across handoffs (or uses explicit `--session-id`), and invokes `agentapi send-message` — works even while Desktop is minimized. |
| `agy-cli` | **Antigravity CLI (`agy`)** (Win11, WSL2, macOS, Linux) | Auto-resolves the latest top-level `conversation_id` for `--cwd` (or uses explicit `--session-id`), checks read-only `conversation_summaries.db` (`CASCADE_RUN_STATUS_IDLE`) + `presence/<uuid>.lock`, and wakes `agy --conversation <uuid> --output-format json -p <prompt>` (`--dangerously-skip-permissions` is opt-in via `--allow-skip-permissions`). |
| `opencode-http` | **OpenCode Desktop + CLI** (`opencode serve`) | Sends `POST <http_url>/session/<id>/message` using OpenCode `parts` schema (auto-creating and persisting a session via `POST /session` if unset). |
| `grok-cli` | **Grok Build (`grok`)** (Win11, WSL2, macOS, Linux) | Wakes `grok --no-auto-update --output-format json [--resume <id>] [--cwd <dir>] -p <prompt>`. |
| `codex-cli` | **OpenAI Codex CLI** (`codex`) (Win11, WSL2, macOS, Linux) | Wakes `codex exec resume --json <id> <prompt>` (or `codex exec --json --add-dir <mailbox> <prompt>` on first run, auto-saving `thread_id`). |
| `vscode-chat` | **VS Code (`v1.145+`)** (Win11, macOS, Linux) | Injects into Agent Mode via `code chat -m agent -r <prompt>`. |
| `tmux` | **Live Interactive TUI** (WSL2, Linux, macOS) | Uses exact `=<session>` / `=<session>:` targeting, pastes a 1-line doorbell via `set-buffer --` + `paste-buffer`, submits with **`Enter` only** (never `Escape`), and verifies delivery via `capture-pane`. |
| `command` | **Custom Script / GUI Vision Fallback** | Runs a custom JSON argv template (`{agent}`, `{message_id}`, `{message_ids}`, `{session_id}`, `{mailbox_dir}`, `{prompt}` + `AIM_MAIL_*` env vars) for tools such as local GUI vision automation. |
| `none` | **Passive Inbox** | Stores envelopes without automated wake-up (manual `inbox`/`read` or `check --inject` turn hooks). |

### Enabling the Antigravity Desktop Sidecar
Antigravity Desktop hot-loads sidecars registered in `~/.gemini/config/config.json` (`%USERPROFILE%\.gemini\config\config.json`) without restarting the app:
```powershell
aim-mail install-agy-sidecar
# Remove anytime with:
aim-mail install-agy-sidecar --uninstall
```

---

## CLI Reference (15 Subcommands)

| Command | Purpose |
| :--- | :--- |
| `aim-mail register <agent> [...]` | Register, update, or `--unregister` an agent, wake driver, `--allow-unapproved`, and `--allow-skip-permissions` |
| `aim-mail agents [--json]` | List registered logical agents and configured targets |
| `aim-mail send --from <a1> --to <a2> --body "..."` | Deliver JSON envelope (`--priority urgent\|normal\|low`, `--coalesce-key`, `--ref`, `--thread-id`, `--operator-approved`, `--wake`, `--allow-deferred`) |
| `aim-mail inbox <agent> [--state ...]` | List/filter envelopes (`--state unread\|claimed\|done\|dead\|active\|all`, `--from`, `--thread-id`, `--priority`, `--query`, `--since`, `--limit`) |
| `aim-mail read <agent> [id] [--claim] [--ack]` | Read a specific or oldest unread/claimed envelope |
| `aim-mail reply <agent> <id> --body "..."` | Reply to `reply_to`, increment `hop_count`, auto-ACK parent (`--priority`, `--coalesce-key`, `--ack-required`, `--wake`) |
| `aim-mail ack <agent> <id> [--note "..."]` | Move envelope to `done/` with completion timestamp |
| `aim-mail check <agent> [--inject] [--quiet]` | `<2ms` (`<15ms` Python) unread check (`--format text\|agy-inject\|agy-stop`, `--json`) |
| `aim-mail wait <agent> [--thread-id ...]` | Block at `0` LLM cost until matching mail arrives (`--claim`, `--timeout`, `--poll-interval`) |
| `aim-mail pulse [--agent ...] [--dry-run]` | Single-pass zero-LLM wake sweep (`--timeout`, `--low-batch-window`) |
| `aim-mail watch [--interval 1.5]` | Continuous zero-LLM Maildir watch loop (`--max-cycles`, `--timeout`, `--low-batch-window`) |
| `aim-mail gc [--done-days 14] [--dead-days 30]` | Prune or `--archive-dir` aged terminal envelopes in `done/` and `dead/` (`--agent`, `--dry-run`) |
| `aim-mail install-agy-sidecar` | Non-destructively install or `--uninstall` the Antigravity Desktop Sidecar |
| `aim-mail install-hooks --harness <name>` | Non-destructively install/uninstall turn-boundary hooks (`agy`, `codex`, `grok`, `opencode`, `generic`) |
| `aim-mail describe [--json]` | Output compact JSON schema of commands, drivers, harnesses, states, priorities, and env vars |

### Environment Variables
- `AIM_MAILBOX_DIR` — Override default `~/.aim/mailbox` root directory (useful for sharing `%USERPROFILE%\.aim\mailbox` across Windows 11 and WSL2).
- `OPENCODE_SERVER_USERNAME` / `OPENCODE_SERVER_PASSWORD` — Optional HTTP Basic Auth credentials for `opencode-http`.

---

## Testing & Explicit Non-Goals

Run the full unit + unmocked CLI E2E suite (tested continuously on `ubuntu-latest` + `windows-latest` CI and scanned by `gitleaks`):
```bash
python3 -m pytest tests/ -v -W error::DeprecationWarning
```

### Architectural Non-Goals (Intentional Guardrails)
- **No SQLite or FTS5 storage:** Avoids cross-OS `9P` / NTFS WAL locking failures.
- **No resident socket daemons or PTY proxies:** Preserves `0` idle RAM and native Windows Desktop compatibility.
- **No startup MCP schema bloat or subcommand sprawl:** Keeps the CLI surface `<= 15` self-describing commands with `0` runtime pip dependencies.
- **No path-glob advisory file leases:** Isolated `git worktree` sandboxes prevent concurrent edit collisions without brittle cross-OS symlink/glob matching.

---

## Ecosystem

Part of the open-source **[A.I.M. Ecosystem](https://github.com/BrianV1981/aim-ecosystem)**.

## License

Released under the [MIT License](LICENSE).
