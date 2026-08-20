"""The argv boundary is the reason this server needs no AppleScript escaping.

Reference implementations concatenate email data into script source and defend it
with a hand-rolled escape function. These tests assert the stronger property:
hostile input survives as literal data and is never evaluated.
"""

import subprocess

import pytest

ECHO = "on run argv\nreturn (item 1 of argv)\nend run"

HOSTILE = [
    'x" & (do shell script "echo OWNED") & "y',           # break out of a string literal
    'a\\"b\\\\c',                                          # backslash and quote soup
    "line1\nline2\rline3\tline4",                          # newlines would break generated source
    'end tell\ntell application "Finder" to delete',       # terminate the enclosing block
    "   unicode line separators",
    "'; osascript -e 'do shell script \"echo OWNED\"",     # shell-style injection
    'ümlaut 漢字 🎯 emoji',
]


@pytest.mark.parametrize("payload", HOSTILE)
def test_argv_round_trips_verbatim(payload):
    proc = subprocess.run(
        ["osascript", "-e", ECHO, "--", payload],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    returned = proc.stdout.rstrip("\n")
    # AppleScript normalises bare CR to LF on the way out; compare on that basis.
    assert returned.replace("\r", "\n") == payload.replace("\r", "\n")


def test_shell_command_is_not_evaluated():
    payload = 'x" & (do shell script "echo OWNED") & "y'
    proc = subprocess.run(
        ["osascript", "-e", ECHO, "--", payload],
        capture_output=True, text=True, timeout=30,
    )
    out = proc.stdout.rstrip("\n")
    # If evaluated, the literal would be replaced by the command's output.
    assert out == payload
    assert "do shell script" in out, "the text must survive as inert data"
    assert out != "xOWNEDy"


def test_no_generated_script_text_anywhere():
    """No module may build AppleScript source by string formatting."""
    import pathlib

    for path in pathlib.Path("mailsync").glob("*.py"):
        src = path.read_text()
        for marker in ("tell application", "osascript -e", "make new outgoing"):
            assert marker not in src, f"{path} appears to generate AppleScript inline"
