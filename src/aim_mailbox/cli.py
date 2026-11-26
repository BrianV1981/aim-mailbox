from __future__ import annotations

import os
import sys

from . import __version__
from ._fast_check import try_fast_empty_check

TYPE_CHECKING = False
if TYPE_CHECKING:
    import argparse
    from typing import Any, Callable
    from .store import MailboxStore


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _resolve_store(args: argparse.Namespace) -> MailboxStore:
    from .store import MailboxStore

    mb_dir = getattr(args, "mailbox_dir", None)
    return MailboxStore(mb_dir)


def _emit(payload: Any, as_json: bool, human_lines: list[str] | None = None) -> None:
    import json

    if as_json or human_lines is None:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for ln in human_lines:
            print(ln)


def cmd_register(args: argparse.Namespace) -> int:
    import json
    from .store import MailboxError

    store = _resolve_store(args)
    if args.unregister:
        removed = store.unregister_agent(args.agent)
        payload = {"agent_id": args.agent, "unregistered": removed}
        _emit(payload, args.json, [f"[{'OK' if removed else 'MISS'}] Unregistered agent '{args.agent}'."])
        return 0 if removed else 1

    cmd_list: list[str] | None = None
    if args.command_json:
        try:
            parsed_cmd = json.loads(args.command_json)
            if not isinstance(parsed_cmd, list) or not all(isinstance(x, str) for x in parsed_cmd):
                raise ValueError("must be a JSON array of strings")
            cmd_list = parsed_cmd
        except ValueError as exc:
            raise MailboxError(f"Invalid --command-json: {exc}") from exc

    entry = store.register_agent(
        args.agent,
        driver=args.driver,
        cwd=args.cwd,
        session_id=args.session_id,
        clear_session_id=args.clear_session_id,
        tmux_session=args.tmux_session,
        http_url=args.http_url,
        command=cmd_list,
        binary=args.binary,
        summary_db=args.summary_db,
        allow_unapproved=args.allow_unapproved,
        dangerously_skip_permissions=args.dangerously_skip_permissions,
    )
    _emit(
        entry,
        args.json,
        [f"[OK] Registered agent '{entry['agent_id']}' (driver={entry.get('driver')})."],
    )
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    agents = store.load_agents()
    if args.json:
        _emit({"agents": agents}, True)
        return 0
    if not agents:
        print("No registered agents.")
        return 0
    for aid, cfg in sorted(agents.items()):
        drv = str(cfg.get("driver", "none"))
        sid = cfg.get("session_id") or cfg.get("tmux_session") or cfg.get("http_url")
        if not sid and drv in ("agy-desktop-sidecar", "agy-cli") and cfg.get("cwd"):
            sid = "auto(cwd)"
        sid = sid or "-"
        cwd = cfg.get("cwd") or "-"
        print(f"{aid:<22} driver={drv:<20} target={sid} cwd={cwd}")
    return 0


