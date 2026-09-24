"""编织指令解析器：按括号与倍数计算每行的消耗/产出针数。

支持的记号（大小写不敏感，可用空格或逗号分隔）：
  k       下针：消耗 1 针，产出 1 针
  p       上针：消耗 1 针，产出 1 针
  yo      挂线：不消耗，产出 1 针
  k2tog   两针并一针：消耗 2 针，产出 1 针
  (序列)*n          括号序列重复 n 次，可嵌套；单个记号后也可跟 *n
  (序列)*{a|b|c}    按尺码分别取倍数：竖线分隔的正整数按尺码顺序一一对应，
                    项数必须与尺码个数一致；单个记号后同样可跟 *{a|b|c}

普通 *n 对所有尺码生效，*{a|b|c} 与 *n 两种重复可互相嵌套。
解析只做算术累计，绝不展开重复项，因此 (k)*1000000000 也能瞬间算完。

解析结果除消耗/产出针数外，还给出每个记号在各行中的出现次数（按尺码分别累计，
重复与嵌套同样只做乘法），供线材用量估算复用。
"""

import re

# 记号 -> (消耗针数, 产出针数)
STITCH_COUNTS = {
    'k': (1, 1),
    'p': (1, 1),
    'yo': (0, 1),
    'k2tog': (2, 1),
}

# 记号出现次数按此固定顺序返回，线材估算据此与克重一一对应
STITCH_NAMES = ('k', 'p', 'yo', 'k2tog')


def zero_stitch_counts(num_sizes):
    """全部记号次数为 0 的 {记号: [各尺码次数]} 字典。"""
    return {name: [0] * num_sizes for name in STITCH_NAMES}


def add_stitch_counts(a, b):
    """两个记号次数字典逐项相加，返回新字典。"""
    return {name: [x + y for x, y in zip(a[name], b[name])] for name in STITCH_NAMES}


def scale_stitch_counts(counts, mults):
    """各尺码的记号次数分别乘上该尺码的倍数，返回新字典。"""
    return {name: [v * m for v, m in zip(counts[name], mults)]
            for name in STITCH_NAMES}


# 注意 k2tog 必须排在 k 之前，保证最长匹配
TOKEN_RE = re.compile(r"""
    (?P<STITCH>k2tog|yo|k|p)
  | (?P<LPAREN>\()
  | (?P<RPAREN>\))
  | (?P<STAR>\*)
  | (?P<LBRACE>\{)
  | (?P<RBRACE>\})
  | (?P<PIPE>\|)
  | (?P<NUM>\d+)
  | (?P<SEP>[\s,]+)
""", re.IGNORECASE | re.VERBOSE)


class ParseError(ValueError):
    """指令解析错误，message 中已包含定位信息。

    pos 为出错字符在原文中的下标（从 0 计），供接口返回结构化的字符位置；
    无法定位到具体字符时为 None。
    """

    def __init__(self, message, pos=None):
        super().__init__(message)
        self.pos = pos


def _tokenize(text):
    tokens = []
    pos = 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise ParseError(
                "无法识别的记号 {!r}（第 {} 个字符）".format(text[pos], pos + 1), pos)
        if m.lastgroup != 'SEP':
            tokens.append((m.lastgroup, m.group(), m.start()))
        pos = m.end()
    return tokens


