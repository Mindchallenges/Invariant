"""Lexical masks and a path-aware ``executable_text`` (IDEA_031 A3 increment 1).

ADR-012 D4: a ``pattern`` rule reads code, comments or strings, and the
adapter that owns the file supplies the mask. An adapter answers only for its
own ecosystem, so in a stacked project the file reaches the adapter whose
language it is written in, or nothing at all. ``executable_text`` gains the
path for the same reason, which is the fix for BUG_103: the Swift reducer read
a single-quoted ``'http://a'`` in a Python or TypeScript file as a string
followed by a comment, and called a real change cosmetic.

Every mask keeps the text's lines and columns, so a match in the masked text
is a match at the same place in the file.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

import pytest

from invariant.adapters import get_adapter
from invariant.adapters.base import LEXICAL_REGIONS, Adapter, masked
from invariant.adapters.python_ import _python_regions
from invariant.core.rules import _executable_text, _lexical_mask, _offered_engines


def same_shape(a: str, b: str) -> bool:
    """Same lines, each of the same length: every column is where it was."""
    return [len(line) for line in a.split("\n")] == [len(line) for line in b.split("\n")]


# -- the seam ------------------------------------------------------------------


class TestTheSeam:
    def test_the_three_regions(self):
        assert LEXICAL_REGIONS == ("code", "comments", "strings")

    @pytest.mark.parametrize("ecosystem", ["generic", "node"])
    def test_an_adapter_without_a_lexer_offers_nothing_and_answers_none(self, ecosystem):
        """DN-9: node and generic gain nothing in this increment."""
        adapter = get_adapter(ecosystem)
        assert adapter.guideline_engines == frozenset()
        assert adapter.lexical_mask(Path("a.ts"), "x // y\n", "code") is None
        assert adapter.executable_text(Path("a.ts"), "x // y\n") is None

    @pytest.mark.parametrize("ecosystem", ["python", "swift_xcode"])
    def test_python_and_swift_offer_the_mask(self, ecosystem):
        assert get_adapter(ecosystem).guideline_engines == frozenset({"mask"})

    def test_the_base_answers_none(self):
        assert Adapter().lexical_mask(Path("a.py"), "x = 1\n", "code") is None

    @pytest.mark.parametrize("ecosystem", ["python", "swift_xcode"])
    def test_an_unknown_region_is_a_programming_error(self, ecosystem):
        suffix = ".py" if ecosystem == "python" else ".swift"
        with pytest.raises(ValueError, match="any"):
            get_adapter(ecosystem).lexical_mask(Path(f"a{suffix}"), "x\n", "any")

    def test_masked_blanks_everything_outside_keep_but_line_ends(self):
        text = "ab\r\ncd\n"
        regions = [("code", 0, 2), ("comments", 2, 7)]
        assert masked(text, regions, "code") == "ab\r\n  \n"
        assert masked(text, regions, "comments") == "  \r\ncd\n"


# -- python --------------------------------------------------------------------


PY = Path("pkg/mod.py")

PY_SOURCE = (
    '"""Module doc."""\n'
    "x = 'http://a' + b  # trailing\n"
    "# whole line\n"
    'y = f"{x!r} and {b}"\n'
)


class TestPythonMask:
    def setup_method(self):
        self.adapter = get_adapter("python")

    def mask(self, text, keep, path=PY):
        return self.adapter.lexical_mask(path, text, keep)

    def test_code_blanks_comments_and_strings_in_place(self):
        out = self.mask(PY_SOURCE, "code")
        assert same_shape(out, PY_SOURCE)
        lines = out.split("\n")
        assert lines[0].strip() == ""
        assert lines[1].rstrip() == "x =" + " " * 11 + " + b"
        assert lines[2].strip() == ""
        assert lines[3].rstrip() == "y ="

    def test_comments_keeps_only_comments(self):
        out = self.mask(PY_SOURCE, "comments")
        assert same_shape(out, PY_SOURCE)
        assert out.split("\n")[1].strip() == "# trailing"
        assert out.split("\n")[2] == "# whole line"
        assert "http" not in out and "x" not in out

    def test_strings_keeps_docstrings_literals_and_fstrings_whole(self):
        out = self.mask(PY_SOURCE, "strings")
        lines = out.split("\n")
        assert lines[0] == '"""Module doc."""'
        assert lines[1].strip() == "'http://a'"
        assert lines[3].strip() == 'f"{x!r} and {b}"'

    def test_an_fstring_replacement_field_is_never_code(self):
        """DN-10: masked whole as a string, on every interpreter, so a
        verdict does not depend on which one tokenized the file."""
        out = self.mask('f"{secret()}"\n', "code")
        assert "secret" not in out

    def test_a_multiline_string_keeps_its_line_ends(self):
        text = 'a = """one\ntwo"""  # c\nb = 1\n'
        out = self.mask(text, "code")
        assert same_shape(out, text)
        assert out.split("\n")[2] == "b = 1"

    def test_columns_count_characters_not_bytes(self):
        text = "s = 'é'  # ü\n"
        assert same_shape(self.mask(text, "comments"), text)
        assert self.mask(text, "comments").rstrip().endswith("# ü")

    def test_crlf_line_ends_survive(self):
        text = "a = 1  # c\r\nb = 2\r\n"
        out = self.mask(text, "code")
        assert out == "a = 1     \r\nb = 2\r\n"

    def test_a_file_ending_inside_a_comment_keeps_its_length(self):
        assert self.mask("x = 1  # end", "comments") == "       # end"

    def test_a_file_outside_python_answers_none(self):
        assert self.mask("x = 1\n", "code", Path("a.swift")) is None
        assert self.mask("x = 1\n", "code", Path("a.pyi")) is None

    def test_untokenizable_text_answers_none(self):
        assert self.mask('x = """never closed\n', "code") is None
        assert self.mask("if x:\n        a\n    b\n", "code") is None


