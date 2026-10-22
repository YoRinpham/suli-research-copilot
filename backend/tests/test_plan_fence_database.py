"""Plan-only transactional fences in an explicitly disposable PostgreSQL database."""
import os,copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import select,update,delete,func,text
from sqlalchemy.orm import Session
from app.persistence.database import get_engine
from app.persistence.models import GenerationRun,ExperimentPlan,Project,Hypothesis,Claim,ClaimEvidence,LandscapeItemClaim,LandscapeItemEvidence,Evidence,GapEvidence,GapClaim,now_utc
from app.services.m4 import M4Service
from app.services.m4_schemas import PlanProposal,M4Assessment
from app.providers.interfaces import LLMResult
from app.providers.llm import LLMTimeout
from test_m4_database import m4_case
pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_INTEGRATION')!='1',reason='Disposable PostgreSQL only')

@pytest.fixture(autouse=True)
def cleanup_extra_claim_fixture_rows(m4_case):
    yield
    _,_,(pid,_,_,original,original_eid,*_)=m4_case
    with Session(get_engine()) as s:
        ids=list(s.scalars(select(Claim.id).where(Claim.project_id==pid,Claim.id!=original)))
        s.execute(delete(LandscapeItemClaim).where(LandscapeItemClaim.claim_id.in_(ids)))
        s.execute(delete(ClaimEvidence).where(ClaimEvidence.claim_id.in_(ids)))
        s.execute(delete(GapClaim).where(GapClaim.claim_id.in_(ids)))
        s.execute(delete(Claim).where(Claim.id.in_(ids)))
        extra_eids=list(s.scalars(select(Evidence.id).where(Evidence.project_id==pid,Evidence.id!=original_eid)))
        s.execute(delete(ClaimEvidence).where(ClaimEvidence.evidence_id.in_(extra_eids)))
        s.execute(delete(LandscapeItemEvidence).where(LandscapeItemEvidence.evidence_id.in_(extra_eids)))
        s.execute(delete(GapEvidence).where(GapEvidence.evidence_id.in_(extra_eids)))
        s.execute(delete(Evidence).where(Evidence.id.in_(extra_eids)));s.commit()

def selected_hypothesis(case):
    client,provider,(pid,lid,*_)=case;root=f'/api/v1/projects/{pid}'
    gap=client.post(root+'/gaps/generate',json={'landscape_id':lid,'expected_question_revision':1}).json()['gaps'][0]
    assert client.patch(root+f"/gaps/{gap['id']}/selection",json={'selection_status':'selected','expected_question_revision':1}).status_code==200
    hyp=client.post(root+f"/gaps/{gap['id']}/hypotheses/generate",json={'count':1,'expected_question_revision':1}).json()['hypotheses'][0]
    assert client.patch(root+f"/hypotheses/{hyp['id']}/selection",json={'selection_status':'selected','expected_question_revision':1}).status_code==200
    return root,hyp['id']

def test_terminal_run_cannot_persist_plan(m4_case):
    client,provider,(pid,*_)=m4_case;root,hid=selected_hypothesis(m4_case);changed=[]
    def make_terminal():
        if changed:return
        with Session(get_engine()) as s:
            run=s.scalar(select(GenerationRun).where(GenerationRun.project_id==pid,GenerationRun.stage=='experiment_plan',GenerationRun.status=='started'))
            if run:changed.append(run.id);run.status='failed';run.error_type='external-terminal';s.commit()
    provider.on_call=make_terminal
    response=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1})
    provider.on_call=None
    assert response.status_code==409,response.text
    with Session(get_engine()) as s:
        assert s.get(GenerationRun,changed[0]).status=='failed'
        assert s.get(GenerationRun,changed[0]).error_type=='external-terminal'
        assert s.scalar(select(func.count()).select_from(ExperimentPlan).where(ExperimentPlan.hypothesis_id==hid))==0

def active_run(s,pid):return s.scalar(select(GenerationRun).where(GenerationRun.project_id==pid,GenerationRun.stage=='experiment_plan',GenerationRun.status=='started'))
def no_plan(s,hid):assert s.scalar(select(func.count()).select_from(ExperimentPlan).where(ExperimentPlan.hypothesis_id==hid))==0

