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


def test_cli_inbox_filters_and_gc_e2e(tmp_path: Path) -> None:
    mb = str(tmp_path / "mb")
    archive_dir = tmp_path / "gc_archive"

    r1 = _run_cli(
        "send", "--mailbox-dir", mb,
        "--from", "alice", "--to", "bob",
        "--subject", "Deploy check", "--body", "Verify staging manifest",
        "--thread-id", "thr_deploy", "--json",
    )
    assert r1.returncode == 0
    m1 = json.loads(r1.stdout)

    r2 = _run_cli(
        "send", "--mailbox-dir", mb,
        "--from", "charlie", "--to", "bob",
        "--subject", "Nightly report", "--body", "All metrics green",
        "--thread-id", "thr_report", "--json",
    )
    assert r2.returncode == 0
    m2 = json.loads(r2.stdout)

    # Filter inbox via CLI flags
    r_filt = _run_cli(
        "inbox", "bob", "--mailbox-dir", mb,
        "--state", "unread", "--from", "alice", "--thread-id", "thr_deploy",
        "--query", "staging", "--limit", "1", "--json",
    )
    assert r_filt.returncode == 0
    filt_data = json.loads(r_filt.stdout)
    assert filt_data["count"] == 1
    assert [m["id"] for m in filt_data["messages"]] == [m1["id"]]

    # ACK m1 so it moves to done/, leave m2 in unread/
    assert _run_cli("ack", "bob", m1["id"], "--mailbox-dir", mb).returncode == 0

    # Dry-run gc with --done-days 0 reports m1 without removing it
    r_gc_dry = _run_cli(
        "gc", "--mailbox-dir", mb, "--agent", "bob",
        "--done-days", "0", "--dry-run", "--archive-dir", str(archive_dir), "--json",
    )
    assert r_gc_dry.returncode == 0
    dry_payload = json.loads(r_gc_dry.stdout)
    assert dry_payload["dry_run"] is True
    assert dry_payload["pruned_count"] == 1
    assert not archive_dir.exists()

    # Live gc archives m1 and leaves unread m2 untouched
    r_gc = _run_cli(
        "gc", "--mailbox-dir", mb, "--agent", "bob",
        "--done-days", "0", "--archive-dir", str(archive_dir), "--json",
    )
    assert r_gc.returncode == 0
    gc_payload = json.loads(r_gc.stdout)
    assert gc_payload["archived_count"] == 1
    assert (archive_dir / "bob" / "done" / f"{m1['id']}.json").is_file()

    r_after = _run_cli("inbox", "bob", "--state", "all", "--mailbox-dir", mb, "--json")
    assert r_after.returncode == 0
    after_msgs = json.loads(r_after.stdout)["messages"]
    assert [m["id"] for m in after_msgs] == [m2["id"]]


def test_cli_priority_coalesce_and_low_wake_fail_closed_e2e(tmp_path: Path) -> None:
    mb = str(tmp_path / "mb")
    cmd_ok = json.dumps([sys.executable, "-c", "print('ok')"])
    assert _run_cli("register", "worker", "--driver", "command", "--command-json", cmd_ok, "--mailbox-dir", mb).returncode == 0

    # send --priority low --wake without --allow-deferred fails closed (rc=2)
    r_low_fail = _run_cli(
        "send", "--mailbox-dir", mb,
        "--from", "lead", "--to", "worker",
        "--body", "low note 1", "--priority", "low", "--coalesce-key", "notes",
        "--operator-approved", "--wake",
    )
    assert r_low_fail.returncode == 2
    assert "LOW_PRIORITY_BATCH_WINDOW" in r_low_fail.stderr

    # send --priority low --wake --allow-deferred exits 0 cleanly
    r_low_ok = _run_cli(
        "send", "--mailbox-dir", mb,
        "--from", "lead", "--to", "worker",
        "--body", "low note 2", "--priority", "low", "--coalesce-key", "notes",
        "--operator-approved", "--wake", "--allow-deferred", "--json",
    )
    assert r_low_ok.returncode == 0
    low2 = json.loads(r_low_ok.stdout)
    assert low2["priority"] == "low"
    assert low2["coalesce_key"] == "notes"

    # send --priority urgent --wake dispatches immediately
    r_urg = _run_cli(
        "send", "--mailbox-dir", mb,
        "--from", "lead", "--to", "worker",
        "--body", "urgent halt", "--priority", "urgent",
        "--operator-approved", "--wake", "--json",
    )
    assert r_urg.returncode == 0
    urg = json.loads(r_urg.stdout)
    assert urg["priority"] == "urgent"
    assert len(urg["wake"]["dispatched"]) == 1

    # inbox --priority filter works end-to-end
    r_inb = _run_cli("inbox", "worker", "--state", "all", "--priority", "urgent", "--mailbox-dir", mb, "--json")
    assert r_inb.returncode == 0
    assert [m["id"] for m in json.loads(r_inb.stdout)["messages"]] == [urg["id"]]


