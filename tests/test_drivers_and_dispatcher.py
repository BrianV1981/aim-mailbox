from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aim_mailbox.dispatcher import pulse_mailbox
from aim_mailbox.drivers import install_agy_sidecar, uninstall_agy_sidecar
from aim_mailbox.store import MailboxStore


def _make_fake_cli(tmp_path: Path, name: str, py_body: str) -> Path:
    py_script = tmp_path / f"{name}_impl.py"
    py_script.write_text(py_body, encoding="utf-8")
    if os.name == "nt":
        wrapper = tmp_path / f"{name}.cmd"
        wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{py_script}" %*\r\n', encoding="utf-8")
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
        store.send_envelope(from_agent="sender", to_agent=aid, body=f"hello {aid}")

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
    store.send_envelope(from_agent="lead", to_agent="busy-worker", body="wait until idle")

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
    store.send_envelope(from_agent="lead", to_agent="fresh-worker", body="first boot")

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
    store.send_envelope(from_agent="wsl-joshua", to_agent="win-desktop", body="ping desktop", ack_required=False)

    # Without ANTIGRAVITY_LS_ADDRESS, delivery fails cleanly and returns to unread/
    monkeypatch.delenv("ANTIGRAVITY_LS_ADDRESS", raising=False)
    s_fail = pulse_mailbox(store)
    assert len(s_fail["failed"]) == 1
    assert len(store.list_inbox("win-desktop", state="unread")) == 1

    # Inside Sidecar (ANTIGRAVITY_LS_ADDRESS present), succeeds and auto-completes (ack_required=False)
    monkeypatch.setenv("ANTIGRAVITY_LS_ADDRESS", "127.0.0.1:55555")
    s_ok = pulse_mailbox(store)
    assert len(s_ok["dispatched"]) == 1
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
    assert (cfg_dir / "sidecars" / "aim-mailbox" / "sidecar.json").is_file()
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
        store.send_envelope(from_agent="lead", to_agent="oc-worker", body="check endpoints")

        summary = pulse_mailbox(store)
        assert len(summary["dispatched"]) == 1
        assert [r["path"] for r in recorded] == ["/session", "/session/ses_live_42/message"]
        expected_auth = "Basic " + base64.b64encode(b"admin:s3cr3t").decode("ascii")
        assert recorded[0]["auth"] == expected_auth
        assert recorded[1]["auth"] == expected_auth
        saved_agent = store.get_agent("oc-worker")
        assert saved_agent is not None and saved_agent["session_id"] == "ses_live_42"
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
    sent = store.send_envelope(from_agent="aim-joshua", to_agent="tmux-peer", body="Multi-line\nbody\nsafe in maildir")

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

