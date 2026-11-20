from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .store import MailboxError, utc_iso, write_json_atomic

DRIVER_NAMES = (
    "none",
    "agy-desktop-sidecar",
    "agy-cli",
    "opencode-http",
    "grok-cli",
    "codex-cli",
    "vscode-chat",
    "tmux",
    "command",
)


def build_wake_prompt(agent_id: str, envelopes: list[dict[str, Any]], mailbox_dir: Path) -> str:
    ids = ", ".join(str(e["id"]) for e in envelopes[:5])
    senders = ", ".join(sorted({str(e.get("from", "unknown")) for e in envelopes}))
    count = len(envelopes)
    return (
        f"[AIM-MAILBOX WAKEUP] Agent '{agent_id}' has {count} unread message(s) "
        f"from {senders} (id: {ids}). "
        f"Run `aim-mail --mailbox-dir \"{mailbox_dir}\" inbox {agent_id}`, "
        f"read each message (`aim-mail read {agent_id} <id>`), complete authorized tasks, "
        f"and acknowledge (`aim-mail reply` or `aim-mail ack {agent_id} <id>`)."
    )


def build_tmux_doorbell(agent_id: str, envelope: dict[str, Any], mailbox_dir: Path) -> str:
    msg_id = str(envelope["id"])
    sender = str(envelope.get("from", "peer"))
    reply_to = str(envelope.get("reply_to") or sender)
    return (
        f"[AIM-MAIL from:{sender} reply_to:{reply_to} id:{msg_id}] "
        f"Unread mail waiting — run: aim-mail --mailbox-dir \"{mailbox_dir}\" read {agent_id} {msg_id}"
    )


def is_agy_session_idle(session_id: str, summary_db: str | None = None, home_dir: Path | None = None) -> bool:
    candidates: list[Path] = []
    if summary_db:
        candidates.append(Path(summary_db).expanduser())
    else:
        home = home_dir or Path.home()
        candidates.extend(
            [
                home / ".gemini" / "antigravity-cli" / "conversation_summaries.db",
                home / ".gemini" / "antigravity" / "conversation_summaries.db",
            ]
        )

    for db_path in candidates:
        if not db_path.is_file():
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True, timeout=3.0)
            row = conn.execute(
                "SELECT status, not_fully_idle FROM conversation_summaries WHERE conversation_id = ?",
                (session_id,),
            ).fetchone()
            conn.close()
            if row is not None:
                status_val = str(row[0] or "")
                busy_flag = int(row[1] or 0)
                return status_val == "CASCADE_RUN_STATUS_IDLE" and busy_flag == 0
        except sqlite3.Error:
            continue
    return True


def _resolve_agentapi_prefix(binary_override: str | None = None, home_dir: Path | None = None) -> list[str]:
    if binary_override:
        if binary_override.lower().endswith("language_server.exe") or binary_override.endswith("agy.real"):
            return [binary_override, "agentapi"]
        return [binary_override]

    home = home_dir or Path.home()
    win_ls = (
        home
        / "AppData"
        / "Local"
        / "Programs"
        / "Antigravity"
        / "resources"
        / "bin"
        / "language_server.exe"
    )
    if win_ls.is_file():
        return [str(win_ls), "agentapi"]

    which_bin = shutil.which("agentapi")
    if which_bin and not which_bin.lower().endswith(".bat"):
        return [which_bin]

    for cand in (
        home / ".gemini" / "antigravity" / "bin" / "agentapi.bat",
        home / ".gemini" / "antigravity" / "bin" / "agentapi",
        home / ".gemini" / "antigravity-cli" / "bin" / "agentapi",
    ):
        if cand.is_file():
            return [str(cand)]
    return [which_bin or "agentapi"]


