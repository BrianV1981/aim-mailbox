from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .store import MailboxError, _chmod_safe, _unlink_with_retry, validate_agent_id, write_json_atomic

HARNESS_NAMES = ("agy", "opencode", "codex", "grok", "generic")


def _resolve_aim_bin(aim_bin: str | None = None) -> str:
    if aim_bin:
        return aim_bin
    repo_root = Path(__file__).resolve().parent.parent.parent
    if os.name == "nt":
        win_shim = repo_root / "bin" / "aim-mail.cmd"
        if win_shim.is_file():
            return str(win_shim)
    else:
        posix_shim = repo_root / "bin" / "aim-mail"
        if posix_shim.is_file():
            return str(posix_shim)
    return shutil.which("aim-mail") or "aim-mail"


def _quote_cmd_part(part: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([part])
    return shlex.quote(part)


def build_check_command(
    agent_id: str,
    *,
    mailbox_dir: Path | None = None,
    hook_format: str | None = None,
    aim_bin: str | None = None,
) -> str:
    aid = validate_agent_id(agent_id)
    bin_path = _resolve_aim_bin(aim_bin)
    parts = [_quote_cmd_part(bin_path)]
    if mailbox_dir is not None:
        parts.extend(["--mailbox-dir", _quote_cmd_part(str(mailbox_dir))])
    parts.extend(["check", aid, "--inject", "--quiet"])
    if hook_format and hook_format != "text":
        parts.extend(["--format", hook_format])
    return " ".join(parts)


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MailboxError(f"Failed to parse existing hook config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MailboxError(f"Invalid hook config {path}: expected top-level JSON object.")
    return data


def _is_aim_mailbox_command(cmd: Any) -> bool:
    if not isinstance(cmd, str):
        return False
    return "aim-mail" in cmd and "check" in cmd and "--inject" in cmd


def _resolve_target_path(
    harness: str,
    agent_id: str,
    *,
    config_dir: Path | None = None,
    workspace: Path | None = None,
) -> tuple[Path, Path]:
    aid = validate_agent_id(agent_id)
    home = Path.home()
    if harness == "agy":
        if workspace is not None:
            base = workspace.expanduser().resolve() / ".agents"
        else:
            base = (config_dir or (home / ".gemini" / "config")).expanduser().resolve()
        return base, base / "hooks.json"

    if harness == "codex":
        base = (config_dir or (home / ".codex")).expanduser().resolve()
        return base, base / "hooks.json"

    if harness == "grok":
        if workspace is not None:
            base = workspace.expanduser().resolve() / ".grok"
        else:
            base = (config_dir or (home / ".grok")).expanduser().resolve()
        return base, base / "hooks" / "aim-mailbox.json"

    if harness == "opencode":
        base = (config_dir or (home / ".config" / "opencode")).expanduser().resolve()
        ext = "cmd" if os.name == "nt" else "sh"
        return base, base / f"aim-mailbox-hook.{ext}"

    # harness == "generic"
    base = (config_dir or (home / ".aim" / "hooks")).expanduser().resolve()
    ext = "cmd" if os.name == "nt" else "sh"
    return base, base / f"aim-mailbox-{aid}.{ext}"


def _write_script_atomic(dst: Path, content: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_safe(dst.parent, 0o700)
    tmp_path = dst.parent / f".tmp_{dst.name}_{os.getpid()}"
    tmp_path.write_text(content, encoding="utf-8", newline="\n" if os.name != "nt" else "\r\n")
    _chmod_safe(tmp_path, 0o700)
    os.replace(tmp_path, dst)
    _chmod_safe(dst, 0o700)


def install_hooks(
    *,
    harness: str,
    agent_id: str = "default",
    mailbox_dir: Path | None = None,
    config_dir: Path | None = None,
    workspace: Path | None = None,
    aim_bin: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Non-destructively install turn-boundary `check --inject --quiet` hooks for a target harness."""
    h_name = str(harness).strip().lower()
    if h_name not in HARNESS_NAMES:
        raise MailboxError(f"Unsupported harness {harness!r}; choose from {', '.join(HARNESS_NAMES)}.")

    aid = validate_agent_id(agent_id)
    base_dir, target_path = _resolve_target_path(
        h_name,
        aid,
        config_dir=config_dir,
        workspace=workspace,
    )
    resolved_base = base_dir.resolve()
    resolved_target = target_path.resolve()
    if not resolved_target.is_relative_to(resolved_base):
        raise MailboxError(f"Unsafe hook target path {target_path}: escapes {base_dir}.")

    warnings: list[str] = []
    mode = "native"

    if h_name == "agy":
        existing = _read_json_object(target_path)
        inject_cmd = build_check_command(aid, mailbox_dir=mailbox_dir, hook_format="agy-inject", aim_bin=aim_bin)
        stop_cmd = build_check_command(aid, mailbox_dir=mailbox_dir, hook_format="agy-stop", aim_bin=aim_bin)
        merged = dict(existing)
        merged["aim-mailbox"] = {
            "enabled": True,
            "SessionStart": [{"type": "command", "command": inject_cmd, "timeout": 5}],
            "PreInvocation": [{"type": "command", "command": inject_cmd, "timeout": 5}],
            "Stop": [{"type": "command", "command": stop_cmd, "timeout": 5}],
        }
        if not dry_run:
            write_json_atomic(target_path, merged)
        cmd_preview = inject_cmd

    elif h_name == "codex":
        existing = _read_json_object(target_path)
        cmd_str = build_check_command(aid, mailbox_dir=mailbox_dir, aim_bin=aim_bin)
        merged = dict(existing)
        hooks_map = dict(merged.get("hooks") or {}) if isinstance(merged.get("hooks"), dict) else {}
        raw_stop = list(hooks_map.get("Stop") or []) if isinstance(hooks_map.get("Stop"), list) else []

        cleaned_stop: list[Any] = []
        for group in raw_stop:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                cleaned_stop.append(group)
                continue
            kept_hooks = [
                h
                for h in group["hooks"]
                if not (
                    isinstance(h, dict)
                    and (
                        str(h.get("statusMessage", "")).startswith("aim-mailbox:")
                        or _is_aim_mailbox_command(h.get("command"))
                    )
                )
            ]
            if kept_hooks:
                new_grp = dict(group)
                new_grp["hooks"] = kept_hooks
                cleaned_stop.append(new_grp)

        cleaned_stop.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": cmd_str,
                        "timeout": 5,
                        "statusMessage": f"aim-mailbox:{aid}",
                    }
                ]
            }
        )
        hooks_map["Stop"] = cleaned_stop
        merged["hooks"] = hooks_map
        if not dry_run:
            write_json_atomic(target_path, merged)
        cmd_preview = cmd_str

    elif h_name == "grok":
        cmd_str = build_check_command(aid, mailbox_dir=mailbox_dir, aim_bin=aim_bin)
        payload = {
            "description": "aim-mailbox turn-boundary unread check",
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": cmd_str,
                                "timeout": 5,
                            }
                        ]
                    }
                ],
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": cmd_str,
                                "timeout": 5,
                            }
                        ]
                    }
                ],
            },
        }
        if not dry_run:
            write_json_atomic(target_path, payload)
        cmd_preview = cmd_str

    else:
        cmd_str = build_check_command(aid, mailbox_dir=mailbox_dir, aim_bin=aim_bin)
        mode = "generic_fallback" if h_name == "opencode" else "generic"
        if h_name == "opencode":
            warnings.append(
                "OpenCode lacks native JSON command hooks in opencode.json; wrote generic turn-boundary hook script instead."
            )
        if os.name == "nt":
            script_body = f"@echo off\r\n{cmd_str}\r\n"
        else:
            script_body = f"#!/usr/bin/env bash\nset -euo pipefail\nexec {cmd_str}\n"
        if not dry_run:
            _write_script_atomic(target_path, script_body)
        cmd_preview = cmd_str

    out: dict[str, Any] = {
        "installed": not dry_run,
        "dry_run": dry_run,
        "harness": h_name,
        "mode": mode,
        "agent_id": aid,
        "target_path": str(target_path),
        "command": cmd_preview,
    }
    if warnings:
        out["warnings"] = warnings
    return out


def uninstall_hooks(
    *,
    harness: str,
    agent_id: str = "default",
    config_dir: Path | None = None,
    workspace: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Idempotently remove only `aim-mailbox` hook entries without disturbing unrelated user config."""
    h_name = str(harness).strip().lower()
    if h_name not in HARNESS_NAMES:
        raise MailboxError(f"Unsupported harness {harness!r}; choose from {', '.join(HARNESS_NAMES)}.")

    aid = validate_agent_id(agent_id)
    base_dir, target_path = _resolve_target_path(
        h_name,
        aid,
        config_dir=config_dir,
        workspace=workspace,
    )
    resolved_base = base_dir.resolve()
    resolved_target = target_path.resolve()
    if not resolved_target.is_relative_to(resolved_base):
        raise MailboxError(f"Unsafe hook target path {target_path}: escapes {base_dir}.")

    removed = False
    if h_name == "agy":
        if target_path.is_file():
            existing = _read_json_object(target_path)
            if "aim-mailbox" in existing:
                removed = True
                if not dry_run:
                    updated = dict(existing)
                    del updated["aim-mailbox"]
                    write_json_atomic(target_path, updated)

    elif h_name == "codex":
        if target_path.is_file():
            existing = _read_json_object(target_path)
            hooks_map = dict(existing.get("hooks") or {}) if isinstance(existing.get("hooks"), dict) else {}
            raw_stop = list(hooks_map.get("Stop") or []) if isinstance(hooks_map.get("Stop"), list) else []
            kept_stop: list[Any] = []
            for group in raw_stop:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    kept_stop.append(group)
                    continue
                kept_hooks = [
                    h
                    for h in group["hooks"]
                    if not (
                        isinstance(h, dict)
                        and (
                            str(h.get("statusMessage", "")).startswith("aim-mailbox:")
                            or _is_aim_mailbox_command(h.get("command"))
                        )
                    )
                ]
                if len(kept_hooks) != len(group["hooks"]):
                    removed = True
                if kept_hooks:
                    new_grp = dict(group)
                    new_grp["hooks"] = kept_hooks
                    kept_stop.append(new_grp)
            if removed and not dry_run:
                updated = dict(existing)
                hooks_map["Stop"] = kept_stop
                updated["hooks"] = hooks_map
                write_json_atomic(target_path, updated)

    else:
        if target_path.is_file():
            removed = True
            if not dry_run:
                _unlink_with_retry(target_path)

    return {
        "uninstalled": removed and not dry_run,
        "found": removed,
        "dry_run": dry_run,
        "harness": h_name,
        "agent_id": aid,
        "target_path": str(target_path),
    }
