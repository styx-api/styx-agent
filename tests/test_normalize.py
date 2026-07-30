"""Tests for deterministic Unicode → ASCII normalization."""

from styx_agent.normalize import decode_source, to_ascii


def test_to_ascii_passes_ascii_through_unchanged():
    s = "plain ASCII text 1x2x3 -- ok"
    assert to_ascii(s) is s or to_ascii(s) == s


def test_to_ascii_transliterates_punctuation():
    assert to_ascii("Tool — does things") == "Tool - does things"  # em dash
    assert to_ascii("range 0–90") == "range 0-90"  # en dash
    assert to_ascii("don’t") == "don't"  # curly apostrophe
    assert to_ascii("“quoted”") == '"quoted"'  # curly double quotes
    assert to_ascii("a…b") == "a...b"  # ellipsis
    assert to_ascii("2 GB") == "2 GB"  # non-breaking space
    assert to_ascii("2×3") == "2x3"  # multiplication sign


def test_to_ascii_folds_accents_and_drops_residual():
    assert to_ascii("Müller") == "Muller"  # ü -> u
    assert to_ascii("café") == "cafe"  # é -> e
    # a symbol with no ASCII intent and no decomposition is dropped (not U+FFFD)
    out = to_ascii("emoji \U0001f600 gone")
    assert out.isascii() and "emoji" in out and "gone" in out and "\U0001f600" not in out


def test_to_ascii_is_idempotent():
    s = "Tool — café “x”"
    once = to_ascii(s)
    assert to_ascii(once) == once
    assert once.isascii()


def test_decode_source_recovers_cp1252_em_dash():
    # cp1252 em-dash byte 0x97 is invalid UTF-8; strict-utf8+replace would give
    # U+FFFD, but the cp1252 fallback recovers the em-dash.
    data = "Tool — x".encode("cp1252")
    assert "�" not in decode_source(data)
    assert to_ascii(decode_source(data)) == "Tool - x"


def test_decode_source_prefers_utf8():
    data = "café".encode()
    assert decode_source(data) == "café"


def test_to_ascii_preserves_line_count():
    # read_file numbers lines, so normalization must never add/remove newlines,
    # even where a mapping expands (… -> ...) or substitutes (nbsp -> space).
    s = "a — b\nc … d\nno break\n2×3\n"
    assert to_ascii(s).count("\n") == s.count("\n")


def test_decode_source_undefined_cp1252_byte_falls_back_to_replacement():
    # 0x81 is an undefined cp1252 slot (and invalid UTF-8): it can't be recovered,
    # becomes U+FFFD, which to_ascii then drops — no crash.
    dec = decode_source(b"a\x81b")
    assert "�" in dec
    assert to_ascii(dec) == "ab"
