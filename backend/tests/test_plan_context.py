"""Deterministic Plan representation regressions; frozen synthetic corpus, no API/DB."""
import copy,json
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import pytest
from app.config.settings import Settings
from app.services.m4 import M4Service
from app.services.errors import ServiceError
from app.services.m4_schemas import PlanProposal

def full_context():return json.loads((Path(__file__).parent/'fixtures/plan_capacity_context.json').read_text(encoding='utf8'))
def info():
    context=full_context();context.pop('research_question')
    h=context['hypothesis'];g=context['gap']
    return {'context':context,'manifest':[{'type':'sentinel','id':'full-authority'}],
        'hypothesis':SimpleNamespace(id=h['id'],claim_links=[SimpleNamespace(claim_id=x,role='extends') for x in h['claim_ids']],evidence_links=[SimpleNamespace(evidence_id=x,role='motivates') for x in h['evidence_motivating_ids']]),
        'gap':SimpleNamespace(id=g['id'],evidence_links=[SimpleNamespace(evidence_id=x,role='supports') for x in g['evidence_for_ids']]),
        'landscape':SimpleNamespace(id=context['landscape']['id']),
        'claim_sources':{x['id']:set(x['evidence_for_ids']+x['evidence_against_ids']) for x in context['claims']},
        'claim_ids':{x['id'] for x in context['claims']},'evidence_ids':{x['id'] for x in context['evidence']},
        'items':{x['id']:{} for x in context['landscape']['items']},
        'gap_claim_ids':set(g['claim_ids']),'gap_evidence_ids':set(g['evidence_for_ids']+g['evidence_against_ids'])}

class AdmissionReached(Exception):pass
def test_frozen_44370_context_admitted_before_run_after_compaction():
    source=full_context();assert len(str(source))==44370
    @contextmanager
    def session_factory():
        yield SimpleNamespace(add=lambda run:(_ for _ in ()).throw(AdmissionReached()))
    service=M4Service(Settings(database_url='postgresql+psycopg://offline:offline@127.0.0.1:1/unavailable'),None,session_factory=session_factory)
    service._project=lambda *a,**kw:SimpleNamespace(research_question=source['research_question'])
    service._hypothesis_context=lambda *a:info()
    with pytest.raises(AdmissionReached):service.generate_plan('offline',source['hypothesis']['id'],1)

def test_full_44370_guard_still_rejects_without_mutating_or_relaxing_limit():
    source=full_context();original=copy.deepcopy(source)
    with pytest.raises(ServiceError):M4Service._check_size(source,Settings(database_url='postgresql+psycopg://x:x@localhost:1/offline'))
    assert source==original

def test_builder_matches_reviewed_10282_complete_representation_and_is_pure():
    original=info();before=copy.deepcopy(original)
    question=full_context()['research_question']
    payload={'research_question':question,**M4Service._plan_provider_context(original)}
    expected=json.loads((Path(__file__).parent/'fixtures/plan_compact_expected.json').read_text(encoding='utf8'))
    assert payload==expected and len(str(payload))==10282
    assert original==before
    assert payload['hypothesis']==original['context']['hypothesis']
    assert payload['gap']==original['context']['gap']
    retained=payload['evidence'][0];assert retained==next(r for r in original['context']['evidence'] if r['id']==retained['id'])
    assert retained['page_number']==11
    payload['hypothesis']['assumptions'].append('Mutation of returned technical test value only')
    assert original==before

def test_multisource_gap_does_not_expand_narrow_hypothesis_or_advisory_boundary():
    original=info();context=original['context'];h=context['hypothesis'];g=context['gap']
    excluded_claim=next(r for r in context['claims'] if r['id'] not in h['claim_ids'])
    excluded_evidence=next(r for r in context['evidence'] if r['id'] not in h['evidence_motivating_ids'])
    g['claim_ids'].append(excluded_claim['id']);g['evidence_for_ids'].append(excluded_evidence['id'])
    original['gap_claim_ids'].add(excluded_claim['id']);original['gap_evidence_ids'].add(excluded_evidence['id'])
    original['gap'].evidence_links.append(SimpleNamespace(evidence_id=excluded_evidence['id'],role='supports'))
    before=copy.deepcopy(original);payload=M4Service._plan_provider_context(original)
    assert payload['gap']==g and payload['hypothesis']==h
    assert [r['id'] for r in payload['claims']]==h['claim_ids']
    assert [r['id'] for r in payload['evidence']]==h['evidence_motivating_ids']
    visible=M4Service._plan_visible_sources(payload)
    assert excluded_claim['id'] not in visible and excluded_evidence['id'] not in visible
    assert set(payload['gap']['claim_ids'])-visible=={excluded_claim['id']}
    assert payload['source_roles']['hypothesis_claims']==[{'claim_id':h['claim_ids'][0],'role':'extends'}]
    assert payload['source_roles']['hypothesis_evidence']==[{'evidence_id':h['evidence_motivating_ids'][0],'role':'motivates'}]
    assert payload['source_roles']['gap_evidence']==[{'evidence_id':h['evidence_motivating_ids'][0],'role':'supports'}]
    assert original==before

@pytest.mark.parametrize('name,chars,accepted',[('minimal_valid',10995,True),('scalar_maxima_valid',33743,False),('exact_30000',30000,True),('exact_30001',30001,False)])
def test_actual_advisory_capacity_boundaries_use_complete_valid_output(name,chars,accepted):
    fixture=json.loads((Path(__file__).parent/'fixtures/plan_capacity_outputs.json').read_text())['fixtures'][name]
    plan=PlanProposal.model_validate(fixture)
    scientific={'research_question':full_context()['research_question'],**M4Service._plan_provider_context(info())}
    wrapper={'scientific_input':scientific,'proposal':plan.model_dump()}
    assert len(str(wrapper))==chars
    settings=Settings(database_url='postgresql+psycopg://x:x@localhost:1/offline')
    if accepted:M4Service._check_size(wrapper,settings)
    else:
        with pytest.raises(ServiceError):M4Service._check_size(wrapper,settings)

def test_claim_and_hypothesis_evidence_roles_remain_distinct_with_challenging_sources():
    original=info();context=original['context'];h=context['hypothesis']
    extra=next(r for r in context['evidence'] if r['id'] not in h['evidence_motivating_ids'])
    h['evidence_challenging_ids']=[extra['id']]
    original['hypothesis'].evidence_links.append(SimpleNamespace(evidence_id=extra['id'],role='challenges'))
    original['hypothesis'].claim_links[0].role='challenges'
    payload=M4Service._plan_provider_context(original)
    assert payload['hypothesis']['evidence_challenging_ids']==[extra['id']]
    assert payload['source_roles']['hypothesis_claims'][0]['role']=='challenges'
    assert payload['source_roles']['hypothesis_evidence'][-1]['role']=='challenges'
    for row in payload['evidence']:assert row==next(r for r in context['evidence'] if r['id']==row['id'])
