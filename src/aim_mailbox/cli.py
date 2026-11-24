from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .dispatcher import pulse_mailbox, watch_mailbox
from .drivers import DRIVER_NAMES, install_agy_sidecar, uninstall_agy_sidecar
from .store import BOX_STATES, MailboxError, MailboxStore


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _resolve_store(args: argparse.Namespace) -> MailboxStore:
    mb_dir = getattr(args, "mailbox_dir", None)
    return MailboxStore(mb_dir)


def _emit(payload: Any, as_json: bool, human_lines: list[str] | None = None) -> None:
    if as_json or human_lines is None:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for ln in human_lines:
            print(ln)


def cmd_register(args: argparse.Namespace) -> int:
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
        tmux_session=args.tmux_session,
        http_url=args.http_url,
        command=cmd_list,
        binary=args.binary,
        summary_db=args.summary_db,
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
        sid = cfg.get("session_id") or cfg.get("tmux_session") or cfg.get("http_url") or "-"
        cwd = cfg.get("cwd") or "-"
        print(f"{aid:<22} driver={str(cfg.get('driver', 'none')):<20} target={sid} cwd={cwd}")
    return 0


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
    )
    wake_summary = None
    if args.wake:
        wake_summary = pulse_mailbox(store, agent_id=sent["to"])
        sent["wake"] = wake_summary

    _emit(
        sent,
        args.json,
        [
            f"[SENT] id={sent['id']} thread={sent['thread_id']} from={sent['from']} -> to={sent['to']}",
            *(
                [f"[WAKE] dispatched={len(wake_summary['dispatched'])} deferred={len(wake_summary['deferred'])} failed={len(wake_summary['failed'])}"]
                if wake_summary
                else []
            ),
        ],
    )
    if wake_summary and wake_summary["failed"]:
        return 1
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    items = store.list_inbox(args.agent, state=args.state)
    if args.json:
        _emit({"agent": args.agent, "state": args.state, "count": len(items), "messages": items}, True)
        return 0
    if not items:
        print(f"Inbox ({args.state}) empty for '{args.agent}'.")
        return 0
    for msg in items:
        sub = f" | {msg['subject']}" if msg.get("subject") else ""
        print(f"[{msg['state'].upper()}] {msg['id']} from:{msg['from']} reply_to:{msg.get('reply_to', msg['from'])}{sub}")
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
        f"From:      {msg['from']} (reply_to: {msg.get('reply_to', msg['from'])})",
        f"To:        {msg['to']}",
        f"Created:   {msg['created_at']}",
    ]
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
    )
    wake_summary = None
    if args.wake:
        wake_summary = pulse_mailbox(store, agent_id=sent["to"])
        sent["wake"] = wake_summary

    _emit(
        sent,
        args.json,
        [f"[REPLIED] id={sent['id']} thread={sent['thread_id']} from={sent['from']} -> to={sent['to']} (hop={sent['hop_count']}/{sent['max_hops']})"],
    )
    if wake_summary and wake_summary["failed"]:
        return 1
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    acked = store.ack_message(args.agent, args.message_id, note=args.note or "")
    _emit(acked, args.json, [f"[ACKED] id={acked['id']} agent={args.agent} -> state=done"])
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    unread = store.list_inbox(args.agent, state="unread")
    count = len(unread)
    if args.json:
        _emit({"agent": args.agent, "unread_count": count, "message_ids": [m["id"] for m in unread]}, True)
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
    store = _resolve_store(args)
    summary = pulse_mailbox(
        store,
        agent_id=args.agent,
        dry_run=args.dry_run,
        timeout=args.timeout,
    )
    _emit(
        summary,
        args.json,
        [
            f"[PULSE] checked={summary['checked_agents']} dispatched={len(summary['dispatched'])} "
            f"deferred={len(summary['deferred'])} quarantined={len(summary['quarantined'])} failed={len(summary['failed'])}"
        ],
    )
    return 1 if summary["failed"] else 0


def cmd_watch(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    return watch_mailbox(
        store,
        agent_id=args.agent,
        interval=args.interval,
        max_cycles=args.max_cycles,
        timeout=args.timeout,
    )


def cmd_install_agy_sidecar(args: argparse.Namespace) -> int:
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
        ],
    )
    return 0


def cmd_describe(_args: argparse.Namespace) -> int:
    spec = {
        "name": "aim-mail",
        "version": __version__,
        "drivers": list(DRIVER_NAMES),
        "states": list(BOX_STATES),
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
    "install-agy-sidecar": cmd_install_agy_sidecar,
    "describe": cmd_describe,
}


def build_parser() -> argparse.ArgumentParser:
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
    p_reg.add_argument("--session-id", default=None, help="Pinned conversation/session UUID")
    p_reg.add_argument("--tmux-session", default=None, help="Target tmux session name (tmux driver)")
    p_reg.add_argument("--http-url", default=None, help="Base HTTP URL (opencode-http driver)")
    p_reg.add_argument("--binary", default=None, help="Optional explicit harness binary path")
    p_reg.add_argument("--summary-db", default=None, help="Optional explicit Antigravity conversation_summaries.db path")
    p_reg.add_argument("--command-json", default=None, help="JSON list of command args for 'command' driver")
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
    p_send.add_argument("--wake", action="store_true", help="Immediately trigger a pulse dispatch for recipient")
    p_send.add_argument("--json", action="store_true", help="Emit JSON output")

    p_inbox = sub.add_parser("inbox", parents=[common], help="List envelopes in an agent's mailbox")
    p_inbox.add_argument("agent", help="Target agent ID")
    p_inbox.add_argument("--state", choices=(*BOX_STATES, "active", "all"), default="unread", help="Envelope state filter")
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
    p_reply.add_argument("--wake", action="store_true", help="Immediately trigger a pulse dispatch for reply recipient")
    p_reply.add_argument("--json", action="store_true", help="Emit JSON output")

    p_ack = sub.add_parser("ack", parents=[common], help="Acknowledge an envelope and move it to done/")
    p_ack.add_argument("agent", help="Target agent ID")
    p_ack.add_argument("message_id", help="Message ID or unique prefix")
    p_ack.add_argument("--note", default="", help="Optional completion note")
    p_ack.add_argument("--json", action="store_true", help="Emit JSON output")

    p_check = sub.add_parser("check", parents=[common], help="Fast (<5ms) unread check for turn hooks")
    p_check.add_argument("agent", help="Target agent ID")
    p_check.add_argument("--inject", action="store_true", help="Print steering notice only when unread > 0")
    p_check.add_argument("--quiet", action="store_true", help="Suppress output")
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
    p_pulse.add_argument("--json", action="store_true", help="Emit JSON output")

    p_watch = sub.add_parser("watch", parents=[common], help="Continuous zero-LLM Maildir watch loop (for Sidecars)")
    p_watch.add_argument("--agent", default=None, help="Watch only a single agent ID")
    p_watch.add_argument("--interval", type=float, default=1.5, help="Poll interval in seconds (default: 1.5)")
    p_watch.add_argument("--max-cycles", type=int, default=None, help="Optional cycle limit (testing)")
    p_watch.add_argument("--timeout", type=float, default=300.0, help="Per-driver execution timeout in seconds")

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

    p_desc = sub.add_parser("describe", parents=[common], help="Print compact JSON schema of CLI commands and drivers")
    p_desc.add_argument("--json", action="store_true", help="Emit JSON output (always JSON)")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
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
