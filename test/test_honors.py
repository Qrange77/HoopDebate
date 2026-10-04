"""Honors category isolation, season deduplication, audited comparisons and preload."""
from test.test_debate import review_response
import json
import unittest
from unittest.mock import patch
from backend.data.honors import normalize_awards
from backend.data.nba import DataUnavailable
from backend.debate.tools import DebateTools, DebateConfig, PlayerAwards, CompareAwards, Scope, Claim, Audit, DEBATE_TOOLS
from backend.debate.agent import run_debate, evidence_catalog
from test.test_debate import FakeData, response, submit


def award(description, season='1995-96', level=None, **extra):
    return {'PERSON_ID':1,'DESCRIPTION':description,'SEASON':season,'ALL_NBA_TEAM_NUMBER':level,**extra}

class HonorsTests(unittest.TestCase):
    def setUp(self):
        self.data=FakeData()
        self.config=DebateConfig(supported_player=1,opponent_player=2)
        self.tools=DebateTools(self.config,data=self.data,names={'1':'Alpha','2':'Beta'})

    def test_mvp_categories_do_not_mix_and_semantic_duplicates_collapse(self):
        rows=[award('NBA Most Valuable Player'),award('NBA Most Valuable Player',SUBTYPE1='other sponsor'),
              award('NBA Finals Most Valuable Player'),award('NBA All-Star Most Valuable Player'),
              award('NBA Sporting News Most Valuable Player of the Year'),award('NBA Champion'),
              award('NBA Champion',TEAM='different formatting'),award('NBA Most Valuable Player',PERSON_ID=2)]
        r=normalize_awards(rows,1)
        self.assertEqual(r['MVP']['value'],1)
        self.assertEqual(r['FINALS_MVP']['value'],1)
        self.assertEqual(r['CHAMPIONSHIPS']['value'],1)
        self.assertIsNone(r['DPOY']['value'])

    def test_all_nba_first_team_separate_from_all_selections(self):
        r=normalize_awards([award('All-NBA',level='1'),award('All-NBA','1996-97','2'),
                            award('All-NBA','1997-98','3'),award('All-Defensive Team',level='1')],1)
        self.assertEqual(r['ALL_NBA']['value'],3)
        self.assertEqual(r['ALL_NBA_FIRST']['value'],1)
        self.assertEqual(r['ALL_NBA_SECOND']['covered_seasons'],['1996-97'])
        self.assertEqual(r['ALL_DEFENSIVE_FIRST']['value'],1)

    def test_awards_filter_and_scope_not_boxscore_phase(self):
        self.data.awards=[award('NBA Champion'),award('NBA Champion','1996-97')]
        r=self.tools.player_awards(PlayerAwards(player_id=1,scope=Scope(start=1995,end=1995,phase='Playoffs'),award='CHAMPIONSHIPS'))
        self.assertEqual(r['values']['CHAMPIONSHIPS']['value'],1)
        self.assertEqual(r['scope']['basis'],'totals')
        self.assertEqual(r['scope']['phase'],'Regular Season')
        card=self.tools.audit_argument(Audit(claims=[Claim(evidence_id=r['id'],metric='CHAMPIONSHIPS',player_id=1,
                                                         scope=Scope.model_validate(r['scope']),value=1)]))['cards'][0]
        self.assertTrue(card['award'])
        self.assertIn('honors by award season',card['scope'])
        self.assertNotIn('per game',card['scope'])
        self.assertEqual(card['covered_seasons'],['1995-96'])

    def test_missing_awards_before_existence_and_empty_response_not_zero(self):
        self.data.awards=[award('NBA Champion','1960-61')]
        r=self.tools.player_awards(PlayerAwards(player_id=1))
        self.assertIsNone(r['values']['FINALS_MVP']['value'])
        self.assertIsNone(r['values']['DPOY']['value'])
        self.data.awards=[]
        with self.assertRaises(DataUnavailable):self.tools.player_awards(PlayerAwards(player_id=1))

    def test_comparison_cards_and_summary_preserve_winning_seasons(self):
        # FakeData's default honors omit PERSON_ID, simulating each queried player's own dataset.
        r=self.tools.compare_awards(CompareAwards(left_player=1,right_player=2))
        self.assertEqual(r['metrics']['MVP']['left_value'],1)
        self.assertEqual(r['metrics']['MVP']['relation'],'equal')
        self.assertFalse(r['metrics']['DPOY']['comparable'])
        claim=Claim(evidence_id=r['id'],metric='MVP',player_id=1,scope=Scope(basis='totals'),value=1,other_value=1,relation='equal')
        audit=self.tools.audit_argument(Audit(claims=[claim]))
        self.assertTrue(audit['valid'])
        self.assertEqual(audit['cards'][0]['other_covered_seasons'],['1995-96'])
        self.assertEqual(audit['cards'][0]['label'],'Regular-season MVP')
        summary=self.tools.honors_summary()
        self.assertEqual(len(summary['rows']),7)
        self.assertIsNone(summary['rows'][0]['left_value'])
        claim.metric='DPOY';claim.value=0;claim.other_value=0
        self.assertFalse(self.tools.audit_argument(Audit(claims=[claim]))['valid'])

    def test_invalid_player_category_and_comparison_window_rejected(self):
        self.assertIn('error',self.tools.run('player_awards',{'player_id':999}))
        self.assertIn('error',self.tools.run('player_awards',{'player_id':1,'award':'fake_trophy'}))
        with self.assertRaises(ValueError):CompareAwards(left_player=1,right_player=1)
        with self.assertRaises(ValueError):CompareAwards(left_player=1,right_player=2,right_scope=Scope(start=1995,end=1995))
        names={t['function']['name'] for t in DEBATE_TOOLS}
        self.assertTrue({'player_awards','compare_awards'}<=names)


    def followup_evidence(self):
        # Minimal reproduction: total selections favor Beta, First Team favors Alpha.
        self.data.awards = [award('All-NBA', f'{2000+i}-{(2001+i)%100:02}',
                                  level='1' if i < first else '3', PERSON_ID=player)
                            for player, total, first in [(1,8,6),(2,11,4)] for i in range(total)]
        return self.tools.compare_awards(CompareAwards(left_player=1,right_player=2))

    def test_saved_old_honors_expand_comparison_without_fetch(self):
        old = self.followup_evidence()
        self.assertNotIn('ALL_NBA_FIRST',old['metrics'])
        with patch.object(self.data,'fetch',side_effect=AssertionError('No fetch needed')):
            restored = self.tools.saved_honors_comparison()
        self.assertEqual(restored['metrics']['ALL_NBA_FIRST']['left_value'],6)
        self.assertEqual(restored['metrics']['ALL_NBA_FIRST']['right_value'],4)
        self.assertEqual(old['left']['id'],restored['left']['id'])

    def test_child_reference_repair_requires_exact_facts_and_real_parent(self):
        old = self.followup_evidence()
        expanded = self.tools.saved_honors_comparison()
        claim = Claim(evidence_id=old['left']['id'],metric='ALL_NBA_FIRST',player_id=1,
                      scope=Scope(basis='totals'),value=6,other_value=4,relation='higher')
        fixed, changes = self.tools.repair_comparison_references([claim])
        self.assertEqual(fixed[0].evidence_id,expanded['id'])
        self.assertEqual(len(changes),1)
        self.assertTrue(self.tools.audit_argument(Audit(claims=fixed))['valid'])
        for change in ({'value':7},{'other_value':5},{'relation':'lower'}, {'player_id':2},
                       {'scope':Scope(basis='totals',phase='Playoffs')}, {'evidence_id':'invented'},
                       {'metric':'DPOY','value':0,'other_value':0,'relation':'equal'}):
            with self.subTest(change=change):
                wrong = claim.model_copy(update=change)
                repaired, changes = self.tools.repair_comparison_references([wrong])
                self.assertEqual(changes,[])
                self.assertFalse(self.tools.audit_argument(Audit(claims=repaired))['valid'])
        reverse = claim.model_copy(update={'evidence_id':old['right']['id'],'player_id':2,
                                           'value':4,'other_value':6,'relation':'lower'})
        fixed, changes = self.tools.repair_comparison_references([reverse])
        self.assertTrue(self.tools.audit_argument(Audit(claims=fixed))['valid'])

    def test_three_followups_reuse_honors_and_preserve_sides_and_concessions(self):
        old = self.followup_evidence()
        self.tools.saved_honors_comparison()  # Previously obtained comparison snapshot.
        claims = [{'evidence_id':old['left']['id'],'metric':metric,'player_id':1,
                   'scope':Scope(basis='totals').model_dump(),'value':value,
                   'other_value':other,'relation':direction}
                  for metric,value,other,direction in [('ALL_NBA',8,11,'lower'),('ALL_NBA_FIRST',6,4,'higher')]]
        draft = json.dumps({'response':'Beta has 11 All-NBA selections to Alpha’s 8. But Alpha has 6 All-NBA First Team selections to Beta’s 4.',
                            'claims':claims,'conceded_claim_indexes':[0],'topic':'All-NBA'})
        for mode, defender in [('rebuttal',1),('debate',2)]:
            with self.subTest(mode=mode):
                messages = [{'role':'system','content':''}]
                evidence, state = self.tools.evidence, None
                with patch.object(self.data,'fetch',side_effect=AssertionError('Reuse saved records')):
                    for turn, question in enumerate(['But Beta has 11 nba-team','What about first teams?','Do you still concede total selections?']):
                        messages.append({'role':'user','content':question})
                        outputs = ([submit(draft)] * 3 if mode == 'debate' and turn else
                                   [submit(draft),review_response({'status': 'pass', 'issues': []})])
                        with patch('backend.debate.agent.complete',side_effect=outputs):
                            text,logs,result,evidence,state = run_debate(messages,mode,self.config,{'1':'Alpha','2':'Beta'},evidence=evidence,state=state,data=self.data)
                        self.assertEqual(result['review_status'], 'limited' if mode == 'debate' and turn else 'reviewed')
                        self.assertEqual(len(result['cards']), 0 if mode == 'debate' and turn else 2)
                        self.assertIn(f'You defend NBA player {defender}',messages[0]['content'])
                        self.assertEqual(len(state['concessions']),1)
                        self.assertIn('resolve_evidence_references',[log['name'] for log in logs])

    def test_failed_validation_with_saved_data_does_not_claim_data_outage(self):
        self.followup_evidence()
        with patch('backend.debate.agent.complete',return_value=response('malformed output')):
            result=run_debate([{'role':'system','content':''}], 'rebuttal',self.config,
                              {'1':'Alpha','2':'Beta'},evidence=self.tools.evidence,data=self.data)
        self.assertEqual(result[2]['review_status'],'limited')
        self.assertIn('review limits',result[0])
        self.assertNotIn('when NBA data is available',result[0])


    def test_two_first_team_claims_and_generic_championship_prose_pass_harness(self):
        record=self.followup_evidence()
        claims=[dict(evidence_id=record[side]['id'],metric='ALL_NBA_FIRST',player_id=player,
                     scope=Scope(basis='totals').model_dump(),value=value)
                for side,player,value in [('left',1,6),('right',2,4)]]
        draft=json.dumps({'response':'Alpha has 6 All-NBA First Team selections to Beta’s 4. That championship hardware is a team résumé, not proof of individual isolation.',
                          'claims':claims})
        with patch('backend.debate.agent.complete',side_effect=[submit(draft),review_response({'status': 'pass', 'issues': []})]) as model:
            result=run_debate([{'role':'system','content':''},{'role':'user','content':'But Beta has more championships'}],
                              'rebuttal',self.config,{'1':'Alpha','2':'Beta'},evidence=self.tools.evidence,data=self.data)
        self.assertEqual(result[2]['review_status'],'reviewed')
        self.assertEqual(len(result[2]['cards']),2)
        self.assertEqual(model.call_count,2)  # Writer + reviewer; no unnecessary correction.

    def test_honor_fact_ids_are_available_without_team_context(self):
        record=self.tools.compare_awards(CompareAwards(left_player=1,right_player=2))
        catalog=evidence_catalog(self.tools,[record])
        fact=catalog['facts'][0]
        draft=json.dumps({'response':'Alpha has 1 MVP. That is an individual honor worth discussing.', 'fact_ids':[fact['fact_id']]})
        with patch('backend.debate.agent.complete',side_effect=[submit(draft),review_response({'status': 'pass', 'issues': []})]):
            result=run_debate([{'role':'system','content':''},{'role':'user','content':'What about MVPs?'}],
                              'rebuttal',self.config,{'1':'Alpha','2':'Beta'},evidence=self.tools.evidence,data=self.data)
        self.assertEqual(result[2]['review_status'],'reviewed')
        self.assertEqual(result[2]['cards'][0]['title'],'MVP')
        self.assertEqual(result[2]['claims'][0]['scope']['basis'],'totals')


    def test_no_cards_never_claims_a_verified_summary(self):
        with patch('backend.debate.agent.complete',return_value=response('malformed')):
            result=run_debate([{'role':'system','content':''},{'role':'user','content':'An unrelated question'}],
                              'rebuttal',self.config,{'1':'Alpha','2':'Beta'},data=self.data)
        self.assertEqual(result[2]['cards'],[])
        self.assertIn('No verified claims',result[2]['review_note'])
        self.assertNotIn('fixed summary',result[2]['review_note'])


