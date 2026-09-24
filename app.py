"""编织说明逐行针数校验 —— Flask 后端。

POST /api/validate        单尺码校验
  请求: {"cast_on": 12, "pattern": "(k2tog yo)*6\np*12\n..."}
  响应: {
    "ok": true/false,
    "rows": [{"line", "instruction", "in", "consume", "produce", "out"}, ...],
    "error": null | {"line", "type": "parse"|"count"|"cast_on", "message",
                     "char"?, "available"?, "consume"?, "diff"?},
    "final": 全部通过时的最终针数
  }

POST /api/validate-sizes  多尺码校验（可附带线材用量估算）
  请求: {"sizes": [{"name": "S", "cast_on": 12}, ...], "pattern": "...",
         "yarn": {"k": 0.5, "p": 0.5, "yo": 0.2, "k2tog": 1.0,
                  "waste_percent": 10, "grams_per_ball": 50}}
  响应: {
    "ok": true/false,
    "sizes": [{"name", "cast_on", "ok", "rows", "error", "final", "yarn"?}, ...]
  }
  提供 yarn 时，每行额外返回该尺码截至该行的累计克重 "weight"，尺码结果
  额外包含:
    "yarn": {"raw_total", "with_waste", "balls", "partial", "complete"}
  尺码列表或线材参数非法时:
    {"ok": false, "sizes": [], "error": {"type": "sizes"|"yarn", ...}}

逐行推算规则：每个尺码独立地以上一行的产出作为下一行的可用针数；某尺码
消耗与可用针数不一致（不足或超出）时，仅该尺码停止并记录行号与差额，
其余尺码继续推算。指令本身无法解析（含 *{a|b} 项数与尺码数不符、倍数
非法）时所有尺码一并停止，错误中给出原文行号与字符位置。每个尺码的
final 为最后一行有效推算后的针数（全部通过时即最终针数）。

线材用量按每个尺码每行各记号的实际出现次数（嵌套与按尺码重复均计入）
乘以单记号克重累计；遇针数错误的尺码停止后续行，其用量为部分估算
（partial=true）；语法错误同样导致全部尺码的用量为部分估算。含损耗
总量 = 原始总量 ×(1+waste_percent/100)，线团数为其除以每团克重后
向上取整。
"""

import math
import os
import re

from flask import Flask, jsonify, render_template, request

from stitch_parser import STITCH_NAMES, ParseError, parse_row, parse_row_details

app = Flask(__name__)


def _parse_cast_on(raw):
    """校验起针数，返回 (数值, 错误消息)。只接受正整数。"""
    if isinstance(raw, bool):  # bool 是 int 的子类，需先排除
        return None, "起针数必须为正整数"
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        s = raw.strip()
        if not s.isdigit():
            return None, "起针数必须为正整数"
        value = int(s)
    else:
        return None, "起针数必须为正整数"
    if value <= 0:
        return None, "起针数必须为正整数（大于 0）"
    return value, None


def _parse_sizes(raw):
    """校验尺码列表，返回 ([{"name", "cast_on"}, ...], 错误消息)。"""
    if not isinstance(raw, list) or not raw:
        return None, "尺码列表不能为空"
    sizes = []
    for idx, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            return None, "第 {} 个尺码格式错误，应为 {{\"name\", \"cast_on\"}}".format(idx)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, "第 {} 个尺码的名称不能为空".format(idx)
        cast_on, err = _parse_cast_on(item.get("cast_on"))
        if err:
            return None, "尺码 {} 的起针数无效：{}".format(name.strip(), err)
        sizes.append({"name": name.strip(), "cast_on": cast_on})
    return sizes, None


# 线材参数字段 -> 中文标签
_YARN_FIELDS = {
    'k': '下针 k',
    'p': '上针 p',
    'yo': '挂线 yo',
    'k2tog': '两针并一针 k2tog',
    'waste_percent': '损耗百分比',
    'grams_per_ball': '每团克重',
}

_NUMBER_RE = re.compile(r'^[+-]?(\d+(?:\.\d*)?|\.\d+)$')


def _parse_nonneg_number(raw, label, positive=False):
    """解析非负（positive=True 时必须严格为正）数值，接受数字或纯数字字符串。

    拒绝布尔值、指数记法（如 1e2）与无法解析的内容，返回 (数值, 错误消息)。
    """
    if isinstance(raw, bool):
        return None, "{}必须为数值".format(label)
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str) and _NUMBER_RE.match(raw.strip()):
        value = float(raw.strip())
    else:
        return None, "{}必须为数值".format(label)
    if not math.isfinite(value) or value < 0:
        return None, "{}不能为负数".format(label)
    if positive and value <= 0:
        return None, "{}必须大于 0".format(label)
    return value, None


def _parse_yarn(raw):
    """校验线材用量参数，返回 (dict 或 None, 错误消息)。

    缺省（不传 yarn）时返回 (None, None)，表示不做用量估算。
    """
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "线材参数格式错误，应为对象 {\"k\", \"p\", ...}"
    yarn = {}
    for key in ('k', 'p', 'yo', 'k2tog', 'waste_percent'):
        if key not in raw:
            return None, "缺少{}参数".format(_YARN_FIELDS[key])
        value, err = _parse_nonneg_number(raw[key], _YARN_FIELDS[key])
        if err:
            return None, err
        yarn[key] = value
    if 'grams_per_ball' not in raw:
        return None, "缺少{}参数".format(_YARN_FIELDS['grams_per_ball'])
    value, err = _parse_nonneg_number(
        raw['grams_per_ball'], _YARN_FIELDS['grams_per_ball'], positive=True)
    if err:
        return None, err
    yarn['grams_per_ball'] = value
    return yarn, None


