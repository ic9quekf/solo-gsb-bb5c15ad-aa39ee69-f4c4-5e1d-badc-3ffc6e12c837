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
         "yarn": {"weights": {"k": 克, "p": 克, "yo": 克, "k2tog": 克},
                  "waste_percent": 损耗百分比, "ball_grams": 每团克重}}
  响应: {
    "ok": true/false,
    "sizes": [{"name", "cast_on", "ok", "rows", "error", "final", "yarn"?}, ...]
  }
  给出 yarn 时，每行 rows[i] 另带 "weight"（该尺码截至该行的累计原始克重），
  尺码结果另带 "yarn": {"raw_total", "with_waste", "balls", "partial"}：
  原始总量按各尺码实际记号次数（含嵌套与 *{a|b} 重复）乘单个记号克重累计；
  含损耗总量 = 原始总量 ×(1+损耗%)；线团数对含损耗总量向上取整；该尺码因
  针数/语法错误未走完全部行时 partial 为 true（部分估算）。
  尺码列表非法时: {"ok": false, "sizes": [], "error": {"type": "sizes", ...}}
  线材参数非法时: {"ok": false, "sizes": [], "error": {"type": "yarn", ...}}

逐行推算规则：每个尺码独立地以上一行的产出作为下一行的可用针数；某尺码
消耗与可用针数不一致（不足或超出）时，仅该尺码停止并记录行号与差额，
其余尺码继续推算。指令本身无法解析（含 *{a|b} 项数与尺码数不符、倍数
非法）时所有尺码一并停止，错误中给出原文行号与字符位置。每个尺码的
final 为最后一行有效推算后的针数（全部通过时即最终针数）。
"""

import math
import os

from flask import Flask, jsonify, render_template, request

from stitch_parser import STITCH_NAMES, ParseError, parse_row, parse_row_sizes

app = Flask(__name__)

# 克重累计为浮点运算，返回前统一保留的小数位数（线团数仍按未舍入值取整）
WEIGHT_DECIMALS = 3
# 抵消浮点误差，避免 50.0 克被算成需要 2 团
BALL_EPSILON = 1e-9


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


def _parse_nonnegative_number(raw, field_label):
    """解析非负有限数值，返回 (数值, 错误消息)。

    接受 int/float（排除 bool）与数字字符串（首尾空白与结尾的 % 会被去掉）。
    """
    if isinstance(raw, bool):
        return None, "{}必须是非负数字".format(field_label)
    if isinstance(raw, int):
        value = float(raw)
    elif isinstance(raw, float):
        value = raw
    elif isinstance(raw, str):
        s = raw.strip().rstrip('%').strip()
        if not s:
            return None, "{}必须是非负数字".format(field_label)
        try:
            value = float(s)
        except ValueError:
            return None, "{}必须是非负数字".format(field_label)
    else:
        return None, "{}必须是非负数字".format(field_label)
    if not math.isfinite(value) or value < 0:
        return None, "{}必须是非负数字".format(field_label)
    return value, None


def _parse_yarn(raw):
    """校验线材用量参数，返回 ({"weights", "waste_percent", "ball_grams"}, 错误消息)。

    weights: {k, p, yo, k2tog} 的单个记号克重，均为非负数，缺失按 0 处理；
    waste_percent: 损耗百分比，非负数，缺失按 0 处理；
    ball_grams: 每团克重，必须为正数。
    """
    if not isinstance(raw, dict):
        return None, "线材参数格式错误，应为 {\"weights\", \"waste_percent\", \"ball_grams\"}"
    weights_raw = raw.get("weights", {})
    if not isinstance(weights_raw, dict):
        return None, "记号克重 weights 应为 {\"k\", \"p\", \"yo\", \"k2tog\"} 形式的对象"
    weights = {}
    labels = {"k": "记号 k 的单个克重", "p": "记号 p 的单个克重",
              "yo": "记号 yo 的单个克重", "k2tog": "记号 k2tog 的单个克重"}
    for name in STITCH_NAMES:
        value, err = _parse_nonnegative_number(weights_raw.get(name, 0), labels[name])
        if err:
            return None, err
        weights[name] = value

    waste, err = _parse_nonnegative_number(raw.get("waste_percent", 0), "损耗百分比")
    if err:
        return None, err

    ball, err = _parse_nonnegative_number(raw.get("ball_grams"), "每团克重")
    if err:
        return None, err
    if ball <= 0:
        return None, "每团克重必须为正数（大于 0）"

    return {"weights": weights, "waste_percent": waste, "ball_grams": ball}, None


def _row_weight(stitch_counts, size_idx, weights):
    """按某尺码该行各记号的实际出现次数乘以单个记号克重，返回本行原始克重。"""
    return sum(stitch_counts[name][size_idx] * weights[name] for name in STITCH_NAMES)


def _yarn_summary(raw_total, yarn_cfg):
    """由原始克重汇总含损耗总量、向上取整的线团数。"""
    with_waste = raw_total * (1 + yarn_cfg["waste_percent"] / 100.0)
    balls = math.ceil(with_waste / yarn_cfg["ball_grams"] - BALL_EPSILON) \
        if with_waste > 0 else 0
    return {
        "raw_total": round(raw_total, WEIGHT_DECIMALS),
        "with_waste": round(with_waste, WEIGHT_DECIMALS),
        "balls": balls,
    }


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
            consume, produce, _stitch_counts = parse_row(text)
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

    yarn_cfg = None
    if data.get("yarn") is not None:
        yarn_cfg, err = _parse_yarn(data.get("yarn"))
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
        "raw_weight": 0.0,
    } for s in sizes]

    # 非空指令行总数：某尺码有效行数不足它即为部分估算
    total_lines = sum(1 for raw_line in pattern.splitlines() if raw_line.strip())

    for lineno, raw_line in enumerate(pattern.splitlines(), 1):
        if not any(st["active"] for st in states):
            break  # 所有尺码都已停止，后续行不再推算
        text = raw_line.strip()
        if not text:  # 跳过空行，行号仍按原始文本计算
            continue
        try:
            consumes, produces, stitch_counts = parse_row_sizes(text, num_sizes)
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
            if yarn_cfg is not None:
                st["raw_weight"] += _row_weight(stitch_counts, i, yarn_cfg["weights"])
                row["weight"] = round(st["raw_weight"], WEIGHT_DECIMALS)
            st["rows"].append(row)
            st["available"] = produce

    result_sizes = []
    for st in states:
        item = {
            "name": st["name"],
            "cast_on": st["cast_on"],
            "ok": st["error"] is None,
            "rows": st["rows"],
            "error": st["error"],
            "final": st["available"],
        }
        if yarn_cfg is not None:
            summary = _yarn_summary(st["raw_weight"], yarn_cfg)
            # 针数错误只停该尺码；语法错误停全部尺码——无论哪种，有效行
            # 少于指令总行数时用量都是不完整的部分估算
            summary["partial"] = len(st["rows"]) < total_lines
            item["yarn"] = summary
        result_sizes.append(item)
    return jsonify({
        "ok": all(s["ok"] for s in result_sizes),
        "sizes": result_sizes,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
