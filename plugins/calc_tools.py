"""Exact maths and unit conversion (no AI guessing)."""
import ast
import math
import operator as op

from core.result import done, failed

NAME = "calc_tools"
DESCRIPTION = "Safe calculator + unit converter (length, weight, temperature, data, time, speed)."
METADATA = {"version": "1.0.0", "capabilities": ["calculator", "math", "convert", "units", "percent"], "permissions": [], "timeout": 10}

DECLARATION = {
    "name": "calculate",
    "description": "Exact maths: expression like '(1200*18)/100 + sqrt(144)', or unit conversion with value+from+to (km->mile, kg->lb, c->f, gb->mb, hour->min, kmh->mph).",
    "parameters": {"type": "OBJECT", "properties": {
        "expression": {"type": "STRING", "description": "Maths expression"},
        "value": {"type": "NUMBER", "description": "Number to convert"}, "from": {"type": "STRING", "description": "Unit from"},
        "to": {"type": "STRING", "description": "Unit to"}}, "required": []},
}
_OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Pow: op.pow, ast.Mod: op.mod,
        ast.FloorDiv: op.floordiv, ast.USub: op.neg, ast.UAdd: op.pos}
_FN = {k: getattr(math, k) for k in ("sqrt", "sin", "cos", "tan", "log", "log10", "exp", "floor", "ceil", "factorial", "radians", "degrees")}
_FN.update(abs=abs, round=round, min=min, max=max)
_CONST = {"pi": math.pi, "e": math.e}
_LIN = {  # to base unit
    "m": 1, "km": 1000, "cm": .01, "mm": .001, "mile": 1609.344, "mi": 1609.344, "ft": .3048, "foot": .3048, "inch": .0254, "in": .0254, "yard": .9144,
    "kg": 1, "g": .001, "mg": 1e-6, "lb": .45359237, "pound": .45359237, "oz": .028349523, "ton": 1000,
    "b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4,
    "s": 1, "sec": 1, "min": 60, "minute": 60, "hour": 3600, "hr": 3600, "day": 86400, "week": 604800,
    "ms": .001, "kmh": 1 / 3.6, "mph": .44704, "mps": 1, "knot": .514444,
    "l": 1, "liter": 1, "ml": .001, "gallon": 3.78541,
}
_GROUP = {"m": "len", "km": "len", "cm": "len", "mm": "len", "mile": "len", "mi": "len", "ft": "len", "foot": "len", "inch": "len", "in": "len", "yard": "len",
          "kg": "mass", "g": "mass", "mg": "mass", "lb": "mass", "pound": "mass", "oz": "mass", "ton": "mass",
          "b": "data", "kb": "data", "mb": "data", "gb": "data", "tb": "data",
          "s": "time", "sec": "time", "min": "time", "minute": "time", "hour": "time", "hr": "time", "day": "time", "week": "time", "ms": "time",
          "kmh": "speed", "mph": "speed", "mps": "speed", "knot": "speed", "l": "vol", "liter": "vol", "ml": "vol", "gallon": "vol"}


def _ev(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        if isinstance(n.op, ast.Pow) and abs(_ev(n.right)) > 1000:
            raise ValueError("exponent too large")
        return _OPS[type(n.op)](_ev(n.left), _ev(n.right))
    if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_ev(n.operand))
    if isinstance(n, ast.Name) and n.id in _CONST:
        return _CONST[n.id]
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FN and not n.keywords:
        return _FN[n.func.id](*[_ev(a) for a in n.args])
    raise ValueError("unsupported expression")


def run(args, ctx):
    fr, to = str(args.get("from", "")).lower().strip(), str(args.get("to", "")).lower().strip()
    if args.get("value") is not None and fr and to:
        v = float(args["value"])
        temps = {"c", "f", "k"}
        if fr in temps and to in temps:
            c = v if fr == "c" else (v - 32) * 5 / 9 if fr == "f" else v - 273.15
            r = c if to == "c" else c * 9 / 5 + 32 if to == "f" else c + 273.15
            return done(f"{v:g} {fr.upper()} = {r:.2f} {to.upper()}")
        if fr in _GROUP and to in _GROUP and _GROUP[fr] == _GROUP[to]:
            r = v * _LIN[fr] / _LIN[to]
            return done(f"{v:g} {fr} = {r:,.6g} {to}")
        return failed("Cannot convert", f"'{fr}' and '{to}' are unknown or not the same kind of unit")
    expr = str(args.get("expression", "")).replace("^", "**").replace("\u00d7", "*").replace("\u00f7", "/").replace(",", "")
    if not expr.strip():
        return failed("Nothing to calculate", "give an expression or value+from+to")
    try:
        r = _ev(ast.parse(expr.strip(), mode="eval").body)
        return done(f"{expr.strip()} = {r:,.10g}")
    except ZeroDivisionError:
        return failed("Cannot calculate", "division by zero")
    except Exception as e:
        return failed("Cannot calculate", f"{type(e).__name__}: {e}")
