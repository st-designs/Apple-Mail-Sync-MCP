"""Runs the bundled AppleScripts.

Scripts are static files on disk and every dynamic value is passed as a separate
argv entry, so there is no script text to escape and no injection surface. execve
hands the arguments to osascript verbatim: no shell, no re-parsing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent / "applescript"
DEFAULT_TIMEOUT = 45


class AppleScriptError(RuntimeError):
    pass


def mail_is_running() -> bool:
    r = subprocess.run(["pgrep", "-x", "Mail"], capture_output=True)
    return r.returncode == 0


def ensure_mail_running(timeout: int = 30) -> None:
    """Mail has to be up to compose or fetch. Launch it without stealing focus."""
    if mail_is_running():
        return
    subprocess.run(["open", "-g", "-a", "Mail"], capture_output=True, timeout=timeout)
    import time

    for _ in range(timeout * 2):
        if mail_is_running():
            time.sleep(1.5)  # let the scripting bridge finish coming up
            return
        time.sleep(0.5)
    raise AppleScriptError("Mail.app did not start; it is required for this operation.")


def run(script: str, *args: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    path = SCRIPT_DIR / f"{script}.applescript"
    if not path.is_file():
        raise AppleScriptError(f"Missing script: {path}")

    argv = ["osascript", str(path), *[str(a) for a in args]]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AppleScriptError(
            f"{script} timed out after {timeout}s. Mail may be showing a dialog that needs attention."
        ) from None

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "Not authorized" in err or "-1743" in err:
            raise AppleScriptError(
                "macOS blocked control of Mail. Grant permission under "
                "System Settings > Privacy & Security > Automation."
            )
        raise AppleScriptError(err or f"{script} failed with status {proc.returncode}")
    return (proc.stdout or "").strip()
