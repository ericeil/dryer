"""Normalize a tree-sitter tree the way dry4clj normalizes a Clojure form.

The name in call position stays: `filter`, `Println`, `map`, an operator, the
type constructed by `new`. Local names, field names, type names in argument
position, and literals become generic markers. Two functions that call the
same operations in the same shape therefore share a fingerprint set even when
their locals differ.

Kotlin puts the arguments of a call in `call_suffix`. A navigation suffix keeps
its name when it is the thing being called, or when it is a `::` reference.
"""

from __future__ import annotations

from dryer.shape import K
from dryer.treesitter import node_text

_SKIP = {
    "comment",
    "line_comment",
    "block_comment",
    "documentation_comment",
    "doc_comment",
    "multiline_comment",
}

_IDENTIFIERS = {
    "identifier",
    "type_identifier",
    "field_identifier",
    "property_identifier",
    "shorthand_property_identifier",
    "shorthand_property_identifier_pattern",
    "package_identifier",
    "simple_identifier",
}

_LITERALS = {
    "string",
    "string_literal",
    "interpreted_string_literal",
    "raw_string_literal",
    "char_literal",
    "rune_literal",
    "character_literal",
    "number",
    "integer",
    "float",
    "decimal_integer_literal",
    "hex_integer_literal",
    "octal_integer_literal",
    "binary_integer_literal",
    "decimal_floating_point_literal",
    "hex_floating_point_literal",
    "int_literal",
    "float_literal",
    "imaginary_literal",
    "integer_literal",
    "real_literal",
    "long_literal",
    "unsigned_literal",
    "true",
    "false",
    "null",
    "nil",
    "none",
    "undefined",
    "null_literal",
    "boolean_literal",
    "template_string",
    "regex",
    "regex_literal",
}

_OPERATORS = {
    "+",
    "-",
    "*",
    "/",
    "%",
    "**",
    "==",
    "!=",
    "<",
    ">",
    "<=",
    ">=",
    "===",
    "!==",
    "&&",
    "||",
    "&",
    "|",
    "^",
    "<<",
    ">>",
    ">>>",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "^=",
    "<<=",
    ">>=",
    ">>>=",
    "!",
    "~",
    "++",
    "--",
    "and",
    "or",
    "not",
    "??",
    "?.",
    "=",
    ":=",
    "=>",
    "?",
    "?:",
    "!!",
    "..<",
    "as?",
}

_CALLS = {
    "call",
    "call_expression",
    "method_invocation",
    "object_creation_expression",
    "new_expression",
}

_ATTRIBUTES = {
    "attribute",
    "member_expression",
    "selector_expression",
    "field_access",
    "field_expression",
}

_SCOPED = {
    "scoped_identifier",
    "scoped_type_identifier",
}

_ARG_LISTS = {"argument_list", "arguments"}
_TYPE_ARGS = {"type_arguments", "type_parameters"}

_INTERPOLATED = {
    "interpolation",
    "interpolated_identifier",
    "interpolated_expression",
}

_DATA: bytes = b""


def normalize(node, data: bytes):
    """Normalized tree for `node`, or None when the node is only punctuation."""

    global _DATA
    _DATA = data
    return _normalize(node)


def _text(node) -> str:
    return node_text(_DATA, node)


_PENDING = object()


def _interpolated(node) -> bool:
    return any(child.type in _INTERPOLATED for child in node.children)


def _is_literal(node) -> bool:
    if node.type not in _LITERALS:
        return False
    if node.type in {"string", "string_literal"} and _interpolated(node):
        return False
    return True


def _named_before_args(node):
    incoming = []
    for child in node.named_children:
        if child.type in _ARG_LISTS:
            return incoming, child
        incoming.append(child)
    return incoming, None


def _without_type_args(nodes):
    type_args = [child for child in nodes if child.type in _TYPE_ARGS]
    rest = [child for child in nodes if child.type not in _TYPE_ARGS]
    return rest, type_args


def _callee_leaf(node):
    if node.type in _IDENTIFIERS:
        return [K("symbol"), _text(node)]
    return _PENDING