@pytest.mark.parametrize('state',['succeeded','absent','wrong_stage','wrong_project'])
def test_nonstarted_absent_or_mismatched_run_is_rejected_without_failure_overwrite(m4_case,state):
    client,provider,(pid,*_)=m4_case;root,hid=selected_hypothesis(m4_case);changed=[];other=[]
    def mutate():
        if changed:return
        with Session(get_engine()) as s:
            run=active_run(s,pid)
            if not run:return
            changed.append(run.id)
            if state=='absent':s.delete(run)
            elif state=='wrong_stage':run.stage='claim'
            elif state=='wrong_project':
                p=Project(title='Technical wrong-owner fixture',research_question='Synthetic only');s.add(p);s.flush();other.append(p.id);run.project_id=p.id
            else:run.status=state
            s.commit()
    provider.on_call=mutate
    try:
        response=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1});provider.on_call=None
        assert response.status_code==409,response.text
        with Session(get_engine()) as s:
            no_plan(s,hid);run=s.get(GenerationRun,changed[0])
            if state=='absent':assert run is None
            elif state in {'wrong_stage','wrong_project'}:assert run.status=='started' and run.error_type is None
            else:assert run.status==state
    finally:
        provider.on_call=None
        if other:
            with Session(get_engine()) as s:
                run=s.get(GenerationRun,changed[0]);run.project_id=pid;s.flush();s.delete(s.get(Project,other[0]));s.commit()

def test_cas_failure_rolls_back_every_plan_write(m4_case,monkeypatch):
    client,provider,(pid,*_)=m4_case;root,hid=selected_hypothesis(m4_case);actual=Session.execute;hits=[]
    def execute(s,statement,*args,**kwargs):
        if getattr(getattr(statement,'table',None),'name',None)=='generation_runs' and getattr(statement,'is_update',False):
            values=getattr(statement,'_values',{})
            if any(getattr(k,'key',k)=='status' and getattr(v,'value',None)=='succeeded' for k,v in values.items()):
                hits.append(True);return SimpleNamespace(rowcount=0)
        return actual(s,statement,*args,**kwargs)
    monkeypatch.setattr(Session,'execute',execute)
    response=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1})
    assert response.status_code==409 and hits==[True]
    with Session(get_engine()) as s:
        no_plan(s,hid);run=s.scalar(select(GenerationRun).where(GenerationRun.project_id==pid,GenerationRun.stage=='experiment_plan'))
        assert run.status=='failed' and run.error_type=='generation_superseded'

def test_normal_success_is_one_pending_plan_atomically_linked_to_original_run(m4_case):
    client,provider,(pid,*_)=m4_case;root,hid=selected_hypothesis(m4_case)
    result=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1})
    assert result.status_code==201,result.text
    receipt=result.json()
    with Session(get_engine()) as s:
        plans=list(s.scalars(select(ExperimentPlan).where(ExperimentPlan.hypothesis_id==hid)))
        assert len(plans)==1 and plans[0].review_status=='pending_review' and plans[0].reviewed_at is None
        assert plans[0].generation_run_id==receipt['generation_run_id']
        run=s.get(GenerationRun,receipt['generation_run_id'])
        assert run.status=='succeeded' and run.project_id==pid and run.stage=='experiment_plan' and run.attempt_count==2

def extra_claim(case):
    _,_,(pid,_,item,cid,eid,*_)=case
    with Session(get_engine()) as s:
        original=s.get(Claim,cid)
        row=Claim(project_id=pid,text='Technical excluded Claim fixture only',claim_type='finding',human_grounding_status='supported',human_reviewed_at=now_utc(),confidence='medium',uncertainty='Synthetic fixture',selection_status='selected',question_revision=1,origin='model',generation_run_id=original.generation_run_id)
        s.add(row);s.flush();s.add_all([ClaimEvidence(claim_id=row.id,evidence_id=eid,role='supports'),LandscapeItemClaim(item_id=item,claim_id=row.id)]);s.commit();return row.id

@pytest.mark.parametrize('phase',['before_run','final'])
def test_excluded_provider_claim_change_still_triggers_full_manifest_fence(m4_case,monkeypatch,phase):
    client,provider,(pid,*_)=m4_case;root,hid=selected_hypothesis(m4_case);excluded=extra_claim(m4_case);changed=[]
    def change():
        if changed:return
        with Session(get_engine()) as s:s.get(Claim,excluded).text+=' CHANGED';s.commit()
        changed.append(True)
    if phase=='before_run':
        original=M4Service._hypothesis_context;calls=[]
        def loader(self,*args):
            calls.append(True)
            if len(calls)==2:change()
            return original(self,*args)
        monkeypatch.setattr(M4Service,'_hypothesis_context',loader)
    else:provider.on_call=change
    response=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1});provider.on_call=None
    assert response.status_code==409,response.text
    with Session(get_engine()) as s:
        no_plan(s,hid);runs=list(s.scalars(select(GenerationRun).where(GenerationRun.project_id==pid,GenerationRun.stage=='experiment_plan')))
        if phase=='before_run':assert runs==[]
        else:assert len(runs)==1 and runs[0].status=='failed'

