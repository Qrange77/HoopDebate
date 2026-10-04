"""Exact NBA award categories; counts are distinct sourced award seasons.

Missing categories are unknown, not zero. Championships are team achievements;
MVP, Finals MVP and All-Star MVP are deliberately separate descriptions.
"""
import re

# code: (display label, exact NBA DESCRIPTION, optional team number)
AWARDS = {
    'CHAMPIONSHIPS': ('NBA championships', 'NBA Champion', None),
    'MVP': ('Regular-season MVP', 'NBA Most Valuable Player', None),
    'FINALS_MVP': ('Finals MVP', 'NBA Finals Most Valuable Player', None),
    'DPOY': ('Defensive Player of the Year', 'NBA Defensive Player of the Year', None),
    'ALL_NBA': ('All-NBA selections', 'All-NBA', None),
    'ALL_NBA_FIRST': ('All-NBA First Team', 'All-NBA', '1'),
    'ALL_NBA_SECOND': ('All-NBA Second Team', 'All-NBA', '2'),
    'ALL_NBA_THIRD': ('All-NBA Third Team', 'All-NBA', '3'),
    'ALL_DEFENSIVE': ('All-Defensive selections', 'All-Defensive Team', None),
    'ALL_DEFENSIVE_FIRST': ('All-Defensive First Team', 'All-Defensive Team', '1'),
    'ALL_DEFENSIVE_SECOND': ('All-Defensive Second Team', 'All-Defensive Team', '2'),
    'ALL_STAR': ('All-Star selections', 'NBA All-Star', None),
    'ROY': ('Rookie of the Year', 'NBA Rookie of the Year', None),
    'SIXTH_MAN': ('Sixth Man of the Year', 'NBA Sixth Man of the Year', None),
    'MIP': ('Most Improved Player', 'NBA Most Improved Player', None),
}
DEFAULT_AWARDS = ['CHAMPIONSHIPS', 'MVP', 'FINALS_MVP', 'DPOY', 'ALL_NBA', 'ALL_DEFENSIVE', 'ALL_STAR']


def normalize_awards(rows, player_id, start=None, end=None):
    values = {}
    for code, (label, description, level) in AWARDS.items():
        seasons = set()
        for row in rows:
            if row.get('PERSON_ID') is not None and str(row['PERSON_ID']) != str(player_id):
                continue
            season = str(row.get('SEASON') or '')
            match = re.fullmatch(r'(\d{4})-(\d{2})', season)
            if not match or row.get('DESCRIPTION') != description:
                continue
            if level is not None and str(row.get('ALL_NBA_TEAM_NUMBER')) != level:
                continue
            year = int(match[1])
            if start is not None and not start <= year <= end:
                continue
            seasons.add(season)
        values[code] = {'label': label, 'value': len(seasons) if seasons else None,
                        'unit': 'titles' if code == 'CHAMPIONSHIPS' else 'selections' if code.startswith(('ALL_NBA', 'ALL_DEFENSIVE', 'ALL_STAR')) else 'awards',
                        'games': None, 'covered_seasons': sorted(seasons), 'complete': bool(seasons),
                        'formula': 'count(distinct sourced award seasons)', 'inputs': {},
                        'unavailable_reason': None if seasons else 'No matching source records in this range; not proof of zero awards.'}
    return values
