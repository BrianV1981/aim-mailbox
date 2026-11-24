from __future__ import annotations

import sys
import time
from typing import Any

from .drivers import ASYNC_WAKE_DRIVERS, dispatch_wake
from .store import MailboxStore, validate_agent_id


def pulse_mailbox(
    store: MailboxStore,
    *,
    agent_id: str | None = None,
    dry_run: bool = False,
    lease_seconds: float = 900.0,
    lock_ttl_seconds: float = 600.0,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """
    Execute one zero-LLM dispatch sweep across registered agents (or a single agent).
    1. Reclaims expired claimed/ leases.
    2. Enforces hop_count <= max_hops (quarantining loop violations to dead/ before waking any LLM).
    3. Acquires a per-agent dispatch lock, claims unread envelopes, invokes the agent's driver,
       and persists the delivery receipt (auto-saving newly discovered session_id values).
    """
    agents_map = store.load_agents()
    if agent_id:
        aid = validate_agent_id(agent_id)
        cfg = agents_map.get(aid)
        targets = {aid: cfg} if cfg else {}
    else:
        targets = agents_map

    summary: dict[str, Any] = {
        "checked_agents": len(targets),
        "dispatched": [],
        "quarantined": [],
        "deferred": [],
        "failed": [],
        "skipped": [],
    }

    for aid, cfg in sorted(targets.items()):
        try:
            store.reclaim_expired_claims(aid, lease_seconds=lease_seconds)
            driver = str(cfg.get("driver") or "none").strip()
            if driver == "none":
                summary["skipped"].append({"agent_id": aid, "reason": "PASSIVE_DRIVER"})
                continue

            unread = store.list_inbox(aid, state="unread")
            if not unread:
                continue

            valid_unread: list[dict[str, Any]] = []
            for env in unread:
                hop_count = int(env.get("hop_count", 0))
                max_hops = int(env.get("max_hops", 1))
                if hop_count > max_hops:
                    if not dry_run:
                        store.quarantine_message(
                            aid,
                            str(env["id"]),
                            reason=f"MAX_HOPS_EXCEEDED ({hop_count} > {max_hops})",
                        )
                    summary["quarantined"].append(
                        {"agent_id": aid, "message_id": env["id"], "reason": "MAX_HOPS_EXCEEDED"}
                    )
                else:
                    valid_unread.append(env)

            if not valid_unread:
                continue

            if dry_run:
                res = dispatch_wake(cfg, valid_unread, mailbox_dir=store.root, dry_run=True, timeout=timeout)
                summary["dispatched"].append(
                    {
                        "agent_id": aid,
                        "driver": driver,
                        "message_ids": [e["id"] for e in valid_unread],
                        "result": res,
                    }
                )
                continue

            effective_lock_ttl = max(float(lock_ttl_seconds), float(timeout) + 60.0)
            lock_token = store.acquire_agent_lock(aid, ttl_seconds=effective_lock_ttl)
            if not lock_token:
                summary["deferred"].append({"agent_id": aid, "reason": "AGENT_LOCK_HELD"})
                continue

            claimed: list[dict[str, Any]] = []
            try:
                for env in valid_unread:
                    c_env = store.claim_envelope(aid, str(env["id"]))
                    if c_env is not None:
                        claimed.append(c_env)

                if not claimed:
                    continue

                def _persist_sid(new_sid: str) -> None:
                    if not cfg.get("session_id"):
                        store.register_agent(aid, session_id=new_sid)

                res = dispatch_wake(
                    cfg,
                    claimed,
                    mailbox_dir=store.root,
                    dry_run=False,
                    timeout=timeout,
                    on_session_created=_persist_sid,
                )
                status = res.get("status")

                if status == "DELIVERED":
                    new_sid = res.get("session_id")
                    if new_sid and not cfg.get("session_id"):
                        store.register_agent(aid, session_id=str(new_sid))
                    for c_env in claimed:
                        # Re-check whether the woken CLI turn already ACKed or quarantined during execution
                        current = store.read_message(aid, str(c_env["id"]), auto_ack_no_ack_required=False)
                        cur_state = current.get("state")
                        if cur_state == "done":
                            store.ack_message(aid, str(c_env["id"]), delivery=res)
                        elif cur_state != "dead":
                            store.record_wake_delivery(
                                aid,
                                str(c_env["id"]),
                                res,
                                auto_complete_if_no_ack=(driver not in ASYNC_WAKE_DRIVERS),
                            )
                    summary["dispatched"].append(
                        {
                            "agent_id": aid,
                            "driver": driver,
                            "message_ids": [c["id"] for c in claimed],
                            "result": res,
                        }
                    )
                elif status == "BUSY_DEFERRED":
                    for c_env in claimed:
                        store.release_claim(
                            aid,
                            str(c_env["id"]),
                            last_error=str(res.get("stderr") or "BUSY_DEFERRED"),
                            delivery=res,
                            defer=True,
                        )
                    summary["deferred"].append(
                        {
                            "agent_id": aid,
                            "driver": driver,
                            "message_ids": [c["id"] for c in claimed],
                            "reason": "BUSY_DEFERRED",
                        }
                    )
                else:
                    err_msg = str(res.get("stderr") or res.get("stdout") or "WAKE_FAILED").strip()
                    for c_env in claimed:
                        rel = store.release_claim(
                            aid,
                            str(c_env["id"]),
                            last_error=err_msg,
                            delivery=res,
                            defer=False,
                        )
                        if rel.get("state") == "dead":
                            summary["quarantined"].append(
                                {
                                    "agent_id": aid,
                                    "message_id": c_env["id"],
                                    "reason": rel.get("dead_reason", "MAX_ATTEMPTS_EXCEEDED"),
                                }
                            )
                    summary["failed"].append(
                        {
                            "agent_id": aid,
                            "driver": driver,
                            "message_ids": [c["id"] for c in claimed],
                            "error": err_msg,
                        }
                    )
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                for c_env in claimed:
                    try:
                        rel = store.release_claim(
                            aid,
                            str(c_env["id"]),
                            last_error=err_msg,
                            defer=False,
                        )
                        if rel.get("state") == "dead":
                            summary["quarantined"].append(
                                {
                                    "agent_id": aid,
                                    "message_id": c_env["id"],
                                    "reason": rel.get("dead_reason", "MAX_ATTEMPTS_EXCEEDED"),
                                }
                            )
                    except Exception:
                        pass
                summary["failed"].append(
                    {
                        "agent_id": aid,
                        "driver": driver,
                        "message_ids": [c["id"] for c in claimed],
                        "error": err_msg,
                    }
                )
            finally:
                store.release_agent_lock(aid, lock_token)
        except Exception as exc:
            summary["failed"].append(
                {
                    "agent_id": aid,
                    "driver": str((cfg or {}).get("driver") or "none"),
                    "message_ids": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    return summary


def watch_mailbox(
    store: MailboxStore,
    *,
    agent_id: str | None = None,
    interval: float = 1.5,
    max_cycles: int | None = None,
    timeout: float = 300.0,
) -> int:
    cycles = 0
    sleep_sec = max(0.1, float(interval))
    while True:
        try:
            pulse_mailbox(store, agent_id=agent_id, timeout=timeout)
        except Exception as exc:
            print(f"[WATCH_ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return 0
        time.sleep(sleep_sec)
