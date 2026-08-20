# MailSyncMCP

Search, read and reply to every account in Apple Mail from Claude, without
handing over a single password.

Works with Gmail, Microsoft 365, iCloud and custom-domain IMAP alike, because it
reads what Mail has already synced rather than talking to each provider. Runs
entirely on your Mac. Nothing is uploaded anywhere.

## Why it works this way

Apple Mail already syncs every account you have and already holds their
credentials in the Keychain, refreshing them on its own. This server reads Mail's
local store and asks Mail to compose. It never sees a password or a token, never
stores one, and never triggers a re-authentication. Adding an account means adding
it in Mail; nothing here changes.

The alternative, talking to each provider directly, means a Google Cloud project,
an Azure app registration, and a refresh token per account. Worse, work and
university accounts are usually locked down so that a third-party OAuth app needs
administrator consent, which is often simply refused. Reading what Mail has
already synced sidesteps all of it.

## Safety

Reading and searching are free. Sending is not.

1. **No credentials.** Nothing to leak or expire.
2. **Two-phase commit.** `prepare_*` tools validate a request, render a full
   preview, and return a single-use token that expires in five minutes. They
   change nothing. `commit_action(token)` is the only tool that acts, and it
   takes a token and nothing else, so the message that goes out is always the
   message that was shown.
3. **Client permission prompt** on `commit_action`.
4. **Untrusted-content fencing.** Message bodies come back wrapped and marked as
   data. Because sending needs a token that only a rendered preview can mint, an
   instruction buried in an email cannot cause mail to be sent.
5. **Audit log** at `~/.local/share/mailsync/audit.jsonl`, append-only.

The server speaks stdio only. There is no listener, no daemon and no timer, so it
acts only when a tool is called from the chat session.

Set `MAILSYNC_READ_ONLY=1` to disable composing entirely for a session.

### On AppleScript

Every script in `applescript/` is a static file that receives its values through
`argv`. No script text is ever generated, so quotes, backslashes, newlines and
AppleScript syntax in an email survive as literal data. `tests/test_applescript_safety.py`
asserts this against hostile payloads. Other Apple Mail MCP servers concatenate
message data into script source and defend it with an escaping function; this
one has no injection surface to escape.

## Tools

Read: `list_accounts`, `search_mail`, `get_message`, `get_thread`,
`list_attachments`, `save_attachment`, `mailbox_stats`, `open_in_mail`,
`refresh_index`

Compose: `prepare_send`, `prepare_reply`, `prepare_forward`, `list_pending`,
`commit_action`

There is deliberately nothing that flags, moves, archives or deletes mail. The
only thing this server can change is that a new message gets sent or drafted.

## Links back to Mail

Every message returned carries a `message://` URL built from its RFC-822
Message-ID. Clicking one opens that message in Apple Mail, so answers in chat
stay traceable to the real thing. Messages Mail has not stored locally have no
Message-ID and so get no link.

Chat clients sanitise link targets to an allowlist of schemes (http, https,
mailto), so a `message://` link renders as dead text rather than something you can
click. The `open_in_mail` tool exists for that reason: ask to open a message and
it goes straight to Mail. The URL is still printed so it can be copied or used
outside chat.

`open_in_mail` takes `whole_thread=True`, but Mail opens each message in its own
window: there is no scriptable threaded view. `set selected messages` on the main
viewer was tested repeatedly and does not work on macOS 26 in conversation mode,
returning objects the AppleScript bridge cannot coerce back. `get_thread` is the
better way to read a conversation.

A link always opens a single message, not the conversation around it. Mail
registers only three URL schemes (`mailto`, `message`, `mail-pref-pane`) and none
of them addresses a thread. Driving Mail's own threaded list through AppleScript
was tried and abandoned: on macOS 26 `set selected messages` either selects the
wrong conversation or reports an empty selection, in both a Gmail All Mail
mailbox and a plain INBOX. Rather than ship that, `get_message` lists the rest of
the conversation with a link per message, and `get_thread` renders the whole
exchange in order.

## Install

```
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m mailsync --build-index
```

### Registering it

Claude Code and Claude for Desktop are separate apps with separate config files.
Registering in one does nothing for the other:

| Surface | Config file |
| --- | --- |
| Claude Code | `~/.claude.json`, top-level `mcpServers` |
| Claude for Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` |

Add the same entry to whichever you use, pointing at the venv's Python:

```json
{
  "mcpServers": {
    "mailsync": {
      "command": "/absolute/path/to/Apple-Mail-Sync-MCP/.venv/bin/python",
      "args": ["-m", "mailsync"]
    }
  }
}
```

Because the package is installed into the venv with `pip install -e .`, the entry
does not depend on a working directory. Quit and reopen the app afterwards.

A local server like this one runs as a child process on your Mac, so it is only
available to apps running there. claude.ai in a browser cannot reach it.

Each surface starts its own OS process; stdio has no way to share one. That does
not fork the setup, because both entries run the same installed package and every
process reads and writes the same index at `~/.local/share/mailsync/mailsync.db`.
Edit the code once and both pick it up on their next restart. Concurrent access is
safe: the index runs in WAL mode with a busy timeout, tested with five readers and
two simultaneous rebuilds.

### Full Disk Access

`~/Library/Mail` is gated behind Full Disk Access, and an MCP server inherits the
grant of whichever app launched it. Claude Code and Claude for Desktop are
separate TCC clients, so one can work while the other cannot. Grant it under
System Settings > Privacy & Security > Full Disk Access, then quit and reopen the
app completely.

Without it, search and reading still work from the local index, which lives
outside the protected area. Only `refresh_index` fails, so the data goes stale.
The tool says so plainly rather than failing obscurely.

Composing additionally needs Mail.app running and Automation permission, which
macOS prompts for on first use.

## Partial messages

Mail keeps header-only copies of messages it has not fully downloaded: about
a quarter of them on a typical mailbox. Those stay searchable on sender, subject, date and whatever
preview text Mail stored. `get_message` takes `fetch_if_partial=True` to ask Mail
for the full source, which makes it pull the body from the server. That needs
Mail running, so it is opt-in rather than automatic.

## How the index works

`~/.local/share/mailsync/mailsync.db` holds an FTS5 index over message bodies
extracted from `~/Library/Mail/V10/**/*.emlx`, keyed to Mail's own Envelope
Index. Mail's database is copied aside before reading and is never opened
writable or locked.

A first build takes about a minute for 12,800 messages. After that a refresh is
one to two seconds, since only files whose mtime moved get re-parsed. Searches
return in single-digit to low tens of milliseconds.

Bodies are searchable for the roughly 70 percent of messages Mail has downloaded
in full. The rest are searchable on sender, subject, date and Mail's own preview
text, and `get_message` falls back to those. Coverage rises on its own as Mail
syncs.

## Maintenance

Apple's Envelope Index schema is undocumented and can change in a macOS update.
All of it is confined to `mailsync/index.py`, and the body index can be rebuilt
from the `.emlx` files at any time with `--build-index --full`.
