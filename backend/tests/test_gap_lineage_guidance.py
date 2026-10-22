"""Synthetic engineering examples, never historical discarded responses."""
from copy import deepcopy
import json

import pytest
from app.config.settings import get_settings
from app.prompts.m4 import GAP_PROMPT, GAP_STRUCTURED_REPAIR
from app.providers.interfaces import LLMResult
from app.services.errors import ServiceError
from app.services.gap_context import GapContext
from app.services.m4 import M4Service
from app.services.m4_schemas import GapBatch, GapProposal
from app.services.scientific import ScientificService

FOREIGN = '00000000-0000-4000-8000-000000000999'
PRIVATE = 'SYNTHETIC_PRIVATE_EXCEPTION_AND_IDENTIFIER'
def proposal():
    return {'title':'Synthetic engineering candidate', 'description':'Synthetic corpus limitation',
        'gap_type':'missing_comparison','why_it_matters':'Synthetic test only',
        'uncertainty':'No scientific result','assumptions':[], 'novelty_scope':'supplied_literature',
        'landscape_item_ids':['synthetic-item'], 'claim_ids':['synthetic-claim-1'],
        'evidence_for_ids':['synthetic-evidence-1'], 'evidence_against_ids':[]}

class Spy:
    def __init__(self, outputs): self.outputs=iter(outputs); self.calls=[]
    def generate_structured(self, prompt, schema, context):
        self.calls.append((prompt,schema,deepcopy(context)))
        return LLMResult(deepcopy(next(self.outputs)), 'qwen3.8-max', 1)

class UnitService(ScientificService):
    def __init__(self, provider):
        super().__init__(get_settings().model_copy(update={'qwen_model':'qwen3.8-max'}),provider)
        self.failures=[]
    def _failed(self,*args): self.failures.append(args)

def invoke(outputs, validator, *, captured=False):
    spy=Spy(outputs); service=UnitService(spy)
    context={'synthetic_immutable_context':PRIVATE}
    from app.services.gap_diagnostics import GapCapture, _ACTIVE
    capture=GapCapture('00000000-0000-4000-8000-000000000001',None) if captured else None
    if capture: capture.run_id='00000000-0000-4000-8000-000000000002'
    token=_ACTIVE.set(capture)
    try:
        result=service._generate('synthetic-run',GAP_PROMPT,GapBatch,context,validator,
            trace_stage='gap_generation',repair_instruction=GAP_STRUCTURED_REPAIR)
    finally: _ACTIVE.reset(token)
    return result,spy,service,context

def test_synthetic_mismatch_feedback_reaches_existing_repair():
    good=proposal(); bad={**good,'evidence_for_ids':['synthetic-evidence-2']}
    items={'synthetic-item':{'claims':{'synthetic-claim-1','synthetic-claim-2'},
                              'evidence':{'synthetic-evidence-1','synthetic-evidence-2'}}}
    def validate(batch):
        M4Service.validate_gap_sources(batch.gaps[0],items,
            {'synthetic-claim-1':{'synthetic-evidence-1'},'synthetic-claim-2':{'synthetic-evidence-2'}})
    (output,attempts),spy,service,context=invoke([{'gaps':[bad]},{'gaps':[good]}],validate)
    assert 'gap_evidence_claim_mismatch' in spy.calls[1][0]
    assert 'Gap repair feedback:' not in spy.calls[0][0]
    assert len(spy.calls)==2 and attempts==2 and service.failures==[]
    assert spy.calls[0][2]==spy.calls[1][2]==context
    assert output.gaps[0].model_dump()==good

def test_synthetic_foreign_item_feedback_never_echoes_identifier():
    good=proposal(); bad={**good,'landscape_item_ids':[FOREIGN]}
    def validate(batch):
        M4Service.validate_gap_sources(batch.gaps[0],{'synthetic-item':{
            'claims':{'synthetic-claim-1'},'evidence':{'synthetic-evidence-1'}}},
            {'synthetic-claim-1':{'synthetic-evidence-1'}})
    _,spy,_,_=invoke([{'gaps':[bad]},{'gaps':[good]}],validate)
    assert 'gap_foreign_landscape_item' in spy.calls[1][0]
    assert FOREIGN not in spy.calls[1][0]

def test_unknown_hostile_exception_has_fixed_feedback_and_no_prose(caplog):
    import logging
    caplog.set_level(logging.INFO,logger='uvicorn.error')
    good={'gaps':[proposal()]}; rounds=0
    def validate(batch):
        nonlocal rounds
        rounds+=1
        if rounds==1:
            error=ValueError(PRIVATE)
            error.validation_code=PRIVATE
            error.candidate_index=999999
            raise error
    _,spy,_,_=invoke([good,good],validate,captured=True)
    assert 'gap_generation_invalid' in spy.calls[1][0]
    assert PRIVATE not in spy.calls[1][0] and PRIVATE not in caplog.text
    assert 'gap_trace' in caplog.text
    assert any('domain_validation' in r.message for r in caplog.records)

