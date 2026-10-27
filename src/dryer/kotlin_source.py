"""Blank Kotlin context-receiver clauses before tree-sitter sees them.

tree-sitter-kotlin does not parse context receivers. A clause such as
`context(Foo)` before `fun` / `val`, or a function type `context(Foo) () -> R`,
is an ERROR node. The bad parse turns `object Name { ... }` into an
expression, so every function in the object disappears.

The clause is replaced with spaces of the same length. Newlines stay, so line
numbers do not move. A call named `context` — `.context(ctx)` or
`val x = context(1)` — is left alone, including when the next declaration is a
function. Strings, comments, and character literals are not rewritten.
"""

from __future__ import annotations

# Words that may sit between a context-receiver clause and the declaration.
_MODIFIERS = {
    "public",
    "private",
    "protected",
    "internal",
    "override",
    "open",
    "abstract",
    "final",
    "suspend",
    "inline",
    "tailrec",
    "operator",
    "infix",
    "external",
    "expect",
    "actual",
    "inner",
    "annotation",
    "crossinline",
    "noinline",
}
_DECLARATIONS = {"fun", "val", "var", "get", "set"}


def blank_context_receivers(source: str) -> str:
    """Replace context-receiver clauses with spaces of the same length.

    Newlines stay, so a function's line numbers are the lines in the file.
    Strings and comments are left alone, including a call named `context`.
    """

    if "context" not in source:
        return source
    chars = list(source)
    i = 0
    while i < len(chars):
        i = _scan_context(chars, i)
    return "".join(chars)


def _scan_context(chars: list[str], i: int) -> int:
    skipped = _skip_trivia(chars, i)
    if skipped != i:
        return skipped
    if _is_word(chars, i, "context"):
        keyword_end = i + len("context")
        open_paren = _skip_ws(chars, keyword_end)
        if open_paren < len(chars) and chars[open_paren] == "(":
            close_paren = _match_paren(chars, open_paren)
            if close_paren is not None and _introduces_receiver(chars, i, close_paren + 1):
                _blank(chars, i, close_paren)
                return close_paren + 1
    return i + 1


def _introduces_receiver(chars: list[str], keyword_at: int, i: int) -> bool:
    """True for `context(T) fun` and for a function type `context(T) () -> R`.

    A call named `context` is left alone. `.context(ctx)` is a method, and
    `val x = context(1)` is a call even when a `fun` follows it.
    """

    if _is_function_type(chars, i):
        return True
    if not _at_declaration_position(chars, keyword_at):
        return False
    i = _skip_declaration_prefix(chars, i)
    if i >= len(chars):
        return False
    word, _end = _read_word(chars, i)
    return word in _DECLARATIONS


def _is_function_type(chars: list[str], i: int) -> bool:
    i = _skip_declaration_prefix(chars, i)
    if i >= len(chars) or chars[i] != "(":
        return False
    close_paren = _match_paren(chars, i)
    if close_paren is None:
        return False
    arrow = _skip_ws(chars, close_paren + 1)
    return arrow + 1 < len(chars) and chars[arrow] == "-" and chars[arrow + 1] == ">"


def _at_declaration_position(chars: list[str], i: int) -> bool:
    """True when `context` starts a declaration, not an expression."""

    line_start = i
    while line_start > 0 and chars[line_start - 1] not in "\n\r":
        line_start -= 1
    cursor = line_start
    while cursor < i:
        while cursor < i and chars[cursor] in " \t":
            cursor += 1
        if cursor >= i:
            return True
        if chars[cursor] == "@":
            end = _skip_annotation(chars, cursor)
            if end <= cursor:
                return False
            cursor = end
            continue
        word, end = _read_word(chars, cursor)
        if word in _MODIFIERS and end <= i:
            cursor = end
            continue
        return False
    return True


def _skip_declaration_prefix(chars: list[str], i: int) -> int:
    while True:
        i = _skip_ws(chars, i)
        if i >= len(chars):
            return i
        if chars[i] == "@":
            i = _skip_annotation(chars, i)
            continue
        word, end = _read_word(chars, i)
        if word not in _MODIFIERS:
            return i
        i = end


def _skip_annotation(chars: list[str], i: int) -> int:
    i += 1
    if i < len(chars) and chars[i] == "[":
        close = _match_closer(chars, i, "[", "]")
        return i if close is None else close + 1
    while i < len(chars) and (chars[i].isalnum() or chars[i] in "._:"):
        i += 1
    i = _skip_ws(chars, i)
    if i < len(chars) and chars[i] == "(":
        close = _match_paren(chars, i)
        return i if close is None else close + 1
    return i


