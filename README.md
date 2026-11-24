# aim-mailbox (`aim-mail`)

**Zero-daemon, zero-LLM local Maildir post office and cross-OS wake dispatcher for AI coding agents.**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)
[![A.I.M. Ecosystem](https://img.shields.io/badge/A.I.M.-Ecosystem-00d4ff.svg)](https://github.com/BrianV1981/aim-ecosystem)

`aim-mailbox` (`v0.1.1`) lets local AI coding agents communicate asynchronously across **Linux, WSL2, macOS, and Native Windows 11** and across different harnesses (**Antigravity Desktop/IDE, Antigravity CLI, OpenCode, Grok Build, Codex, VS Code, and `tmux`**) using **pure Python 3.10+ stdlib (`0` pip runtime dependencies, `0` background database daemons, and `0` idle LLM tokens)**.

---

## Why `aim-mailbox` Exists

Traditional terminal agent relays either rely on POSIX-only `tmux` buffer pasting (which cannot reach **native Windows 11** or **Desktop/IDE GUI windows**) or force you to run heavy background socket servers and ~30-tool MCP schemas. Meanwhile, lightweight SQLite-only mail CLIs lack wake dispatchers and suffer `database is locked` failures across the **Windows 11 NTFS ↔ WSL2 `/mnt/c` (`9P`)** boundary.

`aim-mailbox` separates **durable storage** from **last-mile wake-up**:
1. **Lock-Free Atomic Maildir Storage (`os.replace`):** Envelopes move atomically across `tmp/` → `unread/` → `claimed/` → `done/` (`dead/`), working identically across native Windows 11 PowerShell and WSL2 Linux on the same shared folder.
2. **Zero-Token Cron / Sidecar Dispatcher (`pulse` & `watch`):** Scans `unread/` in `<5ms` at `0` LLM cost, enforces `max_hops` loop protection and per-agent lease locks in pure Python, and wakes the recipient **only** when mail is waiting.
3. **Compact ~50-Token CLI Surface:** Any agent in any harness discovers the full tool via `aim-mail --help` or `aim-mail describe` without MCP context bloat.

---

## Installation

### Option A: `pip` / `pipx` (Recommended)
```bash
pip install -e .
aim-mail --version
```

### Option B: Zero-Install Shims (Clone & Run)
- **Linux / macOS / WSL2:** `./bin/aim-mail --help`
- **Windows PowerShell & CMD:** `.\bin\aim-mail.cmd --help` *(or `.\bin\aim-mail.ps1 --help` when PowerShell `ExecutionPolicy` permits scripts)*

### Sharing One Mailbox Between Native Windows 11 and WSL2
Point WSL2 at your Windows user mailbox directory so both environments read and write the same atomic Maildir:
```bash
# Inside WSL2 (~/.bashrc)
export AIM_MAILBOX_DIR="/mnt/c/Users/<YourWindowsUser>/.aim/mailbox"
```

---

## Quickstart

```bash
# 1. Register logical agents and their wake drivers
aim-mail register aim-auditor --driver agy-cli --cwd ~/aim-auditor
aim-mail register copy-cat --driver agy-desktop-sidecar --session-id <conversation-uuid>
aim-mail register backend-dev --driver opencode-http --http-url http://127.0.0.1:4096

# 2. Send a message (with optional file pointer and Operator approval)
aim-mail send \
  --from aim-joshua \
  --to copy-cat \
  --subject "Schema check" \
  --body "Please validate ep02 manifest structure." \
  --ref specs/ep02.json \
  --operator-approved

# 3. Recipient reads, replies (auto-incrementing hop_count & ACKing parent), or ACKs
aim-mail inbox copy-cat
aim-mail read copy-cat --claim
aim-mail reply copy-cat <msg_id> --body "Verified: schema passes validation."

# 4. Run single-pass wake sweep (from cron / Windows Task Scheduler)
aim-mail pulse
```

---

## Pluggable Wake Drivers (`v0.1.0`)

| Driver | Target Surface | Session Targeting & Wake Mechanics |
| :--- | :--- | :--- |
| `agy-desktop-sidecar` | **Antigravity Desktop / IDE** (Win11, macOS, Linux) | Runs inside an official Antigravity Sidecar (`aim-mail install-agy-sidecar`) and invokes `agentapi send-message <conversation_id> <prompt>` — works even while the Desktop app is minimized. |
| `agy-cli` | **Antigravity CLI (`agy`)** (Win11, WSL2, macOS, Linux) | Checks read-only `conversation_summaries.db` (`CASCADE_RUN_STATUS_IDLE`) + `presence/<uuid>.lock`, wakes `agy --conversation <uuid> --output-format json -p <prompt>` (permission skipping is disabled by default; opt-in via `--allow-skip-permissions`), and auto-saves first-run `conversation_id` to `agents.json`. |
| `opencode-http` | **OpenCode Desktop + CLI** (`opencode serve`) | Sends `POST http://127.0.0.1:4096/session/<id>/message` (auto-creating a session via `POST /session` if unset). |
| `grok-cli` | **Grok Build (`grok`)** (Win11, WSL2, macOS, Linux) | Wakes `grok --no-auto-update --output-format json [--resume <id>] [--cwd <dir>] -p <prompt>`. |
| `codex-cli` | **OpenAI Codex CLI** (Win11, WSL2, macOS, Linux) | Wakes `codex exec resume <id> <prompt>` (or `codex exec <prompt>` on initial run). |
| `vscode-chat` | **VS Code (`v1.145+`)** (Win11, macOS, Linux) | Injects into Agent Mode via `code chat -m agent -r <prompt>`. |
| `tmux` | **Live Interactive TUI** (WSL2, Linux, macOS) | Pastes a 1-line doorbell (`Run: aim-mail read <agent> <id>`) via `paste-buffer`, submits with **`Enter` only** (never `Escape`), and verifies delivery via `capture-pane`. |
| `command` | **Custom Script / GUI Vision Fallback** | Runs a custom JSON argv template (`{agent}`, `{message_id}`, `{session_id}`, `{mailbox_dir}`, `{prompt}` + `AIM_MAIL_*` env vars) for tools such as local GUI vision automation on closed desktop chat panels. |
| `none` | **Passive Inbox** | Stores envelopes without automated wake-up (manual `inbox`/`read` or `check --inject` turn hooks). |

### Enabling the Windows 11 Antigravity Desktop Sidecar
Antigravity Desktop hot-loads sidecars registered in `%USERPROFILE%\.gemini\config\config.json` without restarting the app:
```powershell
aim-mail install-agy-sidecar
# Remove anytime with:
aim-mail install-agy-sidecar --uninstall
```

---

## CLI Reference

| Command | Purpose |
| :--- | :--- |
| `aim-mail register <agent> [...]` | Register, update, or `--unregister` an agent and wake driver in `agents.json` |
| `aim-mail agents [--json]` | List registered logical agents and configured targets |
| `aim-mail send --from <a1> --to <a2> --body "..."` | Atomically deliver a JSON envelope to `boxes/<to>/unread/` (optional `--wake`) |
| `aim-mail inbox <agent> [--state ...]` | List envelopes (`unread`, `claimed`, `done`, `dead`, or `all`) |
| `aim-mail read <agent> [id] [--claim] [--ack]` | Read a specific or oldest unread envelope |
| `aim-mail reply <agent> <id> --body "..."` | Reply to `reply_to`, increment `hop_count`, and auto-ACK parent |
| `aim-mail ack <agent> <id> [--note "..."]` | Move envelope to `done/` with completion timestamp |
| `aim-mail check <agent> [--inject]` | `<5ms` unread check for client pre/post-turn hooks |
| `aim-mail wait <agent> [--thread-id ...]` | Block at `0` LLM cost until matching unread mail lands |
| `aim-mail pulse [--agent ...] [--dry-run]` | Single-pass zero-LLM wake dispatcher for `cron` / Task Scheduler |
| `aim-mail watch [--interval 1.5]` | Continuous zero-LLM Maildir watcher for Sidecars |
| `aim-mail install-agy-sidecar` | Non-destructively install or `--uninstall` the Antigravity Desktop Sidecar |
| `aim-mail describe` | Output compact JSON schema of commands, states, and drivers |

---

## Ecosystem

Part of the open-source **[A.I.M. Ecosystem](https://github.com/BrianV1981/aim-ecosystem)**.

## License

Released under the [MIT License](LICENSE).
