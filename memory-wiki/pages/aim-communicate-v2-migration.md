# Unified Skill Strategy: Overhauling `aim-communicate` (`v2.0`)

## Overview
Rather than introducing a second competing inter-agent skill alongside `aim-communicate`, **`aim-communicate` in `aim-skill-library` will be upgraded to `v2.0` on top of `aim-mailbox`**. Every agent across all workspaces, `aim-audit`, and `aim-coagents` keeps calling one canonical skill while gaining cross-OS, cross-harness Maildir delivery.

---

## 1. Why Unify Under `aim-communicate` (`v2.0`)
1. **Zero Fleet Split-Brain:** All `AGENTS.md` blueprints, `aim-auditor` workflows, and sovereign Co-Agent nodes already reference `aim-communicate` and its permission/loop mandates. Two parallel communication skills would force models to guess between `tmux` pastes and `aim-mail` envelopes.
2. **`tmux` Preserved as a First-Class Driver:** Existing POSIX/WSL `tmux` fleets lose nothing — `tmux` becomes the `"tmux"` driver inside `aim-mailbox` (`agents.json`), firing a 1-line wake nudge into live terminal panes after persisting the durable envelope.
3. **Universal Two-Way Interoperability:** A Grok CLI agent in WSL2, an Antigravity Desktop agent minimized on Windows 11, and an OpenCode server session can message each other using the exact same `aim-mail` CLI verbs without knowing which OS or harness hosts the peer.

---

## 2. Structural Resolution of `aim-skill-library` Issues `#17` & `#19`
Upgrading `aim-communicate` to wrap `aim-mailbox` eliminates all four root causes documented in `BrianV1981/aim-skill-library#19` (and OS-override drift in `#17`):

| Legacy `aim-communicate` Defect (`#17` / `#19`) | How `aim-mailbox` Eliminates It Architecturally |
| :--- | :--- |
| **RC1:** `Escape` → `Enter` on AGY CLI silently deletes multi-line (`20+` line) `paste-buffer` payloads. | Full dispatches stay in Maildir JSON (`unread/<id>.json` + `--ref` files). The `tmux` driver only ever pastes a **single short line** (`Run: aim-mail read <id>`) and submits with **`Enter` only** (never `Escape`). |
| **RC2 & RC3:** `tmux_send.sh` false `VERIFY_FAIL` when TUI collapses pastes into `[Pasted text #1 +N lines]`, causing duplicate manual re-sends. | Delivery and idempotency are tracked by unique envelope ID in `boxes/<agent>/{unread,claimed,done}/`, and 1-line doorbells never collapse into multi-line paste chips. |
| **RC4:** Windows 11 scratchpad override (`vessels/agy/skills/aim-communicate`) vs. Linux `tmux` skill caused 7 divergent `SKILL.md` hashes across symlinks. | **One universal `SKILL.md` across Windows 11, WSL2, macOS, and Linux.** All vessels call `aim-mail send / inbox / read / reply / ack`; OS/harness differences live strictly inside `aim-mailbox` drivers (see [Windows 11 & Cross-OS Drivers](windows-11-and-cross-os-drivers.md)). |

---

## 3. Preserved Governance & Safety Gates
`aim-communicate` `v2.0` carries forward every battle-tested governance rule from `v1` and enforces them deterministically in Python:
1. **Operator Permission Mandate:** Agents remain forbidden from initiating unsolicited peer conversations without explicit Operator authorization; replying to an inbound envelope's `reply_to` address on an authorized `thread_id` is pre-authorized.
2. **Mandatory `from` + `reply_to` Fields:** Enforced at CLI validation time (`aim-mail send` exits non-zero if `--from` or `--to` is omitted; `--reply-to` defaults explicitly to `--from`).
3. **1-Hop Loop Prevention (`max_hops`):** Envelopes carry `hop_count` and `max_hops` (default `1` for automated peer replies, `0` for final closeout `[DO NOT REPLY]` reports). The dispatcher quarantines any message exceeding `max_hops` before waking an LLM.
4. **Honest Delivery Verification:** `aim-mail send` / `pulse` records harness-verified receipts (`returncode == 0` from Sidecar `agentapi`, HTTP `200` from OpenCode, JSON receipt from headless CLI, or `Enter`-only composer/pane check on `tmux`) before marking delivery complete.
