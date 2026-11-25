from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aim_mailbox.dispatcher import pulse_mailbox, watch_mailbox
from aim_mailbox.drivers import (
    _resolve_agentapi_prefix,
    build_tmux_doorbell,
    build_wake_prompt,
    install_agy_sidecar,
    is_agy_session_idle,
    uninstall_agy_sidecar,
)
from aim_mailbox.store import MailboxError, MailboxStore


def _make_fake_cli(tmp_path: Path, name: str, py_body: str) -> Path:
    py_script = tmp_path / f"{name}_impl.py"
    py_script.write_text(py_body, encoding="utf-8")
    if os.name == "nt":
        wrapper = tmp_path / f"{name}.cmd"
        wrapper.write_text(f'@echo off\r\n"{sys.executable}" -X utf8 "{py_script}" %*\r\n', encoding="utf-8")
        return wrapper
    wrapper = tmp_path / name
    wrapper.write_text(f"#!{sys.executable}\n{py_body}", encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def test_pulse_quarantines_max_hops_without_invoking_driver(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    marker = tmp_path / "invoked.txt"
    store.register_agent(
        "peer-b",
        driver="command",
        command=[sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"],
    )
    store.send_envelope(
        from_agent="peer-a",
        to_agent="peer-b",
        body="ping pong loop",
        hop_count=2,
        max_hops=1,
    )

    summary = pulse_mailbox(store)
    assert len(summary["quarantined"]) == 1
    assert summary["dispatched"] == []
    assert not marker.exists()
    dead = store.list_inbox("peer-b", state="dead")
    assert len(dead) == 1
    assert "MAX_HOPS_EXCEEDED" in dead[0]["dead_reason"]


def test_dry_run_across_all_supported_drivers(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    configs = [
        ("a-sidecar", {"driver": "agy-desktop-sidecar", "session_id": "uuid-1"}),
        ("a-cli", {"driver": "agy-cli", "cwd": str(tmp_path)}),
        ("a-http", {"driver": "opencode-http", "http_url": "http://127.0.0.1:4096", "session_id": "ses_1"}),
        ("a-grok", {"driver": "grok-cli", "session_id": "grok-1", "cwd": str(tmp_path)}),
        ("a-codex", {"driver": "codex-cli", "session_id": "th-1"}),
        ("a-vscode", {"driver": "vscode-chat", "cwd": str(tmp_path)}),
        ("a-tmux", {"driver": "tmux", "tmux_session": "dev-pane"}),
        ("a-cmd", {"driver": "command", "command": ["echo", "{agent}", "{message_id}"]}),
    ]
    for aid, kwargs in configs:
        store.register_agent(aid, **kwargs)
        store.send_envelope(from_agent="sender", to_agent=aid, body=f"hello {aid}", operator_approved=True)

    summary = pulse_mailbox(store, dry_run=True)
    assert len(summary["dispatched"]) == len(configs)
    for item in summary["dispatched"]:
        assert item["result"]["status"] == "DRY_RUN"
    # Dry-run never mutates unread/
    for aid, _ in configs:
        assert len(store.list_inbox(aid, state="unread")) == 1


def test_agy_cli_driver_defers_when_busy_and_autopopulates_session_id_when_idle(tmp_path: Path) -> None:
    summary_db = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(str(summary_db))
    conn.execute(
        "CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, status TEXT, not_fully_idle INTEGER)"
    )
    conn.execute("INSERT INTO conversation_summaries VALUES ('uuid-busy', 'CASCADE_RUN_STATUS_RUNNING', 1)")
    conn.commit()
    conn.close()

    fake_agy = _make_fake_cli(
        tmp_path,
        "fake_agy",
        (
            "import json, sys\n"
            "print(json.dumps({'status': 'SUCCESS', 'conversation_id': 'uuid-new-777', 'argv': sys.argv[1:]}))\n"
        ),
    )

    store = MailboxStore(tmp_path / "mailbox")
    store.register_agent(
        "busy-worker",
        driver="agy-cli",
        binary=str(fake_agy),
        session_id="uuid-busy",
        summary_db=str(summary_db),
        cwd=str(tmp_path),
    )
    store.send_envelope(from_agent="lead", to_agent="busy-worker", body="wait until idle", operator_approved=True)

    s_busy = pulse_mailbox(store, agent_id="busy-worker")
    assert len(s_busy["deferred"]) == 1
    unread = store.list_inbox("busy-worker", state="unread")
    assert len(unread) == 1
    assert unread[0]["attempts"] == 0

    # Mark session idle, plus test new agent without session_id auto-persisting conversation_id
    conn = sqlite3.connect(str(summary_db))
    conn.execute(
        "UPDATE conversation_summaries SET status = 'CASCADE_RUN_STATUS_IDLE', not_fully_idle = 0 WHERE conversation_id = 'uuid-busy'"
    )
    conn.commit()
    conn.close()

    store.register_agent("fresh-worker", driver="agy-cli", binary=str(fake_agy), cwd=str(tmp_path))
    store.send_envelope(from_agent="lead", to_agent="fresh-worker", body="first boot", operator_approved=True)

    s_ok = pulse_mailbox(store)
    assert len(s_ok["dispatched"]) == 2
    fresh_cfg = store.get_agent("fresh-worker")
    assert fresh_cfg is not None
    assert fresh_cfg["session_id"] == "uuid-new-777"


def test_agy_desktop_sidecar_driver_and_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_agentapi = _make_fake_cli(
        tmp_path,
        "fake_agentapi",
        (
            "import json, sys\n"
            "print(json.dumps({'response': {'sendMessage': {'recipientId': sys.argv[2], 'content': sys.argv[3]}}}))\n"
        ),
    )
    store = MailboxStore(tmp_path / "mailbox")
    store.register_agent(
        "win-desktop",
        driver="agy-desktop-sidecar",
        binary=str(fake_agentapi),
        session_id="53ddacfd-0000-0000-0000-000000000001",
    )
    store.send_envelope(
        from_agent="wsl-joshua",
        to_agent="win-desktop",
        body="ping desktop",
        ack_required=False,
        operator_approved=True,
    )

    # Without ANTIGRAVITY_LS_ADDRESS, delivery fails cleanly and returns to unread/
    monkeypatch.delenv("ANTIGRAVITY_LS_ADDRESS", raising=False)
    s_fail = pulse_mailbox(store)
    assert len(s_fail["failed"]) == 1
    assert len(store.list_inbox("win-desktop", state="unread")) == 1

    # Inside Sidecar (ANTIGRAVITY_LS_ADDRESS present), succeeds and stays readable in claimed/ until read
    monkeypatch.setenv("ANTIGRAVITY_LS_ADDRESS", "127.0.0.1:55555")
    s_ok = pulse_mailbox(store)
    assert len(s_ok["dispatched"]) == 1
    assert len(store.list_inbox("win-desktop", state="claimed")) == 1
    assert len(store.list_inbox("win-desktop", state="active")) == 1
    assert len(store.list_inbox("win-desktop", state="done")) == 0

    read_msg = store.read_message("win-desktop")
    assert read_msg["body"] == "ping desktop"
    assert read_msg["state"] == "done"
    assert len(store.list_inbox("win-desktop", state="done")) == 1

    # Verify non-destructive sidecar installer & uninstaller
    cfg_dir = tmp_path / "gemini_config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "plugins": {"keep-me": {"version": "1.0"}},
                "sidecars": {"existing/sc": {"projectId": "proj-1", "schemaVersion": 1}},
                "userSettings": {"themeMode": "THEME_MODE_DARK"},
            }
        ),
        encoding="utf-8",
    )

    inst = install_agy_sidecar(store.root, config_dir=cfg_dir, sidecar_id="aim-mailbox")
    assert inst["installed"] is True
    sc_json = cfg_dir / "sidecars" / "aim-mailbox" / "sidecar.json"
    assert sc_json.is_file()
    sc_spec = json.loads(sc_json.read_text(encoding="utf-8"))
    assert sc_spec["agent_permissions"]["access_grants"] == [
        "command(agentapi send-message)",
        "command(agentapi get-conversation-metadata)",
    ]
    merged = json.loads((cfg_dir / "config.json").read_text(encoding="utf-8"))
    assert merged["plugins"]["keep-me"]["version"] == "1.0"
    assert merged["sidecars"]["existing/sc"]["projectId"] == "proj-1"
    assert merged["sidecars"]["aim-mailbox"]["enabled"] is True

    uninst = uninstall_agy_sidecar(config_dir=cfg_dir, sidecar_id="aim-mailbox")
    assert uninst["uninstalled"] is True
    after = json.loads((cfg_dir / "config.json").read_text(encoding="utf-8"))
    assert "aim-mailbox" not in after["sidecars"]
    assert "existing/sc" in after["sidecars"]
    assert not (cfg_dir / "sidecars" / "aim-mailbox").exists()


def test_opencode_http_driver_creates_session_and_posts_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            recorded.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": json.loads(raw),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if self.path == "/session":
                self.wfile.write(json.dumps({"id": "ses_live_42"}).encode("utf-8"))
            else:
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        monkeypatch.setenv("OPENCODE_SERVER_USERNAME", "admin")
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "s3cr3t")
        store = MailboxStore(tmp_path / "mailbox")
        store.register_agent("oc-worker", driver="opencode-http", http_url=f"http://127.0.0.1:{port}")
        store.send_envelope(from_agent="lead", to_agent="oc-worker", body="check endpoints", operator_approved=True)

        summary = pulse_mailbox(store)
        assert len(summary["dispatched"]) == 1
        assert [r["path"] for r in recorded] == ["/session", "/session/ses_live_42/message"]
        assert recorded[1]["body"]["parts"] == [{"type": "text", "text": recorded[1]["body"]["parts"][0]["text"]}]
        expected_auth = "Basic " + base64.b64encode(b"admin:s3cr3t").decode("ascii")
        assert recorded[0]["auth"] == expected_auth
        assert recorded[1]["auth"] == expected_auth
        saved_agent = store.get_agent("oc-worker")
        assert saved_agent is not None and saved_agent["session_id"] == "ses_live_42"
    finally:
        server.shutdown()
        server.server_close()


