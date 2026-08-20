import pytest

from mailsync.search import _fts_query


@pytest.mark.parametrize("raw", ['a"b', "x AND OR y", "foo*", "(bar)", "a^b", "-neg", "a:b", '"'])
def test_hostile_input_never_produces_invalid_fts(raw):
    """FTS5 syntax errors are a denial-of-service on search; operators are dropped."""
    out = _fts_query(raw)
    assert out.count('"') % 2 == 0
    for ch in "*():^":
        assert ch not in out


def test_phrases_are_preserved():
    assert _fts_query('"fuel adjustment"') == '"fuel adjustment"'


def test_bare_terms_are_anded():
    assert _fts_query("invoice overdue") == '"invoice" AND "overdue"'


def test_empty_is_empty():
    assert _fts_query("") == "" and _fts_query("   ") == ""
