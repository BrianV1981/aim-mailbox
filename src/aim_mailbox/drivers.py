from __future__ import annotations

import base64
import http.client
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .store import (
    MailboxError,
    utc_iso,
    validate_agent_id,
    validate_http_url,
    validate_message_id,
    validate_session_id,
    write_json_atomic,
)

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
ASYNC_WAKE_DRIVERS = frozenset({"agy-desktop-sidecar", "tmux", "vscode-chat", "command"})


def build_wake_prompt(agent_id: str, envelopes: list[dict[str, Any]], mailbox_dir: Path) -> str:
    safe_agent = validate_agent_id(agent_id)
    safe_ids = [validate_message_id(str(e["id"]), allow_json_ext=False) for e in envelopes]
    safe_senders = sorted({validate_agent_id(str(e.get("from") or "unknown")) for e in envelopes})
    ids = ", ".join(safe_ids)
    senders = ", ".join(safe_senders)
    count = len(envelopes)
    return (
        f"[AIM-MAILBOX WAKEUP] Agent '{safe_agent}' has {count} pending message(s) "
        f"from {senders} (id: {ids}). "
        f"Run `aim-mail --mailbox-dir \"{mailbox_dir}\" inbox {safe_agent} --state active`, "
        f"read each message (`aim-mail read {safe_agent} <id>`), complete authorized tasks, "
        f"and acknowledge (`aim-mail reply` or `aim-mail ack {safe_agent} <id>`)."
    )


def build_tmux_doorbell(
    agent_id: str,
    envelope_or_envelopes: dict[str, Any] | list[dict[str, Any]],
    mailbox_dir: Path,
) -> str:
    safe_agent = validate_agent_id(agent_id)
    envelopes = envelope_or_envelopes if isinstance(envelope_or_envelopes, list) else [envelope_or_envelopes]
    safe_ids = [validate_message_id(str(e["id"]), allow_json_ext=False) for e in envelopes]
    senders = sorted({validate_agent_id(str(e.get("from") or "peer")) for e in envelopes})
    if len(envelopes) == 1:
        msg_id = safe_ids[0]
        sender = senders[0]
        reply_to = validate_agent_id(str(envelopes[0].get("reply_to") or sender))
        return (
            f"[AIM-MAIL from:{sender} reply_to:{reply_to} id:{msg_id}] "
            f"Pending mail waiting — run: aim-mail --mailbox-dir \"{mailbox_dir}\" read {safe_agent} {msg_id}"
        )
    return (
        f"[AIM-MAIL ({len(envelopes)} msgs) from:{','.join(senders)} ids:{','.join(safe_ids)}] "
        f"Pending mail waiting — run: aim-mail --mailbox-dir \"{mailbox_dir}\" inbox {safe_agent} --state active"
    )


def is_agy_session_idle(session_id: str, summary_db: str | None = None, home_dir: Path | None = None) -> bool:
    clean_sid = validate_session_id(session_id)
    home = home_dir or Path.home()
    candidates: list[Path] = []
    presence_dirs: list[Path] = []
    if summary_db:
        db_p = Path(summary_db).expanduser()
        candidates.append(db_p)
        presence_dirs.append(db_p.parent / "presence")
    else:
        for base in (home / ".gemini" / "antigravity-cli", home / ".gemini" / "antigravity"):
            candidates.append(base / "conversation_summaries.db")
            presence_dirs.append(base / "presence")

    for p_dir in presence_dirs:
        if (p_dir / f"{clean_sid}.lock").exists():
            return False

    for db_path in candidates:
        if not db_path.is_file():
            continue
        try:
            conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=3.0)
            try:
                row = conn.execute(
                    "SELECT status, not_fully_idle FROM conversation_summaries WHERE conversation_id = ?",
                    (clean_sid,),
                ).fetchone()
            finally:
                conn.close()
            if row is not None:
                status_val = str(row[0] or "")
                busy_flag = int(row[1] or 0)
                return status_val == "CASCADE_RUN_STATUS_IDLE" and busy_flag == 0
        except sqlite3.OperationalError:
            return False
        except sqlite3.Error:
            continue
    return True


