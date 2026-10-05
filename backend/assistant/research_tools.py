"""Read-only NBA research tools shared with Debate, without its player lock."""
import copy
import json

from backend.assistant.tools import TOOLS as GAME_TOOLS, run_tool as run_game_tool
from backend.debate.tools import DEBATE_TOOLS, DebateTools

SHARED_NAMES = frozenset({
    'resolve_player', 'query_evidence', 'compare_players', 'find_counterexamples',
    'find_comparative_edges', 'player_awards', 'compare_awards',
    'query_competitive_context', 'compare_competitive_context',
    'query_performance_context',
})
SHARED_TOOLS = copy.deepcopy([t for t in DEBATE_TOOLS if t['function']['name'] in SHARED_NAMES])
for tool in SHARED_TOOLS:
    tool['function']['description'] = tool['function']['description'].replace(
        'the TWO LOCKED players', 'the two supplied players')
TOOLS = GAME_TOOLS + SHARED_TOOLS


class AssistantResearchTools(DebateTools):
    def __init__(self, data=None):
        super().__init__(config=None, data=data)

    def allowed(self, ident):
        # Parameter schemas validate IDs; NBA responses determine availability.
        # Assistant has no configured pair and can change players at any time.
        return None

    def run(self, name, args):
        if name not in SHARED_NAMES:
            return {'error': f"Unknown assistant research tool '{name}'."}
        result = super().run(name, args)
        if name == 'resolve_player':
            for player in result.get('players', []):
                self.names[str(player['id'])] = player['name']
        return result

    def dispatch(self, name, args):
        if name in SHARED_NAMES:
            return json.dumps(self.run(name, args))
        return run_game_tool(name, args)
