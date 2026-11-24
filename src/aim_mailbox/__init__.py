"""aim-mailbox: Zero-daemon, zero-LLM local Maildir post office and wake dispatcher."""

from __future__ import annotations

__version__ = "0.1.2"

from .dispatcher import pulse_mailbox, watch_mailbox
from .drivers import DRIVER_NAMES, install_agy_sidecar, uninstall_agy_sidecar
from .store import MailboxError, MailboxStore, validate_agent_id

__all__ = [
    "__version__",
    "DRIVER_NAMES",
    "MailboxError",
    "MailboxStore",
    "install_agy_sidecar",
    "pulse_mailbox",
    "uninstall_agy_sidecar",
    "validate_agent_id",
    "watch_mailbox",
]