def _is_healthy_agentapi_wrapper(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() in (".exe", ".bat", ".cmd"):
        return True
    try:
        head = path.read_bytes()[:512]
    except OSError:
        return False
    if not head.startswith(b"#!"):
        return True
    if os.name == "nt":
        return False
    text = head.decode("utf-8", errors="ignore")
    m = re.search(r"""exec\s+["']?([^"'\s]+)["']?""", text)
    if m:
        raw_target = m.group(1)
        target = Path(raw_target)
        if (target.is_absolute() or raw_target.startswith("/")) and not target.is_file():
            return False
    return True


def _resolve_agentapi_prefix(binary_override: str | None = None, home_dir: Path | None = None) -> list[str]:
    if binary_override:
        if binary_override.lower().endswith("language_server.exe") or binary_override.endswith("agy.real"):
            return [binary_override, "agentapi"]
        ov_path = Path(binary_override)
        if not ov_path.is_file() or _is_healthy_agentapi_wrapper(ov_path):
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
    if which_bin and not which_bin.lower().endswith((".bat", ".cmd")) and _is_healthy_agentapi_wrapper(Path(which_bin)):
        return [which_bin]

    for cand in (
        home / ".gemini" / "antigravity" / "bin" / "agentapi.exe",
        home / ".gemini" / "antigravity" / "bin" / "agentapi",
        home / ".gemini" / "antigravity-cli" / "bin" / "agentapi",
    ):
        if cand.is_file() and _is_healthy_agentapi_wrapper(cand):
            return [str(cand)]

    for agy_real in (
        home / ".local" / "bin" / "agy.real",
        Path(shutil.which("agy.real")) if shutil.which("agy.real") else None,
    ):
        if agy_real is not None and agy_real.is_file():
            return [str(agy_real), "agentapi"]

    bat_cand = home / ".gemini" / "antigravity" / "bin" / "agentapi.bat"
    if bat_cand.is_file():
        return [str(bat_cand)]
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


def _coerce_output_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)


def _run_capture(
    cmd: list[str],
    *,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    timeout: float = 300.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=timeout,
    )