def _parse_error_obj(lineno, text, exc):
    """构造解析错误对象，附带原文行号与字符位置（1 起）。"""
    err = {
        "line": lineno,
        "type": "parse",
        "instruction": text,
        "message": str(exc),
    }
    if getattr(exc, "pos", None) is not None:
        err["char"] = exc.pos + 1
    return err


def _count_error_obj(lineno, text, available, consume):
    """构造针数差额错误对象。"""
    diff = consume - available
    if diff > 0:
        message = "需要消耗 {} 针，但仅有 {} 针可用，缺少 {} 针".format(
            consume, available, diff)
    else:
        message = "只消耗了 {} 针，可用的 {} 针还剩 {} 针未使用".format(
            consume, available, -diff)
    return {
        "line": lineno,
        "type": "count",
        "instruction": text,
        "available": available,
        "consume": consume,
        "diff": diff,
        "message": message,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/validate")
def validate():
    data = request.get_json(silent=True) or {}

    cast_on, err = _parse_cast_on(data.get("cast_on"))
    if err:
        return jsonify({
            "ok": False,
            "rows": [],
            "error": {"line": None, "type": "cast_on", "message": err},
        })

    pattern = data.get("pattern", "")
    if not isinstance(pattern, str):
        pattern = str(pattern)

    rows = []
    error = None
    available = cast_on
    for lineno, raw_line in enumerate(pattern.splitlines(), 1):
        text = raw_line.strip()
        if not text:  # 跳过空行，行号仍按原始文本计算
            continue
        try:
            consume, produce = parse_row(text)
        except ParseError as exc:
            error = _parse_error_obj(lineno, text, exc)
            break
        if consume != available:
            error = _count_error_obj(lineno, text, available, consume)
            break
        rows.append({
            "line": lineno,
            "instruction": text,
            "in": available,
            "consume": consume,
            "produce": produce,
            "out": produce,
        })
        available = produce

    result = {"ok": error is None, "rows": rows, "error": error}
    if error is None:
        result["final"] = available
    return jsonify(result)


@app.post("/api/validate-sizes")
def validate_sizes():
    data = request.get_json(silent=True) or {}

    sizes, err = _parse_sizes(data.get("sizes"))
    if err:
        return jsonify({
            "ok": False,
            "sizes": [],
            "error": {"line": None, "type": "sizes", "message": err},
        })

    yarn, err = _parse_yarn(data.get("yarn"))
    if err:
        return jsonify({
            "ok": False,
            "sizes": [],
            "error": {"line": None, "type": "yarn", "message": err},
        })

    pattern = data.get("pattern", "")
    if not isinstance(pattern, str):
        pattern = str(pattern)

    num_sizes = len(sizes)
    states = [{
        "name": s["name"],
        "cast_on": s["cast_on"],
        "available": s["cast_on"],
        "rows": [],
        "error": None,
        "active": True,
        "raw_weight": 0.0 if yarn is not None else None,
    } for s in sizes]

    for lineno, raw_line in enumerate(pattern.splitlines(), 1):
        if not any(st["active"] for st in states):
            break  # 所有尺码都已停止，后续行不再推算
        text = raw_line.strip()
        if not text:  # 跳过空行，行号仍按原始文本计算
            continue
        try:
            consumes, produces, tallies = parse_row_details(text, num_sizes)
        except ParseError as exc:
            # 指令非法时任何尺码都无法继续，全部停止并记录同一错误
            error = _parse_error_obj(lineno, text, exc)
            for st in states:
                if st["active"]:
                    st["error"] = error
                    st["active"] = False
            break
        for i, st in enumerate(states):
            if not st["active"]:
                continue
            consume, produce = consumes[i], produces[i]
            if consume != st["available"]:
                # 仅停止该尺码，其余尺码继续推算
                st["error"] = _count_error_obj(lineno, text, st["available"], consume)
                st["active"] = False
                continue
            row = {
                "line": lineno,
                "instruction": text,
                "in": st["available"],
                "consume": consume,
                "produce": produce,
                "out": produce,
            }
            if yarn is not None:
                # 按该尺码本行各记号的实际次数累计原始克重（含嵌套/按尺码重复）
                st["raw_weight"] += sum(
                    tallies[name][i] * yarn[name] for name in STITCH_NAMES)
                row["weight"] = round(st["raw_weight"], 2)
            st["rows"].append(row)
            st["available"] = produce

    result_sizes = []
    for st in states:
        result = {
            "name": st["name"],
            "cast_on": st["cast_on"],
            "ok": st["error"] is None,
            "rows": st["rows"],
            "error": st["error"],
            "final": st["available"],
        }
        if yarn is not None:
            result["yarn"] = _yarn_summary(yarn, st)
        result_sizes.append(result)
    return jsonify({
        "ok": all(s["ok"] for s in result_sizes),
        "sizes": result_sizes,
    })


def _yarn_summary(yarn, state):
    """按尺码累计结果构造线材用量汇总。"""
    raw_total = round(state["raw_weight"], 2)
    factor = 1 + yarn["waste_percent"] / 100.0
    with_waste_exact = state["raw_weight"] * factor
    with_waste = round(with_waste_exact, 2)
    # 抵消浮点误差，避免 3.0000000000000004 被向上取整成 4
    balls = math.ceil(with_waste_exact / yarn["grams_per_ball"] - 1e-9)
    partial = state["error"] is not None
    return {
        "raw_total": raw_total,
        "with_waste": with_waste,
        "balls": max(balls, 0),
        "partial": partial,
        "complete": not partial,
        "waste_percent": yarn["waste_percent"],
        "grams_per_ball": yarn["grams_per_ball"],
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
