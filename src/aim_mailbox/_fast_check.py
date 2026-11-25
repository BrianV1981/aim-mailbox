from __future__ import annotations

import os
import sys

_FAST_AGENT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


def try_fast_empty_check(argv: list[str]) -> int | None:
    """Zero-import fast path for `check` when unread/ is empty (<15ms CLI latency)."""
    if not argv or "check" not in argv:
        return None
    mb_dir: str | None = None
    agent: str | None = None
    inject = False
    quiet = False
    fmt = "text"
    saw_check = False
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("-h", "--help", "--version", "--json"):
            return None
        if tok == "--mailbox-dir":
            if i + 1 >= len(argv):
                return None
            mb_dir = argv[i + 1]
            i += 2
            continue
        if tok.startswith("--mailbox-dir="):
            mb_dir = tok.split("=", 1)[1]
            i += 1
            continue
        if not saw_check:
            if tok == "check":
                saw_check = True
                i += 1
                continue
            return None
        if tok == "--inject":
            inject = True
        elif tok == "--quiet":
            quiet = True
        elif tok == "--format":
            if i + 1 >= len(argv):
                return None
            fmt = argv[i + 1]
            if fmt not in ("text", "agy-inject", "agy-stop"):
                return None
            i += 1
        elif tok.startswith("--format="):
            fmt = tok.split("=", 1)[1]
            if fmt not in ("text", "agy-inject", "agy-stop"):
                return None
        elif tok.startswith("-"):
            return None
        elif agent is None:
            agent = tok
        else:
            return None
        i += 1

    if not saw_check or not agent:
        return None
    cleaned_agent = agent.strip()
    if cleaned_agent.startswith("@"):
        cleaned_agent = cleaned_agent[1:]
    if (
        not cleaned_agent
        or len(cleaned_agent) > 64
        or not cleaned_agent[0].isalnum()
        or cleaned_agent.endswith(".")
        or ".." in cleaned_agent
        or not all(ch in _FAST_AGENT_CHARS for ch in cleaned_agent)
    ):
        return None
    cleaned_agent = cleaned_agent.lower()

    raw_mb = mb_dir or os.environ.get("AIM_MAILBOX_DIR", "").strip() or os.path.join(os.path.expanduser("~"), ".aim", "mailbox")
    unread_dir = os.path.join(os.path.abspath(os.path.expanduser(raw_mb)), "boxes", cleaned_agent, "unread")
    try:
        with os.scandir(unread_dir) as it:
            for entry in it:
                if entry.name.endswith(".json") and entry.is_file():
                    return None
    except FileNotFoundError:
        pass
    except OSError:
        return None

    if fmt in ("agy-inject", "agy-stop"):
        sys.stdout.write("{}\n")
    elif not quiet and not inject:
        sys.stdout.write(f"0 unread messages for '{cleaned_agent}'.\n")
    return 0
