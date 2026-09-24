import unittest

from stitch_parser import STITCH_NAMES, ParseError, parse_row, parse_row_sizes


def counts_row(text):
    """parse_row 的记号次数，取单尺码并按 STITCH_NAMES 排成列表。"""
    _c, _p, stitch_counts = parse_row(text)
    return [stitch_counts[name][0] for name in STITCH_NAMES]


def counts_sizes(text, num_sizes):
    """parse_row_sizes 的记号次数，按 STITCH_NAMES 排成 {记号: [各尺码次数]}。"""
    _c, _p, stitch_counts = parse_row_sizes(text, num_sizes)
    return stitch_counts


class TestParseRow(unittest.TestCase):
    def test_basic_stitches(self):
        c, p, _counts = parse_row('k')
        self.assertEqual((c, p), (1, 1))
        c, p, _counts = parse_row('p')
        self.assertEqual((c, p), (1, 1))
        c, p, _counts = parse_row('yo')
        self.assertEqual((c, p), (0, 1))
        c, p, _counts = parse_row('k2tog')
        self.assertEqual((c, p), (2, 1))

    def test_sequence_with_separators(self):
        self.assertEqual(parse_row('k k yo k2tog')[:2], (4, 4))
        self.assertEqual(parse_row('k, k, yo, k2tog')[:2], (4, 4))
        self.assertEqual(parse_row('kkyo k2tog')[:2], (4, 4))

    def test_stitch_counts_basic(self):
        # STITCH_NAMES = ('k', 'p', 'yo', 'k2tog')
        self.assertEqual(counts_row('k k yo k2tog'), [2, 0, 1, 1])
        self.assertEqual(counts_row('p*3'), [0, 3, 0, 0])
        self.assertEqual(counts_row('(k2tog yo)*6'), [0, 0, 6, 6])

    def test_stitch_counts_nested(self):
        # ((k yo)*2 k2tog)*3：k 6、yo 6、k2tog 3
        self.assertEqual(counts_row('((k yo)*2 k2tog)*3'), [6, 0, 6, 3])

    def test_group_repeat(self):
        self.assertEqual(parse_row('(k2tog yo)*6')[:2], (12, 12))
        self.assertEqual(parse_row('(k yo)*2 p')[:2], (3, 5))

    def test_nested_groups(self):
        # (k yo)*2 -> (2,4)；+ k2tog -> (4,5)；整体 *3
        self.assertEqual(parse_row('((k yo)*2 k2tog)*3')[:2], (12, 15))

    def test_atom_repeat(self):
        self.assertEqual(parse_row('k*3')[:2], (3, 3))
        self.assertEqual(parse_row('k2tog*4')[:2], (8, 4))

    def test_huge_multiplier_is_not_expanded(self):
        self.assertEqual(parse_row('(k yo)*1000000000')[:2], (1000000000, 2000000000))

    def test_case_insensitive(self):
        self.assertEqual(parse_row('K2TOG, YO, K, P')[:2], (4, 4))
        self.assertEqual(counts_row('K2TOG, YO, K, P'), [1, 1, 1, 1])

    def test_invalid_tokens(self):
        for bad in ['ssk', 'k3', 'k 2', 'm1', 'k/p', 'k+yo']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row(bad)

    def test_bad_parentheses(self):
        for bad in ['(k yo', 'k)', '()', '( )', '(k))*2', '(k (yo)']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row(bad)

    def test_bad_multiplier(self):
        for bad in ['k*0', '(k)*0', 'k*-1', 'k*', 'k*yo', '*3 k', '(k)*2*3']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row(bad)

    def test_empty_row(self):
        with self.assertRaises(ParseError):
            parse_row('   ')


class TestParseRowSizes(unittest.TestCase):
    def test_plain_repeat_applies_to_all_sizes(self):
        c, p, _counts = parse_row_sizes('k*3', 3)
        self.assertEqual(c, [3, 3, 3])
        self.assertEqual(p, [3, 3, 3])

    def test_size_repeat_on_atom(self):
        c, p, counts = parse_row_sizes('k*{6|8|10}', 3)
        self.assertEqual(c, [6, 8, 10])
        self.assertEqual(p, [6, 8, 10])
        self.assertEqual(counts['k'], [6, 8, 10])

    def test_size_repeat_on_group(self):
        c, p, counts = parse_row_sizes('(k2tog yo)*{6|8|10}', 3)
        self.assertEqual(c, [12, 16, 20])
        self.assertEqual(p, [12, 16, 20])
        self.assertEqual(counts['yo'], [6, 8, 10])
        self.assertEqual(counts['k2tog'], [6, 8, 10])

    def test_size_repeat_with_spaces(self):
        c, p, _counts = parse_row_sizes('k*{6 | 8 | 10}', 3)
        self.assertEqual(c, [6, 8, 10])
        self.assertEqual(p, [6, 8, 10])

    def test_nested_mixed_repeats(self):
        # k*{2|3} -> c [2,3] p [2,3]；+ yo -> c [2,3] p [3,4]；整体 *2
        c, p, counts = parse_row_sizes('(k*{2|3} yo)*2', 2)
        self.assertEqual(c, [4, 6])
        self.assertEqual(p, [6, 8])
        self.assertEqual(counts['k'], [4, 6])
        self.assertEqual(counts['yo'], [2, 2])

    def test_nested_group_size_repeat_outside(self):
        # (k yo)*2 -> c 2 p 4；整体 *{2|3}
        c, p, counts = parse_row_sizes('((k yo)*2)*{2|3}', 2)
        self.assertEqual(c, [4, 6])
        self.assertEqual(p, [8, 12])
        self.assertEqual(counts['k'], [4, 6])
        self.assertEqual(counts['yo'], [4, 6])

    def test_stitch_counts_per_size_sequence(self):
        counts = counts_sizes('k p*{1|2|3} yo k2tog*{2|1|2}', 3)
        self.assertEqual(counts['k'], [1, 1, 1])
        self.assertEqual(counts['p'], [1, 2, 3])
        self.assertEqual(counts['yo'], [1, 1, 1])
        self.assertEqual(counts['k2tog'], [2, 1, 2])

    def test_single_size_brace(self):
        c, p, _counts = parse_row_sizes('k*{5}', 1)
        self.assertEqual((c, p), ([5], [5]))
        self.assertEqual(parse_row('k*{5}')[:2], (5, 5))

    def test_count_mismatch_reports_position(self):
        with self.assertRaises(ParseError) as ctx:
            parse_row_sizes('kp*{6|8}', 3)
        self.assertIn('项数不符', str(ctx.exception))
        self.assertEqual(ctx.exception.pos, 2)  # '*' 的下标

    def test_bad_size_repeat(self):
        for bad in ['k*{0|2}', 'k*{2|}', 'k*{|2}', 'k*{}', 'k*{2 3}',
                    'k*{2|3', 'k*{2|3}*2', 'k*{a|b}', 'k*2|3']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row_sizes(bad, 2)

    def test_huge_size_multiplier_is_not_expanded(self):
        c, p, counts = parse_row_sizes('(k yo)*{1000000000|1}', 2)
        self.assertEqual(c, [1000000000, 1])
        self.assertEqual(p, [2000000000, 2])
        self.assertEqual(counts['k'], [1000000000, 1])
        self.assertEqual(counts['yo'], [1000000000, 1])


if __name__ == '__main__':
    unittest.main()
