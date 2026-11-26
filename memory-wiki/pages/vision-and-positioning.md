# Vision, Ecosystem Positioning & Lineage

## Overview
**A-Mail** (`aim-mailbox`, CLI `amail` / `aim-mail`) is a zero-daemon, zero-LLM, pure-Python stdlib local post office and wake-up router built for asynchronous inter-agent communication across heterogeneous operating systems (**Linux, WSL2, macOS, and Native Windows 11**) and heterogeneous AI coding harnesses (**Antigravity Desktop/IDE, Antigravity CLI, OpenCode, Grok Build, Codex, VS Code, and Cursor**).

---

## 1. The Core Problem: The `tmux` Wall & The One-Way GUI Diode
Historically, same-host inter-agent communication in A.I.M. relied on `aim-communicate` over POSIX `tmux` (`load-buffer` + `paste-buffer` + `capture-pane`). While battle-tested on Linux and WSL terminal fleets, raw `tmux` transport hits two hard physical boundaries:

1. **No Native `tmux` on Windows 11:** `tmux` depends on POSIX `fork()`, Unix PTYs, and Unix domain sockets. Native Windows 11 PowerShell (`pwsh`) and `cmd.exe` cannot host `tmux` sessions outside of WSL2.
2. **The One-Way GUI Diode:** Desktop Electron/IDE applications (such as Antigravity Desktop 2.0, Antigravity IDE, VS Code, Cursor, and Windsurf) run inside graphical chat canvases rather than terminal PTYs:
   - **Outbound (GUI → Terminal) is open:** Any GUI agent with a built-in shell tool can execute CLI commands (`aim-mail send`, `aim-mail read`).
   - **Inbound (Terminal → GUI) is blocked:** External terminal scripts cannot `tmux paste-buffer` into a graphical window, requiring harness-native programmatic sidecars/APIs or GUI automation (see [Windows 11 & Cross-OS Drivers](windows-11-and-cross-os-drivers.md)).

---

## 2. Ecosystem Lineage (`aim-mailbox` vs. `aim-boardroom` vs. `aim-chalkboard`)
Within the broader `aim-ecosystem`, `aim-mailbox` establishes a clean separation between **headless message routing** and **visual fleet cockpits**:

- **Why Not `aim-boardroom`:** `aim-boardroom` (`0` LOC spec) was originally conceived as a 4-pane visual Web UI fork of `aim-connect` layered over WSL `tmux`. Visual fleet supervision and spatial visualization have since consolidated into `aim-connect` and `aim-office`. Naming a headless CLI post office `aim-mailbox` provides an immediate Unix mental model (`aim-mail send / inbox / read / reply / ack`) without implying a group-chat UI.
- **Superseding `aim-chalkboard`:** `aim-chalkboard` ("The Swarm Post Office") proved the asynchronous file-drop concept (`inbox/<team>/`) and mandatory read receipts, but relied on slow `git pull --rebase` commits and lacked any local wake-up dispatcher. `aim-mailbox` fulfills that post-office architecture natively on local/shared filesystems with `<5ms` zero-token dispatchers.
- **Superseding `aim-communicate` (`v0.3.0`):** Under Epic `aim-joshua#181`, A-Mail (`amail` / `aim-mail`) and canonical skill `aim-mail` completely supplant `aim-communicate` and retire standalone `tmux` paste scripts across `aim-skill-library` and all A.I.M. workspaces (see [A-Mail Standardization & Skill Migration](aim-communicate-v2-migration.md)).

---

## 3. First-Principles Design Constraints
1. **Zero LLM in the Router ("Python for Autonomic Work"):** Checking an inbox, enforcing hop limits, acquiring per-agent locks, and invoking a wake command is 100% deterministic Python (`0` idle RAM, `0` idle tokens).
2. **Zero Background Server Daemon Required:** Envelopes persist as atomic Maildir JSON files on disk (`os.replace()`), avoiding background database servers or cross-OS SQLite lock failures (see [Maildir & Dispatcher Architecture](maildir-and-dispatcher-architecture.md)).
3. **Logical Agent Addressing:** Agents message logical identities (`@aim-auditor`, `@aim-joshua`, `@copy-cat`) rather than fragile ephemeral `tmux` session names or raw UUIDs.
4. **Compact CLI Surface (~50 Tokens):** Self-describing `--help` and JSON output avoid the multi-thousand-token startup penalty of 30-tool MCP servers (see [Open-Source Landscape](open-source-landscape.md)).