def _extract_codex_session_id(raw: str) -> str | None:
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        for key in ("thread_id", "session_id", "conversation_id"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return validate_session_id(val)
    return None


def dispatch_wake(
    agent_cfg: dict[str, Any],
    envelopes: list[dict[str, Any]],
    *,
    mailbox_dir: Path,
    dry_run: bool = False,
    timeout: float = 300.0,
    on_session_created: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    driver = str(agent_cfg.get("driver") or "none").strip()
    if driver not in DRIVER_NAMES:
        raise MailboxError(f"Unsupported driver {driver!r}; valid drivers: {', '.join(DRIVER_NAMES)}.")

    try:
        return _dispatch_wake_inner(
            agent_cfg,
            envelopes,
            driver=driver,
            mailbox_dir=mailbox_dir,
            dry_run=dry_run,
            timeout=timeout,
            on_session_created=on_session_created,
        )
    except subprocess.TimeoutExpired as exc:
        cmd = exc.cmd if isinstance(exc.cmd, list) else [str(exc.cmd)]
        return {
            "status": "FAILED",
            "driver": driver,
            "command": [str(x) for x in cmd],
            "returncode": 124,
            "stdout": _coerce_output_text(exc.stdout),
            "stderr": _coerce_output_text(exc.stderr) or f"Driver '{driver}' timed out after {timeout}s.",
            "delivered_at": utc_iso(),
        }
    except subprocess.CalledProcessError as exc:
        cmd = exc.cmd if isinstance(exc.cmd, list) else [str(exc.cmd)]
        return {
            "status": "FAILED",
            "driver": driver,
            "command": [str(x) for x in cmd],
            "returncode": exc.returncode or 1,
            "stdout": _coerce_output_text(exc.stdout),
            "stderr": _coerce_output_text(exc.stderr) or str(exc),
            "delivered_at": utc_iso(),
        }
    except (OSError, subprocess.SubprocessError, http.client.HTTPException) as exc:
        return {
            "status": "FAILED",
            "driver": driver,
            "returncode": 1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "delivered_at": utc_iso(),
        }


def _dispatch_wake_inner(
    agent_cfg: dict[str, Any],
    envelopes: list[dict[str, Any]],
    *,
    driver: str,
    mailbox_dir: Path,
    dry_run: bool = False,
    timeout: float = 300.0,
    on_session_created: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    agent_id = validate_agent_id(str(agent_cfg["agent_id"]))
    prompt = build_wake_prompt(agent_id, envelopes, mailbox_dir)
    cwd = agent_cfg.get("cwd") or None
    raw_sid = str(agent_cfg.get("session_id") or "").strip()
    session_id = validate_session_id(raw_sid) if raw_sid else None

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
        res = _run_capture(cmd, cwd=cwd, timeout=timeout)
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
        cmd = [str(agy_bin), "--output-format", "json"]
        if agent_cfg.get("dangerously_skip_permissions"):
            cmd.append("--dangerously-skip-permissions")
        if session_id:
            cmd.extend(["--conversation", session_id])
        cmd.extend(["-p", prompt])
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": cmd, "delivered_at": utc_iso()}
        res = _run_capture(cmd, cwd=cwd, timeout=timeout)
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
        base_url = validate_http_url(str(agent_cfg.get("http_url") or "http://127.0.0.1:4096"))
        headers = {"Content-Type": "application/json"}
        pwd = os.environ.get("OPENCODE_SERVER_PASSWORD")
        if pwd is not None:
            user = os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode"
            tok = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {tok}"

        if dry_run:
            sid_seg = urllib.parse.quote(session_id, safe="") if session_id else "<auto>"
            target_ep = f"{base_url}/session/{sid_seg}/message"
            return {"status": "DRY_RUN", "driver": driver, "command": [target_ep], "delivered_at": utc_iso()}

        active_sid = session_id
        try:
            if not active_sid:
                create_req = urllib.request.Request(
                    f"{base_url}/session",
                    data=json.dumps({"title": f"aim-mailbox:{agent_id}"}).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(create_req, timeout=min(15.0, timeout)) as resp:
                    created = json.loads(resp.read().decode("utf-8"))
                    raw_created_sid = str(created.get("id") or "").strip()
                if not raw_created_sid:
                    return {
                        "status": "FAILED",
                        "driver": driver,
                        "returncode": 1,
                        "stderr": "OpenCode POST /session did not return a session 'id'.",
                        "delivered_at": utc_iso(),
                    }
                active_sid = validate_session_id(raw_created_sid)
                if on_session_created is not None:
                    on_session_created(active_sid)

            encoded_sid = urllib.parse.quote(active_sid, safe="")
            msg_url = f"{base_url}/session/{encoded_sid}/message"
            msg_payload = {"parts": [{"type": "text", "text": prompt}]}
            msg_req = urllib.request.Request(
                msg_url,
                data=json.dumps(msg_payload).encode("utf-8"),
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
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except OSError:
                pass
            return {
                "status": "FAILED",
                "driver": driver,
                "command": [base_url],
                "returncode": exc.code or 1,
                "stdout": err_body,
                "stderr": f"HTTP {exc.code}: {err_body or exc.reason}",
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
                "session_id": active_sid,
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
        res = _run_capture(cmd, cwd=cwd, timeout=timeout)
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
            cmd = [str(codex_bin), "exec", "resume", "--json", session_id, prompt]
        else:
            cmd = [str(codex_bin), "exec", "--json", "--add-dir", str(mailbox_dir), prompt]
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": cmd, "delivered_at": utc_iso()}
        res = _run_capture(cmd, cwd=cwd, timeout=timeout)
        discovered_sid = _extract_codex_session_id(res.stdout) or session_id
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

    if driver == "vscode-chat":
        code_bin = agent_cfg.get("binary") or shutil.which("code") or "code"
        cmd = [str(code_bin), "chat", "-m", "agent", "-r", prompt]
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": cmd, "delivered_at": utc_iso()}
        res = _run_capture(cmd, cwd=cwd, timeout=timeout)
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
        clean_target = target_session.lstrip("=")
        session_target = f"={clean_target.split(':', 1)[0]}"
        pane_target = f"={clean_target}" if ":" in clean_target else f"={clean_target}:"
        doorbell = build_tmux_doorbell(agent_id, envelopes, mailbox_dir)
        msg_ids = [validate_message_id(str(e["id"]), allow_json_ext=False) for e in envelopes]
        buf_name = f"aim_mail_{msg_ids[0][-12:]}"
        plan = [
            [str(tmux_bin), "has-session", "-t", session_target],
            [str(tmux_bin), "set-buffer", "-b", buf_name, "--", doorbell],
            [str(tmux_bin), "paste-buffer", "-b", buf_name, "-d", "-p", "-t", pane_target],
            [str(tmux_bin), "send-keys", "-t", pane_target, "Enter"],
        ]
        if dry_run:
            return {"status": "DRY_RUN", "driver": driver, "command": plan[-1], "doorbell": doorbell, "delivered_at": utc_iso()}

        chk = _run_capture(plan[0], timeout=10)
        if chk.returncode != 0:
            return {
                "status": "FAILED",
                "driver": driver,
                "command": plan[0],
                "returncode": chk.returncode,
                "stderr": chk.stderr or f"tmux session '{target_session}' not found.",
                "delivered_at": utc_iso(),
            }
        _run_capture(plan[1], check=True, timeout=10)
        _run_capture(plan[2], check=True, timeout=10)
        time.sleep(0.15)
        # Submit with Enter ONLY (never Escape, preventing multi-line/paste wipe on AGY & Grok)
        sub = _run_capture(plan[3], timeout=10)
        time.sleep(0.4)
        cap = _run_capture(
            [str(tmux_bin), "capture-pane", "-t", pane_target, "-p", "-J", "-S", "-120"],
            timeout=10,
        )
        cap_out = cap.stdout or ""
        cap_lines = [ln for ln in cap_out.splitlines() if ln.strip()]
        last_line = cap_lines[-1] if cap_lines else ""
        unsubmitted_composer = bool(re.match(r"^\s*(?:[>❯$#%➜]|\[?input\]?:?)\s+.*\[AIM-MAIL\b", last_line))
        verified = (
            (sub.returncode == 0)
            and all(mid in cap_out for mid in msg_ids)
            and not unsubmitted_composer
        )
        return {
            "status": "DELIVERED" if verified else "FAILED",
            "driver": driver,
            "command": plan[3],
            "returncode": 0 if verified else 2,
            "stdout": cap_out[-600:] if cap_out else "",
            "stderr": (
                ""
                if verified
                else (
                    "VERIFY_FAIL: doorbell appears unsubmitted in target tmux composer."
                    if unsubmitted_composer
                    else "VERIFY_FAIL: message ID needle(s) not found in target tmux pane capture."
                )
            ),
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
    all_ids = [validate_message_id(str(e["id"]), allow_json_ext=False) for e in envelopes]
    first_id = all_ids[0]
    joined_ids = ",".join(all_ids)
    fmt_vars = {
        "agent": agent_id,
        "message_id": first_id,
        "message_ids": joined_ids,
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
            "AIM_MAIL_MESSAGE_IDS": joined_ids,
            "AIM_MAIL_MESSAGE_COUNT": str(len(all_ids)),
            "AIM_MAIL_SESSION_ID": session_id or "",
            "AIM_MAIL_WAKE_PROMPT": prompt,
        }
    )
    res = _run_capture(cmd, cwd=cwd, env=env, timeout=timeout)
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
    clean_sidecar_id = validate_agent_id(sidecar_id)
    sidecars_root = (cfg_root / "sidecars").resolve()
    sidecar_dir = (sidecars_root / clean_sidecar_id).resolve()
    if not sidecar_dir.is_relative_to(sidecars_root) or sidecar_dir == sidecars_root:
        raise MailboxError(f"Unsafe sidecar_id {sidecar_id!r}: escapes sidecars directory.")
    sidecar_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

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
                "command(agentapi send-message)",
                "command(agentapi get-conversation-metadata)",
            ]
        },
    }
    write_json_atomic(sidecar_dir / "sidecar.json", sidecar_spec)

    config_json_path = cfg_root / "config.json"
    cfg: dict[str, Any] = {}
    if config_json_path.is_file():
        cfg = json.loads(config_json_path.read_text(encoding="utf-8"))

    sidecars_map = cfg.setdefault("sidecars", {})
    existing_entry = dict(sidecars_map.get(clean_sidecar_id) or {})
    existing_entry["enabled"] = True
    existing_entry.setdefault("schemaVersion", 1)
    sidecars_map[clean_sidecar_id] = existing_entry
    write_json_atomic(config_json_path, cfg)

    warnings: list[str] = []
    is_wsl = "microsoft" in platform.release().lower() or bool(os.environ.get("WSL_DISTRO_NAME"))
    if is_wsl and config_dir is None and python_exe is None:
        warnings.append(
            "Running under WSL without --config-dir/--python wrote WSL paths that native Windows Antigravity.exe cannot execute directly."
        )

    out: dict[str, Any] = {
        "installed": True,
        "sidecar_id": clean_sidecar_id,
        "sidecar_dir": str(sidecar_dir),
        "config_json": str(config_json_path),
        "mailbox_dir": str(mailbox_dir),
        "python": str(py_bin),
    }
    if warnings:
        out["warnings"] = warnings
    return out


def uninstall_agy_sidecar(
    *,
    config_dir: Path | None = None,
    sidecar_id: str = "aim-mailbox",
) -> dict[str, Any]:
    cfg_root = (config_dir or (Path.home() / ".gemini" / "config")).expanduser().resolve()
    clean_sidecar_id = validate_agent_id(sidecar_id)
    sidecars_root = (cfg_root / "sidecars").resolve()
    sidecar_dir = (sidecars_root / clean_sidecar_id).resolve()
    if not sidecar_dir.is_relative_to(sidecars_root) or sidecar_dir == sidecars_root:
        raise MailboxError(f"Unsafe sidecar_id {sidecar_id!r}: escapes sidecars directory.")
    config_json_path = cfg_root / "config.json"

    removed_config = False
    if config_json_path.is_file():
        cfg = json.loads(config_json_path.read_text(encoding="utf-8"))
        sidecars_map = cfg.get("sidecars")
        if isinstance(sidecars_map, dict) and clean_sidecar_id in sidecars_map:
            del sidecars_map[clean_sidecar_id]
            write_json_atomic(config_json_path, cfg)
            removed_config = True

    removed_dir = False
    if sidecar_dir.is_dir():
        shutil.rmtree(sidecar_dir)
        removed_dir = True

    return {
        "uninstalled": removed_config or removed_dir,
        "sidecar_id": clean_sidecar_id,
        "removed_config": removed_config,
        "removed_dir": removed_dir,
    }