def minimal_plan():return json.loads((Path(__file__).parent/'fixtures/plan_capacity_outputs.json').read_text())['fixtures']['minimal_valid']

@pytest.mark.parametrize('target',[30000,30001])
def test_advisory_wrapper_boundary_and_oversized_valid_output_is_failed_run_only(m4_case,target):
    client,provider,(pid,*_)=m4_case;root,hid=selected_hypothesis(m4_case);calls=[]
    def generate(prompt,schema,context):
        calls.append((schema,copy.deepcopy(context)))
        if schema is PlanProposal:
            plan=copy.deepcopy(minimal_plan());plan['assumptions']=['X']
            gap=target-len(str({'scientific_input':context,'proposal':plan}))
            assert gap>0;plan['assumptions']=['X'*(gap+1)]
            assert len(str({'scientific_input':context,'proposal':PlanProposal.model_validate(plan).model_dump()}))==target
            return LLMResult(output=plan,model_name='qwen3.8-max',attempt_count=1)
        return LLMResult(output={'verdict':'aligned','risk_flags':[],'source_ids':[],'justification':'Technical capacity fixture only'},model_name='qwen3.8-max',attempt_count=1)
    provider.generate_structured=generate
    response=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1})
    with Session(get_engine()) as s:
        run=s.scalar(select(GenerationRun).where(GenerationRun.project_id==pid,GenerationRun.stage=='experiment_plan'))
        if target==30001:
            assert response.status_code==422 and len(calls)==1 and run.status=='failed';no_plan(s,hid)
        else:assert response.status_code==201 and len(calls)==2 and run.status=='succeeded'

def test_advisory_excluded_broader_authoritative_claim_is_rejected_after_one_repair(m4_case):
    client,provider,(pid,*_)=m4_case;root,hid=selected_hypothesis(m4_case);excluded=extra_claim(m4_case);calls=[]
    def generate(prompt,schema,context):
        calls.append((schema,copy.deepcopy(context)))
        output=minimal_plan() if schema is PlanProposal else {'verdict':'aligned','risk_flags':[],'source_ids':[excluded],'justification':'Invalid technical citation fixture'}
        return LLMResult(output=output,model_name='qwen3.8-max',attempt_count=1)
    provider.generate_structured=generate
    response=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1})
    assert response.status_code==502 and len(calls)==3
    assert all(excluded not in {r['id'] for r in c.get('scientific_input',c)['claims']} for _,c in calls)
    with Session(get_engine()) as s:no_plan(s,hid)

def test_generation_and_advisory_repairs_retain_identical_payloads_and_four_call_limit(m4_case):
    client,provider,(pid,*_)=m4_case;root,hid=selected_hypothesis(m4_case);calls=[];counts={PlanProposal:0,M4Assessment:0}
    def generate(prompt,schema,context):
        counts[schema]+=1;calls.append((schema,prompt,copy.deepcopy(context)))
        if schema is PlanProposal:
            output=copy.deepcopy(minimal_plan())
            if counts[schema]==1:output['baseline']=''
        else:output={'verdict':'aligned','risk_flags':[],'source_ids':[],'justification':'' if counts[schema]==1 else 'Technical repair fixture'}
        return LLMResult(output=output,model_name='qwen3.8-max',attempt_count=3)
    provider.generate_structured=generate
    response=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1})
    assert response.status_code==201 and len(calls)==4
    assert calls[0][2]==calls[1][2] and calls[2][2]==calls[3][2]
    assert calls[0][2]==calls[2][2]['scientific_input']
    assert all('plan-context-v1' in p for _,p,_ in calls)
    with Session(get_engine()) as s:assert s.get(GenerationRun,response.json()['generation_run_id']).attempt_count==12

