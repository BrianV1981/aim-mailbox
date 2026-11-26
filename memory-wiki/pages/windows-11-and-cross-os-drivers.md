# Windows 11, WSL2 & POSIX Harness Driver Matrix

## Overview
While [Maildir & Dispatcher Architecture](maildir-and-dispatcher-architecture.md) provides a single shared envelope store across operating systems, each AI coding harness requires a tailored **last-mile wake driver** to target an exact `session_id` safely. Across Windows 11, WSL2, and Linux, harnesses divide into **API/CLI-open surfaces** (100% deterministic Python, zero screen clicking) and **locked GUI surfaces** (served by the optional Lars vision fallback).

---

## 1. Driver Capability Matrix

| Harness Surface | Driver ID | OS Support | Session Targeting | Needs GUI / Vision? |
| :--- | :--- | :--- | :--- | :--- |
| **Antigravity Desktop / IDE** | `agy-desktop-sidecar` | Win11, macOS, Linux | Auto-resolved latest top-level `conversation_id` for `--cwd` via `conversation_summaries.db` (or explicit `--session-id`) + Sidecar `agentapi send-message` | **No** *(works minimized)* |
| **Antigravity CLI (`agy`)** | `agy-cli` | Win11, WSL2, macOS, Linux | Auto-resolved latest top-level `conversation_id` for `--cwd` (or explicit `--session-id`) via `agy --conversation <uuid> -p` | **No** (`0` idle RAM) |
| **OpenCode Desktop + CLI** | `opencode-http` | Win11, WSL2, macOS, Linux | `POST http://127.0.0.1:4096/session/<id>/message` | **No** |
| **VS Code (`v1.145+`)** | `vscode-chat` | Win11, macOS, Linux | `code chat -m agent -r` | **No** |
| **Grok Build (`grok`)** | `grok-cli` | Win11, WSL2, macOS, Linux | Named ID in `~/.grok/sessions` via `grok --resume <id> -p` | **No** (`0` idle RAM) |
| **Codex CLI (`codex`)** | `codex-cli` | Win11, WSL2, macOS, Linux | `codex exec resume <id> <prompt>` (or `codex exec <prompt>`) | **No** |
| **Live Interactive TUI (`tmux`)** | `tmux` | WSL2, Linux, macOS | `tmux` session name + 1-line `paste-buffer` + `Enter` | **No** |
| **Closed Desktop GUIs / Custom Hooks** (`Cursor`, `Windsurf`, `Codex` Store GUI) | `command` | Win11, Linux Desktop | Custom JSON argv template (`--command-json`, e.g., Lars + Win32 UIA) | **Yes** *(optional)* |
| **Passive Inbox** | `none` | Win11, WSL2, macOS, Linux | Manual `inbox`/`read` or `check --inject` turn hooks | **No** |

---

## 2. Harness-Specific Engineering Mechanics

### A. Antigravity Desktop / IDE (`agy-desktop-sidecar`) — *Empirically Proven Live (2026-11-18)*
- **Storage Split:** Antigravity Desktop (`%USERPROFILE%\.gemini\antigravity`) and Antigravity CLI (`%USERPROFILE%\.gemini\antigravity-cli`) maintain isolated databases; CLI `-p` flags cannot target Desktop sessions.
- **Official Sidecar Mechanism:** Antigravity Desktop natively hosts background scripts configured at `%USERPROFILE%\.gemini\config\sidecars\<sidecar-id>\sidecar.json` and enabled in `%USERPROFILE%\.gemini\config\config.json` (`"sidecars": {"<sidecar-id>": {"enabled": true, "schemaVersion": 1}}`), writing logs to `%USERPROFILE%\.gemini\antigravity\sidecar_data\<sidecar-id>\logs\`.
- **Authenticated `agentapi` Injection:** Antigravity automatically injects `ANTIGRAVITY_LS_ADDRESS` and `agentapi` into managed Sidecar child processes (external shells receive `{"error": "ANTIGRAVITY_LS_ADDRESS is not set"}`).
- **Windows `CreateProcess` Safety Rule:** On Windows 11, invoke `[language_server_exe, "agentapi", "send-message", recipient_id, content]` with `shell=False` rather than wrapping `agentapi.bat %*` in `cmd.exe`, preventing batch metacharacter splitting on quotes, `&`, `|`, or `!`.
- **Verified Live Behavior:** Hot-loads within ~10 seconds of enabling in `config.json` with **zero app restart**, auto-resolves the latest top-level (`nesting_depth = 0`, `killed = 0`) `conversation_id` matching `--cwd` (`workspace_uris`) across `/aim-handoff` session rotations (`v0.2.1`), injects messages as `MESSAGE_PRIORITY_HIGH` even while `Antigravity.exe` is minimized (`Rect = -31992,-32000`), and respects Antigravity's native `MESSAGE_DELIVERY_STRATEGY_NEXT_INVOCATION` queue when the target session is mid-turn. Note: after pulling new `aim-mailbox` Python code on Windows, terminate the running `sidecar_runner.py` PID (`restart_policy: on-failure` respawns it immediately with updated modules).
- **Shared Cross-OS Watcher Trap (`v0.2.2` Target):** When Windows 11 Sidecar `watch` shares `%USERPROFILE%\.aim\mailbox` with WSL2 `driver=tmux` agents, `pulse_mailbox()` must skip POSIX-only drivers (`tmux` without Windows binary, or `/...` POSIX `cwd` paths on `nt`) *before* claiming unread files—otherwise Windows `watch` hits `[WinError 2]` (`tmux` missing) and quarantines WSL2 mail to `dead/`.

### B. Antigravity CLI (`agy-cli` on Native Windows 11, WSL2 & Linux)
- **Native Windows Support:** Official Windows 10/11 (`x64`/`ARM64`) CLI (`irm https://antigravity.google/cli/install.ps1 | iex`) plus native `win_amd64` wheels for the `google-antigravity` Python SDK (`pip install google-antigravity`). Note: `sidecar.json` lifecycle management is Desktop/IDE-only; `agy` CLI uses OS schedulers (`cron` / `schtasks`) + headless resume.
- **Pinning & Verifying the Exact Session:**
  1. First headless turn (`agy --output-format json -p "..."`) returns `"conversation_id": "<uuid>"` in stdout JSON; persist this UUID in `agents.json`.
  2. Existing workspace sessions map via `cache/last_conversations.json` or read-only SQLite queries against `conversation_summaries.db` (`workspace_uris`, `title`).
  3. Pre-flight idle check verifies `status == 'CASCADE_RUN_STATUS_IDLE'` and `not_fully_idle == 0` in `conversation_summaries.db` (and absence of an active interactive lock in `presence/<uuid>.lock`).
