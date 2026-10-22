"""Synthetic capacity/regressions, approved UUIDs; no historical replay."""
from copy import deepcopy
import itertools
import pytest
from app.services.landscape_budget import LandscapeBudget, LandscapeBudgetExceeded, assessment_payload
from test_landscape_corrected_budget import corrected_sources
from test_landscape_canonical_verification import item_verification

from app.services.landscape_budget import encode_assessment, decode_assessment, ASSESSMENT_CONTEXT_VERSION

@pytest.mark.parametrize('verdict', ['direct_conflict','conditional_difference','incomparable','not_conflict','unclear'])
@pytest.mark.parametrize('support', ['supported','unsupported','insufficient_evidence'])
@pytest.mark.parametrize('role', [None,False,True])
def test_every_judgment_field_roundtrips_without_value_or_order_changes(verdict,support,role):
    raw=item_verification(verdict=verdict,support_verdict=support,asserts_direct_conflict=True,
        dimensions=dict(zip(item_verification()['dimensions'],itertools.islice(itertools.cycle(['comparable','different','unknown']),6))),
        flagged_statements=['μ→合成\x00\n\r\t\\\'"','same','same'],qualifications=[],
        evidence_refs=[e['id'] for e in corrected_sources()['evidence']], justification='literal\n科学')
    if role is not None: raw['server_direct_conflict_role']=role
    snapshot=deepcopy(raw); row=encode_assessment(raw)
    assert decode_assessment(row)==snapshot and encode_assessment(decode_assessment(row))==row
    row[4].append('changed outside caller')
    assert raw==snapshot
    assert ASSESSMENT_CONTEXT_VERSION=='assessment-context-v1'

@pytest.mark.parametrize('invalid', ['unknown','mixed_alias','missing','dimension_unknown','dimension_missing','wrong_boolean','wrong_list'])
def test_unknown_missing_and_collision_shapes_reject_explicitly(invalid):
    raw=material_rows()[0]
    if invalid=='unknown': raw['unrecognized']='sensitive'
    elif invalid=='mixed_alias': raw['v']=raw['verdict']
    elif invalid=='missing': del raw['qualifications']
    elif invalid=='dimension_unknown': raw['dimensions']['z']='unknown'
    elif invalid=='dimension_missing': del raw['dimensions']['dataset']
    elif invalid=='wrong_boolean': raw['asserts_direct_conflict']=0
    else: raw['evidence_refs']='UUID'
    with pytest.raises(ValueError) as error: encode_assessment(raw)
    assert 'sensitive' not in str(error.value)

@pytest.mark.parametrize('change', ['short','long','dimension_short','dimension_long','dimension_map'])
def test_decoder_rejects_unknown_or_ambiguous_columns(change):
    row=encode_assessment(material_rows()[0])
    if change=='short': row.pop();row.pop()
    elif change=='long': row.append('unrecognized')
    elif change=='dimension_short': row[2].pop()
    elif change=='dimension_long': row[2].append('unknown')
    else: row[2]={'dataset':'unknown'}
    with pytest.raises(ValueError): decode_assessment(row)

def test_actual_summary_and_repair_payloads_are_encoded_but_storage_stays_canonical():
    from test_landscape_canonical_verification import service,verify,landscape,summary_verification,context
    negative=summary_verification(verdict='unsupported_major_statement',flagged_statements=['Keep uncertainty.'])
    svc,provider,failed,_=service([item_verification(),negative,
        {'summary':'Bounded synthetic protocol only.','uncertainty':'Unresolved.'},summary_verification()])
    output,items,summaries,attempts=verify(svc,landscape())
    assert not failed and attempts==5
    for prompt,schema,payload in provider.calls:
        assert 'assessment-context-v1' in prompt
        if 'item_assessments' in payload:
            assert payload['item_assessments']==assessment_payload(items)
            decoded=[decode_assessment(row) for row in payload['item_assessments']]
            assert decoded==[{k:v for k,v in item.items() if k not in {'support_excerpts','source_binding_origin','source_binding_schema_version'}} for item in items]
    assert items[0]['support_excerpts'][0]['excerpt']==context()['evidence'][0]['source_text']
    assert items[0]['source_binding_schema_version']=='m3-landscape-v9'

