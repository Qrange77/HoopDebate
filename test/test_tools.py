"""Offline contract tests for the independent NBA tools."""
import copy
import inspect
import json
import unittest
from unittest.mock import patch

import requests
import tools


def sample():
    team = {"id": "1", "displayName": "Alpha Aces", "abbreviation": "AAA"}
    other = {"id": "2", "displayName": "Beta Bears", "abbreviation": "BBB"}
    athlete = {"id": "10", "displayName": "Alex Adams", "jersey": "3",
               "position": {"displayName": "Guard"}, "headshot": {"href": "https://example.com/p.png"}}
    competition = {"id": "100", "date": "2026-01-15T23:00Z", "status": {
        "period": 5, "displayClock": "0:00", "type": {"state": "post", "completed": True,
        "description": "Final/OT"}}, "competitors": [
            {"team": team, "score": "110", "homeAway": "home", "winner": True,
             "linescores": [{"displayValue": str(n)} for n in (25, 25, 25, 25, 10)]},
            {"team": other, "score": "105", "homeAway": "away", "winner": False,
             "linescores": [{"displayValue": str(n)} for n in (20, 30, 20, 30, 5)]}]}
    return {"header": {"id": "100", "competitions": [competition]}, "boxscore": {
        "teams": [{"team": team, "statistics": [{"name": "totalRebounds", "label": "Rebounds",
                                                  "abbreviation": "REB", "displayValue": "40"}]}],
        "players": [{"team": team, "statistics": [{"labels": ["PTS", "REB"],
            "keys": ["points", "rebounds"], "athletes": [{"athlete": athlete,
            "stats": ["30", "8"], "starter": True, "didNotPlay": False,
            "reason": "COACH'S DECISION", "ejected": False}]}]}]},
        "leaders": [{"team": team, "leaders": [{"name": "points", "leaders": [
            {"athlete": athlete, "displayValue": "30"}]}]}],
        "plays": [{"id": "p1", "period": {"number": 1}, "clock": {"displayValue": "10:00"},
            "type": {"text": "Jump Shot"}, "text": "Alex Adams makes a jump shot",
            "shootingPlay": True, "scoringPlay": True, "pointsAttempted": 2,
            "participants": [{"athlete": {"id": "10"}}], "coordinate": {"x": 1, "y": 2}},
            {"id": "p2", "period": {"number": 2}, "type": {"text": "Jump Shot"},
             "shootingPlay": True, "participants": [{"athlete": {"id": "20"}},
                                                       {"athlete": {"id": "10"}}]}],
        "gameInfo": {"venue": {"fullName": "Test Arena", "address": {"city": "Test City"}},
                     "officials": [{"displayName": "Referee A", "position": {"displayName": "Referee"}}]},
        "injuries": [{"team": team, "injuries": [{"athlete": athlete, "status": "Out", "date": "2026-09-28"}]}],
        "article": {"headline": "Aces win", "description": "A close game.", "published": "2026-01-16",
                    "links": {"web": {"href": "https://example.com/recap"}}},
        "videos": [{"headline": "Highlights", "links": {"web": {"href": "https://example.com/video"}}}],
        "standings": {"header": "2026-27 Standings", "groups": [{"name": "East",
            "standings": {"entries": [{"id": "1", "team": "Alpha", "stats": [
                {"name": "wins", "displayValue": "3"}]}]}}]}}


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.data = sample()
        self.mock = patch.object(tools, "_fetch", side_effect=self.fetch).start()
        self.addCleanup(patch.stopall)

    def fetch(self, path, **params):
        if path == "summary":
            return self.data
        return {"events": [{"id": "100", "name": "Bears at Aces", "date": "2026-01-15T23:00Z",
                            "competitions": self.data["header"]["competitions"],
                            "status": {"period": 5, "displayClock": "0:00"}}]}

    def call(self, name, **kwargs):
        return json.loads(tools.run_tool(name, {"event_id": "100", **kwargs}))

    def test_all_tool_contracts_and_required_names(self):
        expected = {"game_info": {"start_time", "teams"}, "game_status": {"status", "state", "completed", "period", "clock"},
                    "game_score": {"scores"}, "team_game_info": {"team", "home_away", "winner"},
                    "team_game_stats": {"team", "stats"}, "team_game_leader": {"team", "category", "leaders"},
                    "player_info": {"player", "id", "position", "jersey", "headshot"},
                    "player_game_status": {"player", "starter", "did_not_play", "ejected", "reason"},
                    "player_game_stats": {"player", "stats"}, "game_plays": {"plays", "total", "next_offset"},
                    "player_shots": {"player", "shots", "total", "next_offset"},
                    "game_venue": {"venue", "address"}, "game_officials": {"officials"},
                    "team_injuries": {"team", "injuries", "note"},
                    "game_recap": {"headline", "summary", "published", "url"}, "game_videos": {"videos"},
                    "team_standings": {"team", "season_label", "standings", "note"},
                    "player_advanced_stats": {"player", "team", "headshot", "metrics", "provisional", "note"},
                    "team_advanced_stats": {"team", "metrics", "provisional", "note"}}
        self.assertEqual(set(tools.TOOL_MAP), set(expected) | {"find_games", "game_players", "display_panel"})
        for schema in tools.TOOLS:
            f = schema["function"]
            self.assertEqual(set(f["parameters"]["properties"]), set(inspect.signature(tools.TOOL_MAP[f['name']]).parameters))
            if f['name'].startswith('team_'):
                self.assertIn('team_name', f['parameters']['required'])
            if f['name'].startswith('player_'):
                self.assertIn('player_name', f['parameters']['required'])
        for name, keys in expected.items():
            with self.subTest(name=name):
                args = {}
                if name.startswith("team_"): args["team_name"] = "AAA"
                if name.startswith("player_"): args["player_name"] = "Alex Adams"
                if name == "team_game_leader": args["category"] = "points"
                self.assertEqual(set(self.call(name, **args)), keys)
        games = json.loads(tools.find_games(date="2026-01-15", team_name="AAA"))
        self.assertEqual(games['games'][0]['event_id'], '100')

    def test_scores_full_quarter_overtime_and_missing(self):
        for quarter, value in [(0, '110'), (1, '25'), (5, '10')]:
            self.assertEqual(self.call('game_score', quarter=quarter)['scores'][0]['score'], value)
        self.assertIn('message', self.call('game_score', quarter=6))
        for q in (-1, '1', True): self.assertIn('error', self.call('game_score', quarter=q))
        self.data['header']['competitions'][0]['status']['period'] = 1
        self.assertIn('message', self.call('game_score', quarter=2))
        self.data['header']['competitions'][0]['status']['type']['state'] = 'pre' 
        self.assertIn('message', self.call('game_score'))

    def test_player_and_team_stat_filtering(self):
        self.assertEqual(self.call('player_game_stats', player_name='Alex Adams', stat='PTS')['stats'], {'points': '30'})
        self.assertEqual(self.call('team_game_stats', team_name='AAA', stat='rebounds')['stats'], {'totalRebounds': '40'})
        self.assertIn('available_stats', self.call('player_game_stats', player_name='Alex Adams', stat='unknown'))
        self.assertIsNone(self.call('player_game_status', player_name='Alex Adams')['reason'])
        self.data['boxscore']['players'][0]['statistics'][0]['athletes'][0]['didNotPlay'] = True
        self.assertIn('message', self.call('player_game_stats', player_name='Alex Adams'))

    def test_shots_exclude_assists_and_plays_paginate(self):
        shots = self.call('player_shots', player_name='Alex Adams')
        self.assertEqual(shots['total'], 1)
        self.assertTrue(shots['shots'][0]['made'])
        plays = self.call('game_plays', limit=1)
        self.assertEqual(plays['next_offset'], 1)
        self.assertEqual(self.call('game_plays', offset=1)['plays'][0]['id'], 'p2')
        self.assertEqual(self.call('game_plays', period=2, event_type='jump')['total'], 1)

    def test_ambiguity_invalid_names_dates_and_arguments(self):
        rows = self.data['boxscore']['players'][0]['statistics'][0]['athletes']
        second = copy.deepcopy(rows[0]); second['athlete']['displayName'] = 'Alex Archer'; rows.append(second)
        self.assertEqual(len(self.call('player_info', player_name='Alex')['candidates']), 2)
        self.assertIn('message', self.call('player_info', player_name='Nobody'))
        self.assertIn('message', self.call('game_info', team_name='Unknown'))
        self.assertIn('message', self.call('game_info', date='2026-01-16'))
        self.assertIn('error', self.call('game_info', date='invalid'))
        self.assertIn('error', self.call('team_game_stats'))
        self.assertIn('error', self.call('player_info', player_name=' '))
        self.assertIn('error', json.loads(tools.run_tool('unknown', {})))
        self.assertIn('error', json.loads(tools.run_tool('game_score', [])))

    def test_missing_data_and_network_errors(self):
        for key, tool in [('videos', 'game_videos'), ('article', 'game_recap'), ('plays', 'game_plays')]:
            self.data[key] = []
            self.assertIn('message', self.call(tool))
        self.mock.side_effect = requests.RequestException('offline')
        self.assertIn('error', self.call('game_score'))

    def test_game_status_scoreboard_fallback(self):
        status = self.data['header']['competitions'][0]['status']
        del status['period']; del status['displayClock']
        result = self.call('game_status')
        self.assertEqual(result['period'], 5)
        self.assertEqual(result['clock'], '0:00')

    def test_multiple_and_no_games(self):
        event = self.fetch('scoreboard')['events'][0]
        for events in ([], [event, event]):
            self.mock.side_effect = None
            self.mock.return_value = {'events': events}
            result = json.loads(tools.game_info(date='2026-01-15'))
            self.assertIn('message', result)
            self.assertEqual(len(result['games']), len(events))


if __name__ == '__main__':
    unittest.main()
