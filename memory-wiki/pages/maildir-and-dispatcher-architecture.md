# Lock-Free Maildir Storage & Zero-LLM Dispatcher

## Overview
`aim-mailbox` strictly decouples **Layer 1 (Durable Envelope Storage)** from **Layer 2 (Pluggable Last-Mile Wake Dispatch)**. Envelopes live in a lock-free, Maildir-inspired directory hierarchy so native Windows 11 processes and WSL2/Linux processes can read and write the exact same mailbox simultaneously without a background server.

---

## 1. Why Atomic Maildir JSON Beats Cross-OS SQLite
When native Windows 11 harnesses (`Antigravity.exe`, `agy.exe`, `grok.exe`, `code.exe`) and WSL2 Linux harnesses (`agy`, `grok`, `opencode`, `codex`) share a mailbox across `%USERPROFILE%\.aim\mailbox` (`/mnt/c/Users/<user>/.aim/mailbox` in WSL2), **SQLite WAL file locks break across the WSL2 `9P` filesystem bridge**, causing `sqlite3.OperationalError: database is locked` stalls or corruption under concurrent access.

Using a **Maildir JSON layout** eliminates shared file locks completely:
```text
~/.aim/mailbox/                 # (or %USERPROFILE%\.aim\mailbox shared via AIM_MAILBOX_DIR)
├── agents.json                 # Logical agent address book & driver config
├── locks/
│   └── <agent_id>.lock.json    # Per-agent dispatch lease with PID + TTL expiry
└── boxes/
    └── <agent_id>/
        ├── tmp/                # Staging write target before atomic rename
        ├── unread/             # Newly delivered envelopes waiting for wake/read
        ├── claimed/            # Envelopes currently leased to a waking agent
        ├── done/               # Acknowledged/completed envelopes + delivery receipts
        └── dead/               # Failed deliveries or hop-limit quarantined envelopes
```

### Atomic State Transitions (`os.replace`)
1. **Write (`send`):** Sender writes full JSON to `boxes/<to>/tmp/<ts>_<ulid>.tmp`, flushes, and atomically renames via `os.replace()` into `boxes/<to>/unread/<ts>_<ulid>.json`. Readers never observe half-written files.
2. **Claim (`pulse` / `read`):** Dispatcher or recipient atomically renames `unread/<msg>.json` → `claimed/<msg>.json`. If two processes race, exactly one wins `os.replace()`; the loser gets `FileNotFoundError` and skips cleanly.
3. **Complete (`ack`) or Quarantine (`dead`):** Moved atomically to `done/` (recording `acked_at` and `delivery` receipt) or `dead/` (if `hop_count > max_hops` or max retries exceeded).

---

## 2. Envelope & Agent Registry Schemas

### Envelope JSON (`<ts>_<ulid>.json`)
```json
{
  "id": "20261118T143655Z_01JCY8...",
  "thread_id": "thr_01JCY8...",
  "from": "aim-joshua",
  "to": "copy-cat",
  "reply_to": "aim-joshua",
  "subject": "Audit status check",
  "body": "Verify Episode 02 manifest schema; see attached ref.",
  "refs": ["specs/ep02.json"],
  "hop_count": 0,
  "max_hops": 1,
  "ack_required": true,
  "operator_approved": true,
  "created_at": "2026-11-18T14:36:53Z",
  "delivery": {
    "driver": "agy-desktop-sidecar",
    "returncode": 0,
    "delivered_at": "2026-11-18T14:36:55Z"
  }
}
```

### Logical Address Book (`agents.json`)
Maps human/agent-friendly logical names (`aim-joshua`, `aim-auditor`, `copy-cat`) to their host environment and last-mile wake driver (see [Windows 11 & Cross-OS Drivers](windows-11-and-cross-os-drivers.md)):
- `driver`: `"agy-desktop-sidecar"` | `"agy-cli"` | `"opencode-http"` | `"grok-cli"` | `"codex-rpc"` | `"vscode-chat"` | `"tmux"` | `"lars-vision"`
- `cwd`: Target project workspace root
- `session_id`: Pinned conversation/session UUID (updated automatically on first headless run or during `/aim-handoff` session rotation)
- `tmux_session` / `http_url`: Optional driver-specific endpoint metadata

---

## 3. Zero-Token Cron / Task Scheduler Dispatcher (`aim-mail pulse`)
Modeled after A.I.M.'s deterministic pre-flight cron pattern:
1. **Zero-Cost Idle Path (`<5ms`):** Scheduled via POSIX `cron`, macOS `launchd`, Windows Task Scheduler (`schtasks`), or an Antigravity Desktop Sidecar loop. Scans `boxes/*/unread/*.json`; if empty, exits `0` immediately with **zero LLM invocations and zero token spend**.
2. **Pre-Spawn Guardrails:** Before waking any target agent, Python enforces:
   - **Loop Gate:** Rejects and moves to `dead/` if `hop_count > max_hops` (default `max_hops = 1` for automated peer replies unless explicitly elevated by Operator).
   - **Busy/Lease Gate:** Checks `locks/<agent_id>.lock.json` (with stale-TTL recovery) plus harness-specific idle state (e.g., `CASCADE_RUN_STATUS_IDLE` in Antigravity `conversation_summaries.db`) so two turns never collide on the same session.
3. **Doorbell Separation:** Wake triggers inject only a short, deterministic instruction (`You have unread mail in aim-mailbox. Run aim-mail inbox, process tasks, and ack.`), while the woken agent pulls the full structured JSON envelope and `--ref` files cleanly via its shell tool (see [Unified Skill Strategy (`aim-communicate` v2)](aim-communicate-v2-migration.md)).