def _named_leaf(node, head: bool):
    if node.type in _IDENTIFIERS:
        if head:
            return [K("symbol"), _text(node)]
        return K("symbol")
    if _is_literal(node):
        return K("literal")
    return _PENDING


def _try_leaf(node, mode: str, head: bool):
    if mode == "callee":
        return _callee_leaf(node)
    if mode == "path":
        return _PENDING
    if node.type in _SKIP:
        return None
    if node.type in _OPERATORS:
        return [K("symbol"), node.type]
    if not node.is_named:
        return None
    return _named_leaf(node, head)


def _kotlin_call(node) -> bool:
    return any(child.type == "call_suffix" for child in node.named_children)


def _kotlin_callee_and_suffix(node):
    """The callee is the first named child. `call_suffix` holds the arguments."""

    callee = None
    extras = []
    for child in node.named_children:
        if child.type == "call_suffix":
            extras.extend(child.named_children)
        elif callee is None:
            callee = child
    return callee, extras


def _kotlin_call_jobs(node):
    callee, extras = _kotlin_callee_and_suffix(node)
    jobs = []
    if callee is not None:
        jobs.append((callee, "callee", False))
    jobs.extend((child, "norm", False) for child in extras)
    return jobs


def _call_jobs(node):
    if _kotlin_call(node):
        return _kotlin_call_jobs(node)
    incoming, args = _named_before_args(node)
    rest, type_args = _without_type_args(incoming)
    jobs = []
    if rest:
        *receivers, callee = rest
        jobs.extend((child, "norm", False) for child in receivers)
        jobs.append((callee, "callee", False))
    jobs.extend((child, "norm", False) for child in type_args)
    if args is not None:
        jobs.append((args, "norm", False))
    return jobs


def _attr_jobs(node, head: bool):
    named = list(node.named_children)
    if not named:
        return []
    *objects, name = named
    jobs = [(obj, "norm", False) for obj in objects]
    if name.type in _IDENTIFIERS:
        return jobs
    if head:
        jobs.append((name, "callee", False))
    else:
        jobs.append((name, "norm", False))
    return jobs


def _path_jobs(node):
    if node.type == "navigation_expression":
        return _navigation_jobs(node, True, True)
    jobs = []
    for child in node.named_children:
        if child.type in _IDENTIFIERS:
            continue
        if child.type in _SCOPED or child.type == "generic_type":
            jobs.append((child, "path", False))
        else:
            jobs.append((child, "norm", False))
    return jobs


def _navigation_parts(node):
    named = list(node.named_children)
    if named and named[-1].type == "navigation_suffix":
        return named[:-1], named[-1]
    return named, None


def _reference_suffix(suffix) -> bool:
    return suffix is not None and any(child.type == "::" for child in suffix.children)


def _keeps_suffix_name(suffix, head: bool) -> bool:
    if _reference_suffix(suffix):
        return True
    return head


def _suffix_child_kept(child) -> bool:
    return child.is_named or child.type in _OPERATORS


def _navigation_object_mode(obj, reference: bool):
    """A `::` receiver keeps its names. A call receiver does not."""

    if reference and obj.type in _IDENTIFIERS:
        return "callee", False
    if reference and obj.type == "navigation_expression":
        return "path", False
    return "norm", False


def _navigation_jobs(node, head: bool, keep_receiver: bool = False):
    objects, suffix = _navigation_parts(node)
    reference = keep_receiver or _reference_suffix(suffix)
    jobs = []
    for obj in objects:
        mode, is_head = _navigation_object_mode(obj, reference)
        jobs.append((obj, mode, is_head))
    if suffix is None:
        return jobs
    keep_name = reference or _keeps_suffix_name(suffix, head)
    for child in suffix.children:
        if child.type in _IDENTIFIERS:
            if keep_name:
                jobs.append((child, "callee", False))
            continue
        if _suffix_child_kept(child):
            jobs.append((child, "norm", False))
    return jobs


