from __future__ import annotations

import os
import sys


def _entry() -> int:
    # Fast-path empty-inbox `check` before importing package/cli (~7ms CLI latency)
    if len(sys.argv) > 1 and "check" in sys.argv[1:]:
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        if pkg_dir not in sys.path:
            sys.path.insert(0, pkg_dir)
        from _fast_check import try_fast_empty_check

        rc = try_fast_empty_check(sys.argv[1:])
        if rc is not None:
            return rc

    from aim_mailbox.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_entry())
