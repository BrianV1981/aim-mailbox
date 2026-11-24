from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import aim_mailbox
from aim_mailbox.cli import COMMAND_HANDLERS, build_parser

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"


def _run_cli(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else str(SRC_DIR)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "aim_mailbox", *args],
        cwd=str(REPO_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_parser_and_version_parity() -> None:
    parser = build_parser()
    subparsers_actions = [a for a in parser._actions if hasattr(a, "_name_parser_map")]
    assert len(subparsers_actions) == 1
    parser_verbs = set(subparsers_actions[0]._name_parser_map.keys())
    assert parser_verbs == set(COMMAND_HANDLERS.keys())

    ver_file = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject_txt = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_txt, re.MULTILINE)
    assert m is not None
    assert ver_file == m.group(1) == aim_mailbox.__version__


def test_unmocked_cli_end_to_end_workflow(tmp_path: Path) -> None:
    mb = str(tmp_path / "mb")
    hook_out = tmp_path / "hook_ran.json"

    # 1. describe (both plain and --json)
    r_desc = _run_cli("describe")
    assert r_desc.returncode == 0
    desc = json.loads(r_desc.stdout)
    assert "agy-desktop-sidecar" in desc["drivers"]

    r_desc_json = _run_cli("describe", "--json")
    assert r_desc_json.returncode == 0
    assert json.loads(r_desc_json.stdout) == desc

    # 2. register (supporting --mailbox-dir both before and after subcommand)
    cmd_spec = json.dumps(
        [
            sys.executable,
            "-c",
            (
                "import json, os; from pathlib import Path; "
                f"Path({str(hook_out)!r}).write_text(json.dumps({{'agent': os.environ['AIM_MAIL_AGENT'], 'msg': os.environ['AIM_MAIL_MESSAGE_ID']}}))"
            ),
        ]
    )
    r_reg = _run_cli(
        "--mailbox-dir",
        mb,
        "register",
        "worker-1",
        "--driver",
        "command",
        "--command-json",
        cmd_spec,
        "--json",
    )
    assert r_reg.returncode == 0

    r_agents = _run_cli("agents", "--mailbox-dir", mb, "--json")
    assert r_agents.returncode == 0
    assert "worker-1" in json.loads(r_agents.stdout)["agents"]

    # 3. check --inject is completely silent when inbox is empty
    r_chk_empty = _run_cli("check", "worker-1", "--inject", "--mailbox-dir", mb)
    assert r_chk_empty.returncode == 0
    assert r_chk_empty.stdout == ""

    # 4. send --wake triggers the custom command driver in real subprocess
    r_send = _run_cli(
        "send",
        "--mailbox-dir",
        mb,
        "--from",
        "orchestrator",
        "--to",
        "worker-1",
        "--subject",
        "Build v0.1",
        "--body",
        "Run full test suite.",
        "--ref",
        "README.md",
        "--operator-approved",
        "--wake",
        "--json",
    )
    assert r_send.returncode == 0
    sent = json.loads(r_send.stdout)
    assert hook_out.is_file()
    hook_data = json.loads(hook_out.read_text(encoding="utf-8"))
    assert hook_data == {"agent": "worker-1", "msg": sent["id"]}

    # 5. worker reads & replies; parent auto-ACKs, orchestrator receives reply via `wait`
    r_read = _run_cli("read", "worker-1", sent["id"], "--mailbox-dir", mb, "--json")
    assert r_read.returncode == 0
    assert json.loads(r_read.stdout)["state"] == "claimed"

    r_reply = _run_cli(
        "reply",
        "worker-1",
        sent["id"],
        "--body",
        "All checks green.",
        "--mailbox-dir",
        mb,
        "--json",
    )
    assert r_reply.returncode == 0
    reply_env = json.loads(r_reply.stdout)

    # check --inject now emits steering prompt for orchestrator
    r_chk_full = _run_cli("check", "orchestrator", "--inject", "--mailbox-dir", mb)
    assert r_chk_full.returncode == 0
    assert "1 unread message(s) for 'orchestrator'" in r_chk_full.stdout

    r_wait = _run_cli(
        "wait",
        "orchestrator",
        "--thread-id",
        sent["thread_id"],
        "--timeout",
        "2",
        "--mailbox-dir",
        mb,
        "--json",
    )
    assert r_wait.returncode == 0
    assert json.loads(r_wait.stdout)["id"] == reply_env["id"]

    # 6. ack + watch --max-cycles 1 + install-agy-sidecar
    r_ack = _run_cli("ack", "orchestrator", reply_env["id"], "--note", "Accepted", "--mailbox-dir", mb, "--json")
    assert r_ack.returncode == 0
    assert json.loads(r_ack.stdout)["state"] == "done"

    r_watch = _run_cli("watch", "--max-cycles", "1", "--interval", "0.1", "--mailbox-dir", mb)
    assert r_watch.returncode == 0

    sc_cfg = tmp_path / "sc_cfg"
    r_sc = _run_cli(
        "install-agy-sidecar",
        "--config-dir",
        str(sc_cfg),
        "--mailbox-dir",
        mb,
        "--json",
    )
    assert r_sc.returncode == 0
    assert (sc_cfg / "sidecars" / "aim-mailbox" / "sidecar.json").is_file()

    r_sc_un = _run_cli("install-agy-sidecar", "--config-dir", str(sc_cfg), "--uninstall", "--json")
    assert r_sc_un.returncode == 0

    # 7. path traversal rejected with non-zero exit code
    r_bad = _run_cli("inbox", "../bad", "--mailbox-dir", mb)
    assert r_bad.returncode == 1

    outside_dir = tmp_path / "outside_cli"
    outside_dir.mkdir()
    victim_json = outside_dir / "victim.json"
    victim_json.write_text(json.dumps({"id": "victim", "from": "a", "to": "worker-1", "body": "secret"}), encoding="utf-8")

    for cmd in (
        ("read", "worker-1", "../outside_cli/victim", "--mailbox-dir", mb),
        ("read", "worker-1", "../outside_cli/victim", "--claim", "--mailbox-dir", mb),
        ("ack", "worker-1", "../outside_cli/victim", "--mailbox-dir", mb),
        ("reply", "worker-1", "../outside_cli/victim", "--body", "x", "--mailbox-dir", mb),
        ("install-agy-sidecar", "--config-dir", str(sc_cfg), "--uninstall", "--sidecar-id", "../outside_cli"),
    ):
        res_trav = _run_cli(*cmd)
        assert res_trav.returncode == 1
    assert victim_json.is_file()