def _infix_operands(node):
    named = list(node.named_children)
    if len(named) == 3 and named[1].type in _IDENTIFIERS:
        return named
    return None


def _infix_jobs(node):
    named = _infix_operands(node)
    if named is None:
        return [(child, "norm", False) for child in node.children]
    left, name, right = named
    return [(left, "norm", False), (name, "callee", False), (right, "norm", False)]


def _callable_reference_jobs(node):
    return [
        (child, "norm", False)
        for child in node.named_children
        if child.type not in _IDENTIFIERS
    ]


def _special_jobs(node, head: bool):
    if node.type == "navigation_expression":
        return _navigation_jobs(node, head)
    if node.type == "infix_expression":
        return _infix_jobs(node)
    if node.type == "callable_reference":
        return _callable_reference_jobs(node)
    return None


def _callee_jobs(node):
    if node.type == "navigation_expression":
        return _navigation_jobs(node, True)
    if node.type == "callable_reference":
        return _callable_reference_jobs(node)
    if node.type in _ATTRIBUTES:
        return _attr_jobs(node, True)
    if node.type in _SCOPED or node.type == "generic_type":
        return _path_jobs(node)
    return [(node, "norm", True)]


def _jobs(node, mode: str, head: bool):
    if mode == "callee":
        return _callee_jobs(node)
    if mode == "path":
        return _path_jobs(node)
    if node.type == "parenthesized_expression" and len(node.named_children) == 1:
        return [(node.named_children[0], "norm", head)]
    special = _special_jobs(node, head)
    if special is not None:
        return special
    if node.type in _CALLS:
        return _call_jobs(node)
    if node.type in _ATTRIBUTES:
        return _attr_jobs(node, head)
    return [(child, "norm", False) for child in node.children]


def _name_value(name, head: bool, cache):
    """Keep a member's spelling when it is the thing being called."""

    if name.type in _IDENTIFIERS:
        if head:
            return [K("symbol"), _text(name)]
        return K("symbol")
    if head:
        return cache[(id(name), "callee", False)]
    return cache[(id(name), "norm", False)]


def _assemble_kotlin_call(node, cache):
    callee, extras = _kotlin_callee_and_suffix(node)
    out = [K(node.type)]
    if callee is not None:
        out.append(cache[(id(callee), "callee", False)])
    for child in extras:
        norm = cache[(id(child), "norm", False)]
        if norm is not None:
            out.append(norm)
    return out


def _assemble_call(node, cache):
    if _kotlin_call(node):
        return _assemble_kotlin_call(node, cache)
    incoming, args = _named_before_args(node)
    rest, type_args = _without_type_args(incoming)
    out = [K(node.type)]
    if rest:
        *receivers, callee = rest
        for child in receivers:
            norm = cache[(id(child), "norm", False)]
            if norm is not None:
                out.append(norm)
        out.append(cache[(id(callee), "callee", False)])
    for child in type_args:
        norm = cache[(id(child), "norm", False)]
        if norm is not None:
            out.append(norm)
    if args is not None:
        norm = cache[(id(args), "norm", False)]
        if norm is not None:
            out.append(norm)
    return out


def _assemble_attr(node, head: bool, cache):
    named = list(node.named_children)
    if not named:
        return [K(node.type)]
    *objects, name = named
    parts = [K(node.type)]
    for obj in objects:
        norm = cache[(id(obj), "norm", False)]
        if norm is not None:
            parts.append(norm)
    nested = _name_value(name, head, cache)
    if nested is not None:
        parts.append(nested)
    return parts


def _assemble_path(node, cache):
    """A callee path keeps each name in the path. Type arguments stay structural."""

    if node.type == "navigation_expression":
        return _assemble_navigation(node, True, cache, True)
    parts = [K(node.type)]
    for child in node.named_children:
        if child.type in _IDENTIFIERS:
            parts.append([K("symbol"), _text(child)])
        elif child.type in _SCOPED or child.type == "generic_type":
            parts.append(cache[(id(child), "path", False)])
        else:
            norm = cache[(id(child), "norm", False)]
            if norm is not None:
                parts.append(norm)
    return parts


