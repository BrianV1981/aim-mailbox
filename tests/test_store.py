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
    validate_message_id,
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