def _evaluate_wake_exit_code(
    wake_summary: dict[str, Any] | None,
    target_agent: str,
    *,
    allow_deferred: bool = False,
) -> int:
    if not wake_summary:
        return 0
    if wake_summary.get("dispatched"):
        return 0
    if wake_summary.get("failed"):
        return 1
    deferred = wake_summary.get("deferred") or []
    skipped = wake_summary.get("skipped") or []
    approval_blocked = [d for d in deferred if d.get("reason") == "OPERATOR_APPROVAL_REQUIRED"]
    remote_skipped = [
        s for s in skipped if s.get("reason") in ("HOST_MISMATCH", "SIDECAR_CONTEXT_REQUIRED")
    ]
    hard_skipped = [
        s for s in skipped if s.get("reason") not in ("HOST_MISMATCH", "SIDECAR_CONTEXT_REQUIRED")
    ]
    if allow_deferred and (deferred or remote_skipped) and not approval_blocked and not hard_skipped:
        return 0

    if wake_summary.get("checked_agents", 0) == 0:
        reason = f"target agent '{target_agent}' is not registered in agents.json"
    elif approval_blocked:
        reason = f"initial wake for '{target_agent}' requires --operator-approved (or agent allow_unapproved)"
    elif skipped:
        first_skip = skipped[0]
        skip_reason = str(first_skip.get("reason") or "SKIPPED")
        skip_detail = str(first_skip.get("detail") or "").strip()
        if skip_reason == "PASSIVE_DRIVER":
            reason = f"target agent '{target_agent}' has passive driver 'none'"
        elif skip_reason == "SIDECAR_CONTEXT_REQUIRED":
            reason = (
                f"target agent '{target_agent}' requires Antigravity Desktop Sidecar context "
                f"({skip_reason}: {skip_detail or 'ANTIGRAVITY_LS_ADDRESS is unset'}); "
                "omit --wake or pass --allow-deferred when Sidecar watch is active"
            )
        elif skip_reason == "HOST_MISMATCH":
            reason = (
                f"target agent '{target_agent}' cannot be woken from this OS host "
                f"({skip_reason}: {skip_detail or 'cross-OS driver'}); "
                "omit --wake or pass --allow-deferred if target host watcher is active"
            )
        else:
            reason = f"wake for '{target_agent}' was skipped ({skip_reason})"
    elif wake_summary.get("quarantined"):
        reason = f"envelope for '{target_agent}' was quarantined"
    elif deferred:
        reasons = ", ".join(str(d.get("reason") or "DEFERRED") for d in deferred)
        reason = f"wake for '{target_agent}' was deferred ({reasons}); pass --allow-deferred if a watcher is active"
    else:
        reason = f"no wake driver dispatched for '{target_agent}'"
    print(f"[WAKE_FAIL] {reason}.", file=sys.stderr)
    return 2