def test_opencode_http_default_username_captures_http_error_and_persists_session_before_msg_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[dict[str, Any]] = []

    class ErrorHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            recorded.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": json.loads(raw)})
            if self.path == "/session":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": "ses_keep_99"}).encode("utf-8"))
            else:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"name": "BadRequest", "data": {"message": "bad parts"}}).encode("utf-8"))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ErrorHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.delenv("OPENCODE_SERVER_USERNAME", raising=False)
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "pw")
        store = MailboxStore(tmp_path / "mailbox")
        store.register_agent("oc-err", driver="opencode-http", http_url=f"http://127.0.0.1:{port}")
        store.send_envelope(from_agent="lead", to_agent="oc-err", body="trigger 400", operator_approved=True)

        summary = pulse_mailbox(store)
        assert len(summary["failed"]) == 1
        assert "HTTP 400" in summary["failed"][0]["error"]
        assert "BadRequest" in summary["failed"][0]["error"]
        expected_auth = "Basic " + base64.b64encode(b"opencode:pw").decode("ascii")
        assert recorded[0]["auth"] == expected_auth
        # Session created before failing /message POST must be persisted to avoid orphan leaks on retry
        saved = store.get_agent("oc-err")
        assert saved is not None and saved["session_id"] == "ses_keep_99"
    finally:
        server.shutdown()
        server.server_close()


