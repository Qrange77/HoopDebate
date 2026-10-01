import json
import unittest
from unittest.mock import patch
import tools


class DisplayPanelTests(unittest.TestCase):
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
