from __future__ import annotations

import datetime as dt
import sys
import time
from typing import Any

from .drivers import ASYNC_WAKE_DRIVERS, dispatch_wake
from .store import (
    PRIORITY_RANK,
    MailboxStore,
    parse_utc_iso,
    utc_now,
    validate_agent_id,
)


def _envelope_priority_rank(env: dict[str, Any]) -> int:
    prio = str(env.get("priority") or "normal").strip().lower()
    return PRIORITY_RANK.get(prio, PRIORITY_RANK["normal"])


def _envelope_created_dt(env: dict[str, Any], fallback_now: dt.datetime) -> dt.datetime:
    raw = env.get("created_at")
    if raw:
        try:
            return parse_utc_iso(str(raw))
        except ValueError:
            pass
    return fallback_now


def _plan_priority_and_coalesce(
    valid_unread: list[dict[str, Any]],
    *,
    low_batch_window: float,
    ref_now: dt.datetime,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], list[dict[str, Any]]]:
    """
    Group unread envelopes by coalesce_key, enforce priority ordering (urgent -> normal -> low),
    and defer low-priority envelopes into bounded batch windows until oldest reaches low_batch_window.
    Returns (deferred_low_envelopes, ready_groups, ordered_leaders).
    """
    coalesce_buckets: dict[str, list[dict[str, Any]]] = {}
    groups: list[list[dict[str, Any]]] = []
    for env in valid_unread:
        ck = env.get("coalesce_key")
        key = str(ck).strip() if ck else ""
        if key:
            bucket = coalesce_buckets.get(key)
            if bucket is None:
                bucket = []
                coalesce_buckets[key] = bucket
                groups.append(bucket)
            bucket.append(env)
        else:
            groups.append([env])

    immediate_items: list[tuple[int, str, list[dict[str, Any]]]] = []
    low_items: list[tuple[int, str, dt.datetime, list[dict[str, Any]]]] = []

    for grp in groups:
        grp.sort(key=lambda e: str(e.get("id", "")))
        eff_rank = min(_envelope_priority_rank(e) for e in grp)
        leader = grp[-1]
        if eff_rank == PRIORITY_RANK["low"] and low_batch_window > 0:
            oldest_dt = min(_envelope_created_dt(e, ref_now) for e in grp)
            low_items.append((eff_rank, str(leader["id"]), oldest_dt, grp))
        else:
            immediate_items.append((eff_rank, str(leader["id"]), grp))

    deferred_low: list[dict[str, Any]] = []
    if low_items:
        oldest_low_dt = min(item[2] for item in low_items)
        age_sec = (ref_now - oldest_low_dt).total_seconds()
        if age_sec >= float(low_batch_window):
            for rank, lid, _, grp in low_items:
                immediate_items.append((rank, lid, grp))
        else:
            for _, _, _, grp in low_items:
                deferred_low.extend(grp)

    immediate_items.sort(key=lambda item: (item[0], item[1]))
    ready_groups = [item[2] for item in immediate_items]
    ordered_leaders = [grp[-1] for grp in ready_groups]
    return deferred_low, ready_groups, ordered_leaders


