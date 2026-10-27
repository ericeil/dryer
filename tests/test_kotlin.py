from pathlib import Path

from dryer.cli import run
from dryer.extract import entries_in_source
from dryer.kotlin_source import blank_context_receivers
from dryer.scan import find_duplicates


def forms(source: str, file: str = "a.kt"):
    entries, warning = entries_in_source("kotlin", source, file, file)
    assert warning is None
    return entries


def score(left: str, right: str) -> float:
    found = find_duplicates(forms(left, "a.kt") + forms(right, "b.kt"), 0.0)
    assert len(found) == 1
    return found[0].score


def test_a_bare_property_name_is_not_a_local():
    left = """\
fun indexArrayOrEmpty(needArray: Boolean, count: Int) =
        if (needArray) {
            indexArray(count)
        } else {
            emptyByteArray
        }
"""
    right = left.replace("emptyByteArray", "emptyShortArray")
    renamed = """\
fun indexArrayOrEmpty(ready: Boolean, n: Int) =
        if (ready) {
            indexArray(n)
        } else {
            emptyByteArray
        }
"""
    assert score(left, right) < 0.82
    assert score(left, renamed) == 1.0
    assert "emptyByteArray" in " ".join(forms(left)[0].fingerprints)


def test_a_qualified_member_name_is_not_a_local():
    left = """\
fun getQuantifierConfigs(timelimit: Int, memlimitBytes: Long?): List<Int> {
    return listOf(SolverConfig.cvc5.q.copy(
        timelimit = timelimit,
        memlimitBytes = memlimitBytes
    ))
}
"""
    other = left.replace(".q.copy", ".nonlin.copy").replace(
        "fun getQuantifierConfigs", "fun getNonLinearConfigs"
    )
    renamed = left.replace("timelimit: Int", "limit: Int").replace(
        "timelimit = timelimit", "timelimit = limit"
    )
    assert score(left, other) < 0.82
    assert score(left, renamed) == 1.0
    names = " ".join(forms(left)[0].fingerprints)
    assert "q" in names
    assert "cvc5" in names
    assert "copy" in names
    assert "SolverConfig" in names


def test_an_indexed_property_name_stays_and_the_index_local_drops():
    left = """\
fun reset() {
    for (i in bucketStarts.indices) {
        bucketStarts[i] = 0.toIndex()
    }
}
"""
    renamed = left.replace("(i in", "(j in").replace("[i]", "[j]")
    other = left.replace("bucketStarts", "bucketNexts")
    assert score(left, renamed) == 1.0
    assert score(left, other) < 0.82


def test_a_predicate_passed_as_a_value_still_drops():
    left = """\
fun alpha(xs: IntArray): IntArray {
    val ys = filter(xs, odd)
    return map(ys, inc)
}
"""
    right = """\
fun beta(items: IntArray): IntArray {
    val kept = filter(items, even)
    return map(kept, dec)
}
"""
    assert score(left, right) == 1.0


def test_a_renamed_lambda_parameter_matches_and_it_does_not_stay():
    left = "fun alpha(xs: List<Int>) = xs.map { n -> n + 1 }\n"
    right = "fun beta(items: List<Int>) = items.map { m -> m + 1 }\n"
    implicit = "fun gamma(rows: List<Int>) = rows.map { it + 1 }\n"
    assert score(left, right) == 1.0
    assert score(implicit, implicit.replace("gamma", "delta").replace("rows", "items")) == 1.0
    assert '"it"' not in " ".join(forms(implicit)[0].fingerprints)


def test_a_named_argument_label_stays():
    left = "fun alpha(x: Int) = foo.copy(timelimit = x)\n"
    same = "fun beta(y: Int) = foo.copy(timelimit = y)\n"
    other = "fun gamma(x: Int) = foo.copy(timeout = x)\n"
    assert score(left, same) == 1.0
    assert score(left, other) < 1.0


def test_context_receivers_keep_their_line_and_the_object_body():
    source = """\
package demo

context(Scope)
fun choose(a: Boolean, ready: Boolean): Int {
    if (a && ready) return 1
    return 0
}

object Box {
    fun use(f: context(Foo) () -> Int): Int {
        if (f == null) return 0
        return 1
    }

    context(SimpleDominanceAnalysis)
    @JvmName("dominates2")
    infix fun dominates(a: Boolean): Boolean {
        if (a) return true
        return false
    }

    fun suspendable(f: context(Foo) suspend () -> Int) = if (f == null) 0 else 1
}
"""
    entries, warning = entries_in_source("kotlin", source, "Receivers.kt", "Receivers.kt")
    assert warning is None
    assert [(entry.start_line, entry.end_line) for entry in entries] == [
        (4, 7),
        (10, 13),
        (16, 20),
        (22, 22),
    ]


def test_a_call_named_context_is_not_blanked():
    source = """\
fun exp(): Int {
    return listOf(1).context(0)
}

fun caller(): Int {
    val x = context(1)
    return x
}
"""
    assert blank_context_receivers(source) == source
    entries = forms(source)
    assert [(entry.start_line, entry.end_line) for entry in entries] == [(1, 3), (5, 8)]
    assert "context" in " ".join(entries[0].fingerprints)
    assert "context" in " ".join(entries[1].fingerprints)


def test_context_in_a_string_or_comment_is_not_a_function():
    source = """\
val note = "context(Foo) fun hidden() { if (true) 1 else 0 }"
// context(Foo) fun commented() { if (true) 1 else 0 }
/*
context(Foo)
fun commentedToo() { if (true) {} }
*/

fun real(a: Boolean) = if (a) 1 else 0
"""
    assert "context(Foo)" in blank_context_receivers(source)
    entries = forms(source)
    assert [(entry.start_line, entry.end_line) for entry in entries] == [(8, 8)]


def test_blanking_keeps_the_length_and_the_newlines():
    source = "context(Foo)\nfun choose(a: Boolean) = if (a) 1 else 0\n"
    blanked = blank_context_receivers(source)
    assert len(blanked) == len(source)
    assert blanked.count("\n") == source.count("\n")
    assert "context" not in blanked
    assert "fun choose" in blanked
    dotted = "fun exp() = list.context(ctx)\n"
    assert blank_context_receivers(dotted) == dotted


def test_a_real_parse_error_warns_and_a_missing_semicolon_does_not():
    _entries, warning = entries_in_source(
        "kotlin",
        "class Board {\n fun tick() {}\n companion object { fun create() = Board() }\n}\n",
        "Board.kt",
        "Board.kt",
    )
    assert warning is None
    _entries, warning = entries_in_source(
        "kotlin",
        "package demo\n\nclass Broken {\n fun f( {\n}\n",
        "Broken.kt",
        "Broken.kt",
    )
    assert warning == "parse error; duplicates in this file may be wrong."
    _entries, warning = entries_in_source(
        "python",
        "def broken(\n    return\n",
        "broken.py",
        "broken.py",
    )
    assert warning == "parse error; duplicates in this file may be wrong."


def test_the_report_prints_a_parse_warning(tmp_path: Path, capsys):
    path = tmp_path / "Broken.kt"
    path.write_text("class Broken {\n fun f( {\n}\n", encoding="utf-8")
    assert run(["--root", str(tmp_path), "--min-lines", "1", "--min-nodes", "1"]) == 0
    captured = capsys.readouterr()
    assert "dryer: Broken.kt: parse error; duplicates in this file may be wrong." in captured.err