class _Parser(object):
    def __init__(self, tokens, num_sizes):
        self.tokens = tokens
        self.i = 0
        self.num_sizes = num_sizes

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def advance(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse_row(self):
        """row := sequence EOF，返回 (consumes, produces, stitch_counts)。

        consumes/produces 为长度 num_sizes 的针数列表；stitch_counts 为
        {记号: [各尺码出现次数]}。
        """
        c, p, counts = self.parse_sequence()
        tok = self.peek()
        if tok is not None:
            kind, val, pos = tok
            if kind == 'RPAREN':
                raise ParseError("多余的右括号 ')'（第 {} 个字符）".format(pos + 1), pos)
            if kind == 'NUM':
                raise ParseError(
                    "意外的数字 {!r}（第 {} 个字符）：倍数需写成 (序列)*次数 的形式".format(val, pos + 1), pos)
            if kind == 'STAR':
                raise ParseError(
                    "意外的 '*'（第 {} 个字符）：'*' 必须跟在记号或 ')' 之后".format(pos + 1), pos)
            raise ParseError("意外的记号 {!r}（第 {} 个字符）".format(val, pos + 1), pos)
        return c, p, counts

    def parse_sequence(self):
        """sequence := item+，消耗/产出/记号次数逐项累加"""
        total_c = [0] * self.num_sizes
        total_p = [0] * self.num_sizes
        total_counts = zero_stitch_counts(self.num_sizes)
        while True:
            tok = self.peek()
            if tok is None or tok[0] not in ('STITCH', 'LPAREN'):
                break
            c, p, counts = self.parse_item()
            for j in range(self.num_sizes):
                total_c[j] += c[j]
                total_p[j] += p[j]
            total_counts = add_stitch_counts(total_counts, counts)
        return total_c, total_p, total_counts

    def parse_item(self):
        """item := (STITCH | '(' sequence ')') ('*' repeat)?"""
        kind, val, pos = self.advance()
        if kind == 'STITCH':
            name = val.lower()
            c0, p0 = STITCH_COUNTS[name]
            c = [c0] * self.num_sizes
            p = [p0] * self.num_sizes
            counts = zero_stitch_counts(self.num_sizes)
            counts[name] = [1] * self.num_sizes
        else:  # LPAREN
            c, p, counts = self.parse_sequence()
            if not any(c) and not any(p):
                raise ParseError("括号内不能为空（第 {} 个字符）".format(pos + 1), pos)
            tok = self.peek()
            if tok is None:
                raise ParseError("缺少与 '(' 对应的 ')'（第 {} 个字符）".format(pos + 1), pos)
            if tok[0] != 'RPAREN':
                raise ParseError(
                    "括号内出现意外的记号 {!r}（第 {} 个字符）".format(tok[1], tok[2] + 1), tok[2])
            self.advance()
        tok = self.peek()
        if tok is not None and tok[0] == 'STAR':
            self.advance()
            mults = self.parse_repeat(tok)
            c = [cv * m for cv, m in zip(c, mults)]
            p = [pv * m for pv, m in zip(p, mults)]
            counts = scale_stitch_counts(counts, mults)
        return c, p, counts

    def parse_repeat(self, star_tok):
        """repeat := NUM | '{' NUM ('|' NUM)* '}'，返回长度 num_sizes 的倍数列表。

        普通 *n 对所有尺码生效；*{a|b|c} 按尺码顺序分别取值，项数必须等于尺码数。
        """
        star_pos = star_tok[2]
        tok = self.peek()
        if tok is None:
            raise ParseError(
                "'*' 后必须跟正整数倍数（第 {} 个字符）".format(star_pos + 1), star_pos)
        if tok[0] == 'NUM':
            self.advance()
            return [self._positive_num(tok)] * self.num_sizes
        if tok[0] == 'LBRACE':
            self.advance()
            values = []
            while True:
                ntok = self.peek()
                if ntok is None:
                    raise ParseError(
                        "尺码倍数 '{{...}}' 未闭合，缺少正整数（第 {} 个字符）".format(tok[2] + 1), tok[2])
                if ntok[0] != 'NUM':
                    raise ParseError(
                        "尺码倍数 '{{...}}' 中必须是正整数，收到 {!r}（第 {} 个字符）".format(
                            ntok[1], ntok[2] + 1), ntok[2])
                self.advance()
                values.append(self._positive_num(ntok))
                sep = self.peek()
                if sep is not None and sep[0] == 'PIPE':
                    self.advance()
                    continue
                if sep is not None and sep[0] == 'RBRACE':
                    self.advance()
                    break
                if sep is None:
                    raise ParseError(
                        "尺码倍数 '{{...}}' 缺少右括号 '}}'（第 {} 个字符）".format(tok[2] + 1), tok[2])
                raise ParseError(
                    "尺码倍数 '{{...}}' 的数字需用 '|' 分隔，收到 {!r}（第 {} 个字符）".format(
                        sep[1], sep[2] + 1), sep[2])
            if len(values) != self.num_sizes:
                raise ParseError(
                    "尺码倍数项数不符：共 {} 个尺码，但倍数给出了 {} 个值（第 {} 个字符）".format(
                        self.num_sizes, len(values), star_pos + 1), star_pos)
            return values
        raise ParseError(
            "'*' 后必须跟正整数倍数或按尺码的 '{{a|b|...}}'（第 {} 个字符）".format(tok[2] + 1), tok[2])

    @staticmethod
    def _positive_num(ntok):
        n = int(ntok[1])
        if n <= 0:
            raise ParseError(
                "倍数必须为正整数，收到 {}（第 {} 个字符）".format(n, ntok[2] + 1), ntok[2])
        return n


def parse_row_sizes(text, num_sizes):
    """解析一行指令，返回 (consumes, produces, stitch_counts)。

    consumes/produces 为各尺码的消耗/产出针数列表，长度均等于 num_sizes；
    stitch_counts 为 {记号: [各尺码出现次数]}（顺序见 STITCH_NAMES），
    次数按各尺码实际倍数（含嵌套与 *{a|b|c}）算术累计。非法输入抛出 ParseError。
    """
    if num_sizes < 1:
        raise ValueError("num_sizes 必须为正整数")
    tokens = _tokenize(text)
    if not tokens:
        raise ParseError("该行没有有效指令")
    return _Parser(tokens, num_sizes).parse_row()


def parse_row(text):
    """单尺码解析：返回 (消耗针数, 产出针数, 记号次数字典)。非法输入抛出 ParseError。"""
    consumes, produces, stitch_counts = parse_row_sizes(text, 1)
    return consumes[0], produces[0], stitch_counts