def test_wake_fails_closed_for_unregistered_passive_and_unapproved_targets(tmp_path: Path) -> None:
    mb = str(tmp_path / "mb")

    # 1. send --wake to unregistered agent exits non-zero (rc=2)
    r_unreg = _run_cli(
        "send",
        "--mailbox-dir",
        mb,
        "--from",
        "lead",
        "--to",
        "ghost-agent",
        "--body",
        "nobody home",
        "--operator-approved",
        "--wake",
    )
    assert r_unreg.returncode == 2
    assert "not registered" in r_unreg.stderr

    # 2. send --wake to driver=none agent exits non-zero (rc=2)
    assert _run_cli("register", "passive-bot", "--driver", "none", "--mailbox-dir", mb).returncode == 0
    r_passive = _run_cli(
        "send",
        "--mailbox-dir",
        mb,
        "--from",
        "lead",
        "--to",
        "passive-bot",
        "--body",
        "passive",
        "--operator-approved",
        "--wake",
    )
    assert r_passive.returncode == 2
    assert "passive driver 'none'" in r_passive.stderr

    # 3. send --wake without --operator-approved exits non-zero (rc=2) even on active driver
    cmd_ok = json.dumps([sys.executable, "-c", "print('ok')"])
    assert (
        _run_cli(
            "register",
            "active-bot",
            "--driver",
            "command",
            "--command-json",
            cmd_ok,
            "--mailbox-dir",
            mb,
        ).returncode
        == 0
    )
    r_unapproved = _run_cli(
        "send",
        "--mailbox-dir",
        mb,
        "--from",
        "peer",
        "--to",
        "active-bot",
        "--body",
        "no approval",
        "--wake",
    )
    assert r_unapproved.returncode == 2
    assert "--operator-approved" in r_unapproved.stderr

    # 4. reply --wake to unregistered/passive sender also exits non-zero (rc=2)
    r_seed = _run_cli(
        "send",
        "--mailbox-dir",
        mb,
        "--from",
        "passive-bot",
        "--to",
        "active-bot",
        "--body",
        "please reply",
        "--operator-approved",
        "--json",
    )
    assert r_seed.returncode == 0
    seed_id = json.loads(r_seed.stdout)["id"]
    r_reply_fail = _run_cli(
        "reply",
        "active-bot",
        seed_id,
        "--body",
        "replying to passive",
        "--wake",
        "--mailbox-dir",
        mb,
    )
    assert r_reply_fail.returncode == 2
    assert "passive driver 'none'" in r_reply_fail.stderr