def pulse_mailbox(
    store: MailboxStore,
    *,
    agent_id: str | None = None,
    dry_run: bool = False,
    lease_seconds: float = 900.0,
    lock_ttl_seconds: float = 600.0,
    timeout: float = 300.0,
    low_batch_window: float = 30.0,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """
    Execute one zero-LLM dispatch sweep across registered agents (or a single agent).
    1. Reclaims expired claimed/ leases.
    2. Enforces hop_count <= max_hops and operator_approved across all priorities.
    3. Coalesces matching coalesce_key envelopes, orders urgent -> normal -> low, and
       defers low-priority mail into bounded batch windows until oldest reaches low_batch_window.
    4. Acquires a per-agent dispatch lock, claims unread envelopes, invokes the agent's driver,
       retires superseded coalesced envelopes to done/, and persists the delivery receipt.
    """
    agents_map = store.load_agents()
    if agent_id:
        aid = validate_agent_id(agent_id)
        cfg = agents_map.get(aid)
        targets = {aid: cfg} if cfg else {}
    else:
        targets = agents_map

    ref_now = now or utc_now()
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
                    continue

                is_initial = hop_count == 0 and env.get("in_reply_to") is None
                approved = bool(env.get("operator_approved", False)) or bool(cfg.get("allow_unapproved", False))
                if is_initial and not approved:
                    summary["deferred"].append(
                        {
                            "agent_id": aid,
                            "message_id": env["id"],
                            "reason": "OPERATOR_APPROVAL_REQUIRED",
                        }
                    )
                    continue

                valid_unread.append(env)

            if not valid_unread:
                continue

            deferred_low, ready_groups, ready_leaders = _plan_priority_and_coalesce(
                valid_unread,
                low_batch_window=float(low_batch_window),
                ref_now=ref_now,
            )
            for low_env in deferred_low:
                summary["deferred"].append(
                    {
                        "agent_id": aid,
                        "message_id": low_env["id"],
                        "priority": "low",
                        "reason": "LOW_PRIORITY_BATCH_WINDOW",
                    }
                )

            if not ready_groups:
                continue

            if dry_run:
                dry_superseded = [e["id"] for grp in ready_groups for e in grp[:-1]]
                res = dispatch_wake(cfg, ready_leaders, mailbox_dir=store.root, dry_run=True, timeout=timeout)
                disp_entry: dict[str, Any] = {
                    "agent_id": aid,
                    "driver": driver,
                    "message_ids": [e["id"] for e in ready_leaders],
                    "result": res,
                }
                if dry_superseded:
                    disp_entry["coalesced_superseded_ids"] = dry_superseded
                summary["dispatched"].append(disp_entry)
                continue

            effective_lock_ttl = max(float(lock_ttl_seconds), float(timeout) + 60.0)
            lock_token = store.acquire_agent_lock(aid, ttl_seconds=effective_lock_ttl)
            if not lock_token:
                summary["deferred"].append({"agent_id": aid, "reason": "AGENT_LOCK_HELD"})
                continue

            all_claimed: list[dict[str, Any]] = []
            claimed_groups: list[list[dict[str, Any]]] = []
            try:
                for grp in ready_groups:
                    c_grp: list[dict[str, Any]] = []
                    for env in grp:
                        c_env = store.claim_envelope(aid, str(env["id"]))
                        if c_env is not None:
                            c_grp.append(c_env)
                            all_claimed.append(c_env)
                    if c_grp:
                        claimed_groups.append(c_grp)

                if not claimed_groups:
                    continue

                wake_leaders: list[dict[str, Any]] = []
                superseded_pairs: list[tuple[dict[str, Any], str]] = []
                for c_grp in claimed_groups:
                    leader = c_grp[-1]
                    superseded = c_grp[:-1]
                    if superseded:
                        sup_ids = [str(s["id"]) for s in superseded]
                        combined_ack = any(bool(e.get("ack_required", True)) for e in c_grp)
                        leader = store.update_claimed_metadata(
                            aid,
                            str(leader["id"]),
                            coalesced_ids=sup_ids,
                            ack_required=combined_ack,
                        )
                        for s in superseded:
                            superseded_pairs.append((s, str(leader["id"])))
                    wake_leaders.append(leader)

                def _persist_sid(new_sid: str) -> None:
                    if not cfg.get("session_id"):
                        store.register_agent(aid, session_id=new_sid)

                res = dispatch_wake(
                    cfg,
                    wake_leaders,
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
                    for sup_env, leader_id in superseded_pairs:
                        store.ack_message(
                            aid,
                            str(sup_env["id"]),
                            note=f"Coalesced into {leader_id}",
                            delivery=res,
                            coalesced_into=leader_id,
                        )
                    for c_env in wake_leaders:
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
                    disp_ok: dict[str, Any] = {
                        "agent_id": aid,
                        "driver": driver,
                        "message_ids": [c["id"] for c in wake_leaders],
                        "result": res,
                    }
                    if superseded_pairs:
                        disp_ok["coalesced_superseded_ids"] = [s["id"] for s, _ in superseded_pairs]
                    summary["dispatched"].append(disp_ok)
                elif status == "BUSY_DEFERRED":
                    for c_env in all_claimed:
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
                            "message_ids": [c["id"] for c in wake_leaders],
                            "reason": "BUSY_DEFERRED",
                        }
                    )
                else:
                    err_msg = str(res.get("stderr") or res.get("stdout") or "WAKE_FAILED").strip()
                    for c_env in all_claimed:
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
                            "message_ids": [c["id"] for c in wake_leaders],
                            "error": err_msg,
                        }
                    )
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                for c_env in all_claimed:
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
                        "message_ids": [c["id"] for c in all_claimed],
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
    low_batch_window: float = 30.0,
) -> int:
    cycles = 0
    sleep_sec = max(0.1, float(interval))
    while True:
        try:
            pulse_mailbox(store, agent_id=agent_id, timeout=timeout, low_batch_window=low_batch_window)
        except Exception as exc:
            print(f"[WATCH_ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return 0
        time.sleep(sleep_sec)
