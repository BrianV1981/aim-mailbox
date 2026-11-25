from __future__ import annotations

import datetime as dt
import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from aim_mailbox.store import (
    MailboxError,
    MailboxStore,
    utc_iso,
    utc_now,
    validate_agent_id,
    validate_http_url,
    validate_message_id,
    validate_session_id,
)


def test_validate_agent_id_normalizes_and_blocks_unsafe_names() -> None:
    assert validate_agent_id("@aim-auditor") == "aim-auditor"
    assert validate_agent_id("worker_01.dev") == "worker_01.dev"

    for bad in ("", "@", "../escape", "a/b", r"a\b", ".hidden", "-dash", "CON", "nul.txt", "COM1", "LPT9", "a..b"):
        with pytest.raises(MailboxError):
            validate_agent_id(bad)


def test_maildir_send_read_reply_ack_lifecycle(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    sent = store.send_envelope(
        from_agent="@aim-joshua",
        to_agent="@copy-cat",
        subject="Manifest check",
        body="Validate Episode 02 JSON.",
        refs=["specs/ep02.json"],
        operator_approved=True,
    )
    assert sent["state"] == "unread"
    assert sent["from"] == "aim-joshua"
    assert sent["to"] == "copy-cat"
    assert sent["reply_to"] == "aim-joshua"

    unread = store.list_inbox("copy-cat", state="unread")
    assert len(unread) == 1
    assert unread[0]["id"] == sent["id"]

    claimed = store.read_message("copy-cat", claim=True)
    assert claimed["state"] == "claimed"
    assert claimed["attempts"] == 1
    assert store.list_inbox("copy-cat", state="unread") == []
    assert len(store.list_inbox("copy-cat", state="claimed")) == 1

    reply = store.reply_message("copy-cat", sent["id"][:15], body="Validated cleanly.")
    assert reply["from"] == "copy-cat"
    assert reply["to"] == "aim-joshua"
    assert reply["thread_id"] == sent["thread_id"]
    assert reply["in_reply_to"] == sent["id"]
    assert reply["hop_count"] == 1
    assert reply["operator_approved"] is True

    # Parent message was auto-ACKed to done/
    done = store.list_inbox("copy-cat", state="done")
    assert len(done) == 1
    assert done[0]["id"] == sent["id"]

    # Sender received the reply in unread/
    joshua_unread = store.list_inbox("aim-joshua", state="unread")
    assert len(joshua_unread) == 1
    acked = store.ack_message("aim-joshua", reply["id"], note="Closed loop")
    assert acked["state"] == "done"
    assert acked["ack_note"] == "Closed loop"


def test_agent_lock_and_stale_ttl_recovery(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    tok1 = store.acquire_agent_lock("worker-a", ttl_seconds=60.0)
    assert tok1 is not None
    assert store.acquire_agent_lock("worker-a", ttl_seconds=60.0) is None
    assert store.release_agent_lock("worker-a", "wrong-token") is False
    assert store.release_agent_lock("worker-a", tok1) is True

    # Simulate an expired lock from a crashed process
    tok2 = store.acquire_agent_lock("worker-a", ttl_seconds=1.0)
    assert tok2 is not None
    lock_file = store.locks_dir / "worker-a.lock.json"
    lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
    lock_data["expires_at"] = utc_iso(utc_now() - dt.timedelta(seconds=10))
    lock_file.write_text(json.dumps(lock_data), encoding="utf-8")

    tok3 = store.acquire_agent_lock("worker-a", ttl_seconds=60.0)
    assert tok3 is not None and tok3 != tok2
    assert store.release_agent_lock("worker-a", tok3) is True


def test_defer_does_not_burn_attempts_while_failures_quarantine(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    msg = store.send_envelope(from_agent="a", to_agent="b", body="ping", max_attempts=2)

    c1 = store.claim_envelope("b", msg["id"])
    assert c1 is not None and c1["attempts"] == 1

    # Deferring because target session is busy must NOT burn attempts
    deferred = store.release_claim("b", msg["id"], last_error="BUSY", defer=True)
    assert deferred["state"] == "unread"
    assert deferred["attempts"] == 0

    # Real crash failures increment attempts and quarantine at max_attempts=2
    store.claim_envelope("b", msg["id"])
    r1 = store.release_claim("b", msg["id"], last_error="FAIL_1", defer=False)
    assert r1["state"] == "unread"
    assert r1["attempts"] == 1

    store.claim_envelope("b", msg["id"])
    r2 = store.release_claim("b", msg["id"], last_error="FAIL_2", defer=False)
    assert r2["state"] == "dead"
    assert r2["dead_reason"] == "FAIL_2"


def test_reclaim_expired_claims_and_wait_for_mail(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    msg = store.send_envelope(from_agent="orchestrator", to_agent="worker", body="task")
    store.claim_envelope("worker", msg["id"])

    claimed_file = store.boxes_dir / "worker" / "claimed" / f"{msg['id']}.json"
    data = json.loads(claimed_file.read_text(encoding="utf-8"))
    data["claimed_at"] = utc_iso(utc_now() - dt.timedelta(seconds=120))
    claimed_file.write_text(json.dumps(data), encoding="utf-8")

    reclaimed = store.reclaim_expired_claims("worker", lease_seconds=30.0)
    assert reclaimed == [msg["id"]]
    assert len(store.list_inbox("worker", state="unread")) == 1

    # Async delivery while waiting on specific thread
    def delayed_reply() -> None:
        time.sleep(0.15)
        store.reply_message("worker", msg["id"], body="done")

    t = threading.Thread(target=delayed_reply, daemon=True)
    t.start()
    arrived = store.wait_for_mail("orchestrator", thread_id=msg["thread_id"], timeout=3.0, poll_interval=0.05)
    t.join(timeout=2.0)
    assert arrived is not None
    assert arrived["body"] == "done"

    assert store.wait_for_mail("nobody", timeout=0.1, poll_interval=0.05) is None


def test_message_id_path_traversal_blocked_and_permissions_enforced(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.json"
    victim.write_text(json.dumps({"id": "victim", "from": "a", "to": "b", "body": "secret"}), encoding="utf-8")

    store = MailboxStore(tmp_path / "mailbox")
    sent = store.send_envelope(from_agent="sender", to_agent="worker", body="hello")
    store.register_agent("worker", driver="none")
    tok = store.acquire_agent_lock("worker", ttl_seconds=10.0)
    assert tok is not None

    for bad_id in ("", "../outside/victim", "../../../../outside/victim", r"..\victim", ".hidden", "id\nline"):
        with pytest.raises(MailboxError):
            validate_message_id(bad_id)
        with pytest.raises(MailboxError):
            store.read_message("worker", bad_id)
        with pytest.raises(MailboxError):
            store.ack_message("worker", bad_id)
        with pytest.raises(MailboxError):
            store.reply_message("worker", bad_id, body="nope")
        with pytest.raises(MailboxError):
            store.claim_envelope("worker", bad_id)

    assert victim.is_file(), "External file must never be unlinked by traversal ack"

    # Malicious on-disk envelope 'id' field must be blocked
    tampered = store.boxes_dir / "worker" / "unread" / "20261118T000000000000Z_deadbeef.json"
    tampered.write_text(
        json.dumps({"id": "../../../../outside/pwned", "from": "sender", "to": "worker", "body": "bad"}),
        encoding="utf-8",
    )
    with pytest.raises(MailboxError):
        store.ack_message("worker", "20261118T000000000000Z_deadbeef")
    tampered.unlink()

    if os.name == "posix":
        assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(store.locks_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((store.boxes_dir / "worker" / "unread").stat().st_mode) == 0o700
        assert stat.S_IMODE(store.agents_file.stat().st_mode) == 0o600
        assert stat.S_IMODE((store.locks_dir / "worker.lock.json").stat().st_mode) == 0o600
        env_file = store.boxes_dir / "worker" / "unread" / f"{sent['id']}.json"
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    store.release_agent_lock("worker", tok)


def test_agent_id_normalization_agents_lock_and_atomic_state_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno as _errno

    # Trailing dots blocked; mixed case normalized to lowercase
    with pytest.raises(MailboxError):
        validate_agent_id("worker.")
    assert validate_agent_id("@CopyCat") == "copycat"

    store = MailboxStore(tmp_path / "mailbox")
    store.register_agent("CopyCat", driver="none")
    store.register_agent("OtherAgent", driver="none")
    assert set(store.load_agents().keys()) == {"copycat", "otheragent"}

    # Corrupt agents.json must NOT be silently wiped on register_agent
    store.agents_file.write_text("{corrupt-json", encoding="utf-8")
    with pytest.raises(MailboxError):
        store.register_agent("third", driver="none")
    assert store.agents_file.read_text(encoding="utf-8") == "{corrupt-json"

    # Reset valid agents.json and verify concurrent register_agent does not drop entries
    store.agents_file.unlink()
    errors: list[Exception] = []

    def _reg(idx: int) -> None:
        try:
            store.register_agent(f"peer-{idx}", driver="none")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_reg, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert errors == []
    assert len(store.load_agents()) == 6

    # release_claim clears claimed_at and retries EBUSY on os.replace without duplicating files
    msg = store.send_envelope(from_agent="peer-0", to_agent="peer-1", body="atomic check")
    claimed = store.claim_envelope("peer-1", msg["id"])
    assert claimed is not None and "claimed_at" in claimed

    orig_replace = os.replace
    calls = {"ebusy": 0}

    def flaky_replace(src: Any, dst: Any) -> None:
        if calls["ebusy"] == 0 and str(dst).endswith(f"{msg['id']}.json") and "unread" in str(dst):
            calls["ebusy"] += 1
            raise OSError(_errno.EBUSY, "Device or resource busy")
        orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    released = store.release_claim("peer-1", msg["id"], defer=True)
    assert calls["ebusy"] == 1
    assert released["state"] == "unread"
    assert "claimed_at" not in released
    assert len(store.list_inbox("peer-1", state="claimed")) == 0
    assert len(store.list_inbox("peer-1", state="unread")) == 1

    # Concurrent stale-lock reclamation never unlinks a peer's freshly acquired lock
    tok_stale = store.acquire_agent_lock("peer-1", ttl_seconds=1.0)
    assert tok_stale is not None
    lock_file = store.locks_dir / "peer-1.lock.json"
    lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
    lock_data["expires_at"] = utc_iso(utc_now() - dt.timedelta(seconds=10))
    lock_file.write_text(json.dumps(lock_data), encoding="utf-8")

    winners: list[str] = []
    barrier = threading.Barrier(4)

    def _contend() -> None:
        barrier.wait()
        tok = store.acquire_agent_lock("peer-1", ttl_seconds=60.0)
        if tok is not None:
            winners.append(tok)

    contenders = [threading.Thread(target=_contend) for _ in range(4)]
    for t in contenders:
        t.start()
    for t in contenders:
        t.join(timeout=5.0)
    assert len(winners) == 1
    assert store.release_agent_lock("peer-1", winners[0]) is True


def test_thread_hop_propagation_over_hop_rejection_dead_parent_and_wait_watch_race(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    m0 = store.send_envelope(from_agent="lead", to_agent="worker", body="start", max_hops=1)
    assert m0["hop_count"] == 0

    # send --thread-id must propagate hop_count and prevent max_hops bypass
    m1 = store.send_envelope(
        from_agent="worker",
        to_agent="lead",
        body="follow up",
        thread_id=m0["thread_id"],
        max_hops=99,
    )
    assert m1["hop_count"] == 1
    assert m1["max_hops"] == 1

    # Third hop on same thread via send --thread-id exceeds max_hops=1 -> rejected
    with pytest.raises(MailboxError, match="hop limit exceeded"):
        store.send_envelope(from_agent="lead", to_agent="worker", body="loop", thread_id=m0["thread_id"])

    # reply_message beyond max_hops must reject BEFORE writing into unread/
    with pytest.raises(MailboxError, match="hop limit exceeded"):
        store.reply_message("lead", m1["id"], body="reply loop")
    assert store.list_inbox("worker", state="unread") == [m0]

    # Replying to a quarantined dead/ parent must fail cleanly without sending a reply
    store.quarantine_message("worker", m0["id"], reason="BAD")
    before_lead = store.list_inbox("lead", state="all")
    with pytest.raises(MailboxError, match="dead/"):
        store.reply_message("worker", m0["id"], body="zombie reply")
    assert store.list_inbox("lead", state="all") == before_lead

    # wait_for_mail detects post-start claimed/done arrival when watcher claims concurrently
    # and wait(claim=True) atomically claims unread arrival
    req = store.send_envelope(from_agent="lead", to_agent="worker", body="async", max_hops=2)

    def watch_claims_after_delay() -> None:
        time.sleep(0.12)
        rep = store.reply_message("worker", req["id"], body="fast reply")
        store.claim_envelope("lead", rep["id"])

    t = threading.Thread(target=watch_claims_after_delay, daemon=True)
    t.start()
    arrived = store.wait_for_mail("lead", thread_id=req["thread_id"], timeout=3.0, poll_interval=0.05)
    t.join(timeout=2.0)
    assert arrived is not None
    assert arrived["body"] == "fast reply"
    assert arrived["state"] == "claimed"

    # wait(claim=True) claims unread arrival directly
    req2 = store.send_envelope(from_agent="worker", to_agent="lead", body="claim me")
    claimed = store.wait_for_mail("lead", thread_id=req2["thread_id"], timeout=1.0, claim=True)
    assert claimed is not None
    assert claimed["id"] == req2["id"]
    assert claimed["state"] == "claimed"
    assert all(m["id"] != req2["id"] for m in store.list_inbox("lead", state="unread"))


def test_idless_read_does_not_replay_done_or_crash_on_dead(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    m1 = store.send_envelope(from_agent="a", to_agent="b", body="completed task")
    store.ack_message("b", m1["id"])
    assert len(store.list_inbox("b", state="done")) == 1
    assert store.list_inbox("b", state="active") == []

    # ID-less read must NOT replay historical done/ envelopes
    with pytest.raises(MailboxError, match="No unread or claimed messages pending"):
        store.read_message("b")

    # Even with only dead/ messages present, ID-less read --ack raises a clean MailboxError
    m2 = store.send_envelope(from_agent="a", to_agent="b", body="dead task")
    store.quarantine_message("b", m2["id"], reason="DEAD")
    with pytest.raises(MailboxError, match="No unread or claimed messages pending"):
        store.read_message("b", ack=True)

    # Async reply (ack_required=False) stays readable in unread/claimed until read
    rep = store.send_envelope(from_agent="b", to_agent="a", body="async reply", ack_required=False)
    store.claim_envelope("a", rep["id"])
    assert [m["id"] for m in store.list_inbox("a", state="active")] == [rep["id"]]
    read_rep = store.read_message("a")
    assert read_rep["id"] == rep["id"]
    assert read_rep["state"] == "done"


def test_session_id_and_http_url_validation(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    for bad_sid in ("", "-flag", "--exec", "bad sid", "bad\nsid"):
        with pytest.raises(MailboxError):
            validate_session_id(bad_sid)
        with pytest.raises(MailboxError):
            store.register_agent("w1", driver="agy-cli", session_id=bad_sid)

    for bad_url in ("", "file:///etc/passwd", "ftp://example.com", "http://"):
        with pytest.raises(MailboxError):
            validate_http_url(bad_url)
        with pytest.raises(MailboxError):
            store.register_agent("w2", driver="opencode-http", http_url=bad_url)

    assert validate_http_url("http://127.0.0.1:4096/") == "http://127.0.0.1:4096"


def test_inbox_filters_and_gc_retention_safety(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")

    m1 = store.send_envelope(
        from_agent="alpha",
        to_agent="worker",
        subject="Schema migration",
        body="Check v0.2 layout",
        refs=["docs/schema.md"],
        thread_id="thr_alpha",
    )
    m2 = store.send_envelope(
        from_agent="beta",
        to_agent="worker",
        subject="Build status",
        body="Green on Linux",
        thread_id="thr_beta",
    )
    m3 = store.send_envelope(
        from_agent="alpha",
        to_agent="worker",
        subject="Follow up",
        body="Another message about schema",
        thread_id="thr_alpha",
        max_hops=3,
    )

    # Filter by --from (with '@' normalization), --thread-id, --query, --since, --limit
    assert [m["id"] for m in store.list_inbox("worker", state="unread", from_agent="@Alpha")] == [m1["id"], m3["id"]]
    assert [m["id"] for m in store.list_inbox("worker", state="unread", thread_id="thr_beta")] == [m2["id"]]
    assert [m["id"] for m in store.list_inbox("worker", state="unread", query="DOCS/SCHEMA.MD")] == [m1["id"]]
    assert [m["id"] for m in store.list_inbox("worker", state="unread", query="schema", limit=1)] == [m1["id"]]
    assert [m["id"] for m in store.list_inbox("worker", state="unread", since=m2["created_at"])] == [m2["id"], m3["id"]]

    with pytest.raises(MailboxError):
        store.list_inbox("worker", limit=0)
    with pytest.raises(MailboxError):
        store.list_inbox("worker", since="not-a-timestamp")
    with pytest.raises(MailboxError):
        store.list_inbox("worker", query="   ")

    # Setup retention states: unread/, claimed/, done/ (old + fresh), dead/ (old + fresh)
    store.claim_envelope("worker", m2["id"])
    old_done = store.ack_message("worker", m1["id"])
    fresh_done_env = store.send_envelope(from_agent="alpha", to_agent="worker", body="fresh done")
    store.ack_message("worker", fresh_done_env["id"])

    old_dead_env = store.send_envelope(from_agent="alpha", to_agent="worker", body="old dead")
    store.quarantine_message("worker", old_dead_env["id"], reason="STALE")
    fresh_dead_env = store.send_envelope(from_agent="alpha", to_agent="worker", body="fresh dead")
    store.quarantine_message("worker", fresh_dead_env["id"], reason="RECENT")

    now = utc_now()
    old_done_path = store.boxes_dir / "worker" / "done" / f"{old_done['id']}.json"
    old_done_data = json.loads(old_done_path.read_text(encoding="utf-8"))
    old_done_data["acked_at"] = utc_iso(now - dt.timedelta(days=20))
    old_done_path.write_text(json.dumps(old_done_data), encoding="utf-8")

    old_dead_path = store.boxes_dir / "worker" / "dead" / f"{old_dead_env['id']}.json"
    old_dead_data = json.loads(old_dead_path.read_text(encoding="utf-8"))
    old_dead_data["quarantined_at"] = utc_iso(now - dt.timedelta(days=45))
    old_dead_path.write_text(json.dumps(old_dead_data), encoding="utf-8")

    # Unsafe archive_dir overlapping mailbox root / boxes / locks must be rejected
    for unsafe_dir in (store.root, store.boxes_dir, store.boxes_dir / "worker", store.locks_dir):
        with pytest.raises(MailboxError, match="Unsafe archive directory"):
            store.gc_mailbox(archive_dir=unsafe_dir)

    with pytest.raises(MailboxError):
        store.gc_mailbox(done_days=-1)

    # Dry-run archive reports eligible done/ and dead/ without writing archive or deleting files
    archive_dir = tmp_path / "archive_out"
    dry = store.gc_mailbox(done_days=14, dead_days=30, dry_run=True, archive_dir=archive_dir, now=now)
    assert dry["dry_run"] is True
    assert dry["pruned_count"] == 2
    assert dry["by_state"] == {"done": 1, "dead": 1}
    assert {item["message_id"] for item in dry["items"]} == {old_done["id"], old_dead_env["id"]}
    assert not archive_dir.exists()
    assert old_done_path.is_file()
    assert old_dead_path.is_file()

    # Live archive moves aged done/ and dead/ safely and preserves unread/, claimed/, and fresh terminal envelopes
    live = store.gc_mailbox(done_days=14, dead_days=30, dry_run=False, archive_dir=archive_dir, now=now)
    assert live["dry_run"] is False
    assert live["archived_count"] == 2
    assert not old_done_path.exists()
    assert not old_dead_path.exists()
    assert (archive_dir / "worker" / "done" / f"{old_done['id']}.json").is_file()
    assert (archive_dir / "worker" / "dead" / f"{old_dead_env['id']}.json").is_file()
    if os.name == "posix":
        assert stat.S_IMODE((archive_dir / "worker" / "done" / f"{old_done['id']}.json").stat().st_mode) == 0o600

    # Active unread/ and claimed/ plus fresh done/ and dead/ remain intact
    assert [m["id"] for m in store.list_inbox("worker", state="unread")] == [m3["id"]]
    assert [m["id"] for m in store.list_inbox("worker", state="claimed")] == [m2["id"]]
    assert [m["id"] for m in store.list_inbox("worker", state="done")] == [fresh_done_env["id"]]
    assert [m["id"] for m in store.list_inbox("worker", state="dead")] == [fresh_dead_env["id"]]

    # Direct prune (without archive_dir) with done_days=0 deletes remaining done/ while preserving unread/claimed/dead
    pruned = store.gc_mailbox(agent_id="worker", done_days=0, dead_days=30, now=now + dt.timedelta(seconds=1))
    assert pruned["pruned_count"] == 1
    assert pruned["archived_count"] == 0
    assert pruned["by_state"] == {"done": 1, "dead": 0}
    assert store.list_inbox("worker", state="done") == []
    assert len(store.list_inbox("worker", state="unread")) == 1
    assert len(store.list_inbox("worker", state="claimed")) == 1
    assert len(store.list_inbox("worker", state="dead")) == 1


def test_priority_and_coalesce_key_schema_and_v012_backcompat(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    urgent = store.send_envelope(
        from_agent="lead",
        to_agent="worker",
        body="STOP now",
        priority="URGENT",
        coalesce_key="build:main",
        operator_approved=True,
    )
    assert urgent["priority"] == "urgent"
    assert urgent["coalesce_key"] == "build:main"

    with pytest.raises(MailboxError):
        store.send_envelope(from_agent="lead", to_agent="worker", body="bad", priority="critical")
    with pytest.raises(MailboxError):
        store.send_envelope(from_agent="lead", to_agent="worker", body="bad", coalesce_key="bad\nkey")

    # Legacy v0.1.2 envelope without priority/coalesce_key fields loads seamlessly with defaults
    legacy_id = "20261118T120000000000Z_legacy01"
    legacy_path = store.boxes_dir / "worker" / "unread" / f"{legacy_id}.json"
    legacy_path.write_text(
        json.dumps(
            {
                "id": legacy_id,
                "thread_id": "thr_legacy",
                "in_reply_to": None,
                "from": "old-sender",
                "to": "worker",
                "reply_to": "old-sender",
                "subject": "Legacy",
                "body": "v0.1.2 format",
                "refs": [],
                "hop_count": 0,
                "max_hops": 1,
                "ack_required": True,
                "operator_approved": True,
                "attempts": 0,
                "max_attempts": 3,
                "created_at": "2026-11-18T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    urgent_only = store.list_inbox("worker", state="unread", priority="urgent")
    assert [m["id"] for m in urgent_only] == [urgent["id"]]
    normal_only = store.list_inbox("worker", state="unread", priority="normal")
    assert [m["id"] for m in normal_only] == [legacy_id]
    assert normal_only[0]["priority"] == "normal"
    assert normal_only[0]["coalesce_key"] is None

    rep = store.reply_message("worker", urgent["id"], body="acknowledged stop", priority="low", coalesce_key="ack:stop")
    assert rep["priority"] == "low"
    assert rep["coalesce_key"] == "ack:stop"