@pytest.mark.parametrize('failure',['timeout','invalid_output','advisory_capacity'])
@pytest.mark.parametrize('state',['wrong_stage','wrong_project','terminal'])
def test_ownership_changes_on_generation_or_capacity_error_do_not_overwrite_run(m4_case,failure,state):
    client,provider,(pid,*_)=m4_case;root,hid=selected_hypothesis(m4_case);changed=[];other=[];fingerprints=[];calls=[]
    def mutate():
        if changed:return
        with Session(get_engine()) as s:
            run=active_run(s,pid);assert run;changed.append(run.id)
            if state=='wrong_stage':run.stage='claim'
            elif state=='wrong_project':
                p=Project(title='Technical error-owner fixture',research_question='Synthetic only');s.add(p);s.flush();other.append(p.id);run.project_id=p.id
            else:run.status='failed';run.error_type='external-terminal'
            s.commit();fingerprints.append(s.scalar(text('SELECT md5(to_jsonb(t)::text) FROM generation_runs t WHERE id=:id'),{'id':run.id}))
    def generate(prompt,schema,context):
        calls.append(schema);mutate()
        if failure=='timeout':raise LLMTimeout('Technical timeout fixture',1)
        if failure=='invalid_output':return LLMResult(output={},model_name='qwen3.8-max',attempt_count=1)
        plan=copy.deepcopy(minimal_plan());plan['assumptions']=['X']
        pad=30001-len(str({'scientific_input':context,'proposal':plan}));assert pad>0
        plan['assumptions']=['X'*(pad+1)]
        assert len(str({'scientific_input':context,'proposal':PlanProposal.model_validate(plan).model_dump()}))==30001
        return LLMResult(output=plan,model_name='qwen3.8-max',attempt_count=1)
    provider.generate_structured=generate
    try:
        response=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1})
        assert response.status_code=={'timeout':504,'invalid_output':502,'advisory_capacity':422}[failure],response.text
        assert len(calls)==(2 if failure=='invalid_output' else 1)
        with Session(get_engine()) as s:
            no_plan(s,hid)
            assert s.scalar(text('SELECT md5(to_jsonb(t)::text) FROM generation_runs t WHERE id=:id'),{'id':changed[0]})==fingerprints[0]
    finally:
        if other:
            with Session(get_engine()) as s:
                s.get(GenerationRun,changed[0]).project_id=pid;s.flush();s.delete(s.get(Project,other[0]));s.commit()

@pytest.mark.parametrize('domain',['claim','evidence'])
def test_gap_array_mentions_do_not_make_excluded_sources_visible_to_advisory(m4_case,domain):
    client,provider,(pid,_,_,cid,eid,*_)=m4_case;root,hid=selected_hypothesis(m4_case)
    if domain=='claim':
        excluded=extra_claim(m4_case)
        with Session(get_engine()) as s:
            gid=s.get(Hypothesis,hid).research_gap_id;s.add(GapClaim(gap_id=gid,claim_id=excluded));s.commit()
    else:
        with Session(get_engine()) as s:
            original=s.get(Evidence,eid);row=Evidence(project_id=pid,paper_chunk_id=original.paper_chunk_id,source_text=original.source_text,evidence_type='limitation',selection_status='selected',question_revision=1)
            s.add(row);s.flush();excluded=row.id;gid=s.get(Hypothesis,hid).research_gap_id
            s.add_all([ClaimEvidence(claim_id=cid,evidence_id=excluded,role='supports'),GapEvidence(gap_id=gid,evidence_id=excluded,role='supports')]);s.commit()
    calls=[]
    def generate(prompt,schema,context):
        calls.append((schema,copy.deepcopy(context)))
        output=minimal_plan() if schema is PlanProposal else {'verdict':'aligned','risk_flags':[],'source_ids':[excluded],'justification':'Technical invalid Gap-array citation'}
        return LLMResult(output=output,model_name='qwen3.8-max',attempt_count=1)
    provider.generate_structured=generate
    response=client.post(root+f'/hypotheses/{hid}/experiment-plans/generate',json={'expected_question_revision':1})
    assert response.status_code==502 and len(calls)==3
    key='claims' if domain=='claim' else 'evidence';gap_key='claim_ids' if domain=='claim' else 'evidence_for_ids'
    assert excluded in calls[0][1]['gap'][gap_key]
    for _,context in calls:
        visible=context.get('scientific_input',context)
        assert excluded not in {r['id'] for r in visible[key]}
    with Session(get_engine()) as s:no_plan(s,hid)
