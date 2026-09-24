import unittest

from app import app


class TestValidateApi(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def post(self, cast_on, pattern):
        return self.client.post('/api/validate', json={
            'cast_on': cast_on,
            'pattern': pattern,
        }).get_json()

    def test_all_valid(self):
        data = self.post(12, '(k2tog yo)*6\np*12\n(k2tog)*6\n(yo k2tog)*3\nk*6')
        self.assertTrue(data['ok'])
        self.assertIsNone(data['error'])
        self.assertEqual(len(data['rows']), 5)
        self.assertEqual(data['rows'][0]['in'], 12)
        self.assertEqual(data['rows'][0]['consume'], 12)
        self.assertEqual(data['rows'][0]['out'], 12)
        self.assertEqual(data['rows'][2]['out'], 6)
        self.assertEqual(data['final'], 6)

    def test_count_shortfall(self):
        # 第 2 行需要 13 针，但只有 12 针，缺 1 针
        data = self.post(12, 'k*12\nk*13\nk*12')
        self.assertFalse(data['ok'])
        self.assertEqual(len(data['rows']), 1)  # 错误行及之后不再推算
        err = data['error']
        self.assertEqual(err['type'], 'count')
        self.assertEqual(err['line'], 2)
        self.assertEqual(err['diff'], 1)

    def test_count_leftover(self):
        # 第 2 行只消耗 10 针，剩余 2 针未使用
        data = self.post(12, 'k*12\nk*10')
        self.assertFalse(data['ok'])
        err = data['error']
        self.assertEqual(err['line'], 2)
        self.assertEqual(err['diff'], -2)

    def test_parse_error_stops(self):
        data = self.post(4, 'k*4\nssk\nk*4')
        self.assertFalse(data['ok'])
        self.assertEqual(len(data['rows']), 1)
        self.assertEqual(data['error']['type'], 'parse')
        self.assertEqual(data['error']['line'], 2)

    def test_blank_lines_skipped_but_line_numbers_kept(self):
        data = self.post(2, 'k*2\n\nk*3')
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['line'], 3)

    def test_bad_cast_on(self):
        for bad in [0, -3, 'abc', '2.5', None, True]:
            data = self.post(bad, 'k*2')
            self.assertFalse(data['ok'], msg=repr(bad))
            self.assertEqual(data['error']['type'], 'cast_on')

    def test_string_cast_on_accepted(self):
        data = self.post('4', 'k*4')
        self.assertTrue(data['ok'])
        self.assertEqual(data['final'], 4)

    def test_single_size_rejects_multi_size_repeat(self):
        # 单尺码接口下 *{a|b} 项数（2）与尺码数（1）不符
        data = self.post(4, 'k*{2|2}')
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['type'], 'parse')
        self.assertIn('项数不符', data['error']['message'])