- **Execution Command:**
  ```powershell
  agy.exe --conversation <uuid> --output-format json -p "You have unread mail. Run aim-mail inbox --state active, complete tasks, and ack."
  ```
  Boots the exact historical session in `cwd`, executes tool calls, appends the turn to `conversations/<uuid>.db`, prints a JSON receipt, and exits cleanly (`0` idle RAM). Permission skipping (`--dangerously-skip-permissions`) is **disabled by default** and opt-in per registered agent (`--allow-skip-permissions` / `--dangerously-skip-permissions`). Never hold the same `<uuid>` open simultaneously in an interactive terminal window.
- **WSL2 → Windows Subprocess Trap:** Whenever invoking `powershell.exe` or Windows Python from WSL2, always close stdin (`< /dev/null` and `-NoLogo -NonInteractive -NoProfile`) so PowerShell never hangs waiting on open stdin.

### C. OpenCode Desktop + CLI (`opencode-http`)
- OpenCode's Desktop app and TUI share a local background server (`opencode serve --port 4096 --hostname 127.0.0.1`, optional basic auth via `OPENCODE_SERVER_USERNAME` / `OPENCODE_SERVER_PASSWORD`).
- Create sessions via `POST /session` and inject prompts into an exact live session via `POST http://127.0.0.1:4096/session/<session_id>/message` — updating both GUI and CLI clients in real time.

### D. VS Code `v1.145+` (`vscode-chat`)
- Verified on Windows 11 (`Microsoft VS Code\bin\code.cmd`):
  - `code.exe chat -m agent -r "<prompt>"` (`--reuse-window`) routes a prompt directly into Agent Mode inside an active window.
  - `code-tunnel.exe agent` (`host`, `ps`, `endpoints`, `logs`, `stop`) publishes live machine-readable JSON agent-host endpoints under `<user-data-dir>/agent-host/local-endpoint/entries/`.

### E. Grok Build (`grok-cli` — `xAI.GrokBuild`)
- Installs natively on Windows 11 (`winget install xAI.GrokBuild` or `irm https://x.ai/cli/install.ps1 | iex`) and POSIX (`install.sh`), storing named sessions under `%USERPROFILE%\.grok\sessions` (`~/.grok/sessions`).
- Headless stateful execution:
  ```powershell
  grok --resume <session-id> --cwd <project_dir> --no-auto-update --output-format json -p "Check aim-mail inbox"
  ```

### F. OpenAI Codex (`codex-cli` vs. Windows Store GUI)
- **Headless / Server (`codex app-server --listen stdio://`):** Full JSON-RPC 2.0 control (`thread/resume` + `turn/start` targeting an exact `threadId`).
- **Windows Store Desktop GUI (`Codex.app`):** Retains an exclusive writer lock on open GUI threads (`"thread <id> already has an active writer"`), blocking external RPC attachment while open in the GUI sidebar.

### G. Cursor, Windsurf & The Optional `command` (Lars GUI Vision) Fallback
- **Cursor Headless CLI:** `cursor-agent -p "<prompt>" --workspace <path> --output-format json` (`irm 'https://cursor.com/install?win32=true' | iex`) supports headless runs.
- **Closed Desktop GUI Panels (`Cursor` Composer, `Windsurf` Cascade, `Codex` Store GUI):** Expose no external CLI/URI/HTTP endpoint to inject into an already-open sidebar chat. For persistent visible GUI sessions on these editors, the optional **Lars local vision + Win32 UIA driver** enforces a 4-gate safety sequence:
  1. Tag mail-enabled GUI chats with a unique sidebar prefix (`[MAIL:<agent>]`).
  2. Focus the app window and verify visually that no streaming/`Stop` indicator is active.
  3. Click `[MAIL:<agent>]` in the sidebar and **verify the active canvas header matches `[MAIL:<agent>]` before pasting** (abort on mismatch).
  4. Paste only a 4-word clipboard trigger (`Run: aim-mail inbox`), press `Enter`, and visually verify turn start while real payloads stay in Maildir JSON (see [Unified Skill Strategy (`aim-communicate` v2)](aim-communicate-v2-migration.md)).