def _skip_trivia(chars: list[str], i: int) -> int:
    if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "/":
        return _skip_line_comment(chars, i)
    if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "*":
        return _skip_block_comment(chars, i)
    if chars[i] == '"':
        return _skip_string(chars, i)
    if chars[i] == "'":
        return _skip_char(chars, i)
    return i


def _skip_line_comment(chars: list[str], i: int) -> int:
    newline = _find_char(chars, "\n", i)
    return len(chars) if newline is None else newline


def _skip_block_comment(chars: list[str], i: int) -> int:
    i += 2
    depth = 1
    while i < len(chars) and depth:
        if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "*":
            depth += 1
            i += 2
            continue
        if i + 1 < len(chars) and chars[i] == "*" and chars[i + 1] == "/":
            depth -= 1
            i += 2
            continue
        i += 1
    return i


def _skip_string(chars: list[str], i: int) -> int:
    if _starts_with(chars, i, '"""'):
        return _skip_raw_string(chars, i)
    i += 1
    while i < len(chars):
        if chars[i] == "\\":
            i += 2
            continue
        if chars[i] == "$":
            i = _skip_interpolation(chars, i)
            continue
        if chars[i] == '"':
            return i + 1
        if chars[i] == "\n":
            return i
        i += 1
    return i


def _skip_raw_string(chars: list[str], i: int) -> int:
    i += 3
    while i < len(chars):
        if _starts_with(chars, i, '"""'):
            return i + 3
        if chars[i] == "$":
            i = _skip_interpolation(chars, i)
            continue
        i += 1
    return i


def _skip_interpolation(chars: list[str], i: int) -> int:
    if i + 1 < len(chars) and chars[i + 1] == "{":
        close = _match_closer(chars, i + 1, "{", "}")
        return i + 1 if close is None else close + 1
    if i + 1 < len(chars) and (chars[i + 1].isalpha() or chars[i + 1] == "_"):
        i += 2
        while i < len(chars) and (chars[i].isalnum() or chars[i] == "_"):
            i += 1
        return i
    return i + 1


def _skip_char(chars: list[str], i: int) -> int:
    if i + 2 < len(chars) and chars[i + 1] == "\\" and chars[i + 2] == "'":
        return i + 4 if i + 3 < len(chars) and chars[i + 3] == "'" else i + 1
    if i + 2 < len(chars) and chars[i + 1] != "'" and chars[i + 1] != "\n" and chars[i + 2] == "'":
        return i + 3
    if i + 1 < len(chars) and chars[i + 1] == "\\":
        end = i + 2
        while end < len(chars) and chars[end] != "'" and chars[end] != "\n":
            end += 1
        return end + 1 if end < len(chars) and chars[end] == "'" else i + 1
    return i + 1


def _match_paren(chars: list[str], i: int) -> int | None:
    return _match_closer(chars, i, "(", ")")


def _match_closer(chars: list[str], i: int, open_char: str, close_char: str) -> int | None:
    depth = 0
    while i < len(chars):
        skipped = _skip_trivia(chars, i)
        if skipped != i:
            i = skipped
            continue
        if chars[i] == open_char:
            depth += 1
        elif chars[i] == close_char:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _skip_ws(chars: list[str], i: int) -> int:
    while i < len(chars) and chars[i] in " \t\r\n":
        i += 1
    return i


def _read_word(chars: list[str], i: int) -> tuple[str, int]:
    if i >= len(chars) or not (chars[i].isalpha() or chars[i] == "_"):
        return "", i
    end = i + 1
    while end < len(chars) and (chars[end].isalnum() or chars[end] == "_"):
        end += 1
    return "".join(chars[i:end]), end


def _is_word(chars: list[str], i: int, word: str) -> bool:
    if not _starts_with(chars, i, word):
        return False
    before_ok = i == 0 or not (chars[i - 1].isalnum() or chars[i - 1] == "_")
    after = i + len(word)
    after_ok = after >= len(chars) or not (chars[after].isalnum() or chars[after] == "_")
    return before_ok and after_ok


def _starts_with(chars: list[str], i: int, text: str) -> bool:
    return chars[i : i + len(text)] == list(text)


def _find_char(chars: list[str], needle: str, i: int) -> int | None:
    while i < len(chars):
        if chars[i] == needle:
            return i
        i += 1
    return None


def _blank(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end + 1):
        if chars[index] not in "\n\r":
            chars[index] = " "

