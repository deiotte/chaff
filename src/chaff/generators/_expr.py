"""A tiny, safe formula evaluator for derived columns (ADR-0012).

`derived` columns compute a value from other cells in the same row —
`total = price * qty`. The formula is a string; column names are the
variables. This module turns that string into a value **without ever
calling `eval`/`exec`**: it parses to an AST and walks a strict whitelist
of node types. A spec can arrive over the API or from an LLM draft, so
arbitrary code execution is a hard no — anything outside the whitelist
raises `FormulaError`.

Purity is the point: given the same row values, a formula always yields
the same result — no rng, no clock. Derived columns therefore add zero
entropy and seed determinism (INV-3) holds for free.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

__all__ = ["FormulaError", "referenced_names", "validate_expr", "safe_eval"]


class FormulaError(ValueError):
    """A formula is malformed, uses something unsupported, or references an
    unknown column."""


# Whitelisted operators. Anything not here is rejected by node type.
_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
# Whitelisted callables. No attribute access, so `().__class__...` escapes
# can't be reached — these names are the only callables that exist.
_FUNCS = {
    "round": round, "min": min, "max": max, "abs": abs, "len": len,
    "int": int, "float": float, "str": str, "bool": bool,
}
# ── Cost budget (ADR-0029) ───────────────────────────────────────────
# A formula is the one place a spec can turn a small input into a large
# output: `note * 1000000` builds a megabyte from one cell, and it does it
# once *per row*. Bounding the exponent alone was not enough — the exponent
# guard is per-node, so `(10 ** 1000) ** 1000` walks straight past it, and
# repetition (`*`) and concatenation (`+`) were never bounded at all.

#: Longest formula accepted. Parsing is itself work, and no legible formula
#: comes close; a 1 MB formula string is an amplification vector on its own.
_MAX_EXPR_CHARS = 2000

#: Guard against pathological blowups like 9 ** 9 ** 9 tying up the process.
_MAX_POW_EXP = 1000

#: Ceiling on the measured size of any intermediate value. A derived cell is
#: a demo field, not a payload: 100 KB is far past anything legible and far
#: below anything that hurts. Applied to intermediates, not just the result,
#: so a large value can never be built and then discarded.
_MAX_VALUE_SIZE = 100_000

#: Ceiling on integer magnitude, in bits. Python itself refuses to render an
#: int beyond ~4300 digits, so anything larger cannot reach a dataset anyway —
#: it can only burn CPU and memory on the way to being unusable.
_MAX_INT_BITS = 8192


def _size(value: Any) -> int:
    """Measured size of a value, counting nested structure.

    `len` alone is not a budget: `[[x] * 1000] * 1000` has a length of 1000
    and a million elements, and nesting that twice more is a gigabyte. So a
    sequence is charged for its own length *plus* the size of everything in
    it, which is what makes the repetition product bounded rather than just
    the outermost repeat. Recursion is safe because every intermediate has
    already been checked against `_MAX_VALUE_SIZE`.
    """
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (list, tuple)):
        return len(value) + sum(_size(v) for v in value)
    if isinstance(value, bool):
        return 1
    if isinstance(value, int):
        return max(1, value.bit_length() // 8)
    return 1


def _too_big(predicted: int, what: str) -> FormulaError:
    return FormulaError(
        f"formula would build {what} of about {predicted:,} units, over the "
        f"{_MAX_VALUE_SIZE:,} limit for one value. A derived column is a field, "
        "not a payload — reduce the repetition.")


def _guard_binop(op: type, left: Any, right: Any) -> None:
    """Refuse an operation whose result would blow the budget, *before* it runs.

    Checking the result afterwards would mean allocating the megabyte first,
    which is most of the damage. Every amplifying operator has a size that can
    be predicted from its operands, so none of them needs to be evaluated to
    be judged.
    """
    if op is ast.Mult:
        for seq, n in ((left, right), (right, left)):
            if isinstance(seq, (str, list, tuple)) and isinstance(n, int) \
                    and not isinstance(n, bool) and n > 0:
                predicted = _size(seq) * n
                if predicted > _MAX_VALUE_SIZE:
                    raise _too_big(predicted, "a repeated value")
    elif op is ast.Add:
        # Test the operands against the base types, not against `type(left)`:
        # a str *subclass* on one side would make `isinstance(right, type(left))`
        # false and skip the check entirely.
        both_text = isinstance(left, str) and isinstance(right, str)
        both_seq = (isinstance(left, (list, tuple))
                    and isinstance(right, (list, tuple)))
        if both_text or both_seq:
            predicted = _size(left) + _size(right)
            if predicted > _MAX_VALUE_SIZE:
                raise _too_big(predicted, "a joined value")
    elif op is ast.Mod:
        # `"%1000000d" % 5` is a megabyte, and the width lives inside a format
        # string, so unlike the operators above there is nothing to predict
        # from the operands' sizes. printf formatting has no documented use in
        # a derived column; numeric modulo (`id % 10`) is the real one and is
        # untouched. Found by the backstop below, which caught it only after
        # allocating 100 MB — which is most of the damage.
        if isinstance(left, str):
            raise FormulaError(
                "'%' on text is string formatting, which isn't supported in a "
                "formula (it can build an arbitrarily large value). Use + to "
                "join text; % on numbers still works.")
    elif op is ast.Pow:
        if isinstance(right, (int, float)) and right > _MAX_POW_EXP:
            raise FormulaError(f"exponent too large (>{_MAX_POW_EXP})")
        # The exponent check above is per-node, so a chained power slips past
        # it: (10 ** 1000) ** 1000 has an exponent of exactly 1000 at each
        # step and a result of 10**1000000. Predicting the width closes that.
        if isinstance(left, int) and isinstance(right, int) \
                and not isinstance(left, bool) and right > 0:
            if left.bit_length() * right > _MAX_INT_BITS:
                raise FormulaError(
                    f"formula would build a number of about "
                    f"{left.bit_length() * right:,} bits, over the "
                    f"{_MAX_INT_BITS:,}-bit limit for one value.")


def _parse(expr: str) -> ast.Expression:
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaError("formula is empty")
    if len(expr) > _MAX_EXPR_CHARS:
        raise FormulaError(
            f"formula is {len(expr):,} characters, over the {_MAX_EXPR_CHARS:,} "
            "limit. A derived column is a one-line calculation.")
    try:
        return ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"formula is not valid: {expr!r} ({e.msg})") from None


def referenced_names(expr: str) -> set[str]:
    """The column names a formula reads (function names excluded)."""
    tree = _parse(expr)
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - set(_FUNCS)


def validate_expr(expr: str) -> set[str]:
    """Parse the formula and confirm every node is on the whitelist. Returns
    the referenced column names. Raises `FormulaError` otherwise. Cheap
    enough to run at spec-load time so bad formulas fail before generation."""
    tree = _parse(expr)
    _check(tree.body)
    return referenced_names(expr)


def _check(node: ast.AST) -> None:
    """Reject any node type outside the whitelist, recursively."""
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, str, bool)) and node.value is not None:
            raise FormulaError(f"unsupported constant: {node.value!r}")
    elif isinstance(node, ast.Name):
        pass  # resolved against the row at eval time
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BIN_OPS:
            raise FormulaError(f"unsupported operator: {type(node.op).__name__}")
        _check(node.left); _check(node.right)
    elif isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY_OPS:
            raise FormulaError(f"unsupported operator: {type(node.op).__name__}")
        _check(node.operand)
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            _check(v)
    elif isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _CMP_OPS:
                raise FormulaError(f"unsupported comparison: {type(op).__name__}")
        _check(node.left)
        for c in node.comparators:
            _check(c)
    elif isinstance(node, ast.IfExp):
        _check(node.test); _check(node.body); _check(node.orelse)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for el in node.elts:
            _check(el)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise FormulaError(f"only these functions are allowed: {sorted(_FUNCS)}")
        if node.keywords:
            raise FormulaError("keyword arguments aren't supported in formulas")
        for a in node.args:
            _check(a)
    else:
        raise FormulaError(f"unsupported expression element: {type(node).__name__}")


def safe_eval(expr: str, names: dict[str, Any]) -> Any:
    """Evaluate `expr` against a `names` mapping (the row's cells). Assumes
    the whitelist already holds — call `validate_expr` first at load time."""
    return _ev(_parse(expr).body, names)


def _ev(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise FormulaError(
                f"formula references unknown column '{node.id}'. Make sure it's "
                "spelled right and declared before this column.")
        return names[node.id]
    if isinstance(node, ast.BinOp):
        left, right = _ev(node.left, names), _ev(node.right, names)
        op = type(node.op)
        _guard_binop(op, left, right)
        result = _BIN_OPS[op](left, right)
        # Backstop: the predictions above cover every operator that can
        # amplify, but a guard is worth more when it doesn't depend on having
        # enumerated them correctly.
        if _size(result) > _MAX_VALUE_SIZE:
            raise _too_big(_size(result), "a value")
        return result
    if isinstance(node, ast.UnaryOp):
        return _UNARY_OPS[type(node.op)](_ev(node.operand, names))
    if isinstance(node, ast.BoolOp):
        vals = [_ev(v, names) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(vals) and vals[-1]
        return next((v for v in vals if v), vals[-1])
    if isinstance(node, ast.Compare):
        left = _ev(node.left, names)
        for op, comp_node in zip(node.ops, node.comparators):
            right = _ev(comp_node, names)
            if not _CMP_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _ev(node.body, names) if _ev(node.test, names) else _ev(node.orelse, names)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_ev(el, names) for el in node.elts]
    if isinstance(node, ast.Call):
        return _FUNCS[node.func.id](*[_ev(a, names) for a in node.args])
    raise FormulaError(f"unsupported expression element: {type(node).__name__}")
