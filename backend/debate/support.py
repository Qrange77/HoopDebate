"""Per-turn, precommitted teammate pairs; outcomes never decide research breadth."""
import copy
from typing import Literal
from pydantic import Field
from backend.data.nba import stable_id
from backend.debate.tools import CompareContext

SupportScope = Literal['none', 'strongest_pair', 'two_pairs']
TeamIntent = Literal['unrelated', 'count_only', 'individual_credit']


class TeammatePair(CompareContext):
    focus: Literal['key_teammates'] = 'key_teammates'
    left_teammate_id: int = Field(gt=0)
    right_teammate_id: int = Field(gt=0)
    teammate_selection_reason: str = Field(min_length=1, max_length=1000,
        description='Apply the same importance criteria on both sides, name alternatives and justify this pair. For two pairs nominate both pairs before either exact lookup.')


def request_key(p):
    return stable_id(dict(phase=p.phase, focus=p.focus, sides=sorted([
        (p.left_player, p.left_season, p.left_team_id or 0, p.left_teammate_id or 0),
        (p.right_player, p.right_season, p.right_team_id or 0, p.right_teammate_id or 0)])))


class SupportResearch:
    def __init__(self, config):
        self.focal_ids = {config.supported_player, config.opponent_player}
        self.required = 0
        self.scope = 'none'
        self.tasks = []
        self.discoveries = []
        self.results = {}
        self.changes = []

    def snapshot(self):
        return copy.deepcopy(dict(support_scope=self.scope, required_pairs=self.required,
                                  tasks=self.tasks, changes=self.changes, next_action=self.next_action()))

    def next_action(self):
        if not self.pending():
            return None
        if not self.discoveries:
            return dict(tool='compare_competitive_context', instruction=
                'Discover rosters NOW: use the locked focal player IDs, chosen season START years, phase and selection_reason. '
                'Set focus=key_teammates and OMIT both teammate IDs. Do not register null teammate IDs in plan_argument. '
                'This lookup discovers the IDs you need; do not replace this task with personal stats.')
        if any(t['status'] == 'pending' and not t['request'] for t in self.tasks):
            return dict(tool='plan_argument', instruction=
                'Register ALL available required teammate_pairs using the discovered candidate IDs, with symmetric selection reasons. '
                'The second pair uses the next important DISTINCT teammate on each side, not the strongest teammate again.')
        task = next(t for t in self.tasks if t['status'] == 'pending')
        return dict(tool='compare_competitive_context', args=task['request'],
                    instruction='Fetch this registered pair. Prior results do not cancel this task.')

    def pending(self):
        return any(t['status'] == 'pending' for t in self.tasks)

    def require_from_review(self, scope):
        """Recover missed obligations without choosing more pairs after a result."""
        required = {'none':0, 'strongest_pair':1, 'two_pairs':2}[scope]
        if required <= self.required:
            return
        if any(t['request'] and t['status'] != 'pending' for t in self.tasks):
            for index in range(len(self.tasks), required):
                self.tasks.append(dict(id=f'pair_{index+1}', status='unavailable', request=None,
                    evidence_id=None, reason='Required breadth was identified only after an exact result. No outcome-dependent second selection is allowed; the broader conclusion must remain limited.'))
            self.required, self.scope = required, scope
        else:
            self.register(scope, [])

    def register(self, scope, pairs, replacement_reason=''):
        required = max(self.required, {'none':0, 'strongest_pair':1, 'two_pairs':2}[scope])
        if required > self.required and any(t['request'] and t['status'] != 'pending' for t in self.tasks):
            raise ValueError('Commit two-pair breadth before the first exact result, not after seeing its direction.')
        tasks = copy.deepcopy(self.tasks)
        for index in range(len(tasks), required):
            tasks.append(dict(id=f'pair_{index+1}', status='pending', request=None, evidence_id=None, reason=''))
        if pairs:
            active = [t for t in tasks if t['request'] or t['status'] != 'unavailable']
            if len(pairs) != len(active):
                raise ValueError('Register ALL required available pairs together before the first exact comparison.')
            normalized = [self.validate_pair(p) for p in pairs]
            # Compare the same scope, and use two distinct teammates on EACH side.
            samples, selected = [], {player:set() for player in self.focal_ids}
            for p in normalized:
                sample = []
                for side in ('left','right'):
                    player, mate = p[side+'_player'], p[side+'_teammate_id']
                    if mate in selected[player]:
                        raise ValueError('The second pair must use another teammate on each side.')
                    selected[player].add(mate)
                    sample.append((player, p[side+'_season'], p[side+'_team_id']))
                samples.append((p['phase'], sorted(sample)))
            if any(sample != samples[0] for sample in samples):
                raise ValueError('Both pairs must use the same team-season samples and phase.')
            changes = []
            for task, params in zip(active, normalized):
                if task['request'] and request_key(CompareContext(**task['request'])) != request_key(CompareContext(**params)):
                    if task['status'] != 'unavailable' or not replacement_reason.strip():
                        raise ValueError('A registered valid pair cannot be changed because of its result. Only unavailable pairs may be replaced with a recorded reason.')
                    changes.append(dict(task_id=task['id'], previous=copy.deepcopy(task), reason=replacement_reason))
                    task.update(status='pending', evidence_id=None, reason='')
                task['request'] = params
            self.changes.extend(changes)
        self.required, self.tasks = required, tasks
        self.scope = 'two_pairs' if required == 2 else 'strongest_pair' if required else 'none'

    def validate_pair(self, p):
        if {p.left_player, p.right_player} != self.focal_ids:
            raise ValueError('Use both locked focal players.')
        if {p.left_teammate_id, p.right_teammate_id} & self.focal_ids:
            raise ValueError('Exclude both focal players from the teammate selections.')
        params = p.model_dump()
        for side in ('left','right'):
            matches = [d for d in self.discoveries if d['player_id'] == params[side+'_player']
                       and d['season'] == params[side+'_season'] and d['phase'] == p.phase
                       and (params[side+'_team_id'] is None or params[side+'_team_id'] == d['team_id'])]
            if len(matches) != 1 or params[side+'_teammate_id'] not in matches[0]['candidates']:
                raise ValueError('Discover the chosen team-season roster first and choose a verified candidate from it.')
            params[side+'_team_id'] = matches[0]['team_id']
        return params

    def before_query(self, args):
        p = CompareContext.model_validate(args)
        if p.focus != 'key_teammates' or not self.required:
            return p, None
        if p.left_teammate_id is not None:
            params = self.validate_pair(TeammatePair(**p.model_dump()))
            p = CompareContext(**params)
            if any(t['status'] == 'pending' and not t['request'] for t in self.tasks):
                raise ValueError('Update plan_argument with all teammate_pairs before fetching the first selected pair.')
            if not any(t['request'] and request_key(CompareContext(**t['request'])) == request_key(p) for t in self.tasks):
                raise ValueError('This pair is not in the precommitted research plan.')
        return p, self.results.get(request_key(p))

    def observe(self, p, result):
        if not self.required or p.focus != 'key_teammates':
            return
        self.results[request_key(p)] = copy.deepcopy(result)
        if result.get('status') == 'select_teammates':
            sizes = []
            for side in ('left','right'):
                r = result[side]
                candidates = {c['player_id'] for c in r['candidates']} - self.focal_ids
                d = dict(player_id=r['player_id'], season=getattr(p, side+'_season'), phase=p.phase,
                         team_id=r['context']['team_id'], candidates=candidates)
                self.discoveries = [old for old in self.discoveries if (old['player_id'],old['season'],old['phase'],old['team_id']) !=
                                    (d['player_id'],d['season'],d['phase'],d['team_id'])] + [d]
                sizes.append(len(candidates))
            for task in self.tasks[:min(sizes)]:
                if task['request'] is None:
                    task.update(status='pending', reason='')
            for task in self.tasks[min(sizes):]:
                if task['request'] is None:
                    task.update(status='unavailable', reason='Not enough distinct verified teammates on both rosters.')
            return
        if p.left_teammate_id is None:
            for task in self.tasks:
                if task['status'] == 'pending' and task['request'] is None:
                    task.update(status='unavailable', reason='Roster discovery unavailable; no support comparison established.')
            return
        for task in self.tasks:
            if task['request'] and request_key(CompareContext(**task['request'])) == request_key(p):
                if result.get('kind') == 'comparison' and result.get('focus') == 'key_teammates':
                    task.update(status='completed', evidence_id=result['id'], reason='')
                else:
                    task.update(status='unavailable', reason='Selected pair data unavailable; missing data is not weak support.')

    def expire(self):
        for task in self.tasks:
            if task['status'] == 'pending':
                task.update(status='unavailable', reason='Turn research budget exhausted before this comparison completed.')