def test_generation_and_repair_explain_joint_contract_without_role_equivalence():
    for text in [GAP_PROMPT.text,GAP_STRUCTURED_REPAIR]:
        assert 'union of Claims linked to the chosen LandscapeItems' in text
        assert 'union of Evidence linked to the explicitly cited Claims' in text
        assert 'inherited' in text
        assert 'Claim evidence roles do not determine Gap evidence roles' in text
        assert 'Never treat integer references as public IDs' in text
        assert 'items[0]' not in text  # No single-item preference or substitution example.

def test_existing_inherited_and_distinct_gap_roles_are_valid():
    value=GapProposal.model_validate({**proposal(),'evidence_for_ids':['synthetic-claim-challenge'],
        'evidence_against_ids':['synthetic-claim-support']})
    # Both roles may motivate/challenge a Gap independently of their Claim roles.
    M4Service.validate_gap_sources(value,{'synthetic-item':{'claims':{'synthetic-claim-1'},
        'evidence':{'synthetic-claim-support','synthetic-claim-challenge'}}},
        {'synthetic-claim-1':{'synthetic-claim-support','synthetic-claim-challenge'}})
    with pytest.raises(ValueError):
        GapProposal.model_validate({**value.model_dump(),'evidence_against_ids':value.evidence_for_ids})

def test_synthetic_invalid_replacement_remains_rejected_without_substitution():
    bad={**proposal(),'claim_ids':[PRIVATE]}; spy=Spy([{'gaps':[bad]},{'gaps':[bad]}]); service=UnitService(spy)
    def validate(batch):
        M4Service.validate_gap_sources(batch.gaps[0],{'synthetic-item':{
            'claims':{'synthetic-claim-1'},'evidence':{'synthetic-evidence-1'}}},{'synthetic-claim-1':{'synthetic-evidence-1'}})
    with pytest.raises(ServiceError) as error:
        service._generate('synthetic-run',GAP_PROMPT,GapBatch,{},validate,trace_stage='gap_generation',
            repair_instruction=GAP_STRUCTURED_REPAIR)
    assert error.value.code=='structured_output_invalid' and len(spy.calls)==2
    assert service.failures==[('synthetic-run','structured_output_invalid',2)]
    assert PRIVATE not in spy.calls[1][0]

@pytest.mark.parametrize('fault',['missing','forged_code','forged_index','forged_extra','raw_feedback'])
def test_forged_typed_error_uses_fixed_fallback(fault):
    from app.services.gap_lineage import GapLineageError, GapRepairFeedback
    error=GapLineageError('gap_foreign_landscape_item',0)
    if fault=='missing': del error.feedback
    elif fault=='raw_feedback': error.feedback={'validation_code':PRIVATE}
    else:
        fields={'validation_code':'gap_foreign_landscape_item','candidate_index':0}
        if fault=='forged_code': fields['validation_code']=PRIVATE
        elif fault=='forged_index': fields['candidate_index']=999999
        else: fields[PRIVATE]=PRIVATE
        error.feedback=GapRepairFeedback.model_construct(**fields)
        if fault=='forged_extra':
            object.__setattr__(error.feedback,'__pydantic_extra__',{PRIVATE:PRIVATE})
    feedback=GapRepairFeedback.from_error(error)
    assert feedback.model_dump()=={'validation_code':'gap_generation_invalid','candidate_index':None}
    assert PRIVATE not in feedback.instructions()

@pytest.mark.parametrize('index',[-1,10,True,'0',999999])
def test_feedback_rejects_unbounded_or_noninteger_candidate_index(index):
    from app.services.gap_lineage import GapRepairFeedback
    with pytest.raises(ValueError): GapRepairFeedback(candidate_index=index)

def test_unrelated_shared_generation_does_not_receive_gap_feedback():
    from app.prompts.m4 import HYPOTHESIS_PROMPT
    spy=Spy([{'gaps':[proposal()]},{'gaps':[proposal()]}]); service=UnitService(spy)
    calls=0
    def validate(batch):
        nonlocal calls
        calls+=1
        if calls==1: raise ValueError(PRIVATE)
    service._generate('synthetic-run',HYPOTHESIS_PROMPT,GapBatch,{},validate,
        trace_stage='hypothesis_batch')
    assert len(spy.calls)==2 and 'Gap repair feedback:' not in spy.calls[1][0]