def test_zero_install_launcher_shims(tmp_path: Path) -> None:
    if os.name == "nt":
        cmd_shim = REPO_ROOT / "bin" / "aim-mail.cmd"
        res = subprocess.run(
            ["cmd.exe", "/c", str(cmd_shim), "--version"],
            cwd=str(tmp_path),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res.returncode == 0
        assert aim_mailbox.__version__ in res.stdout

        res_fail = subprocess.run(
            ["cmd.exe", "/c", str(cmd_shim), "inbox", "../bad", "--mailbox-dir", str(tmp_path / "mb")],
            cwd=str(tmp_path),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res_fail.returncode != 0
    else:
        sh_shim = REPO_ROOT / "bin" / "aim-mail"
        res = subprocess.run(
            ["bash", str(sh_shim), "--version"],
            cwd=str(tmp_path),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res.returncode == 0
        assert aim_mailbox.__version__ in res.stdout

        res_fail = subprocess.run(
            ["bash", str(sh_shim), "inbox", "../bad", "--mailbox-dir", str(tmp_path / "mb")],
            cwd=str(tmp_path),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res_fail.returncode != 0

        # Regression (#2): invoking via an external symlink (e.g. ~/.local/bin/aim-mail) must resolve REPO_ROOT
        ext_bin = tmp_path / "local_bin"
        ext_bin.mkdir()
        sym = ext_bin / "aim-mail"
        sym.symlink_to(sh_shim)
        res_sym = subprocess.run(
            [str(sym), "--version"],
            cwd=str(tmp_path),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res_sym.returncode == 0
        assert aim_mailbox.__version__ in res_sym.stdout


def test_no_hardcoded_host_paths_in_repo() -> None:
    forbidden = re.compile(r"(/home/[a-zA-Z0-9_-]+|C:\\\\Users\\\\[a-zA-Z0-9_-]+|/mnt/c/Users/(?!<)[a-zA-Z0-9_-]+)")
    scan_roots = [
        REPO_ROOT / "src",
        REPO_ROOT / "bin",
        REPO_ROOT / "tests",
        REPO_ROOT / "memory-wiki",
        REPO_ROOT / "README.md",
    ]
    offenders: list[str] = []
    for item in scan_roots:
        files = [item] if item.is_file() else [p for p in item.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        for fpath in files:
            if fpath.name == "test_cli_e2e.py":
                continue
            txt = fpath.read_text(encoding="utf-8", errors="ignore")
            if forbidden.search(txt):
                offenders.append(str(fpath.relative_to(REPO_ROOT)))
    assert offenders == [], f"Hardcoded host user paths found in: {offenders}"