def test_tmux_driver_sends_enter_only_never_escape(tmp_path: Path) -> None:
    calls_log = tmp_path / "tmux_calls.jsonl"
    fake_tmux = _make_fake_cli(
        tmp_path,
        "fake_tmux",
        (
            "import json, sys\n"
            "from pathlib import Path\n"
            f"log_p = Path({str(calls_log)!r})\n"
            "args = sys.argv[1:]\n"
            "with log_p.open('a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps(args) + '\\n')\n"
            "if args and args[0] == 'capture-pane':\n"
            "    for line in log_p.read_text(encoding='utf-8').splitlines():\n"
            "        row = json.loads(line)\n"
            "        if row and row[0] == 'set-buffer':\n"
            "            print(row[-1])\n"
        ),
    )

    store = MailboxStore(tmp_path / "mailbox")
    store.register_agent("tmux-peer", driver="tmux", binary=str(fake_tmux), tmux_session="grok-audit")
    sent = store.send_envelope(
        from_agent="aim-joshua",
        to_agent="tmux-peer",
        body="Multi-line\nbody\nsafe in maildir",
        operator_approved=True,
    )

    summary = pulse_mailbox(store)
    assert len(summary["dispatched"]) == 1
    logged = [json.loads(ln) for ln in calls_log.read_text(encoding="utf-8").splitlines()]
    send_key_calls = [c for c in logged if c and c[0] == "send-keys"]
    assert len(send_key_calls) == 1
    assert "Enter" in send_key_calls[0]
    assert all("Escape" not in c for c in logged)
    # Multi-line body never pasted into tmux buffer — only 1-line doorbell
    set_buf_calls = [c for c in logged if c and c[0] == "set-buffer"]
    assert len(set_buf_calls) == 1
    assert "\n" not in set_buf_calls[0][-1]
    assert sent["id"] in set_buf_calls[0][-1]