def tokens_as_older_pythons_emit_them(text: str):
    """A ``t"..."`` literal before 3.14: a NAME prefix glued to a STRING."""
    tok = tokenize.TokenInfo
    return [
        tok(tokenize.NAME, "y", (1, 0), (1, 1), text),
        tok(tokenize.OP, "=", (1, 2), (1, 3), text),
        tok(tokenize.NAME, "rt", (1, 4), (1, 6), text),
        tok(tokenize.STRING, '"{q}"', (1, 6), (1, 11), text),
        tok(tokenize.OP, "+", (1, 12), (1, 13), text),
        tok(tokenize.NAME, "t", (1, 14), (1, 15), text),
        tok(tokenize.OP, "(", (1, 15), (1, 16), text),
        tok(tokenize.OP, ")", (1, 16), (1, 17), text),
        tok(tokenize.NEWLINE, "\n", (1, 17), (1, 18), text),
        tok(tokenize.ENDMARKER, "", (2, 0), (2, 0), text),
    ]


class TestPythonRegionsAcrossInterpreters:
    def test_a_tstring_prefix_glued_to_its_string_is_one_string(self):
        """Before 3.14 the tokenizer reads ``t"..."`` as a name and a string.
        They are one literal, and a bare name ``t`` beside a call is not."""
        text = 'y = rt"{q}" + t()\n'
        regions = _python_regions(text, tokens_as_older_pythons_emit_them(text))
        strings = [text[s:e] for kind, s, e in regions if kind == "strings"]
        assert strings == ['rt"{q}"']
        assert masked(text, regions, "code") == "y =         + t()\n"

    def test_this_interpreter_agrees(self):
        text = 'y = rt"{q}" + t()\n'
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        assert masked(text, _python_regions(text, tokens), "code") == (
            "y =         + t()\n"
        )


class TestPythonExecutableText:
    def setup_method(self):
        self.adapter = get_adapter("python")

    def reduce(self, text, path=PY):
        return self.adapter.executable_text(path, text)

    def test_comments_blank_lines_and_layout_are_not_code(self):
        before = "def f(a, b):\n    return a + b  # sum\n"
        after = "# leading\n\ndef f(a,\n      b):\n\n    return a+b\n"
        assert self.reduce(before) == self.reduce(after)

    def test_a_reindented_block_is_the_same_program(self):
        assert self.reduce("if x:\n    a()\n") == self.reduce("if x:\n        a()\n")

    def test_moving_a_statement_out_of_its_block_is_a_change(self):
        assert self.reduce("if x:\n    a()\n    b()\n") != self.reduce(
            "if x:\n    a()\nb()\n"
        )

    def test_the_bug_103_change_is_a_change(self):
        assert self.reduce("x = 'http://a' + b\n") != self.reduce("x = 'http://zzz' + c\n")

    def test_a_docstring_edit_is_kept_as_a_change(self):
        """Reducing too little is the safe direction (DN-10)."""
        assert self.reduce('def f():\n    """One."""\n') != self.reduce(
            'def f():\n    """Two."""\n'
        )

    def test_fstring_spacing_is_compared_verbatim(self):
        """Merged whole, so 3.12's tokens inside the braces cannot make
        ``{ x }`` and ``{x}`` equal on one interpreter and not another."""
        assert self.reduce('f"{ x }"\n') != self.reduce('f"{x}"\n')

    def test_a_file_outside_python_answers_none(self):
        assert self.reduce("x = 1\n", Path("app/mod.ts")) is None

    def test_untokenizable_text_answers_none(self):
        assert self.reduce("x = (1,\n") is None