def _extract_json_dict(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def dispatch_wake(
    agent_cfg: dict[str, Any],
    envelopes: list[dict[str, Any]],
    *,
    mailbox_dir: Path,
    dry_run: bool = False,
    timeout: float = 300.0,
) -> dict[str, Any]:
    driver = str(agent_cfg.get("driver") or "none").strip()
    if driver not in DRIVER_NAMES:
        raise MailboxError(f"Unsupported driver {driver!r}; valid drivers: {', '.join(DRIVER_NAMES)}.")

    agent_id = str(agent_cfg["agent_id"])
    prompt = build_wake_prompt(agent_id, envelopes, mailbox_dir)
    cwd = agent_cfg.get("cwd") or None
    session_id = str(agent_cfg.get("session_id") or "").strip() or None

    if driver == "none":
        return {
            "status": "SKIPPED_PASSIVE",
            "driver": driver,
            "delivered_at": utc_iso(),
        }

    if driver == "agy-desktop-sidecar":
        if not session_id:
            return {
                "status": "FAILED",
                "driver": driver,
                "returncode": 1,
                "stderr": f"Agent '{agent_id}' with driver 'agy-desktop-sidecar' requires --session-id (<conversation_id>).",
                "delivered_at": utc_iso(),
            }
        prefix = _resolve_agentapi_prefix(agent_cfg.get("binary"))
        cmd = [*prefix, "send-message", session_id, prompt]
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": cmd, "delivered_at": utc_iso()}
        if not os.environ.get("ANTIGRAVITY_LS_ADDRESS"):
            return {
                "status": "FAILED",
                "driver": driver,
                "command": cmd,
                "returncode": 1,
                "stderr": (
                    "ANTIGRAVITY_LS_ADDRESS is not set. Run `aim-mail watch` inside an Antigravity Desktop "
                    "Sidecar (`aim-mail install-agy-sidecar`)."
                ),
                "delivered_at": utc_iso(),
            }
        res = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "status": "DELIVERED" if res.returncode == 0 else "FAILED",
            "driver": driver,
            "command": cmd,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "delivered_at": utc_iso(),
        }

    if driver == "agy-cli":
        if session_id and not is_agy_session_idle(session_id, summary_db=agent_cfg.get("summary_db")):
            return {
                "status": "BUSY_DEFERRED",
                "driver": driver,
                "session_id": session_id,
                "stderr": f"Session {session_id} is currently active (not_fully_idle); deferring wake.",
                "delivered_at": utc_iso(),
            }
        agy_bin = agent_cfg.get("binary") or shutil.which("agy") or "agy"
        cmd = [str(agy_bin), "--output-format", "json", "--dangerously-skip-permissions"]
        if session_id:
            cmd.extend(["--conversation", session_id])
        cmd.extend(["-p", prompt])
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": cmd, "delivered_at": utc_iso()}
        res = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        parsed = _extract_json_dict(res.stdout) or {}
        discovered_sid = str(parsed.get("conversation_id") or session_id or "").strip() or None
        return {
            "status": "DELIVERED" if res.returncode == 0 else "FAILED",
            "driver": driver,
            "command": cmd,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "session_id": discovered_sid,
            "delivered_at": utc_iso(),
        }

    if driver == "opencode-http":
        base_url = str(agent_cfg.get("http_url") or "http://127.0.0.1:4096").rstrip("/")
        headers = {"Content-Type": "application/json"}
        user = os.environ.get("OPENCODE_SERVER_USERNAME")
        pwd = os.environ.get("OPENCODE_SERVER_PASSWORD")
        if user and pwd is not None:
            tok = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {tok}"

        if dry_run:
            target_ep = f"{base_url}/session/{session_id or '<auto>'}/message"
            return {"status": "DRY_RUN", "driver": driver, "command": [target_ep], "delivered_at": utc_iso()}

        try:
            active_sid = session_id
            if not active_sid:
                create_req = urllib.request.Request(
                    f"{base_url}/session",
                    data=json.dumps({"title": f"aim-mailbox:{agent_id}"}).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(create_req, timeout=min(15.0, timeout)) as resp:
                    created = json.loads(resp.read().decode("utf-8"))
                    active_sid = str(created.get("id") or "").strip()
                if not active_sid:
                    return {
                        "status": "FAILED",
                        "driver": driver,
                        "returncode": 1,
                        "stderr": "OpenCode POST /session did not return a session 'id'.",
                        "delivered_at": utc_iso(),
                    }

            msg_url = f"{base_url}/session/{active_sid}/message"
            msg_req = urllib.request.Request(
                msg_url,
                data=json.dumps({"prompt": prompt}).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(msg_req, timeout=timeout) as resp:
                body_text = resp.read().decode("utf-8", errors="replace")
                status_code = resp.status
            return {
                "status": "DELIVERED" if 200 <= status_code < 300 else "FAILED",
                "driver": driver,
                "command": [msg_url],
                "returncode": 0 if 200 <= status_code < 300 else 1,
                "stdout": body_text,
                "stderr": "",
                "session_id": active_sid,
                "delivered_at": utc_iso(),
            }
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {
                "status": "FAILED",
                "driver": driver,
                "command": [base_url],
                "returncode": 1,
                "stdout": "",
                "stderr": str(exc),
                "delivered_at": utc_iso(),
            }

    if driver == "grok-cli":
        grok_bin = agent_cfg.get("binary") or shutil.which("grok") or "grok"
        cmd = [str(grok_bin), "--no-auto-update", "--output-format", "json"]
        if session_id:
            cmd.extend(["--resume", session_id])
        if cwd:
            cmd.extend(["--cwd", str(cwd)])
        cmd.extend(["-p", prompt])
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": cmd, "delivered_at": utc_iso()}
        res = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "status": "DELIVERED" if res.returncode == 0 else "FAILED",
            "driver": driver,
            "command": cmd,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "session_id": session_id,
            "delivered_at": utc_iso(),
        }

    if driver == "codex-cli":
        codex_bin = agent_cfg.get("binary") or shutil.which("codex") or "codex"
        if session_id:
            cmd = [str(codex_bin), "exec", "resume", session_id, prompt]
        else:
            cmd = [str(codex_bin), "exec", prompt]
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": cmd, "delivered_at": utc_iso()}
        res = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "status": "DELIVERED" if res.returncode == 0 else "FAILED",
            "driver": driver,
            "command": cmd,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "delivered_at": utc_iso(),
        }

    if driver == "vscode-chat":
        code_bin = agent_cfg.get("binary") or shutil.which("code") or "code"
        cmd = [str(code_bin), "chat", "-m", "agent", "-r", prompt]
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": cmd, "delivered_at": utc_iso()}
        res = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "status": "DELIVERED" if res.returncode == 0 else "FAILED",
            "driver": driver,
            "command": cmd,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "delivered_at": utc_iso(),
        }

    if driver == "tmux":
        tmux_bin = agent_cfg.get("binary") or shutil.which("tmux") or "tmux"
        target_session = str(agent_cfg.get("tmux_session") or agent_id).strip()
        doorbell = build_tmux_doorbell(agent_id, envelopes[0], mailbox_dir)
        msg_id = str(envelopes[0]["id"])
        buf_name = f"aim_mail_{msg_id[-12:]}"
        plan = [
            [str(tmux_bin), "has-session", "-t", target_session],
            [str(tmux_bin), "set-buffer", "-b", buf_name, doorbell],
            [str(tmux_bin), "paste-buffer", "-b", buf_name, "-d", "-p", "-t", target_session],
            [str(tmux_bin), "send-keys", "-t", target_session, "Enter"],
        ]
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": plan[-1], "doorbell": doorbell, "delivered_at": utc_iso()}

        chk = subprocess.run(plan[0], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        if chk.returncode != 0:
            return {
                "status": "FAILED",
                "driver": driver,
                "command": plan[0],
                "returncode": chk.returncode,
                "stderr": chk.stderr or f"tmux session '{target_session}' not found.",
                "delivered_at": utc_iso(),
            }
        subprocess.run(plan[1], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=10)
        subprocess.run(plan[2], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=10)
        time.sleep(0.15)
        # Submit with Enter ONLY (never Escape, preventing multi-line/paste wipe on AGY & Grok)
        sub = subprocess.run(plan[3], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        time.sleep(0.4)
        cap = subprocess.run(
            [str(tmux_bin), "capture-pane", "-t", target_session, "-p", "-J", "-S", "-120"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        verified = (sub.returncode == 0) and (msg_id in cap.stdout)
        return {
            "status": "DELIVERED" if verified else "FAILED",
            "driver": driver,
            "command": plan[3],
            "returncode": 0 if verified else 2,
            "stdout": cap.stdout[-600:] if cap.stdout else "",
            "stderr": "" if verified else "VERIFY_FAIL: message ID needle not found in target tmux pane capture.",
            "delivered_at": utc_iso(),
        }

    # driver == "command" (Custom hook / Lars vision GUI adapter)
    raw_template = agent_cfg.get("command")
    if not isinstance(raw_template, list) or not raw_template:
        return {
            "status": "FAILED",
            "driver": driver,
            "returncode": 1,
            "stderr": f"Agent '{agent_id}' with driver 'command' requires a non-empty command list (--command-json).",
            "delivered_at": utc_iso(),
        }
    first_id = str(envelopes[0]["id"])
    fmt_vars = {
        "agent": agent_id,
        "message_id": first_id,
        "session_id": session_id or "",
        "mailbox_dir": str(mailbox_dir),
        "prompt": prompt,
    }
    cmd = []
    for part in raw_template:
        expanded = str(part)
        for k, v in fmt_vars.items():
            expanded = expanded.replace("{" + k + "}", v)
        cmd.append(expanded)
    if dry_run:
        return {"status": "DRY_RUN", "driver": driver, "command": cmd, "delivered_at": utc_iso()}

    env = os.environ.copy()
    env.update(
        {
            "AIM_MAILBOX_DIR": str(mailbox_dir),
            "AIM_MAIL_AGENT": agent_id,
            "AIM_MAIL_MESSAGE_ID": first_id,
            "AIM_MAIL_SESSION_ID": session_id or "",
            "AIM_MAIL_WAKE_PROMPT": prompt,
        }
    )
    res = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "status": "DELIVERED" if res.returncode == 0 else "FAILED",
        "driver": driver,
        "command": cmd,
        "returncode": res.returncode,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "delivered_at": utc_iso(),
    }


def install_agy_sidecar(
    mailbox_dir: Path,
    *,
    config_dir: Path | None = None,
    sidecar_id: str = "aim-mailbox",
    python_exe: str | None = None,
    interval: float = 1.5,
) -> dict[str, Any]:
    """Install and enable an Antigravity Desktop Sidecar running `aim-mail watch` non-destructively."""
    cfg_root = (config_dir or (Path.home() / ".gemini" / "config")).expanduser().resolve()
    sidecar_dir = cfg_root / "sidecars" / sidecar_id
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    py_bin = python_exe or sys.executable
    pkg_src_root = str(Path(__file__).resolve().parent.parent)

    runner_code = (
        "import sys\n"
        f"pkg_src = {pkg_src_root!r}\n"
        "if pkg_src not in sys.path:\n"
        "    sys.path.insert(0, pkg_src)\n"
        "from aim_mailbox.cli import main\n"
        "if __name__ == '__main__':\n"
        f"    raise SystemExit(main(['--mailbox-dir', {str(mailbox_dir)!r}, 'watch', '--interval', {str(interval)!r}]))\n"
    )
    runner_path = sidecar_dir / "sidecar_runner.py"
    runner_path.write_text(runner_code, encoding="utf-8", newline="\n")

    sidecar_spec = {
        "command": str(py_bin),
        "args": ["-u", "sidecar_runner.py"],
        "restart_policy": "on-failure",
        "display_name": "AIM Mailbox Sidecar",
        "description": "Zero-LLM Maildir watcher delivering inbound agent mail via agentapi send-message",
        "agent_permissions": {
            "access_grants": [
                "agentapi send-message",
                "agentapi get-conversation-metadata",
            ]
        },
    }
    write_json_atomic(sidecar_dir / "sidecar.json", sidecar_spec)

    config_json_path = cfg_root / "config.json"
    cfg: dict[str, Any] = {}
    if config_json_path.is_file():
        cfg = json.loads(config_json_path.read_text(encoding="utf-8"))

    sidecars_map = cfg.setdefault("sidecars", {})
    existing_entry = dict(sidecars_map.get(sidecar_id) or {})
    existing_entry["enabled"] = True
    existing_entry.setdefault("schemaVersion", 1)
    sidecars_map[sidecar_id] = existing_entry
    write_json_atomic(config_json_path, cfg)

    return {
        "installed": True,
        "sidecar_id": sidecar_id,
        "sidecar_dir": str(sidecar_dir),
        "config_json": str(config_json_path),
        "mailbox_dir": str(mailbox_dir),
        "python": str(py_bin),
    }


def uninstall_agy_sidecar(
    *,
    config_dir: Path | None = None,
    sidecar_id: str = "aim-mailbox",
) -> dict[str, Any]:
    cfg_root = (config_dir or (Path.home() / ".gemini" / "config")).expanduser().resolve()
    sidecar_dir = cfg_root / "sidecars" / sidecar_id
    config_json_path = cfg_root / "config.json"

    removed_config = False
    if config_json_path.is_file():
        cfg = json.loads(config_json_path.read_text(encoding="utf-8"))
        sidecars_map = cfg.get("sidecars")
        if isinstance(sidecars_map, dict) and sidecar_id in sidecars_map:
            del sidecars_map[sidecar_id]
            write_json_atomic(config_json_path, cfg)
            removed_config = True

    removed_dir = False
    if sidecar_dir.is_dir():
        shutil.rmtree(sidecar_dir)
        removed_dir = True

    return {
        "uninstalled": removed_config or removed_dir,
        "sidecar_id": sidecar_id,
        "removed_config": removed_config,
        "removed_dir": removed_dir,
    }
