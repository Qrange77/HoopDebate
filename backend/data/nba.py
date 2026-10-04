"""NBA historical data, bounded requests and a provenance-preserving SQLite cache."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
import sqlite3
from threading import RLock
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from backend.paths import PROJECT_ROOT

from nba_api.stats.endpoints import commonallplayers, playercareerstats, playerawards, playergamelog, leaguedashteamstats, leaguedashplayerstats, teamplayerdashboard, teamyearbyyearstats
from nba_api.stats.static import players

CACHE_PATH = PROJECT_ROOT / 'data_cache' / 'nba.sqlite3'
CACHE_LOCK = RLock()
ENDPOINTS = {
    'directory': commonallplayers.CommonAllPlayers,
    'career': playercareerstats.PlayerCareerStats,
    'awards': playerawards.PlayerAwards,
    'games': playergamelog.PlayerGameLog,
    'league': leaguedashteamstats.LeagueDashTeamStats,
    'league_players': leaguedashplayerstats.LeagueDashPlayerStats,
    'team_players': teamplayerdashboard.TeamPlayerDashboard,
    'team_history': teamyearbyyearstats.TeamYearByYearStats,
}
ENDPOINT_NAMES = {'directory': 'commonallplayers', 'career': 'playercareerstats', 'awards': 'playerawards',
                  'games': 'playergamelog', 'league': 'leaguedashteamstats', 'league_players':'leaguedashplayerstats', 'team_players':'teamplayerdashboard', 'team_history':'teamyearbyyearstats'}
ALIASES = {'lebron': 'LeBron James', 'king james': 'LeBron James', 'mj': 'Michael Jordan',
           'steph': 'Stephen Curry', 'steph curry': 'Stephen Curry', 'shaq': "Shaquille O'Neal",
           'kobe': 'Kobe Bryant', 'kd': 'Kevin Durant'}
FIRST_RECORDED = {'STL': 1973, 'BLK': 1973, 'OREB': 1973, 'DREB': 1973, 'TOV': 1977,
                  'FG3M': 1979, 'FG3A': 1979}
BASE_METRICS = ('GP', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'MIN', 'FGM', 'FGA', 'FG3M', 'FG3A', 'FTM', 'FTA', 'OREB', 'DREB')


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value)).casefold() if c.isalnum())


def season_label(year):
    return f'{year}-{str(year + 1)[-2:]}'


def current_season():
    now = datetime.now(timezone.utc)
    # July marks the next league year; this deliberately refreshes recent seasons conservatively.
    return now.year if now.month >= 7 else now.year - 1


class DataUnavailable(Exception):
    pass


class NBAData:
    def __init__(self, cache_path=None, budget=60, max_cache_bytes=None):
        self.path = Path(cache_path or CACHE_PATH)
        self.network_budget = max(0.0, float(budget))
        self.network_elapsed = 0.0
        self.memo = {}
        self.failures = {}
        self.max_cache_bytes = max_cache_bytes if max_cache_bytes is not None else int(float(os.getenv('NBA_CACHE_MAX_MB', '100')) * 1024 * 1024)
        if self.max_cache_bytes < 16384:
            raise ValueError('NBA cache limit must be at least 16 KiB.')

    @property
    def remaining_seconds(self):
        return max(0.0, self.network_budget - self.network_elapsed)

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_LOCK:
            db = sqlite3.connect(self.path, timeout=5)
            try:
                with db:
                    db.execute('CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, body TEXT NOT NULL, last_access REAL NOT NULL DEFAULT 0)')
                    if 'last_access' not in {r[1] for r in db.execute('PRAGMA table_info(responses)')}:
                        db.execute('ALTER TABLE responses ADD COLUMN last_access REAL NOT NULL DEFAULT 0')
                    yield db
                self._trim_cache(db)
            finally:
                db.close()

    def _trim_cache(self, db):
        """Evict least recently read queries, then reclaim SQLite's free pages.

        The cap covers this database, not separately saved conversation evidence.
        """
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        if db.execute('PRAGMA page_count').fetchone()[0] * page_size <= self.max_cache_bytes:
            return
        with db:
            while True:
                used = (db.execute('PRAGMA page_count').fetchone()[0] - db.execute('PRAGMA freelist_count').fetchone()[0]) * page_size
                if used <= self.max_cache_bytes:
                    break
                oldest = db.execute('SELECT key FROM responses ORDER BY last_access, key LIMIT 1').fetchone()
                if not oldest:
                    break
                db.execute('DELETE FROM responses WHERE key=?', oldest)
        db.execute('VACUUM')

    def _live(self, kind, params, timeout):
        response = ENDPOINTS[kind](**params, timeout=timeout)
        raw = response.get_dict()
        if not isinstance(raw, dict) or not raw.get('resultSets'):
            raise DataUnavailable('The NBA response did not contain a recognized dataset.')
        return raw, response.get_normalized_dict()

    def fetch(self, kind, **params):
        key = json.dumps([kind, params], sort_keys=True)
        if key in self.memo:
            return self.memo[key]
        if key in self.failures:
            raise DataUnavailable(self.failures[key])
        with self._connection() as db:
            row = db.execute('SELECT body FROM responses WHERE key=?', (key,)).fetchone()
            if row:
                db.execute('UPDATE responses SET last_access=? WHERE key=?', (time.time(), key))
        cached = json.loads(row[0]) if row else None
        year = int(str(params.get('season', current_season()))[:4])
        ttl = 30 * 86400 if kind in ('games', 'league', 'league_players', 'team_players') and year < current_season() - 1 else 86400
        if cached and time.time() - cached['fetched_epoch'] < ttl:
            cached['stale'] = False
            self.memo[key] = cached
            return cached
        remaining = self.remaining_seconds
        try:
            if remaining <= 0:
                raise DataUnavailable('The historical data budget was reached; coverage is partial.')
            started = time.monotonic()
            try:
                raw, normalized = self._live(kind, params, min(12, remaining))
            finally:
                self.network_elapsed += max(0.0, time.monotonic() - started)
            entry = {'raw': raw, 'data': normalized, 'source': 'NBA.com',
                     'source_url': f'https://stats.nba.com/stats/{ENDPOINT_NAMES[kind]}',
                     'parameters': params, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                     'fetched_epoch': time.time(), 'stale': False}
            body = json.dumps(entry)
            # An oversized result is usable this turn, but must not flush the cache.
            if len(body.encode()) + len(key.encode()) + 16384 <= self.max_cache_bytes:
                with self._connection() as db:
                    db.execute('INSERT OR REPLACE INTO responses (key, body, last_access) VALUES (?, ?, ?)', (key, body, time.time()))
            self.memo[key] = entry
            return entry
        except Exception as exc:
            reason = f'NBA {kind} data unavailable ({type(exc).__name__}).'
            if isinstance(exc, DataUnavailable):
                reason = str(exc)
            if cached:
                cached.update(stale=True, refresh_error=reason)
                self.memo[key] = cached
                return cached
            self.failures[key] = reason
            raise DataUnavailable(reason) from exc

    def directory(self):
        try:
            data = self.fetch('directory', is_only_current_season=0, league_id='00', season=season_label(current_season()))
            rows = data['data'].get('CommonAllPlayers', [])
            if not rows:
                raise DataUnavailable('The player directory is empty.')
            return [{'id': int(r['PERSON_ID']), 'name': r['DISPLAY_FIRST_LAST'],
                     'from_year': r.get('FROM_YEAR'), 'to_year': r.get('TO_YEAR'),
                     'active': bool(r.get('ROSTERSTATUS')), 'source': 'NBA.com', 'stale': data['stale']} for r in rows]
        except DataUnavailable:
            # The package ships real NBA identifiers, never fabricated statistics or years.
            return [{'id': p['id'], 'name': p['full_name'], 'from_year': None, 'to_year': None,
                     'active': p['is_active'], 'source': 'nba_api bundled directory; years unavailable',
                     'stale': True} for p in players.get_players()]

    def resolve(self, query):
        query = ALIASES.get(query.strip().casefold(), query)
        key = normalize(query)
        rows = self.directory()
        exact = [p for p in rows if normalize(p['name']) == key or str(p['id']) == query]
        matches = exact or [p for p in rows if key and key in normalize(p['name'])]
        return {'players': matches[:25], 'complete': len(matches) <= 25, 'resolved': len(matches) == 1}


def provenance(entry, player_id=None):
    return {'provider': 'NBA.com', 'url': (f'https://www.nba.com/stats/player/{player_id}/traditional' if player_id else
            entry['source_url']), 'endpoint': entry['source_url'], 'parameters': entry['parameters'],
            'fetched_at': entry['fetched_at'], 'stale': entry['stale'],
            'warning': entry.get('refresh_error')}


def number(value):
    if isinstance(value, bool) or value is None or value == '':
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def season_rows(rows, start=None, end=None):
    groups = {}
    for original in rows:
        r = dict(original)
        try:
            year = int(r['SEASON_ID'][:4])
        except (ValueError, TypeError, KeyError):
            continue
        if r.get('LEAGUE_ID', '00') != '00' or (start is not None and year < start) or (end is not None and year > end):
            continue
        for key, first in FIRST_RECORDED.items():
            if year < first:
                r[key] = None
        groups.setdefault(year, []).append(r)
    result = []
    for year, entries in sorted(groups.items()):
        total = next((r for r in entries if r.get('TEAM_ID') == 0 or r.get('TEAM_ABBREVIATION') in ('TOT', 'TOTAL')), None)
        if total:
            result.append(total)
        else:
            # Deduplicate team rows before combining a traded player's season.
            unique = {r.get('TEAM_ID'): r for r in entries}.values()
            combined = {'SEASON_ID': season_label(year)}
            for metric in BASE_METRICS:
                values = [number(r.get(metric)) for r in unique]
                combined[metric] = sum(values) if all(v is not None for v in values) else None
            result.append(combined)
    return result


def aggregate(rows, basis='per_game'):
    seasons = [r['SEASON_ID'] for r in rows]
    totals, metrics = {}, {}
    for key in BASE_METRICS:
        available = [r for r in rows if number(r.get(key)) is not None]
        values = [number(r.get(key)) for r in available]
        total = sum(values) if values else None
        games_values = [number(r.get('GP')) for r in available]
        games = sum(games_values) if games_values and all(v is not None for v in games_values) else None
        totals[key] = total if len(available) == len(rows) else None
        value = total
        if basis == 'per_game' and key != 'GP':
            value = total / games if total is not None and games else None
        metrics[key] = {'value': value, 'unit': 'games' if key == 'GP' else ('per game' if basis == 'per_game' else 'total'),
                        'games': games, 'covered_seasons': [r['SEASON_ID'] for r in available],
                        'complete': len(available) == len(rows) and bool(rows),
                        'formula': f'{key} / GP' if basis == 'per_game' and key != 'GP' else f'sum({key})',
                        'inputs': {key: total, 'GP': games}}
    for metric, keys, formula in [
        ('FG_PCT', ['FGM', 'FGA'], '100 * FGM / FGA'),
        ('FG3_PCT', ['FG3M', 'FG3A'], '100 * FG3M / FG3A'),
        ('FT_PCT', ['FTM', 'FTA'], '100 * FTM / FTA'),
        ('TS_PCT', ['PTS', 'FGA', 'FTA'], '100 * PTS / (2 * (FGA + 0.44 * FTA))'),
        ('EFG_PCT', ['FGM', 'FG3M', 'FGA'], '100 * (FGM + 0.5 * FG3M) / FGA')]:
        inputs = {k: totals[k] for k in keys}
        valid = bool(rows) and all(v is not None for v in inputs.values())
        value = None
        if valid:
            denominator = 2 * (totals['FGA'] + .44 * totals['FTA']) if metric == 'TS_PCT' else totals[keys[-1]]
            numerator = totals['PTS'] if metric == 'TS_PCT' else (totals['FGM'] + .5 * totals['FG3M'] if metric == 'EFG_PCT' else totals[keys[0]])
            if denominator:
                value = 100 * numerator / denominator
        metrics[metric] = {'value': value, 'unit': '%', 'games': totals['GP'], 'covered_seasons': seasons if valid else [],
                           'complete': valid and value is not None, 'formula': formula, 'inputs': inputs}
    return metrics


def stable_id(value):
    return 'ev_' + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