def _assemble_navigation(node, head: bool, cache, keep_receiver: bool = False):
    objects, suffix = _navigation_parts(node)
    reference = keep_receiver or _reference_suffix(suffix)
    parts = [K(node.type)]
    for obj in objects:
        mode, is_head = _navigation_object_mode(obj, reference)
        norm = cache[(id(obj), mode, is_head)]
        if norm is not None:
            parts.append(norm)
    if suffix is None:
        return parts
    keep_name = reference or _keeps_suffix_name(suffix, head)
    for child in suffix.children:
        if child.type in _IDENTIFIERS:
            if keep_name:
                parts.append(cache[(id(child), "callee", False)])
            else:
                parts.append(K("symbol"))
            continue
        if _suffix_child_kept(child):
            norm = cache[(id(child), "norm", False)]
            if norm is not None:
                parts.append(norm)
    return parts


def _assemble_infix(node, cache):
    named = _infix_operands(node)
    if named is None:
        return _assemble_children(node, cache)
    parts = [K(node.type)]
    for child, mode in zip(named, ("norm", "callee", "norm")):
        norm = cache[(id(child), mode, False)]
        if norm is not None:
            parts.append(norm)
    return parts


def _assemble_callable_reference(node, cache):
    """Both sides of `::` stay. The grammar reads the receiver as a type name."""

    parts = [K(node.type)]
    for child in node.named_children:
        if child.type in _IDENTIFIERS:
            parts.append([K("symbol"), _text(child)])
            continue
        norm = cache[(id(child), "norm", False)]
        if norm is not None:
            parts.append(norm)
    return parts


def _special_value(node, head: bool, cache):
    if node.type == "navigation_expression":
        return _assemble_navigation(node, head, cache)
    if node.type == "infix_expression":
        return _assemble_infix(node, cache)
    if node.type == "callable_reference":
        return _assemble_callable_reference(node, cache)
    return _PENDING


def _callee_value(node, cache):
    if node.type == "navigation_expression":
        return _assemble_navigation(node, True, cache)
    if node.type == "callable_reference":
        return _assemble_callable_reference(node, cache)
    if node.type in _ATTRIBUTES:
        return _assemble_attr(node, True, cache)
    if node.type in _SCOPED or node.type == "generic_type":
        return _assemble_path(node, cache)
    return cache[(id(node), "norm", True)]


def _assemble_children(node, cache):
    children = []
    for child in node.children:
        norm = cache[(id(child), "norm", False)]
        if norm is not None:
            children.append(norm)
    return [K(node.type), *children]


def _assemble(node, mode: str, head: bool, cache):
    if mode == "callee":
        return _callee_value(node, cache)
    if mode == "path":
        return _assemble_path(node, cache)
    if node.type == "parenthesized_expression" and len(node.named_children) == 1:
        child = node.named_children[0]
        return cache[(id(child), "norm", head)]
    special = _special_value(node, head, cache)
    if special is not _PENDING:
        return special
    if node.type in _CALLS:
        return _assemble_call(node, cache)
    if node.type in _ATTRIBUTES:
        return _assemble_attr(node, head, cache)
    return _assemble_children(node, cache)


def _normalize(node, head: bool = False):
    # Post-order. A 1,200-deep expression must not raise RecursionError.
    cache = {}
    stack = [(node, "norm", head, False)]
    while stack:
        current, mode, is_head, expanded = stack.pop()
        key = (id(current), mode, is_head)
        if not expanded:
            if key in cache:
                continue
            leaf = _try_leaf(current, mode, is_head)
            if leaf is not _PENDING:
                cache[key] = leaf
                continue
            stack.append((current, mode, is_head, True))
            for child, child_mode, child_head in reversed(_jobs(current, mode, is_head)):
                stack.append((child, child_mode, child_head, False))
            continue
        cache[key] = _assemble(current, mode, is_head, cache)
    return cache[(id(node), "norm", head)]