def test_install_hooks_all_harnesses_and_agy_check_formats(tmp_path: Path) -> None:
    mb = str(tmp_path / "mb")

    # 1. agy: non-destructive merge + dry-run + idempotent uninstall
    agy_cfg = tmp_path / "agy_cfg"
    agy_cfg.mkdir()
    (agy_cfg / "hooks.json").write_text(
        json.dumps({"user-linter": {"enabled": True, "Stop": [{"type": "command", "command": "echo keep"}]}}),
        encoding="utf-8",
    )
    r_agy_dry = _run_cli(
        "install-hooks", "--harness", "agy", "--agent", "w-agy",
        "--config-dir", str(agy_cfg), "--mailbox-dir", mb, "--dry-run", "--json",
    )
    assert r_agy_dry.returncode == 0
    dry_payload = json.loads(r_agy_dry.stdout)
    assert dry_payload["dry_run"] is True
    assert "aim-mailbox" not in json.loads((agy_cfg / "hooks.json").read_text(encoding="utf-8"))

    r_agy = _run_cli(
        "install-hooks", "--harness", "agy", "--agent", "w-agy",
        "--config-dir", str(agy_cfg), "--mailbox-dir", mb, "--json",
    )
    assert r_agy.returncode == 0
    agy_data = json.loads((agy_cfg / "hooks.json").read_text(encoding="utf-8"))
    assert "user-linter" in agy_data
    assert agy_data["aim-mailbox"]["enabled"] is True
    assert agy_data["aim-mailbox"]["SessionStart"][0]["type"] == "command"
    assert "--format agy-inject" in agy_data["aim-mailbox"]["PreInvocation"][0]["command"]
    assert "--format agy-stop" in agy_data["aim-mailbox"]["Stop"][0]["command"]

    r_agy_un = _run_cli(
        "install-hooks", "--harness", "agy", "--agent", "w-agy",
        "--config-dir", str(agy_cfg), "--uninstall", "--json",
    )
    assert r_agy_un.returncode == 0
    assert json.loads(r_agy_un.stdout)["uninstalled"] is True
    agy_after = json.loads((agy_cfg / "hooks.json").read_text(encoding="utf-8"))
    assert "aim-mailbox" not in agy_after and "user-linter" in agy_after

    # 2. codex: preserves existing Stop hooks and avoids duplicates on re-install
    codex_cfg = tmp_path / "codex_cfg"
    codex_cfg.mkdir()
    (codex_cfg / "hooks.json").write_text(
        json.dumps(
            {
                "description": "User Codex hooks",
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "echo chime", "timeout": 5}]}]
                },
            }
        ),
        encoding="utf-8",
    )
    for _ in range(2):
        assert (
            _run_cli(
                "install-hooks", "--harness", "codex", "--agent", "w-codex",
                "--config-dir", str(codex_cfg), "--mailbox-dir", mb, "--json",
            ).returncode
            == 0
        )
    codex_data = json.loads((codex_cfg / "hooks.json").read_text(encoding="utf-8"))
    assert len(codex_data["hooks"]["Stop"]) == 2
    assert codex_data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo chime"
    assert codex_data["hooks"]["Stop"][1]["hooks"][0]["statusMessage"] == "aim-mailbox:w-codex"

    assert (
        _run_cli(
            "install-hooks", "--harness", "codex", "--agent", "w-codex",
            "--config-dir", str(codex_cfg), "--uninstall",
        ).returncode
        == 0
    )
    codex_after = json.loads((codex_cfg / "hooks.json").read_text(encoding="utf-8"))
    assert len(codex_after["hooks"]["Stop"]) == 1
    assert codex_after["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo chime"

    # 3. grok: writes hooks/aim-mailbox.json without touching sibling hook files
    grok_cfg = tmp_path / "grok_cfg"
    (grok_cfg / "hooks").mkdir(parents=True)
    sibling = grok_cfg / "hooks" / "existing.json"
    sibling.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    r_grok = _run_cli(
        "install-hooks", "--harness", "grok", "--agent", "w-grok",
        "--config-dir", str(grok_cfg), "--mailbox-dir", mb, "--json",
    )
    assert r_grok.returncode == 0
    grok_file = grok_cfg / "hooks" / "aim-mailbox.json"
    assert grok_file.is_file()
    grok_json = json.loads(grok_file.read_text(encoding="utf-8"))
    assert "SessionStart" in grok_json["hooks"] and "Stop" in grok_json["hooks"]
    assert _run_cli("install-hooks", "--harness", "grok", "--agent", "w-grok", "--config-dir", str(grok_cfg), "--uninstall").returncode == 0
    assert not grok_file.exists() and sibling.is_file()

    # 4. opencode (generic fallback leaves opencode.json untouched) & generic
    oc_cfg = tmp_path / "oc_cfg"
    oc_cfg.mkdir()
    oc_json = oc_cfg / "opencode.json"
    oc_json.write_text(json.dumps({"$schema": "https://opencode.ai/config.json"}), encoding="utf-8")
    r_oc = _run_cli(
        "install-hooks", "--harness", "opencode", "--agent", "w-oc",
        "--config-dir", str(oc_cfg), "--mailbox-dir", mb, "--json",
    )
    assert r_oc.returncode == 0
    oc_res = json.loads(r_oc.stdout)
    assert oc_res["mode"] == "generic_fallback"
    assert Path(oc_res["target_path"]).is_file()
    assert json.loads(oc_json.read_text(encoding="utf-8")) == {"$schema": "https://opencode.ai/config.json"}
    assert _run_cli("install-hooks", "--harness", "opencode", "--agent", "w-oc", "--config-dir", str(oc_cfg), "--uninstall").returncode == 0
    assert not Path(oc_res["target_path"]).exists()

    gen_cfg = tmp_path / "gen_cfg"
    r_gen = _run_cli(
        "install-hooks", "--harness", "generic", "--agent", "w-gen",
        "--config-dir", str(gen_cfg), "--mailbox-dir", mb, "--json",
    )
    assert r_gen.returncode == 0
    gen_path = Path(json.loads(r_gen.stdout)["target_path"])
    assert gen_path.is_file()
    assert _run_cli("install-hooks", "--harness", "generic", "--agent", "w-gen", "--config-dir", str(gen_cfg), "--uninstall").returncode == 0
    assert not gen_path.exists()

    # 5. agy-inject & agy-stop check formats + anti-loop continuationActive + <15ms empty latency
    r_empty_inj = _run_cli("check", "w-agy", "--inject", "--quiet", "--format", "agy-inject", "--mailbox-dir", mb)
    assert r_empty_inj.returncode == 0
    assert json.loads(r_empty_inj.stdout) == {}

    assert _run_cli("send", "--mailbox-dir", mb, "--from", "lead", "--to", "w-agy", "--body", "ping").returncode == 0
    r_full_inj = _run_cli("check", "w-agy", "--inject", "--quiet", "--format", "agy-inject", "--mailbox-dir", mb)
    assert r_full_inj.returncode == 0
    inj_payload = json.loads(r_full_inj.stdout)
    assert "1 unread message(s) for 'w-agy'" in inj_payload["injectSteps"][0]["systemMessage"]["systemMessage"]

    r_stop_cont = _run_cli("check", "w-agy", "--inject", "--quiet", "--format", "agy-stop", "--mailbox-dir", mb)
    assert r_stop_cont.returncode == 0
    assert json.loads(r_stop_cont.stdout)["decision"] == "continue"

    # Anti-loop: continuationActive=true on stdin suppresses repeated Stop continuation
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    r_stop_loop = subprocess.run(
        [sys.executable, "-m", "aim_mailbox", "check", "w-agy", "--inject", "--quiet", "--format", "agy-stop", "--mailbox-dir", mb],
        cwd=str(REPO_ROOT),
        env=env,
        input=json.dumps({"continuationActive": True}),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert r_stop_loop.returncode == 0
    assert json.loads(r_stop_loop.stdout) == {}

    if os.name != "nt":
        import time as _time

        sh_shim = REPO_ROOT / "bin" / "aim-mail"
        # Warmup once for bytecode cache, then measure empty-inbox check latency
        subprocess.run([str(sh_shim), "check", "empty-agent", "--inject", "--quiet", "--mailbox-dir", mb], check=True)
        samples: list[float] = []
        for _ in range(5):
            t0 = _time.perf_counter()
            r_fast = subprocess.run(
                [str(sh_shim), "check", "empty-agent", "--inject", "--quiet", "--mailbox-dir", mb],
                capture_output=True,
                text=True,
                check=True,
            )
            samples.append((_time.perf_counter() - t0) * 1000.0)
            assert r_fast.stdout == ""
        assert min(samples) < 15.0, f"Empty check --inject --quiet exceeded 15ms: {samples}"
