import json
import unittest
from unittest.mock import patch
from backend.assistant import tools
class DisplayPanelTests(unittest.TestCase):
    def test_raw_box_score_from_followup(self):
        stats = dict(zip(
            ('FGA', 'TOV', 'FTA', 'FTM', 'FGM', 'MIN', 'PTS', 'AST', 'BLK', 'STL', 'REB'),
            ('16', '2', '4', '4', '9', '37', '26', '4', '0', '4', '13')))
        panel = {'player': 'Paolo Banchero', 'metrics': stats}
        with patch.object(tools, '_fetch') as fetch:
            result = json.loads(tools.run_tool('display_panel', {'panel': panel}))
            fetch.assert_not_called()
        for key, value in stats.items():
            self.assertEqual(result['metrics'][key]['value'], float(value))
            self.assertEqual(result['metrics'][key]['label'], key)
        self.assertEqual(panel['metrics'], stats)
        self.assertIsInstance(panel['metrics']['PTS'], str)

    def test_scalar_missing_and_invalid_values(self):
        result = json.loads(tools.display_panel({'player': 'A', 'metrics': {
            'PTS': 26, 'BLK': 0, 'REB': None}}))
        self.assertEqual(result['metrics']['PTS']['value'], 26)
        self.assertEqual(result['metrics']['BLK']['value'], 0)
        self.assertIsNone(result['metrics']['REB']['value'])
        for value in ('NaN', 'Infinity', [], True):
            result = json.loads(tools.display_panel({'player': 'A', 'metrics': {'FG': value}}))
            self.assertIn('Display panel validation failed', result['error'])

    def test_profile_then_box_score_panel(self):
        profile = {'player': 'Paolo Banchero', 'position': 'Forward', 'jersey': '5'}
        first = json.loads(tools.display_panel(profile))
        self.assertEqual(first['metrics'], {})
        second = json.loads(tools.display_panel({**profile, 'metrics': {
            'PTS': '26', 'FG': '9-16', 'MIN': '37:12', 'FG%': '56.3%'}}))
        self.assertEqual(second['position'], 'Forward')
        self.assertEqual(second['metrics']['PTS']['value'], 26)
        for key, value in [('FG', '9-16'), ('MIN', '37:12'), ('FG%', '56.3%')]:
            self.assertEqual(second['metrics'][key]['display_value'], value)
            self.assertIsNone(second['metrics'][key]['value'])
        self.assertEqual(json.loads(tools.display_panel(profile))['metrics'], {})

    def test_display_preserves_supplied_data_without_fetching(self):
        panel = {'player': 'Example', 'team': 'Team', 'provisional': True,
                 'metrics': {'efg_pct': {'label': 'eFG%', 'value': None, 'unit': '%',
                             'formula': '100 * (FGM + 0.5 * 3PM) / FGA',
                             'inputs': {'FGA': 0}, 'unavailable_reason': 'Undefined'}}}
        with patch.object(tools, '_fetch') as fetch:
            result = json.loads(tools.display_panel(panel))
            fetch.assert_not_called()
        self.assertTrue(result['provisional'])
        self.assertIsNone(result['metrics']['efg_pct']['value'])
        self.assertEqual(result['metrics']['efg_pct']['inputs'], {'FGA': 0})

    def test_invalid_panels_and_structured_schema(self):
        for panel in ({}, {'player': 'A', 'metrics': []}, {'player': 'A', 'unknown': 1}):
            self.assertIn('error', json.loads(tools.display_panel(panel)))
        schema = next(t for t in tools.TOOLS if t['function']['name'] == 'display_panel')
        param = schema['function']['parameters']['properties']['panel']
        self.assertEqual(param['type'], 'object')
        self.assertNotIn('$ref', json.dumps(param))
