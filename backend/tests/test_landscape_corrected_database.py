"""Corrected exact source identities through normal HTTP in disposable PostgreSQL."""
import json
import os
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.persistence.database import get_engine
from app.persistence.models import Claim,ClaimEvidence,Evidence,GenerationRun,Paper,PaperChunk,Project,ResearchLandscape
from app.services.scientific import ScientificService
from app.config.settings import get_settings
from test_m3_database import live_m3
from test_landscape_corrected_budget import corrected_sources,corrected_output,C60,E60
from test_landscape_canonical_verification import item_verification,summary_verification

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_INTEGRATION')!='1',reason='Requires isolated PostgreSQL')

@pytest.fixture
def corrected_live(live_m3):
    client,provider,source=live_m3
    project,_,_,_=source('Synthetic fixture registration, not production.')
    context=corrected_sources()
    with Session(get_engine()) as session:
        p=session.get(Project,project);p.research_question=context['research_question']
        paper=Paper(id=context['evidence'][0]['paper_id'],project_id=project,source_filename='synthetic-v02.pdf',storage_key='isolated-synthetic-v02.pdf',parse_status='indexed')
        session.add(paper);session.flush()
        chunks=json.loads((Path(__file__).parent/'fixtures/landscape_budget_corrected_chunks.json').read_text(encoding='utf8'))
        for chunk in chunks: session.add(PaperChunk(**chunk))
        session.flush()
        for e in context['evidence']:
            session.add(Evidence(id=e['id'],project_id=project,paper_chunk_id=e['paper_chunk_id'],source_text=e['source_text'],evidence_type='method_description',selection_status='selected',question_revision=1))
        run=GenerationRun(project_id=project,stage='claim',status='succeeded',provider='deterministic_fixture',model='mock',prompt_name='fixture',prompt_version='fixture',prompt_hash='0'*64,schema_version='fixture',research_question_snapshot=p.research_question,question_revision=1,input_manifest=[],input_digest='0'*64)
        session.add(run);session.flush()
        for c in context['claims']:
            session.add(Claim(id=c['id'],project_id=project,text=c['text'],claim_type=c['claim_type'],human_grounding_status=c['human_grounding_status'],confidence='unknown',uncertainty=c['uncertainty'],selection_status='selected',question_revision=1,origin='model',generation_run_id=run.id))
        session.flush()
        for c in context['claims']:
            for field,role in [('evidence_for_ids','supports'),('evidence_against_ids','challenges')]:
                session.add_all([ClaimEvidence(claim_id=c['id'],evidence_id=e,role=role) for e in c[field]])
        session.commit()
        _,actual,_,union=ScientificService(get_settings(),None)._claim_input(session,project,1,[c['id'] for c in context['claims']])
        assert actual=={k:context[k] for k in ['claims','evidence']}
        assert union=={e['id'] for e in context['evidence']}
    output=corrected_output(context)
    provider.responses=[output.model_dump()]
    provider.verification_responses=[item_verification(evidence_refs=i.evidence_ids,justification='Deterministic procedural fixture.') for i in output.items]+[summary_verification(evidence_refs=[E60])]
    provider.contexts.clear()
    return client,provider,project,context


def test_corrected_scope_atomic_success_pending_human_review(corrected_live):
    client,provider,project,context=corrected_live
    response=client.post(f'/api/v1/projects/{project}/landscapes/generate',json={'claim_ids':[c['id'] for c in context['claims']],'expected_question_revision':1})
    assert response.status_code==201,response.text
    data=response.json();landscape=data['landscape']
    assert landscape['review_status']=='pending_review' and not landscape['eligible_for_downstream']
    assert {c for i in landscape['items'] for c in i['claim_ids']}=={c['id'] for c in context['claims']}
    assert {e for i in landscape['items'] for e in i['evidence_ids']}=={e['id'] for e in context['evidence']}
    assert all(len(str(c))<=30000 for c in provider.contexts)
    with Session(get_engine()) as session:
        run=session.get(GenerationRun,data['generation_run_id']);assert run.status=='succeeded' and run.attempt_count==5
        assert {i['id'] for i in run.input_manifest if i['type']=='claim'}=={c['id'] for c in context['claims']}
        assert {i['id'] for i in run.input_manifest if i['type']=='evidence'}=={e['id'] for e in context['evidence']}
        assert len(list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id==project))))==1
        by_id={e['id']:e for e in context['evidence']}
        for item in landscape['items']:
            assert item['ai_conflict_assessment']['support_verdict']=='supported'
            for q in item['ai_conflict_assessment']['support_excerpts']:
                original=by_id[q['evidence_id']]
                assert q['excerpt']==original['source_text'] and q['page_number']==original['page_number']


def test_c60_source_state_invalidation_blocks_atomic_persistence(corrected_live):
    client,provider,project,context=corrected_live
    def invalidate():
        provider.on_call=None
        with Session(get_engine()) as session:
            session.get(Evidence,E60).selection_status='rejected';session.commit()
    provider.on_call=invalidate
    response=client.post(f'/api/v1/projects/{project}/landscapes/generate',json={'claim_ids':[c['id'] for c in context['claims']],'expected_question_revision':1})
    assert response.status_code==409,response.text
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id==project)))
        run=session.get(GenerationRun,response.json()['error']['details']['generation_run_id'])
        assert run.status=='failed' and run.error_type=='invalid_state'
        assert session.get(Claim,C60).human_grounding_status=='supported' and session.get(Claim,C60).selection_status=='selected'


@pytest.mark.parametrize('repair_size', [4627, 4628, 5506])
def test_exact_scope_budget_repair_atomic_boundary(corrected_live, repair_size):
    from test_landscape_response_contract import sized_output
    client, provider, project, context = corrected_live
    provider.responses = [sized_output(5142), sized_output(repair_size)]
    response = client.post(f'/api/v1/projects/{project}/landscapes/generate', json={
        'claim_ids': [c['id'] for c in context['claims']], 'expected_question_revision': 1})
    succeeded = repair_size == 4627
    assert response.status_code == (201 if succeeded else 422), response.text
    data = response.json()
    run_id = data['generation_run_id'] if succeeded else data['error']['details']['generation_run_id']
    if not succeeded:
        assert data['error']['code'] == 'landscape_context_budget_exceeded'
        assert data['error']['details']['measured_size'] == repair_size
        assert data['error']['details']['size_budget'] == 4627
        assert data['error']['details']['persistence_occurred'] is False
    with Session(get_engine()) as session:
        run = session.get(GenerationRun, run_id)
        assert run.status == ('succeeded' if succeeded else 'failed')
        assert run.attempt_count == (6 if succeeded else 2)
        landscapes = list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project)))
        assert len(landscapes) == int(succeeded)
        assert all(session.get(Claim, c['id']).selection_status == 'selected' and
                   session.get(Claim, c['id']).human_grounding_status == 'supported' for c in context['claims'])
        assert all(session.get(Evidence, e['id']).selection_status == 'selected' for e in context['evidence'])