class TestValidateSizesApi(unittest.TestCase):
    SIZES = [{'name': 'S', 'cast_on': 12},
             {'name': 'M', 'cast_on': 16},
             {'name': 'L', 'cast_on': 20}]

    YARN = {'weights': {'k': 1, 'p': 1, 'yo': 0.3, 'k2tog': 2},
            'waste_percent': 10, 'ball_grams': 50}

    def setUp(self):
        self.client = app.test_client()

    def post(self, sizes, pattern, yarn=None):
        body = {'sizes': sizes, 'pattern': pattern}
        if yarn is not None:
            body['yarn'] = yarn
        return self.client.post('/api/validate-sizes', json=body).get_json()

    def test_all_valid(self):
        pattern = '\n'.join([
            '(k2tog yo)*{6|8|10}',
            'p*{12|16|20}',
            '(k2tog)*{6|8|10}',
            '(k2tog)*{3|4|5}',
            'k*{3|4|5}',
        ])
        data = self.post(self.SIZES, pattern)
        self.assertTrue(data['ok'])
        self.assertEqual(len(data['sizes']), 3)
        self.assertEqual([s['name'] for s in data['sizes']], ['S', 'M', 'L'])
        self.assertEqual([s['final'] for s in data['sizes']], [3, 4, 5])
        for s in data['sizes']:
            self.assertTrue(s['ok'])
            self.assertIsNone(s['error'])
            self.assertEqual(len(s['rows']), 5)
        self.assertEqual(data['sizes'][0]['rows'][0]['in'], 12)
        self.assertEqual(data['sizes'][1]['rows'][0]['consume'], 16)
        self.assertEqual(data['sizes'][2]['rows'][2]['out'], 10)

    def test_plain_repeat_applies_to_every_size(self):
        sizes = [{'name': 'A', 'cast_on': 4}, {'name': 'B', 'cast_on': 4}]
        data = self.post(sizes, '(k2tog yo)*2\np*4')
        self.assertTrue(data['ok'])
        self.assertEqual([s['final'] for s in data['sizes']], [4, 4])

    def test_failing_size_stops_independently(self):
        # 第 2 行 M 差 1 针：仅 M 停止，S/L 继续推算到结束
        sizes = [{'name': 'S', 'cast_on': 4},
                 {'name': 'M', 'cast_on': 6},
                 {'name': 'L', 'cast_on': 8}]
        pattern = 'k*{4|6|8}\n(k yo)*{4|5|8}\nk*{8|1|16}'
        data = self.post(sizes, pattern)
        self.assertFalse(data['ok'])
        s, m, l = data['sizes']
        self.assertTrue(s['ok'])
        self.assertEqual(len(s['rows']), 3)
        self.assertEqual(s['final'], 8)
        self.assertFalse(m['ok'])
        self.assertEqual(m['error']['type'], 'count')
        self.assertEqual(m['error']['line'], 2)
        self.assertEqual(m['error']['diff'], -1)
        self.assertEqual(len(m['rows']), 1)  # 错误行及之后不再推算
        self.assertEqual(m['final'], 6)       # 最后有效行之后的针数
        self.assertTrue(l['ok'])
        self.assertEqual(len(l['rows']), 3)
        self.assertEqual(l['final'], 16)

    def test_parse_error_stops_all_sizes(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\nssk\nk*1')
        self.assertFalse(data['ok'])
        for s in data['sizes']:
            self.assertFalse(s['ok'])
            self.assertEqual(s['error']['type'], 'parse')
            self.assertEqual(s['error']['line'], 2)
            self.assertEqual(len(s['rows']), 1)

    def test_size_count_mismatch_reports_line_and_char(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\np*{6|8}')
        self.assertFalse(data['ok'])
        err = data['sizes'][0]['error']
        self.assertEqual(err['type'], 'parse')
        self.assertEqual(err['line'], 2)
        self.assertEqual(err['char'], 2)  # '*' 是第 2 个字符
        self.assertIn('项数不符', err['message'])

    def test_bad_multiplier_reports_line_and_char(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\n(p)*0')
        err = data['sizes'][0]['error']
        self.assertEqual(err['type'], 'parse')
        self.assertEqual(err['line'], 2)
        self.assertEqual(err['char'], 5)  # '0' 是第 5 个字符

    def test_blank_lines_skipped_but_line_numbers_kept(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\n\nssk')
        self.assertFalse(data['ok'])
        self.assertEqual(data['sizes'][0]['error']['line'], 3)

    def test_bad_sizes(self):
        for bad in [None, [], 'S', [{}], [{'name': '', 'cast_on': 4}],
                    [{'name': 'S'}], [{'name': 'S', 'cast_on': 0}],
                    [{'name': 'S', 'cast_on': True}],
                    [{'name': 'S', 'cast_on': 'x'}]]:
            data = self.post(bad, 'k*4')
            self.assertFalse(data['ok'], msg=repr(bad))
            self.assertEqual(data['error']['type'], 'sizes')
            self.assertEqual(data['sizes'], [])

    def test_string_cast_on_accepted(self):
        data = self.post([{'name': 'S', 'cast_on': '4'}], 'k*4')
        self.assertTrue(data['ok'])
        self.assertEqual(data['sizes'][0]['final'], 4)

    def test_no_yarn_means_backward_compatible_response(self):
        data = self.post(self.SIZES, 'k*{12|16|20}')
        self.assertTrue(data['ok'])
        s = data['sizes'][0]
        self.assertNotIn('yarn', s)
        self.assertNotIn('weight', s['rows'][0])

    def test_yarn_cumulative_weights_per_row(self):
        pattern = '(k2tog yo)*{6|8|10}\np*{12|16|20}'
        data = self.post(self.SIZES, pattern, self.YARN)
        self.assertTrue(data['ok'])
        s, m, l = data['sizes']
        # S 第 1 行：6 个 k2tog(2g) + 6 个 yo(0.3g) = 13.8g
        self.assertAlmostEqual(s['rows'][0]['weight'], 13.8)
        # 第 2 行再加 12 个 p(1g)，累计 25.8g
        self.assertAlmostEqual(s['rows'][1]['weight'], 25.8)
        self.assertAlmostEqual(m['rows'][1]['weight'], 16 + 2.4 + 16)
        self.assertAlmostEqual(l['rows'][1]['weight'], 20 + 3.0 + 20)

    def test_yarn_totals_waste_and_balls(self):
        # 仅一行 k*10：原始 10g，含 10% 损耗为 11g，50g/团 -> 1 团
        data = self.post([{'name': 'S', 'cast_on': 10}], 'k*10', self.YARN)
        yarn = data['sizes'][0]['yarn']
        self.assertAlmostEqual(yarn['raw_total'], 10.0)
        self.assertAlmostEqual(yarn['with_waste'], 11.0)
        self.assertEqual(yarn['balls'], 1)
        self.assertFalse(yarn['partial'])

    def test_yarn_balls_round_up(self):
        # 50g 原始 + 10% = 55g，50g/团 -> 2 团
        yarn_cfg = dict(self.YARN)
        data = self.post([{'name': 'S', 'cast_on': 50}], 'k*50', yarn_cfg)
        self.assertEqual(data['sizes'][0]['yarn']['balls'], 2)

    def test_yarn_balls_exact_multiple_not_extra_ball(self):
        # 0% 损耗、500g、50g/团正好 10 团，浮点误差不应多算一团
        yarn_cfg = {'weights': {'k': 1, 'p': 1, 'yo': 0, 'k2tog': 0},
                    'waste_percent': 0, 'ball_grams': 50}
        data = self.post([{'name': 'S', 'cast_on': 500}], 'k*500', yarn_cfg)
        self.assertEqual(data['sizes'][0]['yarn']['balls'], 10)

    def test_yarn_zero_weight_zero_balls(self):
        yarn_cfg = {'weights': {'k': 0, 'p': 0, 'yo': 0, 'k2tog': 0},
                    'waste_percent': 10, 'ball_grams': 50}
        data = self.post([{'name': 'S', 'cast_on': 4}], 'k*4', yarn_cfg)
        yarn = data['sizes'][0]['yarn']
        self.assertEqual(yarn['raw_total'], 0)
        self.assertEqual(yarn['with_waste'], 0)
        self.assertEqual(yarn['balls'], 0)

    def test_yarn_nested_and_size_repeats_counted_per_size(self):
        # ((k yo)*2 k2tog)：单次消耗 4、产出 5；*{3|4} 后消耗 12/16、产出 15/20
        # S(起12) -> k 6、yo 6、k2tog 3 = 6 + 1.8 + 6 = 13.8
        # L(起16) -> k 8、yo 8、k2tog 4 = 8 + 2.4 + 8 = 18.4
        sizes = [{'name': 'S', 'cast_on': 12}, {'name': 'L', 'cast_on': 16}]
        data = self.post(sizes, '((k yo)*2 k2tog)*{3|4}', self.YARN)
        self.assertTrue(data['ok'])
        self.assertAlmostEqual(data['sizes'][0]['yarn']['raw_total'], 13.8)
        self.assertAlmostEqual(data['sizes'][1]['yarn']['raw_total'], 18.4)

    def test_yarn_partial_when_count_error_stops_size(self):
        # M 在第 2 行针数错误停止，其用量为部分估算；S/L 走完全部行
        sizes = [{'name': 'S', 'cast_on': 4},
                 {'name': 'M', 'cast_on': 6},
                 {'name': 'L', 'cast_on': 8}]
        pattern = 'k*{4|6|8}\n(k yo)*{4|5|8}\nk*{8|1|16}'
        data = self.post(sizes, pattern, self.YARN)
        s, m, l = data['sizes']
        self.assertFalse(s['yarn']['partial'])
        self.assertTrue(m['yarn']['partial'])
        self.assertFalse(l['yarn']['partial'])
        # M 只累计第 1 行：6 个 k
        self.assertAlmostEqual(m['yarn']['raw_total'], 6.0)
        self.assertEqual(len(m['rows']), 1)

    def test_yarn_partial_for_all_when_parse_error(self):
        data = self.post(self.SIZES, 'k*{12|16|20}\nssk\nk*1', self.YARN)
        for s in data['sizes']:
            self.assertTrue(s['yarn']['partial'])

    def test_yarn_string_numbers_and_percent_suffix_accepted(self):
        yarn = {'weights': {'k': '1', 'p': '1', 'yo': '0.3', 'k2tog': '2'},
                'waste_percent': '10%', 'ball_grams': '50'}
        data = self.post([{'name': 'S', 'cast_on': 10}], 'k*10', yarn)
        self.assertTrue(data['ok'])
        self.assertAlmostEqual(data['sizes'][0]['yarn']['with_waste'], 11.0)

    def test_bad_yarn_params(self):
        bad_yarns = [
            'x',
            {'weights': 'x', 'ball_grams': 50},
            {'weights': {'k': -1}, 'ball_grams': 50},
            {'weights': {'k': 'abc'}, 'ball_grams': 50},
            {'weights': {}, 'waste_percent': -5, 'ball_grams': 50},
            {'weights': {}, 'waste_percent': 'x', 'ball_grams': 50},
            {'weights': {}, 'ball_grams': 0},
            {'weights': {}, 'ball_grams': -2},
            {'weights': {}, 'ball_grams': 'x'},
            {'weights': {}, 'ball_grams': True},
            {'weights': {'k': True}, 'ball_grams': 50},
            {'weights': {}, 'waste_percent': True, 'ball_grams': 50},
        ]
        for yarn in bad_yarns:
            data = self.post([{'name': 'S', 'cast_on': 4}], 'k*4', yarn)
            self.assertFalse(data['ok'], msg=repr(yarn))
            self.assertEqual(data['error']['type'], 'yarn', msg=repr(yarn))
            self.assertEqual(data['sizes'], [])

    def test_yarn_missing_weights_default_to_zero(self):
        yarn = {'ball_grams': 50}
        data = self.post([{'name': 'S', 'cast_on': 4}], 'k*4', yarn)
        self.assertTrue(data['ok'])
        self.assertEqual(data['sizes'][0]['yarn']['raw_total'], 0)


if __name__ == '__main__':
    unittest.main()