def test_sidecar_traversal_and_prompt_field_validation_blocked(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "gemini_config"
    plugins_dir = cfg_dir / "plugins"
    plugins_dir.mkdir(parents=True)
    sentinel = plugins_dir / "keep.txt"
    sentinel.write_text("do not delete", encoding="utf-8")

    mb = tmp_path / "mailbox"
    for bad_sidecar in ("../plugins", "../../outside", "a/b", r"a\b"):
        with pytest.raises(MailboxError):
            install_agy_sidecar(mb, config_dir=cfg_dir, sidecar_id=bad_sidecar)
        with pytest.raises(MailboxError):
            uninstall_agy_sidecar(config_dir=cfg_dir, sidecar_id=bad_sidecar)

    assert sentinel.is_file()

    assert build_wake_prompt("worker", [{"id": "20261118T000000Z_12345678", "from": "sender"}], mb)
    with pytest.raises(MailboxError):
        build_wake_prompt("worker", [{"id": "bad\nid", "from": "sender"}], mb)
    with pytest.raises(MailboxError):
        build_wake_prompt("worker", [{"id": "20261118T000000Z_12345678", "from": "bad\nfrom"}], mb)
    with pytest.raises(MailboxError):
        build_tmux_doorbell("worker", {"id": "20261118T000000Z_12345678", "from": "sender", "reply_to": "../bad"}, mb)


def test_pulse_scales_agent_lock_ttl_with_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MailboxStore(tmp_path / "mailbox")
    store.register_agent("ttl-worker", driver="command", command=[sys.executable, "-c", "print('ok')"])
    store.send_envelope(from_agent="lead", to_agent="ttl-worker", body="check ttl", operator_approved=True)

    seen_ttls: list[float] = []
    orig_acquire = store.acquire_agent_lock

    def spy_acquire(aid: str, ttl_seconds: float = 600.0) -> str | None:
        seen_ttls.append(ttl_seconds)
        return orig_acquire(aid, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(store, "acquire_agent_lock", spy_acquire)
    pulse_mailbox(store, timeout=900.0)
    assert seen_ttls == [960.0]


def test_pulse_and_watch_isolate_missing_binary_timeout_and_mid_turn_ack_failure(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")

    # Agent 1 (alphabetically first): missing binary -> must fail cleanly, release claim, and NOT starve agent 3
    store.register_agent("alpha-missing", driver="agy-cli", binary=str(tmp_path / "does_not_exist_bin"), cwd=str(tmp_path))
    m_missing = store.send_envelope(from_agent="lead", to_agent="alpha-missing", body="missing bin", operator_approved=True)

    # Agent 2: driver times out -> must return structured FAILED receipt and release claim back to unread/
    slow_cli = _make_fake_cli(tmp_path, "slow_cli", "import time\ntime.sleep(2.0)\n")
    store.register_agent("beta-timeout", driver="grok-cli", binary=str(slow_cli), cwd=str(tmp_path))
    m_timeout = store.send_envelope(from_agent="lead", to_agent="beta-timeout", body="timeout me", operator_approved=True)

    # Agent 3: woken turn ACKs msg[0] mid-turn and then exits 1 (with msg[1] still claimed) -> no MailboxError crash!
    ack_then_fail = _make_fake_cli(
        tmp_path,
        "ack_then_fail",
        (
            "import os, sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / 'src')!r})\n"
            "from aim_mailbox.store import MailboxStore\n"
            "st = MailboxStore(os.environ['AIM_MAILBOX_DIR'])\n"
            "claimed = st.list_inbox('gamma-midack', state='claimed')\n"
            "if claimed:\n"
            "    st.ack_message('gamma-midack', claimed[0]['id'], note='handled first')\n"
            "sys.exit(1)\n"
        ),
    )
    store.register_agent(
        "gamma-midack",
        driver="command",
        command=[str(ack_then_fail)],
        cwd=str(tmp_path),
    )
    m_mid0 = store.send_envelope(from_agent="lead", to_agent="gamma-midack", body="first", operator_approved=True)
    time.sleep(0.01)
    m_mid1 = store.send_envelope(from_agent="lead", to_agent="gamma-midack", body="second", operator_approved=True)

    # Agent 4 (alphabetically last): healthy agent -> must STILL be dispatched!
    healthy_marker = tmp_path / "healthy.txt"
    store.register_agent(
        "zeta-healthy",
        driver="command",
        command=[sys.executable, "-c", f"from pathlib import Path; Path({str(healthy_marker)!r}).write_text('ok')"],
    )
    store.send_envelope(from_agent="lead", to_agent="zeta-healthy", body="still run", operator_approved=True)

    summary = pulse_mailbox(store, timeout=0.2)
    assert len(summary["failed"]) == 3
    assert len(summary["dispatched"]) == 1
    assert summary["dispatched"][0]["agent_id"] == "zeta-healthy"
    assert healthy_marker.is_file()

    # Claims on failing drivers were returned to unread/, and mid-turn ACK stayed in done/ without resurrection
    assert [m["id"] for m in store.list_inbox("alpha-missing", state="unread")] == [m_missing["id"]]
    assert [m["id"] for m in store.list_inbox("beta-timeout", state="unread")] == [m_timeout["id"]]
    assert [m["id"] for m in store.list_inbox("gamma-midack", state="done")] == [m_mid0["id"]]
    assert [m["id"] for m in store.list_inbox("gamma-midack", state="unread")] == [m_mid1["id"]]
    assert store.list_inbox("gamma-midack", state="claimed") == []

    # watch_mailbox must survive cycle exceptions without crashing
    def boom_load() -> dict[str, Any]:
        raise RuntimeError("transient watch failure")

    orig_load = store.load_agents
    store.load_agents = boom_load  # type: ignore[method-assign]
    assert watch_mailbox(store, interval=0.1, max_cycles=1) == 0
    store.load_agents = orig_load  # type: ignore[method-assign]


def test_woken_active_inbox_multi_message_tmux_command_and_swallowed_enter(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path / "mailbox")

    # 1. Multi-message prompt must NOT truncate IDs at 5 and must instruct --state active
    envs = [
        store.send_envelope(from_agent="lead", to_agent="batch-agent", body=f"msg {i}")
        for i in range(6)
    ]
    prompt = build_wake_prompt("batch-agent", envs, store.root)
    assert "--state active" in prompt
    for env in envs:
        assert env["id"] in prompt

    # 2. Multi-message tmux wake includes exact '=<session>', 'set-buffer --', and ALL IDs
    calls_log = tmp_path / "tmux_multi.jsonl"
    fake_tmux = _make_fake_cli(
        tmp_path,
        "fake_tmux_multi",
        (
            "import json, sys\n"
            "from pathlib import Path\n"
            f"log_p = Path({str(calls_log)!r})\n"
            "args = sys.argv[1:]\n"
            "with log_p.open('a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps(args) + '\\n')\n"
            "if args and args[0] == 'capture-pane':\n"
            "    for line in log_p.read_text(encoding='utf-8').splitlines():\n"
            "        row = json.loads(line)\n"
            "        if row and row[0] == 'set-buffer':\n"
            "            print(row[-1])\n"
        ),
    )
    store.register_agent("tmux-batch", driver="tmux", binary=str(fake_tmux), tmux_session="dev-pane")
    tm1 = store.send_envelope(from_agent="lead", to_agent="tmux-batch", body="first", operator_approved=True)
    time.sleep(0.01)
    tm2 = store.send_envelope(from_agent="lead", to_agent="tmux-batch", body="second", operator_approved=True)

    s_tmux = pulse_mailbox(store, agent_id="tmux-batch")
    assert len(s_tmux["dispatched"]) == 1
    logged = [json.loads(ln) for ln in calls_log.read_text(encoding="utf-8").splitlines()]
    assert any(c[:3] == ["has-session", "-t", "=dev-pane"] for c in logged)
    set_buf = next(c for c in logged if c[0] == "set-buffer")
    assert "--" in set_buf
    assert tm1["id"] in set_buf[-1] and tm2["id"] in set_buf[-1]
    assert len(store.list_inbox("tmux-batch", state="active")) == 2

    # 3. Unsubmitted composer line in capture-pane fails verification cleanly
    swallowed_tmux = _make_fake_cli(
        tmp_path,
        "swallowed_tmux",
        (
            "import json, sys\n"
            "from pathlib import Path\n"
            f"log_p = Path({str(tmp_path / 'swallow.jsonl')!r})\n"
            "args = sys.argv[1:]\n"
            "with log_p.open('a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps(args) + '\\n')\n"
            "if args and args[0] == 'capture-pane':\n"
            "    for line in log_p.read_text(encoding='utf-8').splitlines():\n"
            "        row = json.loads(line)\n"
            "        if row and row[0] == 'set-buffer':\n"
            "            print('❯ ' + row[-1])\n"
        ),
    )
    store.register_agent("tmux-swallow", driver="tmux", binary=str(swallowed_tmux), tmux_session="swallow")
    store.send_envelope(from_agent="lead", to_agent="tmux-swallow", body="swallow check", operator_approved=True)
    s_swallow = pulse_mailbox(store, agent_id="tmux-swallow")
    assert len(s_swallow["failed"]) == 1
    assert "unsubmitted" in s_swallow["failed"][0]["error"]

    # 4. Multi-message command wake exposes {message_ids}, AIM_MAIL_MESSAGE_IDS, and AIM_MAIL_MESSAGE_COUNT
    cmd_out = tmp_path / "cmd_batch.json"
    store.register_agent(
        "cmd-batch",
        driver="command",
        command=[
            sys.executable,
            "-c",
            (
                "import json, os, sys; from pathlib import Path; "
                f"Path({str(cmd_out)!r}).write_text(json.dumps({{'arg': sys.argv[1], 'ids': os.environ['AIM_MAIL_MESSAGE_IDS'], 'count': os.environ['AIM_MAIL_MESSAGE_COUNT']}}))"
            ),
            "{message_ids}",
        ],
    )
    cm1 = store.send_envelope(from_agent="lead", to_agent="cmd-batch", body="one", operator_approved=True)
    time.sleep(0.01)
    cm2 = store.send_envelope(from_agent="lead", to_agent="cmd-batch", body="two", operator_approved=True)
    s_cmd = pulse_mailbox(store, agent_id="cmd-batch")
    assert len(s_cmd["dispatched"]) == 1
    batch_data = json.loads(cmd_out.read_text(encoding="utf-8"))
    expected_ids = f"{cm1['id']},{cm2['id']}"
    assert batch_data == {"arg": expected_ids, "ids": expected_ids, "count": "2"}


def test_agentapi_stale_wrapper_agy_idle_presence_lock_and_codex_session_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Stale agentapi wrapper pointing to deleted agy.real.*.old must fall back to healthy agy.real
    fake_home = tmp_path / "home"
    cli_bin = fake_home / ".gemini" / "antigravity-cli" / "bin"
    cli_bin.mkdir(parents=True)
    stale = cli_bin / "agentapi"
    stale.write_text("#!/bin/sh\nexec \"/nonexistent/agy.real.123.old\" agentapi \"$@\"\n", encoding="utf-8")
    local_bin = fake_home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    healthy_real = local_bin / "agy.real"
    healthy_real.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert _resolve_agentapi_prefix(home_dir=fake_home) == [str(healthy_real), "agentapi"]

    # 2. presence/<uuid>.lock and DB lock contention must return False (not idle)
    db_root = fake_home / ".gemini" / "antigravity-cli"
    summary_db = db_root / "conversation_summaries.db"
    conn = sqlite3.connect(str(summary_db))
    conn.execute(
        "CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, status TEXT, not_fully_idle INTEGER)"
    )
    conn.execute("INSERT INTO conversation_summaries VALUES ('sid-1', 'CASCADE_RUN_STATUS_IDLE', 0)")
    conn.commit()
    conn.close()
    assert is_agy_session_idle("sid-1", summary_db=str(summary_db)) is True

    presence = db_root / "presence"
    presence.mkdir(parents=True)
    (presence / "sid-1.lock").write_text("locked", encoding="utf-8")
    assert is_agy_session_idle("sid-1", summary_db=str(summary_db)) is False
    (presence / "sid-1.lock").unlink()

    def raise_locked(*_args: Any, **_kwargs: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", raise_locked)
    assert is_agy_session_idle("sid-1", summary_db=str(summary_db)) is False
    monkeypatch.undo()

    # 3. codex-cli uses --json + --add-dir on first run and auto-saves thread_id
    fake_codex = _make_fake_cli(
        tmp_path,
        "fake_codex",
        (
            "import json, sys\n"
            "print(json.dumps({'type': 'thread.started', 'thread_id': 'th_codex_99', 'argv': sys.argv[1:]}))\n"
        ),
    )
    store = MailboxStore(tmp_path / "mailbox")
    store.register_agent("codex-worker", driver="codex-cli", binary=str(fake_codex), cwd=str(tmp_path))
    store.send_envelope(from_agent="lead", to_agent="codex-worker", body="run codex", operator_approved=True)
    s_codex = pulse_mailbox(store, agent_id="codex-worker")
    assert len(s_codex["dispatched"]) == 1
    cmd_used = s_codex["dispatched"][0]["result"]["command"]
    assert "--json" in cmd_used and "--add-dir" in cmd_used
    saved_codex = store.get_agent("codex-worker")
    assert saved_codex is not None and saved_codex["session_id"] == "th_codex_99"


def test_operator_approved_gate_and_configurable_agy_skip_permissions(tmp_path: Path) -> None:
    fake_agy = _make_fake_cli(
        tmp_path,
        "fake_agy_perm",
        "import json, sys\nprint(json.dumps({'status': 'SUCCESS', 'argv': sys.argv[1:]}))\n",
    )
    store = MailboxStore(tmp_path / "mailbox")
    store.register_agent("guarded", driver="agy-cli", binary=str(fake_agy), cwd=str(tmp_path))

    # Unapproved initial send is blocked from automated pulse wake
    store.send_envelope(from_agent="rogue", to_agent="guarded", body="unapproved", operator_approved=False)
    s_blocked = pulse_mailbox(store, agent_id="guarded")
    assert s_blocked["dispatched"] == []
    assert any(d["reason"] == "OPERATOR_APPROVAL_REQUIRED" for d in s_blocked["deferred"])
    assert len(store.list_inbox("guarded", state="unread")) == 1

    # Approved send dispatches WITHOUT --dangerously-skip-permissions unless explicitly configured
    store.send_envelope(from_agent="lead", to_agent="guarded", body="approved", operator_approved=True)
    s_ok = pulse_mailbox(store, agent_id="guarded")
    assert len(s_ok["dispatched"]) == 1
    cmd_default = s_ok["dispatched"][0]["result"]["command"]
    assert "--dangerously-skip-permissions" not in cmd_default

    # Explicit per-agent dangerously_skip_permissions=True and allow_unapproved=True
    store.register_agent(
        "trusted",
        driver="agy-cli",
        binary=str(fake_agy),
        cwd=str(tmp_path),
        allow_unapproved=True,
        dangerously_skip_permissions=True,
    )
    store.send_envelope(from_agent="peer", to_agent="trusted", body="allowed", operator_approved=False)
    s_trusted = pulse_mailbox(store, agent_id="trusted")
    assert len(s_trusted["dispatched"]) == 1
    assert "--dangerously-skip-permissions" in s_trusted["dispatched"][0]["result"]["command"]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux binary not available on this host")
def test_real_tmux_target_pane_delivery_and_exact_session_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess as _sp
    import uuid as _uuid

    tmux_tmp = tmp_path / f"tmux_sock_{_uuid.uuid4().hex[:8]}"
    tmux_tmp.mkdir(mode=0o700)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_tmp))

    try:
        # Start two sessions where one name is a prefix of the other to verify exact '=' targeting
        _sp.run(["tmux", "new-session", "-d", "-s", "probe-extra", "cat"], check=True, timeout=10)
        _sp.run(["tmux", "new-session", "-d", "-s", "probe", "cat"], check=True, timeout=10)

        sock_chk = _sp.run(
            ["tmux", "display-message", "-t", "=probe:", "-p", "#{socket_path}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        assert Path(sock_chk.stdout.strip()).resolve().is_relative_to(tmux_tmp.resolve())

        store = MailboxStore(tmp_path / "mailbox")
        store.register_agent("probe", driver="tmux", tmux_session="probe")
        sent = store.send_envelope(
            from_agent="lead",
            to_agent="probe",
            body="real tmux integration test",
            operator_approved=True,
        )

        summary = pulse_mailbox(store, agent_id="probe")
        assert summary["failed"] == []
        assert len(summary["dispatched"]) == 1
        assert summary["dispatched"][0]["result"]["status"] == "DELIVERED"

        cap_extra = _sp.run(
            ["tmux", "capture-pane", "-t", "=probe-extra:", "-p"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert sent["id"] not in cap_extra.stdout
    finally:
        if os.environ.get("TMUX") is None and os.environ.get("TMUX_TMPDIR") == str(tmux_tmp):
            sock_chk = _sp.run(
                ["tmux", "display-message", "-p", "#{socket_path}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            sock_p = sock_chk.stdout.strip()
            if sock_chk.returncode == 0 and sock_p and Path(sock_p).resolve().is_relative_to(tmux_tmp.resolve()):
                _sp.run(["tmux", "kill-server"], capture_output=True, timeout=10)


def test_is_healthy_agentapi_wrapper_rejects_shebang_on_windows_and_posix_abs_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aim_mailbox.drivers import _is_healthy_agentapi_wrapper

    shebang_shim = tmp_path / "agentapi"
    shebang_shim.write_text('#!/bin/sh\nexec "/nonexistent/agy.real" agentapi "$@"\n', encoding="utf-8")
    monkeypatch.setattr(os, "name", "nt")
    assert _is_healthy_agentapi_wrapper(shebang_shim) is False

    monkeypatch.setattr(os, "name", "posix")
    assert _is_healthy_agentapi_wrapper(shebang_shim) is False


def test_priority_lanes_coalesce_key_and_safety_gates(tmp_path: Path) -> None:
    import datetime as dt
    from aim_mailbox.store import utc_now

    recorded_cmds: Path = tmp_path / "wake_batches.jsonl"
    fake_cli = _make_fake_cli(
        tmp_path,
        "fake_prio",
        (
            "import json, os, sys\n"
            "from pathlib import Path\n"
            f"Path({str(recorded_cmds)!r}).open('a', encoding='utf-8').write(json.dumps({{'ids': os.environ['AIM_MAIL_MESSAGE_IDS'], 'prompt': os.environ['AIM_MAIL_WAKE_PROMPT']}}) + '\\n')\n"
        ),
    )

    store = MailboxStore(tmp_path / "mailbox")
    store.register_agent("worker", driver="command", command=[str(fake_cli)])

    # 1. Urgent must NEVER bypass operator_approved or max_hops
    unapproved_urgent = store.send_envelope(
        from_agent="rogue",
        to_agent="worker",
        body="unapproved urgent",
        priority="urgent",
        operator_approved=False,
    )
    overhop_urgent = store.send_envelope(
        from_agent="rogue",
        to_agent="worker",
        body="overhop urgent",
        priority="urgent",
        hop_count=2,
        max_hops=1,
        operator_approved=True,
    )
    s_gate = pulse_mailbox(store, agent_id="worker")
    assert s_gate["dispatched"] == []
    assert any(d["message_id"] == unapproved_urgent["id"] and d["reason"] == "OPERATOR_APPROVAL_REQUIRED" for d in s_gate["deferred"])
    assert any(q["message_id"] == overhop_urgent["id"] and q["reason"] == "MAX_HOPS_EXCEEDED" for q in s_gate["quarantined"])
    # Clean out unapproved urgent so next phase tests clean delivery
    store.ack_message("worker", unapproved_urgent["id"])

    # 2. Priority ordering (urgent before normal), coalesce_key retirement without stranding ack_required=True, and low batch window
    n1 = store.send_envelope(
        from_agent="ci", to_agent="worker", body="build 1",
        priority="normal", coalesce_key="ci:main", ack_required=True, operator_approved=True,
    )
    time.sleep(0.01)
    n2 = store.send_envelope(
        from_agent="ci", to_agent="worker", body="build 2",
        priority="normal", coalesce_key="ci:main", ack_required=True, operator_approved=True,
    )
    time.sleep(0.01)
    u1 = store.send_envelope(
        from_agent="lead", to_agent="worker", body="STOP",
        priority="urgent", operator_approved=True,
    )
    l1 = store.send_envelope(
        from_agent="metrics", to_agent="worker", body="low 1",
        priority="low", operator_approved=True,
    )
    l2 = store.send_envelope(
        from_agent="metrics", to_agent="worker", body="low 2",
        priority="low", operator_approved=True,
    )

    s_run = pulse_mailbox(store, agent_id="worker", low_batch_window=30.0)
    assert len(s_run["dispatched"]) == 1
    disp = s_run["dispatched"][0]
    # Urgent u1 comes BEFORE coalesced leader n2; n1 was retired to done/
    assert disp["message_ids"] == [u1["id"], n2["id"]]
    assert disp["coalesced_superseded_ids"] == [n1["id"]]
    assert any(d["message_id"] == l1["id"] and d["reason"] == "LOW_PRIORITY_BATCH_WINDOW" for d in s_run["deferred"])
    assert any(d["message_id"] == l2["id"] and d["reason"] == "LOW_PRIORITY_BATCH_WINDOW" for d in s_run["deferred"])

    # Verify prompt carried [URGENT] and leader n2 recorded coalesced_ids while n1 moved to done/ with coalesced_into
    batches = [json.loads(ln) for ln in recorded_cmds.read_text(encoding="utf-8").splitlines()]
    assert batches[0]["ids"] == f"{u1['id']},{n2['id']}"
    assert "[URGENT]" in batches[0]["prompt"]

    done_msgs = {m["id"]: m for m in store.list_inbox("worker", state="done")}
    assert n1["id"] in done_msgs
    assert done_msgs[n1["id"]]["coalesced_into"] == n2["id"]
    claimed_msgs = {m["id"]: m for m in store.list_inbox("worker", state="claimed")}
    assert claimed_msgs[n2["id"]]["coalesced_ids"] == [n1["id"]]
    assert claimed_msgs[n2["id"]]["ack_required"] is True

    # 3. Once low_batch_window max-age floor elapses, pending low envelopes dispatch together
    s_low_ready = pulse_mailbox(store, agent_id="worker", low_batch_window=30.0, now=utc_now() + dt.timedelta(seconds=35))
    assert len(s_low_ready["dispatched"]) == 1
    assert s_low_ready["dispatched"][0]["message_ids"] == [l1["id"], l2["id"]]

    # 4. If driver fails/defers, coalesced envelopes are NOT prematurely retired to done/
    store.register_agent("flaky", driver="command", command=[sys.executable, "-c", "import sys; sys.exit(1)"])
    f1 = store.send_envelope(from_agent="a", to_agent="flaky", body="c1", coalesce_key="k1", operator_approved=True)
    time.sleep(0.01)
    f2 = store.send_envelope(from_agent="a", to_agent="flaky", body="c2", coalesce_key="k1", operator_approved=True)
    s_fail = pulse_mailbox(store, agent_id="flaky")
    assert len(s_fail["failed"]) == 1
    assert store.list_inbox("flaky", state="done") == []
    assert [m["id"] for m in store.list_inbox("flaky", state="unread")] == [f1["id"], f2["id"]]

