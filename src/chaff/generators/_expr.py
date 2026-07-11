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
# Guard against pathological blowups like 9 ** 9 ** 9 tying up the process.
_MAX_POW_EXP = 1000


def _parse(expr: str) -> ast.Expression:
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaError("formula is empty")
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
        if isinstance(node.op, ast.Pow) and isinstance(right, (int, float)) and right > _MAX_POW_EXP:
            raise FormulaError(f"exponent too large (>{_MAX_POW_EXP})")
        return _BIN_OPS[type(node.op)](left, right)
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
