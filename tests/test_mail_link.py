from mailsync.search import mail_link


def test_builds_a_message_url():
    assert mail_link("abc@example.com") == "message://%3Cabc%40example.com%3E"


def test_percent_encodes_reserved_characters():
    link = mail_link("a+b/c?d=e&f@host.com")
    for ch in "+/?=&@":
        assert ch not in link.replace("message://", "")
    assert link.startswith("message://%3C") and link.endswith("%3E")


def test_empty_id_yields_no_link():
    assert mail_link("") == "" and mail_link(None) == ""


def test_angle_brackets_are_not_doubled():
    # Message-IDs are stored stripped of <>; the URL adds exactly one pair.
    assert mail_link("x@y.com").count("%3C") == 1
    assert mail_link("x@y.com").count("%3E") == 1
