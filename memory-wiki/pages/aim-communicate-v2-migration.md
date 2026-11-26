# A-Mail Standardization & Skill Migration (`v0.3.0`)

## Overview
Starting in `v0.3.0` (`aim-joshua#181`, `aim-mailbox#19`, `aim-skill-library#35`), **A-Mail** (`amail` / `aim-mail` CLI + canonical `skills/aim-mail/SKILL.md`) is the **sole** inter-agent communication standard across J.O.S.H.U.A. and the entire A.I.M. ecosystem—superseding `aim-communicate` (retained for one transition cycle as a minimal deprecation redirect) and retiring standalone `tmux` shell paste wrappers outright.

---

## 1. Why `aim-mail` (`amail`) Supplants `aim-communicate`
1. **One Mental Model, One Verb, One Skill:** Agents previously saw `aim-communicate` in skill manifests and `aim-mail` on `PATH`. Standardizing on **A-Mail** (`amail` / `aim-mail` CLI + `aim-mail` skill) eliminates split-brain naming across `AGENTS.md` (`§8c`), `FLEET_ORCHESTRATION.md` (`§4`), and harness packs.
2. **Eliminating Shell Paste Bypass Risks:** Legacy standalone `tmux` paste wrappers bypassed Maildir receipts, `max_hops` quarantine, fail-closed `--operator-approved` checks, and Windows 11 Desktop Sidecar routing. Routing POSIX `tmux` panes exclusively through `amail`'s built-in `--driver tmux` (`=session:` exact anchoring + `Enter`-only 1-line doorbell) enforces every guardrail uniformly in Python.
3. **Universal Cross-OS Interoperability:** WSL2 `tmux` terminals, native Windows 11 Antigravity Desktop sessions (even while minimized), headless `agy` / `grok` / `codex` CLIs, OpenCode HTTP servers, and VS Code Agent Mode all speak the exact same `amail send / inbox / read / reply / ack / check / wait` lifecycle.

---

## 2. Structural Resolution of Legacy `tmux` Paste Defects
A-Mail eliminates all four historical defects from legacy terminal paste workflows (`aim-skill-library#17` & `#19`):

| Legacy Paste Defect | How A-Mail (`aim-mailbox`) Eliminates It Architecturally |
| :--- | :--- |
| **RC1:** `Escape` → `Enter` on AGY CLI silently deletes multi-line (`20+` line) `paste-buffer` payloads. | Full dispatches stay in Maildir JSON (`unread/<id>.json` + `--ref` files). The `tmux` driver only ever pastes a **single short line** and submits with **`Enter` only** (never `Escape`). |
| **RC2 & RC3:** False `VERIFY_FAIL` when TUIs collapse pastes into `[Pasted text #1 +N lines]`, causing duplicate manual re-sends. | Delivery and idempotency are tracked by unique envelope ID in `boxes/<agent>/{unread,claimed,done}/`, and 1-line doorbells never collapse into multi-line paste chips. |
| **RC4:** Divergent Windows scratchpad vs. Linux `tmux` `SKILL.md` overrides drifting out of sync. | **One universal `skills/aim-mail/SKILL.md` across Windows 11, WSL2, macOS, and Linux.** OS/harness differences live strictly inside `aim-mailbox` drivers (see [Windows 11 & Cross-OS Drivers](windows-11-and-cross-os-drivers.md)). |

---

## 3. Enforced Governance & Safety Gates (`AGENTS.md` `§8c`)
1. **Operator Permission Mandate (`--operator-approved`):** Unsolicited peer dispatches are blocked (`rc=2`) unless `--operator-approved` is passed; replying to an inbound envelope's `reply_to` address on an authorized `thread_id` is pre-authorized.
2. **Mandatory `from` + `reply_to` Fields:** Enforced at CLI validation time (`amail send` requires `--from` and `--to`, defaulting `--reply-to` to `--from`).
3. **1-Hop Loop Prevention (`max_hops=1`):** The dispatcher quarantines any message exceeding `max_hops` to `dead/` before waking an LLM; final closeout reports end with `DO NOT reply` (`--no-ack-required`).
4. **DND & Live-Vertical WIP Guards:** Agents respect DND/BUSY states and WIP locks, never waking DND-protected sessions or mutating live verticals mid-batch.
