import textwrap

from mailsync import emlx


def _write(tmp_path, raw: bytes, name="1.emlx"):
    p = tmp_path / name
    p.write_bytes(str(len(raw)).encode() + b"\n" + raw + b"<?xml version='1.0'?><plist/>")
    return p


def test_strips_length_prefix_and_trailing_plist(tmp_path):
    raw = b"Subject: Hello\r\n\r\nBody text here.\r\n"
    assert emlx.read_rfc822(_write(tmp_path, raw)) == raw


def test_prefers_html_when_plain_part_is_empty(tmp_path):
    raw = textwrap.dedent("""\
        Subject: Alt test
        MIME-Version: 1.0
        Content-Type: multipart/alternative; boundary="b"

        --b
        Content-Type: text/plain; charset=utf-8

        --b
        Content-Type: text/html; charset=utf-8

        <html><body><p>Real content lives here.</p></body></html>
        --b--
        """).encode()
    msg = emlx.parse(_write(tmp_path, raw))
    assert "Real content lives here." in msg.body
    assert msg.body_is_html


def test_decodes_quoted_printable(tmp_path):
    raw = textwrap.dedent("""\
        Subject: QP
        Content-Type: text/plain; charset=utf-8
        Content-Transfer-Encoding: quoted-printable

        Caf=C3=A9 costs =C2=A35
        """).encode()
    assert "Café costs £5" in emlx.parse(_write(tmp_path, raw)).body


def test_html_stripper_drops_script_and_style():
    out = emlx.html_to_text("<style>p{color:red}</style><script>evil()</script><p>Visible</p>")
    assert out == "Visible"


def test_partial_flag(tmp_path):
    p = _write(tmp_path, b"Subject: x\r\n\r\n", name="7.partial.emlx")
    assert emlx.parse(p).partial


def test_extracts_addresses(tmp_path):
    raw = b"From: Jane Doe <jane@x.com>\r\nTo: a@y.com, b@z.com\r\nCc: c@w.com\r\nSubject: s\r\n\r\nhi\r\n"
    m = emlx.parse(_write(tmp_path, raw))
    assert m.from_addr == "jane@x.com" and m.from_name == "Jane Doe"
    assert m.to == ["a@y.com", "b@z.com"] and m.cc == ["c@w.com"]


def test_placeholder_plain_part_loses_to_html(tmp_path):
    """Bulk senders ship a stub text/plain and put the real content in HTML."""
    raw = textwrap.dedent("""\
        Subject: Placeholder test
        MIME-Version: 1.0
        Content-Type: multipart/alternative; boundary="b"

        --b
        Content-Type: text/plain; charset=utf-8

        Plain text version not available
        --b
        Content-Type: text/html; charset=utf-8

        <html><body><p>%s</p></body></html>
        --b--
        """ % ("The actual message content. " * 40)).encode()
    msg = emlx.parse(_write(tmp_path, raw))
    assert "The actual message content." in msg.body
    assert "Plain text version not available" not in msg.body
    assert msg.body_is_html


def test_genuine_short_plain_is_kept(tmp_path):
    """A real short plain body must not be discarded just for being short."""
    raw = textwrap.dedent("""\
        Subject: Short but real
        MIME-Version: 1.0
        Content-Type: multipart/alternative; boundary="b"

        --b
        Content-Type: text/plain; charset=utf-8

        Sounds good, see you then.
        --b
        Content-Type: text/html; charset=utf-8

        <html><body><p>Sounds good, see you then.</p></body></html>
        --b--
        """).encode()
    msg = emlx.parse(_write(tmp_path, raw))
    assert "Sounds good, see you then." in msg.body
    assert not msg.body_is_html
