from __future__ import annotations

import datetime as dt
import errno
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
BOX_STATES = ("unread", "claimed", "done", "dead")


class MailboxError(RuntimeError):
    """Raised when a mailbox validation, lookup, or state transition fails."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_iso(ts: dt.datetime | None = None) -> str:
    val = ts or utc_now()
    return val.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc_iso(raw: str) -> dt.datetime:
    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def validate_agent_id(raw: str) -> str:
    """Normalize (strip optional leading '@', lowercase) and validate a logical agent ID safely across POSIX & Windows."""
    if not isinstance(raw, str):
        raise MailboxError("Agent ID must be a non-empty string.")
    cleaned = raw.strip()
    if cleaned.startswith("@"):
        cleaned = cleaned[1:]
    if not cleaned or cleaned.endswith(".") or not AGENT_ID_RE.match(cleaned):
        raise MailboxError(
            f"Invalid agent ID {raw!r}: use 1-64 alphanumeric, '_', '.', or '-' characters (starting with alphanumeric, no trailing '.')."
        )
    cleaned = cleaned.lower()
    if ".." in cleaned or cleaned.split(".")[0].upper() in WINDOWS_RESERVED_NAMES:
        raise MailboxError(f"Reserved or unsafe agent ID: {raw!r}")
    return cleaned


def validate_message_id(raw: str, *, allow_json_ext: bool = True) -> str:
    """Validate a message ID or lookup prefix, blocking path separators, '..', and control characters."""
    if not isinstance(raw, str):
        raise MailboxError("Message ID must be a non-empty string.")
    cleaned = raw.strip()
    if allow_json_ext and cleaned.endswith(".json"):
        cleaned = cleaned[:-5]
    if not cleaned or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise MailboxError(f"Invalid message ID {raw!r}.")
    if "/" in cleaned or "\\" in cleaned or ".." in cleaned or cleaned.startswith("."):
        raise MailboxError(f"Unsafe message ID {raw!r}: path traversal characters are forbidden.")
    if not MESSAGE_ID_RE.match(cleaned):
        raise MailboxError(f"Invalid message ID {raw!r}.")
    return cleaned


def _chmod_safe(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_safe(path, 0o700)


def _ensure_within(path: Path, base_dir: Path, label: str = "path") -> Path:
    resolved_base = base_dir.resolve()
    resolved_target = path.resolve()
    if not resolved_target.is_relative_to(resolved_base):
        raise MailboxError(f"Path traversal blocked for {label}: {path}")
    return resolved_target


def resolve_mailbox_dir(explicit_dir: str | Path | None = None) -> Path:
    if explicit_dir:
        return Path(explicit_dir).expanduser().resolve()
    env_dir = os.environ.get("AIM_MAILBOX_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return (Path.home() / ".aim" / "mailbox").resolve()


def _is_transient_fs_error(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "errno", None) in (
        errno.EACCES,
        errno.EPERM,
        errno.EBUSY,
    )


def _replace_with_retry(src: Path, dst: Path, retries: int = 6, delay: float = 0.02) -> None:
    last_exc: OSError | None = None
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if not _is_transient_fs_error(exc):
                raise
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(delay * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def _unlink_with_retry(path: Path, retries: int = 6, delay: float = 0.02) -> None:
    last_exc: OSError | None = None
    for attempt in range(retries):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if not _is_transient_fs_error(exc):
                raise
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(delay * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def _read_text_with_retry(path: Path, retries: int = 6, delay: float = 0.02) -> str:
    last_exc: OSError | None = None
    for attempt in range(retries):
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            if isinstance(exc, FileNotFoundError) or not _is_transient_fs_error(exc):
                raise
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(delay * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    return ""


def write_json_atomic(dst: Path, payload: Any, tmp_dir: Path | None = None) -> None:
    _mkdir_private(dst.parent)
    stage_dir = tmp_dir or dst.parent
    _mkdir_private(stage_dir)
    tmp_path = stage_dir / f".tmp_{dst.name}_{uuid.uuid4().hex[:8]}"
    encoded = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    try:
        fd = os.open(str(tmp_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(encoded)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        _chmod_safe(tmp_path, 0o600)
        _replace_with_retry(tmp_path, dst)
        _chmod_safe(dst, 0o600)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def generate_message_id(now: dt.datetime | None = None) -> str:
    ts = (now or utc_now()).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


class MailboxStore:
    """Lock-free Maildir JSON store safe across Linux, macOS, native Windows 11 NTFS, and WSL2 /mnt/c."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root = resolve_mailbox_dir(root_dir)
        self.boxes_dir = self.root / "boxes"
        self.locks_dir = self.root / "locks"
        self.agents_file = self.root / "agents.json"

    def ensure_root(self) -> None:
        _mkdir_private(self.root)
        _mkdir_private(self.boxes_dir)
        _mkdir_private(self.locks_dir)

    def ensure_agent_box(self, agent_id: str) -> Path:
        self.ensure_root()
        aid = validate_agent_id(agent_id)
        box = self.boxes_dir / aid
        _ensure_within(box, self.boxes_dir, "agent box")
        _mkdir_private(box)
        for sub in ("tmp", *BOX_STATES):
            _mkdir_private(box / sub)
        return box

    # ------------------------------------------------------------------
    # Logical Agent Address Book (agents.json)
    # ------------------------------------------------------------------
    def _read_agents_raw(self, *, strict: bool = False) -> dict[str, dict[str, Any]]:
        if not self.agents_file.is_file():
            return {}
        try:
            raw_text = _read_text_with_retry(self.agents_file)
            data = json.loads(raw_text)
            if isinstance(data, dict) and isinstance(data.get("agents"), dict):
                return data["agents"]
            if strict:
                raise MailboxError(f"Corrupt agent registry in {self.agents_file}: expected object with 'agents' dict.")
        except (OSError, json.JSONDecodeError) as exc:
            if strict:
                raise MailboxError(f"Failed to read agent registry {self.agents_file}: {exc}") from exc
        return {}

    def _acquire_agents_lock(self, timeout: float = 5.0) -> tuple[Path, str]:
        self.ensure_root()
        lock_path = self.locks_dir / "_agents.lock.json"
        token = uuid.uuid4().hex
        deadline = time.monotonic() + max(0.5, float(timeout))
        while True:
            now = utc_now()
            payload = {
                "token": token,
                "pid": os.getpid(),
                "acquired_at": utc_iso(now),
                "expires_at": utc_iso(now + dt.timedelta(seconds=10.0)),
            }
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                    fh.write("\n")
                _chmod_safe(lock_path, 0o600)
                return lock_path, token
            except FileExistsError:
                try:
                    existing = json.loads(_read_text_with_retry(lock_path))
                    if utc_now() >= parse_utc_iso(existing["expires_at"]):
                        stale_tok = str(existing.get("token") or "")
                        if stale_tok:
                            self._reclaim_stale_lock(lock_path, "_agents", stale_tok)
                except (OSError, KeyError, ValueError, json.JSONDecodeError):
                    try:
                        if lock_path.exists() and (time.time() - lock_path.stat().st_mtime) > 10.0:
                            _unlink_with_retry(lock_path)
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise MailboxError(f"Timed out waiting for agent registry lock: {lock_path}")
                time.sleep(0.01)

    def _release_agents_lock(self, lock_path: Path, token: str) -> None:
        try:
            existing = json.loads(_read_text_with_retry(lock_path))
            if existing.get("token") == token:
                _unlink_with_retry(lock_path)
        except (OSError, json.JSONDecodeError):
            pass

    def load_agents(self) -> dict[str, dict[str, Any]]:
        return self._read_agents_raw(strict=False)

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        aid = validate_agent_id(agent_id)
        return self.load_agents().get(aid)

    def register_agent(
        self,
        agent_id: str,
        *,
        driver: str | None = None,
        cwd: str | None = None,
        session_id: str | None = None,
        tmux_session: str | None = None,
        http_url: str | None = None,
        command: list[str] | None = None,
        binary: str | None = None,
        summary_db: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_root()
        aid = validate_agent_id(agent_id)
        self.ensure_agent_box(aid)
        lock_path, token = self._acquire_agents_lock()
        try:
            agents = self._read_agents_raw(strict=True)
            entry = dict(agents.get(aid) or {"agent_id": aid, "driver": "none"})
            entry["agent_id"] = aid
            if driver is not None:
                entry["driver"] = driver.strip()
            if cwd is not None:
                entry["cwd"] = cwd
            if session_id is not None:
                entry["session_id"] = session_id
            if tmux_session is not None:
                entry["tmux_session"] = tmux_session
            if http_url is not None:
                entry["http_url"] = http_url
            if command is not None:
                entry["command"] = list(command)
            if binary is not None:
                entry["binary"] = binary
            if summary_db is not None:
                entry["summary_db"] = summary_db
            entry["updated_at"] = utc_iso()
            agents[aid] = entry
            write_json_atomic(self.agents_file, {"schema_version": 1, "agents": agents})
            return entry
        finally:
            self._release_agents_lock(lock_path, token)

    def unregister_agent(self, agent_id: str) -> bool:
        aid = validate_agent_id(agent_id)
        lock_path, token = self._acquire_agents_lock()
        try:
            agents = self._read_agents_raw(strict=True)
            if aid not in agents:
                return False
            del agents[aid]
            write_json_atomic(self.agents_file, {"schema_version": 1, "agents": agents})
            return True
        finally:
            self._release_agents_lock(lock_path, token)

    # ------------------------------------------------------------------
    # Per-Agent Dispatch Lease Locks (locks/<agent>.lock.json)
    # ------------------------------------------------------------------
    def _reclaim_stale_lock(self, lock_path: Path, lock_key: str, expected_token: str | None) -> bool:
        guard_tag = re.sub(r"[^A-Za-z0-9_.-]", "_", expected_token or "corrupt")[:40]
        guard_path = self.locks_dir / f".reclaim_{lock_key}_{guard_tag}"
        try:
            gfd = os.open(str(guard_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(gfd)
        except FileExistsError:
            try:
                if (time.time() - guard_path.stat().st_mtime) > 5.0:
                    _unlink_with_retry(guard_path)
            except OSError:
                pass
            return False

        try:
            if not lock_path.exists():
                return False
            if expected_token is not None:
                try:
                    current = json.loads(_read_text_with_retry(lock_path))
                    if current.get("token") != expected_token:
                        return False
                except (OSError, json.JSONDecodeError):
                    return False
            _unlink_with_retry(lock_path)
            return True
        except OSError:
            return False
        finally:
            try:
                guard_path.unlink()
            except OSError:
                pass

    def acquire_agent_lock(self, agent_id: str, ttl_seconds: float = 600.0) -> str | None:
        self.ensure_root()
        aid = validate_agent_id(agent_id)
        lock_path = self.locks_dir / f"{aid}.lock.json"
        token = uuid.uuid4().hex
        now = utc_now()
        expires = now + dt.timedelta(seconds=max(1.0, float(ttl_seconds)))
        payload = {
            "agent_id": aid,
            "token": token,
            "pid": os.getpid(),
            "acquired_at": utc_iso(now),
            "expires_at": utc_iso(expires),
        }

        for _ in range(2):
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                    fh.write("\n")
                _chmod_safe(lock_path, 0o600)
                return token
            except FileExistsError:
                try:
                    existing = json.loads(_read_text_with_retry(lock_path))
                    exp = parse_utc_iso(existing["expires_at"])
                    if utc_now() >= exp:
                        stale_tok = str(existing.get("token") or "")
                        if stale_tok and self._reclaim_stale_lock(lock_path, aid, stale_tok):
                            continue
                except (OSError, KeyError, ValueError, json.JSONDecodeError):
                    try:
                        if lock_path.exists() and (time.time() - lock_path.stat().st_mtime) > ttl_seconds:
                            if self._reclaim_stale_lock(lock_path, aid, None):
                                continue
                    except OSError:
                        pass
                return None
        return None

    def release_agent_lock(self, agent_id: str, token: str) -> bool:
        aid = validate_agent_id(agent_id)
        lock_path = self.locks_dir / f"{aid}.lock.json"
        if not lock_path.is_file():
            return False
        try:
            existing = json.loads(_read_text_with_retry(lock_path))
            if existing.get("token") != token:
                return False
            _unlink_with_retry(lock_path)
            return True
        except (OSError, json.JSONDecodeError):
            return False

    # ------------------------------------------------------------------
    # Envelope Operations
    # ------------------------------------------------------------------
    def _lookup_thread_bounds(self, thread_id: str) -> tuple[int, int] | None:
        """Return (max_hop_count, min_max_hops) across existing envelopes for a thread_id, if any."""
        if not self.boxes_dir.is_dir():
            return None
        found = False
        highest_hop = 0
        thread_max_hops: int | None = None
        for box in self.boxes_dir.iterdir():
            if not box.is_dir():
                continue
            for st in BOX_STATES:
                st_dir = box / st
                if not st_dir.is_dir():
                    continue
                for p in st_dir.glob("*.json"):
                    try:
                        data = self._load_envelope_file(p, st_dir)
                    except (OSError, json.JSONDecodeError, MailboxError):
                        continue
                    if data.get("thread_id") != thread_id:
                        continue
                    found = True
                    highest_hop = max(highest_hop, int(data.get("hop_count", 0)))
                    env_max = int(data.get("max_hops", 1))
                    thread_max_hops = env_max if thread_max_hops is None else min(thread_max_hops, env_max)
        if not found or thread_max_hops is None:
            return None
        return highest_hop, thread_max_hops

    def send_envelope(
        self,
        *,
        from_agent: str,
        to_agent: str,
        body: str,
        subject: str = "",
        reply_to: str | None = None,
        refs: list[str] | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        hop_count: int = 0,
        max_hops: int | None = 1,
        ack_required: bool = True,
        operator_approved: bool = False,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        sender = validate_agent_id(from_agent)
        recipient = validate_agent_id(to_agent)
        reply_target = validate_agent_id(reply_to) if reply_to else sender
        if not isinstance(body, str) or not body.strip():
            raise MailboxError("Message body must be non-empty.")

        effective_hop = int(hop_count)
        effective_max_hops = 1 if max_hops is None else int(max_hops)
        if thread_id and in_reply_to is None:
            bounds = self._lookup_thread_bounds(thread_id)
            if bounds is not None:
                highest_hop, thread_max_hops = bounds
                effective_hop = max(effective_hop, highest_hop + 1)
                effective_max_hops = thread_max_hops if max_hops is None else min(int(max_hops), thread_max_hops)
                if effective_hop > effective_max_hops:
                    raise MailboxError(
                        f"Thread '{thread_id}' hop limit exceeded ({effective_hop} > {effective_max_hops})."
                    )

        box = self.ensure_agent_box(recipient)
        now = utc_now()
        msg_id = generate_message_id(now)
        envelope: dict[str, Any] = {
            "id": msg_id,
            "thread_id": thread_id or f"thr_{uuid.uuid4().hex[:12]}",
            "in_reply_to": in_reply_to,
            "from": sender,
            "to": recipient,
            "reply_to": reply_target,
            "subject": subject.strip(),
            "body": body.strip(),
            "refs": [str(r) for r in (refs or []) if str(r).strip()],
            "hop_count": effective_hop,
            "max_hops": effective_max_hops,
            "ack_required": bool(ack_required),
            "operator_approved": bool(operator_approved),
            "attempts": 0,
            "max_attempts": max(1, int(max_attempts)),
            "created_at": utc_iso(now),
        }
        write_json_atomic(box / "unread" / f"{msg_id}.json", envelope, tmp_dir=box / "tmp")
        out = dict(envelope)
        out["state"] = "unread"
        return out

    def _load_envelope_file(self, path: Path, state_dir: Path) -> dict[str, Any]:
        _ensure_within(path, state_dir, "envelope")
        canonical_id = validate_message_id(path.stem, allow_json_ext=False)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise MailboxError(f"Invalid envelope format in {path.name}: expected JSON object.")
        if "id" in data and data["id"] is not None:
            validate_message_id(str(data["id"]), allow_json_ext=False)
        data["id"] = canonical_id
        return data

    def list_inbox(self, agent_id: str, state: str = "unread") -> list[dict[str, Any]]:
        aid = validate_agent_id(agent_id)
        if state == "all":
            states: tuple[str, ...] = BOX_STATES
        elif state in BOX_STATES:
            states = (state,)
        else:
            raise MailboxError(f"Unsupported inbox state {state!r}; choose from {', '.join((*BOX_STATES, 'all'))}.")

        box = self.boxes_dir / aid
        if not box.is_dir():
            return []

        results: list[dict[str, Any]] = []
        for st in states:
            st_dir = box / st
            if not st_dir.is_dir():
                continue
            for p in sorted(st_dir.glob("*.json")):
                try:
                    data = self._load_envelope_file(p, st_dir)
                    data["state"] = st
                    results.append(data)
                except (OSError, json.JSONDecodeError, MailboxError):
                    continue
        results.sort(key=lambda d: str(d.get("id", "")))
        return results

    def _find_envelope(
        self,
        agent_id: str,
        msg_id_or_prefix: str | None = None,
        states: tuple[str, ...] = BOX_STATES,
    ) -> tuple[str, Path, dict[str, Any]]:
        aid = validate_agent_id(agent_id)
        box = self.boxes_dir / aid
        if not box.is_dir():
            raise MailboxError(f"No mailbox found for agent '{aid}'.")

        if msg_id_or_prefix is None:
            for st in ("unread", "claimed", *states):
                st_dir = box / st
                if not st_dir.is_dir():
                    continue
                files = sorted(st_dir.glob("*.json"))
                if files:
                    data = self._load_envelope_file(files[0], st_dir)
                    return st, files[0], data
            raise MailboxError(f"No messages found for agent '{aid}'.")

        needle = validate_message_id(msg_id_or_prefix, allow_json_ext=True)

        matches: list[tuple[str, Path, Path]] = []
        for st in states:
            st_dir = box / st
            if not st_dir.is_dir():
                continue
            exact = st_dir / f"{needle}.json"
            if exact.is_file():
                data = self._load_envelope_file(exact, st_dir)
                return st, exact, data
            for cand in sorted(st_dir.glob("*.json")):
                if cand.stem.startswith(needle) or cand.stem.endswith(needle):
                    matches.append((st, st_dir, cand))

        if not matches:
            raise MailboxError(f"Message {msg_id_or_prefix!r} not found for agent '{aid}'.")
        if len(matches) > 1:
            ids = ", ".join(p.stem for _, _, p in matches[:5])
            raise MailboxError(f"Ambiguous message prefix {msg_id_or_prefix!r} (matches: {ids}).")
        st, st_dir, found = matches[0]
        data = self._load_envelope_file(found, st_dir)
        return st, found, data

    def claim_envelope(self, agent_id: str, msg_id: str) -> dict[str, Any] | None:
        """Atomically claim an unread envelope (unread -> claimed). Returns None if another worker claimed it."""
        aid = validate_agent_id(agent_id)
        clean_id = validate_message_id(msg_id, allow_json_ext=True)
        box = self.ensure_agent_box(aid)
        unread_path = box / "unread" / f"{clean_id}.json"
        claimed_path = box / "claimed" / f"{clean_id}.json"
        _ensure_within(unread_path, box / "unread", "unread envelope")
        _ensure_within(claimed_path, box / "claimed", "claimed envelope")
        try:
            _replace_with_retry(unread_path, claimed_path)
        except FileNotFoundError:
            return None

        data = self._load_envelope_file(claimed_path, box / "claimed")
        data["attempts"] = int(data.get("attempts", 0)) + 1
        data["claimed_at"] = utc_iso()
        write_json_atomic(claimed_path, data, tmp_dir=box / "tmp")
        out = dict(data)
        out["state"] = "claimed"
        return out

    def _transition_envelope(
        self,
        src_path: Path,
        dst_path: Path,
        data: dict[str, Any],
        box: Path,
        target_state: str,
    ) -> None:
        _ensure_within(dst_path, box / target_state, "destination envelope")
        _ensure_within(src_path, box, "source envelope")
        if src_path != dst_path:
            _replace_with_retry(src_path, dst_path)
        write_json_atomic(dst_path, data, tmp_dir=box / "tmp")

    def release_claim(
        self,
        agent_id: str,
        msg_id: str,
        *,
        last_error: str = "",
        delivery: dict[str, Any] | None = None,
        defer: bool = False,
    ) -> dict[str, Any]:
        """Return a claimed envelope to unread/ for retry, or quarantine to dead/ if max_attempts reached."""
        aid = validate_agent_id(agent_id)
        clean_id = validate_message_id(msg_id, allow_json_ext=True)
        box = self.ensure_agent_box(aid)
        st, path, data = self._find_envelope(aid, clean_id, states=("claimed", "unread", "done", "dead"))
        if st in ("done", "dead"):
            out = dict(data)
            out["state"] = st
            return out
        done_path = box / "done" / f"{data['id']}.json"
        if done_path.is_file():
            done_data = self._load_envelope_file(done_path, box / "done")
            out = dict(done_data)
            out["state"] = "done"
            return out

        if last_error:
            data["last_error"] = last_error
        if delivery is not None:
            data["delivery"] = delivery

        attempts = int(data.get("attempts", 1))
        if defer and attempts > 0:
            attempts -= 1
            data["attempts"] = attempts
        max_attempts = int(data.get("max_attempts", 3))
        if not defer and attempts >= max_attempts:
            data["dead_reason"] = last_error or "MAX_ATTEMPTS_EXCEEDED"
            data["quarantined_at"] = utc_iso()
            dst = box / "dead" / f"{data['id']}.json"
            target_state = "dead"
        else:
            data.pop("claimed_at", None)
            dst = box / "unread" / f"{data['id']}.json"
            target_state = "unread"

        self._transition_envelope(path, dst, data, box, target_state)
        out = dict(data)
        out["state"] = target_state
        return out

    def reclaim_expired_claims(self, agent_id: str, lease_seconds: float = 900.0) -> list[str]:
        aid = validate_agent_id(agent_id)
        box = self.boxes_dir / aid
        claimed_dir = box / "claimed"
        if not claimed_dir.is_dir():
            return []
        reclaimed: list[str] = []
        now = utc_now()
        for p in sorted(claimed_dir.glob("*.json")):
            try:
                data = self._load_envelope_file(p, claimed_dir)
                claimed_at = parse_utc_iso(data["claimed_at"]) if data.get("claimed_at") else None
                expired = (
                    (now - claimed_at).total_seconds() >= lease_seconds
                    if claimed_at
                    else (time.time() - p.stat().st_mtime) >= lease_seconds
                )
                if expired:
                    self.release_claim(aid, data["id"], last_error="CLAIM_LEASE_EXPIRED")
                    reclaimed.append(str(data["id"]))
            except (OSError, KeyError, ValueError, json.JSONDecodeError, MailboxError):
                continue
        return reclaimed

    def record_wake_delivery(
        self,
        agent_id: str,
        msg_id: str,
        delivery: dict[str, Any],
        *,
        auto_complete_if_no_ack: bool = True,
    ) -> dict[str, Any]:
        """Persist wake delivery receipt on a claimed message (moving to done/ if ack_required is False)."""
        aid = validate_agent_id(agent_id)
        clean_id = validate_message_id(msg_id, allow_json_ext=True)
        box = self.ensure_agent_box(aid)
        st, path, data = self._find_envelope(aid, clean_id, states=("claimed", "unread", "done", "dead"))
        if st == "dead":
            out = dict(data)
            out["state"] = "dead"
            return out
        done_path = box / "done" / f"{data['id']}.json"
        if st != "done" and done_path.is_file():
            done_data = self._load_envelope_file(done_path, box / "done")
            done_data["delivery"] = delivery
            write_json_atomic(done_path, done_data, tmp_dir=box / "tmp")
            out = dict(done_data)
            out["state"] = "done"
            return out
        data["delivery"] = delivery
        if st == "done" or (auto_complete_if_no_ack and not data.get("ack_required", True)):
            data.setdefault("acked_at", utc_iso())
            dst = done_path
            target_state = "done"
        else:
            dst = box / "claimed" / f"{data['id']}.json"
            target_state = "claimed"

        self._transition_envelope(path, dst, data, box, target_state)
        out = dict(data)
        out["state"] = target_state
        return out

    def read_message(
        self,
        agent_id: str,
        msg_id_or_prefix: str | None = None,
        *,
        claim: bool = False,
        ack: bool = False,
    ) -> dict[str, Any]:
        aid = validate_agent_id(agent_id)
        st, _, data = self._find_envelope(aid, msg_id_or_prefix)
        msg_id = str(data["id"])
        if ack:
            return self.ack_message(aid, msg_id)
        if claim and st == "unread":
            claimed = self.claim_envelope(aid, msg_id)
            if claimed is not None:
                return claimed
            st, _, data = self._find_envelope(aid, msg_id)
        out = dict(data)
        out["state"] = st
        return out

    def ack_message(
        self,
        agent_id: str,
        msg_id_or_prefix: str,
        *,
        note: str = "",
        delivery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        aid = validate_agent_id(agent_id)
        box = self.ensure_agent_box(aid)
        _, path, data = self._find_envelope(aid, msg_id_or_prefix, states=("unread", "claimed", "done"))
        data["acked_at"] = data.get("acked_at") or utc_iso()
        if note.strip():
            data["ack_note"] = note.strip()
        if delivery is not None:
            data["delivery"] = delivery
        dst = box / "done" / f"{data['id']}.json"
        self._transition_envelope(path, dst, data, box, "done")
        out = dict(data)
        out["state"] = "done"
        return out

    def quarantine_message(
        self,
        agent_id: str,
        msg_id_or_prefix: str,
        *,
        reason: str,
        delivery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        aid = validate_agent_id(agent_id)
        box = self.ensure_agent_box(aid)
        _, path, data = self._find_envelope(aid, msg_id_or_prefix, states=("unread", "claimed", "dead"))
        data["dead_reason"] = reason
        data["quarantined_at"] = utc_iso()
        if delivery is not None:
            data["delivery"] = delivery
        dst = box / "dead" / f"{data['id']}.json"
        self._transition_envelope(path, dst, data, box, "dead")
        out = dict(data)
        out["state"] = "dead"
        return out

    def reply_message(
        self,
        agent_id: str,
        msg_id_or_prefix: str,
        *,
        body: str,
        subject: str | None = None,
        refs: list[str] | None = None,
        ack_parent: bool = True,
        ack_required: bool = False,
    ) -> dict[str, Any]:
        aid = validate_agent_id(agent_id)
        try:
            _, _, parent = self._find_envelope(aid, msg_id_or_prefix, states=("unread", "claimed", "done"))
        except MailboxError:
            # Give a specific diagnostic if the parent is quarantined in dead/
            try:
                st_dead, _, _ = self._find_envelope(aid, msg_id_or_prefix, states=("dead",))
                if st_dead == "dead":
                    raise MailboxError(
                        f"Cannot reply to quarantined dead/ message {msg_id_or_prefix!r}; re-queue it first."
                    )
            except MailboxError as dead_exc:
                if "quarantined dead/" in str(dead_exc):
                    raise
            raise

        next_hop = int(parent.get("hop_count", 0)) + 1
        max_hops = int(parent.get("max_hops", 1))
        if next_hop > max_hops:
            raise MailboxError(
                f"Cannot reply to '{parent['id']}': hop limit exceeded ({next_hop} > {max_hops})."
            )

        parent_sub = str(parent.get("subject", "")).strip()
        if subject is not None:
            reply_sub = subject.strip()
        elif parent_sub and not parent_sub.lower().startswith("re:"):
            reply_sub = f"Re: {parent_sub}"
        else:
            reply_sub = parent_sub

        sent = self.send_envelope(
            from_agent=aid,
            to_agent=str(parent.get("reply_to") or parent["from"]),
            reply_to=aid,
            subject=reply_sub,
            body=body,
            refs=refs,
            thread_id=str(parent["thread_id"]),
            in_reply_to=str(parent["id"]),
            hop_count=next_hop,
            max_hops=max_hops,
            ack_required=ack_required,
            operator_approved=True,
        )
        if ack_parent:
            self.ack_message(aid, str(parent["id"]), note=f"Replied via {sent['id']}")
        return sent

    def wait_for_mail(
        self,
        agent_id: str,
        *,
        thread_id: str | None = None,
        timeout: float = 60.0,
        poll_interval: float = 0.25,
        claim: bool = False,
    ) -> dict[str, Any] | None:
        aid = validate_agent_id(agent_id)
        started_at = utc_now()
        deadline = time.monotonic() + max(0.0, float(timeout))
        interval = max(0.05, float(poll_interval))
        while True:
            unread = self.list_inbox(aid, state="unread")
            for msg in unread:
                if thread_id is None or msg.get("thread_id") == thread_id:
                    if claim:
                        claimed = self.claim_envelope(aid, str(msg["id"]))
                        if claimed is not None:
                            return claimed
                        continue
                    return msg

            # Also detect envelopes claimed/completed by a concurrent watcher after wait started
            for post_state in ("claimed", "done"):
                for msg in self.list_inbox(aid, state=post_state):
                    if thread_id is not None and msg.get("thread_id") != thread_id:
                        continue
                    ts_raw = msg.get("created_at") or msg.get("claimed_at") or msg.get("acked_at")
                    if not ts_raw:
                        continue
                    try:
                        if parse_utc_iso(str(ts_raw)) >= started_at:
                            return msg
                    except ValueError:
                        continue

            if time.monotonic() >= deadline:
                return None
            time.sleep(min(interval, max(0.01, deadline - time.monotonic())))
