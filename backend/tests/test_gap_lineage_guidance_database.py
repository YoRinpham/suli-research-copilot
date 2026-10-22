"""Synthetic lineage failures in a proved-distinct disposable database only."""
import json, os
from copy import deepcopy
import pytest
from test_gap_capacity_database import exact_gap_case, assert_failed_atomic, gap_runs
from app.providers.interfaces import LLMResult
from app.services.errors import ServiceError
from app.services.m4_schemas import GapBatch

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_INTEGRATION')!='1',reason='Fenced disposable DB required')
FOREIGN='00000000-0000-4000-8000-000000000999'

@pytest.mark.parametrize('branch,code',[('mismatch','gap_evidence_claim_mismatch'),
    ('foreign','gap_foreign_landscape_item'),('incomplete','gap_incomplete_lineage')])
@pytest.mark.parametrize('index',[0,9])
@pytest.mark.parametrize('replacement_valid',[True,False])
def test_gap_lineage_feedback_actual_batch_is_safe_and_bounded(exact_gap_case,branch,code,index,replacement_valid):
    service,provider,project,landscape,context,*_=exact_gap_case
    original=provider.generate_structured; provider.count=index+1
    events=[]; service.trace_sink=events.append; calls=[]; outputs=[]
    def generate(prompt,schema,encoded):
        calls.append({'prompt':prompt,'schema':schema.__name__,'context':deepcopy(encoded)})
        result=original(prompt,schema,encoded)
        value=deepcopy(result.output)
        if schema is GapBatch:
            if provider.generation_calls==1 or not replacement_valid:
                gap=value['gaps'][index]
                if branch=='foreign': gap['landscape_item_ids']=[FOREIGN]
                elif branch=='incomplete': gap['claim_ids']=[FOREIGN]
                else:
                    evidence=gap['evidence_for_ids'][0]
                    by_claim={r['id']:r for r in context['claims']}
                    gap['claim_ids']=[next(cid for cid in gap['claim_ids'] if evidence not in
                        by_claim[cid]['evidence_for_ids']+by_claim[cid]['evidence_against_ids'])]
            outputs.append(deepcopy(value))
        return LLMResult(value,result.model_name,result.attempt_count)
    provider.generate_structured=generate
    if replacement_valid:
        result=service.generate_gaps(project,landscape,1)
        assert len(result['gaps'])==index+1 and provider.assessment_calls==index+1
        for persisted,returned in zip(result['gaps'],outputs[1]['gaps'],strict=True):
            assert all(persisted[k]==v for k,v in returned.items())
        assert gap_runs(project)[0].status=='succeeded'
    else:
        with pytest.raises(ServiceError) as error: service.generate_gaps(project,landscape,1)
        assert error.value.code=='structured_output_invalid' and provider.assessment_calls==0
        assert_failed_atomic(exact_gap_case,2)
    assert provider.generation_calls==2
    first,repair=calls[:2]
    assert 'Gap repair feedback:' not in first['prompt']
    assert 'Gap repair feedback:' in repair['prompt']
    metadata=json.loads(repair['prompt'].split('Gap repair feedback: ',1)[1].split('\n',1)[0])
    assert metadata=={'validation_code':code,'candidate_index':index}
    assert first['context']==repair['context']
    assert FOREIGN not in repair['prompt'] and FOREIGN not in json.dumps(events)
    assert all(e['structured_repairs']<=1 for e in events)
    assert code in {e.get('validation_code') for e in events}