# -- swift ---------------------------------------------------------------------


SW = Path("App/Mod.swift")

SW_SOURCE = (
    "let u = \"http://a\" // trailing\n"
    "/* outer /* nested */ still */ let b = 1\n"
    'let m = """\n  // not a comment\n  """\n'
)


class TestSwiftMask:
    def setup_method(self):
        self.adapter = get_adapter("swift_xcode")

    def mask(self, text, keep, path=SW):
        return self.adapter.lexical_mask(path, text, keep)

    def test_code_blanks_comments_and_strings_in_place(self):
        out = self.mask(SW_SOURCE, "code")
        assert same_shape(out, SW_SOURCE)
        lines = out.split("\n")
        assert lines[0].rstrip() == "let u ="
        assert lines[1].strip() == "let b = 1"
        assert lines[2].rstrip() == "let m ="
        assert "not a comment" not in out

    def test_comments_keeps_only_comments_nested_ones_whole(self):
        out = self.mask(SW_SOURCE, "comments")
        lines = out.split("\n")
        assert lines[0].strip() == "// trailing"
        assert lines[1].strip() == "/* outer /* nested */ still */"
        assert "not a comment" not in out

    def test_strings_keeps_literals_with_their_quotes(self):
        out = self.mask(SW_SOURCE, "strings")
        assert out.split("\n")[0].strip() == '"http://a"'
        assert "// not a comment" in out
        assert same_shape(out, SW_SOURCE)

    def test_a_file_outside_swift_answers_none(self):
        assert self.mask("let a = 1\n", "code", Path("a.py")) is None

    @pytest.mark.parametrize(
        "source", ["let a = 1 /* never closed", 'let s = "never closed', 'let m = """ open']
    )
    def test_text_it_cannot_read_to_the_end_answers_none(self, source):
        assert self.mask(source, "code") is None


class TestSwiftExecutableTextIsSwiftOnly:
    def test_a_file_outside_swift_answers_none(self):
        """BUG_103 at the adapter: the reducer answers for Swift and nothing else."""
        adapter = get_adapter("swift_xcode")
        assert adapter.executable_text(Path("app/mod.ts"), "x = 'http://a' + b\n") is None
        assert adapter.executable_text(Path("app/mod.py"), "x = 'http://a' + b\n") is None


# -- stacking ------------------------------------------------------------------


class TestStacking:
    """The first adapter with an answer wins, as with ``is_test_file``."""

    STACK = ("swift_xcode", "python")

    def adapters(self, *ecosystems):
        return [get_adapter(e) for e in ecosystems or self.STACK]

    def test_a_python_file_reaches_the_python_adapter(self):
        out = _lexical_mask(self.adapters(), PY, "x = 'http://a'  # c\n", "code")
        assert out == "x =                \n"

    def test_a_file_no_adapter_owns_has_no_mask(self):
        assert _lexical_mask(self.adapters(), Path("a.ts"), "x\n", "code") is None

    def test_executable_text_reaches_the_owning_adapter_or_none(self):
        stack = self.adapters()
        assert _executable_text(stack, PY, "x = 1  # a\n") == _executable_text(
            stack, PY, "x = 1  # b\n"
        )
        assert _executable_text(stack, Path("a.ts"), "x = 1\n") is None

    def test_the_offered_engines_are_the_union(self):
        assert _offered_engines(self.adapters("node", "python")) == frozenset({"mask"})
        assert _offered_engines(self.adapters("generic", "node")) == frozenset()