def test_early_prose_reserves_reference_heavy_later_UUID_judgments():
    source=corrected_sources(); b=LandscapeBudget(source,30000)
    claims=[c['id'] for c in source['claims']]; refs=[e['id'] for e in source['evidence']]
    remaining=[{'category':'finding','claim_ids':claims,'evidence_ids':refs[:1]},
               {'category':'finding','claim_ids':claims,'evidence_ids':refs[:12]}]
    a=b.assessment_allocation([],remaining)
    raw=item_verification(evidence_refs=refs[:1],qualifications=['Physical conformity unverified.'])
    raw['justification']='x'*(a.current_response_allowance-len(str(encode_assessment(raw)))+len(raw['justification'])+1)
    with pytest.raises(LandscapeBudgetExceeded):
        b.check_response('LandscapeItemVerification',raw,prior_assessments=[],remaining_items=remaining,landscape=None)
    assert (a.immutable_prior_chars+a.server_overhead_chars+a.list_overhead_chars+
        a.current_response_allowance+a.later_reservation_chars)==2500
    assert a.later_reservation_chars>=b._assessment_floor(remaining[1])+a.server_overhead_chars+2

def test_reference_identity_alone_can_make_five_items_infeasible():
    source=corrected_sources();b=LandscapeBudget(source,30000)
    items=[{'category':'finding','claim_ids':[c['id'] for c in source['claims']],
            'evidence_ids':[e['id'] for e in source['evidence']]} for _ in range(5)]
    with pytest.raises(LandscapeBudgetExceeded) as caught: b.assessment_allocation([],items)
    assert caught.value.candidate_chars is None
    assert caught.value.allocation_details()['assessment_encoding_version']=='assessment-context-v1'

def test_actual_UUID_multi_claim_distinct_reference_coverage_and_floor():
    from app.services.scientific_schemas import LandscapeItemVerification
    from test_landscape_canonical_verification import service
    source=corrected_sources();b=LandscapeBudget(source,30000)
    chosen=[];refs=[]
    for claim in source['claims']:
        candidates=[e for e in claim['evidence_for_ids'] if e not in refs]
        if candidates:chosen.append(claim);refs.append(candidates[0])
        if len(chosen)==3:break
    assert len(refs)==3
    proposal={'category':'conflict','claim_ids':[c['id'] for c in chosen],'evidence_ids':refs[:2]}
    assert b._assessment_floor(proposal)==b._assessment_floor({**proposal,'evidence_ids':refs})
    linked={c['id']:set(c['evidence_for_ids']+c['evidence_against_ids']) for c in chosen}
    scoped={e['id']:e for e in source['evidence'] if e['id'] in set.union(*linked.values())}
    supporting={e for c in chosen for e in c['evidence_for_ids']}
    raw=item_verification(verdict='direct_conflict',asserts_direct_conflict=True,evidence_refs=refs,
        dimensions=dict.fromkeys(item_verification()['dimensions'],'comparable'),
        qualifications=['Synthetic judgment for matching only; not a scientific conflict determination.'])
    svc,_,_,_=service([])
    bound=svc._bind_landscape_verification(LandscapeItemVerification.model_validate(raw),scoped,supporting,linked)
    assert bound['evidence_refs']==refs and len(bound['support_excerpts'])==3
    for i in range(3):
        with pytest.raises(ValueError):
            svc._bind_landscape_verification(LandscapeItemVerification.model_validate({**raw,'evidence_refs':refs[:i]+refs[i+1:]}),scoped,supporting,linked)

def material_rows():
    refs=[e['id'] for e in corrected_sources()['evidence']]
    rows=[]
    for ids in [refs[:4],refs[4:8],refs[8:12],refs[12:16],refs[16:]]:
        rows.append(item_verification(evidence_refs=ids, support_verdict='insufficient_evidence',
            justification='Synthetic protocol; physical conformity is unverified. No empirical effect or superiority is established.',
            qualifications=['Within the supplied fictional corpus only.'], server_direct_conflict_role=False))
    return rows

def test_five_UUID_judgments_preserve_material_prose_and_all_19_references():
    rows=material_rows(); before=deepcopy(rows)
    encoded=assessment_payload(rows)
    assert len(str(encoded)) <= 2500
    assert rows == before
    from app.services.landscape_budget import decode_assessment
    assert [decode_assessment(row) for row in encoded] == rows

def test_unknown_context_fields_reject_without_silent_loss():
    with pytest.raises(ValueError):
        assessment_payload([{**material_rows()[0], 'unaccounted_science':'must not disappear'}])

def test_cumulative_error_identifies_its_measurement_version():
    b=LandscapeBudget(corrected_sources(),30000)
    raw=material_rows()[0];raw['qualifications']=['Material condition.'*200]
    with pytest.raises(LandscapeBudgetExceeded) as caught:b.check_assessments([raw])
    details=caught.value.service_error(repair_attempted=False).details
    assert details.get('assessment_encoding_version')=='assessment-context-v1'