def cmd_send(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    sent = store.send_envelope(
        from_agent=args.from_agent,
        to_agent=args.to_agent,
        reply_to=args.reply_to,
        subject=args.subject or "",
        body=args.body,
        refs=args.refs,
        thread_id=args.thread_id,
        hop_count=args.hop_count,
        max_hops=args.max_hops,
        ack_required=not args.no_ack_required,
        operator_approved=args.operator_approved,
        priority=args.priority,
        coalesce_key=args.coalesce_key,
    )
    wake_summary = None
    if args.wake:
        from .dispatcher import pulse_mailbox

        wake_summary = pulse_mailbox(store, agent_id=sent["to"])
        sent["wake"] = wake_summary

    _emit(
        sent,
        args.json,
        [
            f"[SENT] id={sent['id']} thread={sent['thread_id']} priority={sent['priority']} from={sent['from']} -> to={sent['to']}",
            *(
                [f"[WAKE] dispatched={len(wake_summary['dispatched'])} deferred={len(wake_summary['deferred'])} skipped={len(wake_summary['skipped'])} failed={len(wake_summary['failed'])}"]
                if wake_summary
                else []
            ),
        ],
    )
    return _evaluate_wake_exit_code(wake_summary, sent["to"], allow_deferred=args.allow_deferred)


def cmd_inbox(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    items = store.list_inbox(
        args.agent,
        state=args.state,
        from_agent=args.from_agent,
        thread_id=args.thread_id,
        query=args.query,
        since=args.since,
        limit=args.limit,
        priority=args.priority,
    )
    if args.json:
        _emit({"agent": args.agent, "state": args.state, "count": len(items), "messages": items}, True)
        return 0
    if not items:
        print(f"Inbox ({args.state}) empty for '{args.agent}'.")
        return 0
    for msg in items:
        sub = f" | {msg['subject']}" if msg.get("subject") else ""
        prio = f" [{str(msg.get('priority', 'normal')).upper()}]" if msg.get("priority", "normal") != "normal" else ""
        ck = f" coalesce={msg['coalesce_key']}" if msg.get("coalesce_key") else ""
        print(f"[{msg['state'].upper()}]{prio} {msg['id']} from:{msg['from']} reply_to:{msg.get('reply_to', msg['from'])}{ck}{sub}")
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    res = store.gc_mailbox(
        agent_id=args.agent,
        done_days=args.done_days,
        dead_days=args.dead_days,
        dry_run=args.dry_run,
        archive_dir=args.archive_dir,
    )
    mode = "DRY_RUN" if res["dry_run"] else ("ARCHIVED" if res["archive_dir"] else "PRUNED")
    human = [
        f"[GC:{mode}] checked={res['checked_agents']} count={res['pruned_count']} "
        f"(done={res['by_state']['done']}, dead={res['by_state']['dead']})"
    ]
    if res["archive_dir"]:
        human.append(f"  archive_dir={res['archive_dir']}")
    if res["errors"]:
        human.append(f"  errors={len(res['errors'])}")
    _emit(res, args.json, human)
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    msg = store.read_message(args.agent, args.message_id, claim=args.claim, ack=args.ack)
    if args.json:
        _emit(msg, True)
        return 0
    lines = [
        f"ID:        {msg['id']} [{msg['state'].upper()}]",
        f"Thread:    {msg['thread_id']} (hop {msg.get('hop_count', 0)}/{msg.get('max_hops', 1)})",
        f"Priority:  {msg.get('priority', 'normal')}",
        f"From:      {msg['from']} (reply_to: {msg.get('reply_to', msg['from'])})",
        f"To:        {msg['to']}",
        f"Created:   {msg['created_at']}",
    ]
    if msg.get("coalesce_key"):
        lines.append(f"Coalesce:  {msg['coalesce_key']}")
    if msg.get("coalesced_ids"):
        lines.append(f"Coalesced: {', '.join(msg['coalesced_ids'])}")
    if msg.get("subject"):
        lines.append(f"Subject:   {msg['subject']}")
    if msg.get("refs"):
        lines.append(f"Refs:      {', '.join(msg['refs'])}")
    lines.extend(["", msg["body"]])
    _emit(msg, False, lines)
    return 0


def cmd_reply(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    sent = store.reply_message(
        args.agent,
        args.message_id,
        body=args.body,
        subject=args.subject,
        refs=args.refs,
        ack_parent=not args.no_ack_parent,
        ack_required=args.ack_required,
        priority=args.priority,
        coalesce_key=args.coalesce_key,
    )
    wake_summary = None
    if args.wake:
        from .dispatcher import pulse_mailbox

        wake_summary = pulse_mailbox(store, agent_id=sent["to"])
        sent["wake"] = wake_summary

    _emit(
        sent,
        args.json,
        [f"[REPLIED] id={sent['id']} thread={sent['thread_id']} from={sent['from']} -> to={sent['to']} (hop={sent['hop_count']}/{sent['max_hops']})"],
    )
    return _evaluate_wake_exit_code(wake_summary, sent["to"], allow_deferred=args.allow_deferred)


def cmd_ack(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    acked = store.ack_message(args.agent, args.message_id, note=args.note or "")
    _emit(acked, args.json, [f"[ACKED] id={acked['id']} agent={args.agent} -> state=done"])
    return 0


def _read_stdin_json_nonblocking() -> dict[str, Any]:
    import json

    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        if raw and raw.strip():
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
    except Exception:
        pass
    return {}


def cmd_check(args: argparse.Namespace) -> int:
    import json

    store = _resolve_store(args)
    unread = store.list_inbox(args.agent, state="unread")
    count = len(unread)
    fmt = getattr(args, "format", "text")
    if args.json:
        _emit({"agent": args.agent, "unread_count": count, "message_ids": [m["id"] for m in unread]}, True)
        return 0
    if fmt == "agy-inject":
        if count == 0:
            print("{}")
            return 0
        senders = ", ".join(sorted({str(m.get("from", "peer")) for m in unread}))
        notice = (
            f"[AIM-MAILBOX: {count} unread message(s) for '{args.agent}' from {senders} — "
            f"run `aim-mail inbox {args.agent}` and `aim-mail read {args.agent}`]"
        )
        print(json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": notice}}]}))
        return 0
    if fmt == "agy-stop":
        if count == 0:
            print("{}")
            return 0
        hook_in = _read_stdin_json_nonblocking()
        if hook_in.get("continuationActive"):
            print("{}")
            return 0
        senders = ", ".join(sorted({str(m.get("from", "peer")) for m in unread}))
        notice = (
            f"[AIM-MAILBOX: {count} unread message(s) for '{args.agent}' from {senders} — "
            f"run `aim-mail inbox {args.agent}` and `aim-mail read {args.agent}`]"
        )
        print(json.dumps({"decision": "continue", "reason": notice}))
        return 0

    if count == 0:
        if not args.quiet and not args.inject:
            print(f"0 unread messages for '{args.agent}'.")
        return 0
    senders = ", ".join(sorted({str(m.get("from", "peer")) for m in unread}))
    if args.inject:
        print(
            f"[AIM-MAILBOX: {count} unread message(s) for '{args.agent}' from {senders} — "
            f"run `aim-mail inbox {args.agent}` and `aim-mail read {args.agent}`]"
        )
    elif not args.quiet:
        print(f"{count} unread message(s) for '{args.agent}' from {senders}.")
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    msg = store.wait_for_mail(
        args.agent,
        thread_id=args.thread_id,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        claim=args.claim,
    )
    if msg is None:
        if args.json:
            _emit({"status": "TIMEOUT", "agent": args.agent, "thread_id": args.thread_id}, True)
        else:
            print(f"[TIMEOUT] No matching unread mail for '{args.agent}' within {args.timeout}s.", file=sys.stderr)
        return 1
    _emit(msg, args.json, [f"[ARRIVED] id={msg['id']} from={msg['from']} thread={msg['thread_id']}"])
    return 0


def cmd_pulse(args: argparse.Namespace) -> int:
    from .dispatcher import pulse_mailbox

    store = _resolve_store(args)
    summary = pulse_mailbox(
        store,
        agent_id=args.agent,
        dry_run=args.dry_run,
        timeout=args.timeout,
        low_batch_window=args.low_batch_window,
    )
    _emit(
        summary,
        args.json,
        [
            f"[PULSE] checked={summary['checked_agents']} dispatched={len(summary['dispatched'])} "
            f"deferred={len(summary['deferred'])} skipped={len(summary['skipped'])} "
            f"quarantined={len(summary['quarantined'])} failed={len(summary['failed'])}"
        ],
    )
    return 1 if summary["failed"] else 0


def cmd_watch(args: argparse.Namespace) -> int:
    from .dispatcher import watch_mailbox

    store = _resolve_store(args)
    return watch_mailbox(
        store,
        agent_id=args.agent,
        interval=args.interval,
        max_cycles=args.max_cycles,
        timeout=args.timeout,
        low_batch_window=args.low_batch_window,
    )


def cmd_install_agy_sidecar(args: argparse.Namespace) -> int:
    from pathlib import Path
    from .drivers import install_agy_sidecar, uninstall_agy_sidecar

    store = _resolve_store(args)
    cfg_dir = Path(args.config_dir) if args.config_dir else None
    if args.uninstall:
        res = uninstall_agy_sidecar(config_dir=cfg_dir, sidecar_id=args.sidecar_id)
        _emit(res, args.json, [f"[OK] Uninstalled sidecar '{args.sidecar_id}' (removed={res['uninstalled']})."])
        return 0
    store.ensure_root()
    res = install_agy_sidecar(
        store.root,
        config_dir=cfg_dir,
        sidecar_id=args.sidecar_id,
        python_exe=args.python,
        interval=args.interval,
    )
    _emit(
        res,
        args.json,
        [
            f"[OK] Installed & enabled Antigravity Sidecar '{res['sidecar_id']}'",
            f"     Sidecar dir: {res['sidecar_dir']}",
            f"     Mailbox dir: {res['mailbox_dir']}",
            *(f"[WARN] {w}" for w in res.get("warnings", [])),
        ],
    )
    return 0


def cmd_install_hooks(args: argparse.Namespace) -> int:
    from pathlib import Path
    from .hooks import install_hooks, uninstall_hooks
    from .store import validate_agent_id

    mb_explicit = getattr(args, "mailbox_dir", None)
    mb_path = Path(mb_explicit).expanduser().resolve() if mb_explicit else None
    cfg_dir = Path(args.config_dir) if args.config_dir else None
    ws_dir = Path(args.workspace) if args.workspace else None
    agent_id = args.agent or (Path.cwd().name or "default")
    try:
        agent_id = validate_agent_id(agent_id)
    except Exception:
        if args.agent:
            raise
        agent_id = "default"

    if args.uninstall:
        res = uninstall_hooks(
            harness=args.harness,
            agent_id=agent_id,
            config_dir=cfg_dir,
            workspace=ws_dir,
            dry_run=args.dry_run,
        )
        _emit(
            res,
            args.json,
            [f"[OK] Uninstalled '{res['harness']}' hook at {res['target_path']} (found={res['found']})."],
        )
        return 0

    res = install_hooks(
        harness=args.harness,
        agent_id=agent_id,
        mailbox_dir=mb_path,
        config_dir=cfg_dir,
        workspace=ws_dir,
        dry_run=args.dry_run,
    )
    prefix = "DRY-RUN" if res["dry_run"] else "OK"
    _emit(
        res,
        args.json,
        [
            f"[{prefix}] Installed '{res['harness']}' turn-boundary hook ({res['mode']})",
            f"     Target:  {res['target_path']}",
            f"     Command: {res['command']}",
            *(f"[WARN] {w}" for w in res.get("warnings", [])),
        ],
    )
    return 0


def cmd_describe(_args: argparse.Namespace) -> int:
    import json
    from .drivers import DRIVER_NAMES
    from .hooks import HARNESS_NAMES
    from .store import BOX_STATES, PRIORITY_LEVELS

    spec = {
        "name": "aim-mail",
        "version": __version__,
        "drivers": list(DRIVER_NAMES),
        "harnesses": list(HARNESS_NAMES),
        "states": list(BOX_STATES),
        "priorities": list(PRIORITY_LEVELS),
        "commands": sorted(COMMAND_HANDLERS.keys()),
        "env": {
            "AIM_MAILBOX_DIR": "Override default ~/.aim/mailbox root directory",
            "OPENCODE_SERVER_USERNAME": "Optional Basic Auth username for opencode-http driver",
            "OPENCODE_SERVER_PASSWORD": "Optional Basic Auth password for opencode-http driver",
        },
    }
    print(json.dumps(spec, indent=2))
    return 0


COMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "register": cmd_register,
    "agents": cmd_agents,
    "send": cmd_send,
    "inbox": cmd_inbox,
    "read": cmd_read,
    "reply": cmd_reply,
    "ack": cmd_ack,
    "check": cmd_check,
    "wait": cmd_wait,
    "pulse": cmd_pulse,
    "watch": cmd_watch,
    "gc": cmd_gc,
    "install-agy-sidecar": cmd_install_agy_sidecar,
    "install-hooks": cmd_install_hooks,
    "describe": cmd_describe,
}


def build_parser() -> argparse.ArgumentParser:
    import argparse
    from .drivers import DRIVER_NAMES
    from .hooks import HARNESS_NAMES
    from .store import BOX_STATES, PRIORITY_LEVELS
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--mailbox-dir",
        default=argparse.SUPPRESS,
        help="Mailbox root directory (default: $AIM_MAILBOX_DIR or ~/.aim/mailbox)",
    )

    parser = argparse.ArgumentParser(
        prog="aim-mail",
        description="Zero-daemon, zero-LLM local Maildir post office and cross-OS wake dispatcher for AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"aim-mail {__version__}")
    parser.add_argument(
        "--mailbox-dir",
        default=None,
        help="Mailbox root directory (default: $AIM_MAILBOX_DIR or ~/.aim/mailbox)",
    )

    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_reg = sub.add_parser("register", parents=[common], help="Register or update an agent and wake driver")
    p_reg.add_argument("agent", help="Logical agent ID (e.g., aim-auditor)")
    p_reg.add_argument("--driver", choices=DRIVER_NAMES, default=None, help="Last-mile wake driver")
    p_reg.add_argument("--cwd", default=None, help="Agent project working directory")
    p_reg.add_argument("--session-id", default=None, help="Optional pinned conversation/session UUID (omit to auto-resolve from --cwd on Antigravity)")
    p_reg.add_argument("--clear-session-id", action="store_true", help="Clear pinned session_id so Antigravity drivers follow latest session in --cwd")
    p_reg.add_argument("--tmux-session", default=None, help="Target tmux session name (tmux driver)")
    p_reg.add_argument("--http-url", default=None, help="Base HTTP URL (opencode-http driver)")
    p_reg.add_argument("--binary", default=None, help="Optional explicit harness binary path")
    p_reg.add_argument("--summary-db", default=None, help="Optional explicit Antigravity conversation_summaries.db path")
    p_reg.add_argument("--command-json", default=None, help="JSON list of command args for 'command' driver")
    p_reg.add_argument(
        "--allow-unapproved",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow pulse dispatch for initial messages without --operator-approved",
    )
    p_reg.add_argument(
        "--dangerously-skip-permissions",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Pass --dangerously-skip-permissions when invoking agy-cli driver (disabled by default)",
    )
    p_reg.add_argument(
        "--allow-skip-permissions",
        dest="dangerously_skip_permissions",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Alias for --dangerously-skip-permissions",
    )
    p_reg.add_argument("--unregister", action="store_true", help="Remove agent from registry")
    p_reg.add_argument("--json", action="store_true", help="Emit JSON output")

    p_agents = sub.add_parser("agents", parents=[common], help="List registered agents and drivers")
    p_agents.add_argument("--json", action="store_true", help="Emit JSON output")

    p_send = sub.add_parser("send", parents=[common], help="Send an envelope to a recipient's unread Maildir")
    p_send.add_argument("--from", dest="from_agent", required=True, help="Sender logical agent ID")
    p_send.add_argument("--to", dest="to_agent", required=True, help="Recipient logical agent ID")
    p_send.add_argument("--reply-to", default=None, help="Mandatory reply target (defaults to --from)")
    p_send.add_argument("--subject", default="", help="Short message subject")
    p_send.add_argument("--body", required=True, help="Concise message body")
    p_send.add_argument("--ref", dest="refs", action="append", default=[], help="Referenced file path (repeatable)")
    p_send.add_argument("--thread-id", default=None, help="Optional existing thread ID")
    p_send.add_argument("--hop-count", type=int, default=0, help="Initial hop count (default: 0; auto-incremented on known thread_id)")
    p_send.add_argument("--max-hops", type=int, default=None, help="Max automated reply hops on thread (default: 1 or existing thread max)")
    p_send.add_argument("--no-ack-required", action="store_true", help="Mark message as informational (no ACK needed)")
    p_send.add_argument("--operator-approved", action="store_true", help="Record explicit Operator approval on envelope")
    p_send.add_argument("--priority", choices=PRIORITY_LEVELS, default="normal", help="Envelope priority lane (urgent, normal, low; default: normal)")
    p_send.add_argument("--coalesce-key", default=None, help="Optional key to coalesce duplicate unread wakes")
    p_send.add_argument("--wake", action="store_true", help="Immediately trigger a pulse dispatch for recipient")
    p_send.add_argument("--allow-deferred", action="store_true", help="Treat BUSY_DEFERRED/AGENT_LOCK_HELD/LOW_PRIORITY_BATCH_WINDOW as exit 0 on --wake")
    p_send.add_argument("--json", action="store_true", help="Emit JSON output")

    p_inbox = sub.add_parser("inbox", parents=[common], help="List envelopes in an agent's mailbox")
    p_inbox.add_argument("agent", help="Target agent ID")
    p_inbox.add_argument("--state", choices=(*BOX_STATES, "active", "all"), default="unread", help="Envelope state filter")
    p_inbox.add_argument("--from", dest="from_agent", default=None, help="Filter by sender agent ID")
    p_inbox.add_argument("--thread-id", default=None, help="Filter by thread ID")
    p_inbox.add_argument("--priority", choices=PRIORITY_LEVELS, default=None, help="Filter by priority lane")
    p_inbox.add_argument("--query", default=None, help="Case-insensitive substring search across subject/body/id/refs")
    p_inbox.add_argument("--since", default=None, help="Filter envelopes created at or after ISO-8601 UTC timestamp")
    p_inbox.add_argument("--limit", type=int, default=None, help="Maximum number of matching envelopes to return")
    p_inbox.add_argument("--json", action="store_true", help="Emit JSON output")

    p_read = sub.add_parser("read", parents=[common], help="Read a specific or oldest unread envelope")
    p_read.add_argument("agent", help="Target agent ID")
    p_read.add_argument("message_id", nargs="?", default=None, help="Message ID or unique prefix (default: oldest unread)")
    p_read.add_argument("--claim", action="store_true", help="Atomically move unread envelope to claimed/")
    p_read.add_argument("--ack", action="store_true", help="Atomically acknowledge and move envelope to done/")
    p_read.add_argument("--json", action="store_true", help="Emit JSON output")

    p_reply = sub.add_parser("reply", parents=[common], help="Reply to an envelope's reply_to address and increment hop_count")
    p_reply.add_argument("agent", help="Replying agent ID (owner of parent message)")
    p_reply.add_argument("message_id", help="Parent message ID or unique prefix")
    p_reply.add_argument("--body", required=True, help="Reply body")
    p_reply.add_argument("--subject", default=None, help="Optional subject override")
    p_reply.add_argument("--ref", dest="refs", action="append", default=[], help="Referenced file path (repeatable)")
    p_reply.add_argument("--no-ack-parent", action="store_true", help="Do not automatically ACK parent message")
    p_reply.add_argument("--ack-required", action="store_true", help="Require recipient to ACK this reply")
    p_reply.add_argument("--priority", choices=PRIORITY_LEVELS, default="normal", help="Reply priority lane (urgent, normal, low; default: normal)")
    p_reply.add_argument("--coalesce-key", default=None, help="Optional key to coalesce duplicate unread reply wakes")
    p_reply.add_argument("--wake", action="store_true", help="Immediately trigger a pulse dispatch for reply recipient")
    p_reply.add_argument("--allow-deferred", action="store_true", help="Treat BUSY_DEFERRED/AGENT_LOCK_HELD/LOW_PRIORITY_BATCH_WINDOW as exit 0 on --wake")
    p_reply.add_argument("--json", action="store_true", help="Emit JSON output")

    p_ack = sub.add_parser("ack", parents=[common], help="Acknowledge an envelope and move it to done/")
    p_ack.add_argument("agent", help="Target agent ID")
    p_ack.add_argument("message_id", help="Message ID or unique prefix")
    p_ack.add_argument("--note", default="", help="Optional completion note")
    p_ack.add_argument("--json", action="store_true", help="Emit JSON output")

    p_check = sub.add_parser("check", parents=[common], help="Fast (<15ms) unread check for turn hooks")
    p_check.add_argument("agent", help="Target agent ID")
    p_check.add_argument("--inject", action="store_true", help="Print steering notice only when unread > 0")
    p_check.add_argument("--quiet", action="store_true", help="Suppress output")
    p_check.add_argument("--format", choices=("text", "agy-inject", "agy-stop"), default="text", help="Output format for turn hooks")
    p_check.add_argument("--json", action="store_true", help="Emit JSON output")

    p_wait = sub.add_parser("wait", parents=[common], help="Block at zero LLM cost until unread mail arrives")
    p_wait.add_argument("agent", help="Target agent ID")
    p_wait.add_argument("--thread-id", default=None, help="Optional thread_id filter")
    p_wait.add_argument("--timeout", type=float, default=60.0, help="Timeout in seconds (default: 60)")
    p_wait.add_argument("--poll-interval", type=float, default=0.25, help="Filesystem poll interval in seconds")
    p_wait.add_argument("--claim", action="store_true", help="Atomically claim matching unread envelope")
    p_wait.add_argument("--json", action="store_true", help="Emit JSON output")

    p_pulse = sub.add_parser("pulse", parents=[common], help="Single-pass zero-LLM wake sweep for cron / Task Scheduler")
    p_pulse.add_argument("--agent", default=None, help="Dispatch only for a single agent ID")
    p_pulse.add_argument("--dry-run", action="store_true", help="Inspect planned wake commands without executing")
    p_pulse.add_argument("--timeout", type=float, default=300.0, help="Per-driver execution timeout in seconds")
    p_pulse.add_argument("--low-batch-window", type=float, default=30.0, help="Defer low-priority mail until oldest waits N seconds (default: 30; 0 disables)")
    p_pulse.add_argument("--json", action="store_true", help="Emit JSON output")

    p_watch = sub.add_parser("watch", parents=[common], help="Continuous zero-LLM Maildir watch loop (for Sidecars)")
    p_watch.add_argument("--agent", default=None, help="Watch only a single agent ID")
    p_watch.add_argument("--interval", type=float, default=1.5, help="Poll interval in seconds (default: 1.5)")
    p_watch.add_argument("--max-cycles", type=int, default=None, help="Optional cycle limit (testing)")
    p_watch.add_argument("--timeout", type=float, default=300.0, help="Per-driver execution timeout in seconds")
    p_watch.add_argument("--low-batch-window", type=float, default=30.0, help="Defer low-priority mail until oldest waits N seconds (default: 30; 0 disables)")

    p_gc = sub.add_parser("gc", parents=[common], help="Prune or archive aged terminal envelopes from done/ and dead/")
    p_gc.add_argument("--agent", default=None, help="Limit retention GC to a single agent ID")
    p_gc.add_argument("--done-days", type=int, default=14, help="Retention days for done/ envelopes (default: 14)")
    p_gc.add_argument("--dead-days", type=int, default=30, help="Retention days for dead/ envelopes (default: 30)")
    p_gc.add_argument("--dry-run", action="store_true", help="Report eligible envelopes without deleting or archiving")
    p_gc.add_argument("--archive-dir", default=None, help="Archive aged envelopes to directory before unlinking")
    p_gc.add_argument("--json", action="store_true", help="Emit JSON output")

    p_sidecar = sub.add_parser(
        "install-agy-sidecar",
        parents=[common],
        help="Install or uninstall the Antigravity Desktop Sidecar config",
    )
    p_sidecar.add_argument("--config-dir", default=None, help="Override ~/.gemini/config directory")
    p_sidecar.add_argument("--sidecar-id", default="aim-mailbox", help="Sidecar identifier (default: aim-mailbox)")
    p_sidecar.add_argument("--python", default=None, help="Explicit Python interpreter path for sidecar")
    p_sidecar.add_argument("--interval", type=float, default=1.5, help="Watch poll interval in seconds")
    p_sidecar.add_argument("--uninstall", action="store_true", help="Remove sidecar directory and config entry")
    p_sidecar.add_argument("--json", action="store_true", help="Emit JSON output")

    p_hooks = sub.add_parser(
        "install-hooks",
        parents=[common],
        help="Install or uninstall non-destructive turn-boundary check hooks",
    )
    p_hooks.add_argument("--harness", required=True, choices=HARNESS_NAMES, help="Target AI harness")
    p_hooks.add_argument("--agent", default=None, help="Logical agent ID for check --inject (default: cwd name)")
    p_hooks.add_argument("--config-dir", default=None, help="Override harness config directory")
    p_hooks.add_argument("--workspace", default=None, help="Project workspace root for workspace-scoped hooks (agy/grok)")
    p_hooks.add_argument("--uninstall", action="store_true", help="Remove aim-mailbox hook entries non-destructively")
    p_hooks.add_argument("--dry-run", action="store_true", help="Preview target path and command without writing files")
    p_hooks.add_argument("--json", action="store_true", help="Emit JSON output")

    p_desc = sub.add_parser("describe", parents=[common], help="Print compact JSON schema of CLI commands and drivers")
    p_desc.add_argument("--json", action="store_true", help="Emit JSON output (always JSON)")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    fast_rc = try_fast_empty_check(raw_argv)
    if fast_rc is not None:
        return fast_rc

    from .store import MailboxError
    import json

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    handler = COMMAND_HANDLERS.get(args.subcommand)
    if handler is None:
        parser.error(f"Unknown subcommand: {args.subcommand}")
    try:
        return handler(args)
    except MailboxError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
        else:
            print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
