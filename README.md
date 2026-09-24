# 编织说明逐行针数校验

输入尺码与起针数、每行编织指令，逐行推算消耗/产出针数：每个尺码独立地以上一行的
产出作为下一行的可用针数；某尺码针数不足或超出时仅停止该尺码，其余尺码继续推算。
页面并排展示各尺码的有效行、首个错误与最终针数；指令本身非法（含倍数项数与尺码数
不符）时报原文行号与字符位置。也提供单尺码校验接口。

## 指令语法

| 记号 | 含义 | 消耗 | 产出 |
| ---- | ---- | ---- | ---- |
| `k` | 下针 | 1 | 1 |
| `p` | 上针 | 1 | 1 |
| `yo` | 挂线 | 0 | 1 |
| `k2tog` | 两针并一针 | 2 | 1 |
| `(序列)*n` | 括号序列重复 n 次，可嵌套，对所有尺码生效 | 累加 ×n | 累加 ×n |
| `(序列)*{a\|b\|c}` | 按尺码分别取倍数：竖线分隔的正整数按尺码顺序一一对应 | 累加 ×各自倍数 | 累加 ×各自倍数 |

- 记号大小写不敏感，可用空格或逗号分隔，单个记号后也可直接跟 `*n` 或 `*{a|b|c}`
  （如 `k*6`、`k*{6|8|10}`），两种重复可互相嵌套（如 `(k*{2|3} yo)*2`）。
- 倍数必须为正整数；`*0`、负数、缺失倍数都会被拒绝。
- `*{a|b|c}` 的项数必须与尺码个数一致，否则报错并指出原文行号与字符位置。
- 非法记号（如 `ssk`、`k3`）、括号不匹配、空括号都会被拒绝并指出位置。
- 解析只做算术累计，不展开重复项，`(k)*1000000000` 也能瞬间完成。

多尺码示例（尺码 S/M/L，起针 12/16/20，最终各剩 3/4/5 针）：

```text
(k2tog yo)*{6|8|10}
p*{12|16|20}
(k2tog)*{6|8|10}
(k2tog)*{3|4|5}
k*{3|4|5}
```

## 启动方式

一条命令即可启动（Docker Compose，自动安装依赖并构建）：

```bash
docker compose up --build
```

### 仅 Docker

```bash
docker build -t knit-check .
docker run --rm -p 8000:8000 knit-check
```

### 本地运行

```bash
pip install -r requirements.txt
python app.py            # 开发方式
# 或：gunicorn --bind 0.0.0.0:8000 app:app
```

## 访问地址与端口

- 访问地址：<http://localhost:8000>
- 默认端口：`8000`

### 配置项

| 配置 | 方式 | 默认 |
| ---- | ---- | ---- |
| 服务监听端口 | 环境变量 `PORT` | `8000` |
| 对外映射端口 | `docker-compose.yml` 中 `ports` 的左侧 | `8000` |

例如改用 9000 端口访问：把 `docker-compose.yml` 的端口映射改为 `"9000:8000"` 即可；
本地运行时则使用 `PORT=9000 python app.py`。

## API

### 多尺码校验 `POST /api/validate-sizes`

请求体 JSON（`sizes` 按顺序给出尺码名与起针数，`*{...}` 依此顺序取值）：

```json
{
  "sizes": [
    {"name": "S", "cast_on": 12},
    {"name": "M", "cast_on": 16},
    {"name": "L", "cast_on": 20}
  ],
  "pattern": "(k2tog yo)*{6|8|10}\np*{12|16|20}\n(k2tog)*{6|8|10}"
}
```

响应：`sizes` 中每个尺码独立返回有效行 `rows`、首个错误 `error` 与最终针数
`final`（最后一行有效推算后的针数）；`ok` 为全部尺码是否都通过。

```json
{
  "ok": true,
  "sizes": [
    {
      "name": "S", "cast_on": 12, "ok": true,
      "rows": [{"line": 1, "instruction": "(k2tog yo)*{6|8|10}", "in": 12, "consume": 12, "produce": 12, "out": 12}],
      "error": null, "final": 6
    }
  ]
}
```

- 某尺码针数出现差额时仅该尺码停止，`error` 形如
  `{"line": 2, "type": "count", "diff": -1, "message": "...还剩 1 针未使用"}`，
  其余尺码继续推算。
- 指令无法解析（含 `*{a|b}` 项数与尺码数不符、倍数非法）时所有尺码一并停止，
  `error` 形如 `{"line": 2, "type": "parse", "char": 2, "message": "...项数不符..."}`，
  其中 `line` 为原文行号、`char` 为字符位置（均从 1 计）。
- 尺码列表非法时返回 `{"ok": false, "sizes": [], "error": {"type": "sizes", ...}}`。

### 单尺码校验 `POST /api/validate`

请求体 JSON：

```json
{"cast_on": 12, "pattern": "(k2tog yo)*6\np*12"}
```

响应：

```json
{
  "ok": true,
  "rows": [
    {"line": 1, "instruction": "(k2tog yo)*6", "in": 12, "consume": 12, "produce": 12, "out": 12}
  ],
  "error": null,
  "final": 12
}
```

出错时 `error` 形如 `{"line": 2, "type": "count", "diff": 1, "message": "...缺少 1 针"}`
（`type` 为 `parse` / `count` / `cast_on`，解析错误另带 `char` 字符位置），
`rows` 只包含错误行之前的有效行。

## 运行测试

```bash
python -m unittest discover -v
```

## 项目结构

```text
app.py               Flask 应用与逐行推算逻辑（单尺码 + 多尺码接口）
stitch_parser.py     指令词法/语法解析（算术累计，不展开重复项）
templates/index.html 页面（尺码列表编辑 + 并排结果展示）
static/app.js        原生 JavaScript 前端
static/style.css     样式
test_stitch_parser.py 解析器单元测试
test_app.py          接口集成测试
Dockerfile / docker-compose.yml / requirements.txt
```
