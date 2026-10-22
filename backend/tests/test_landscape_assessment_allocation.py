"""Synthetic allocation cases: none reconstruct discarded historical responses."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re

import pytest
import httpx

from app.services.landscape_budget import LandscapeBudget, LandscapeBudgetExceeded, assessment_payload, encode_assessment
from app.services.scientific_schemas import LandscapeItemVerification, LandscapeOutput
from app.prompts.scientific import LANDSCAPE_PROMPT
from app.providers.llm import DashScopeLLMProvider
from app.services.errors import ServiceError
from test_landscape_canonical_verification import context, item_verification, landscape, service, verify, summary_verification


def budget():
    return LandscapeBudget(context(), 30000)


def items(count):
    return [deepcopy(landscape().items[0].model_dump()) for _ in range(count)]


def stored(raw, role=False):
    return {**raw, 'server_direct_conflict_role': role,
            'support_excerpts': [{'excerpt': 'excluded canonical text'}],
            'source_binding_origin': 'server', 'source_binding_schema_version': 'v'}


def padded(raw, size):
    raw = deepcopy(raw)
    raw['qualifications'] = ['x']
    extra = size - len(str(encode_assessment(raw)))
    assert extra >= 0
    raw['qualifications'][0] += 'x' * extra
    assert len(str(encode_assessment(raw))) == size
    return raw


def test_allocation_conserves_cap_and_uses_actual_prior():
    b = budget()
    a = b.assessment_allocation([], items(4))
    assert a.remaining_items == 4
    assert a.immutable_prior_chars == 2
    assert a.current_response_allowance < b.assessment_chars - 2 - a.server_overhead_chars
    assert (a.immutable_prior_chars + a.server_overhead_chars + a.list_overhead_chars
            + a.current_response_allowance + a.later_reservation_chars) == 2500
    accepted = stored(item_verification())
    later = b.assessment_allocation([accepted], items(3))
    assert later.immutable_prior_chars == len(str(assessment_payload([accepted])))
    assert later.current_response_allowance > a.current_response_allowance


def test_early_verbosity_rejected_even_if_cumulative_total_fits():
    b = budget()
    a = b.assessment_allocation([], items(4))
    raw = padded(item_verification(), a.current_response_allowance + 1)
    with pytest.raises(LandscapeBudgetExceeded) as caught:
        b.check_response('LandscapeItemVerification', raw, prior_assessments=[],
                         landscape=None, remaining_items=items(4))
    e = caught.value
    assert e.measured < 2500
    assert e.service_error(repair_attempted=False).details['required_reduction_chars'] == 1
    feedback = e.repair_instructions()
    assert 'current_response_allowance=' in feedback and 'immutable_prior_chars=2' in feedback
    assert 'only the current' in feedback and 'qualifications' in feedback
    assert raw['justification'] not in feedback


@pytest.mark.parametrize('role', [False, True])
@pytest.mark.parametrize('count', [1, 4])
def test_sequence_exact_quota_and_one_character_overflow(count, role):
    b, accepted = budget(), []
    remaining = items(count)
    for index in range(count):
        a = b.assessment_allocation(accepted, remaining[index:])
        raw = padded(item_verification(), a.current_response_allowance)
        b.response_schema(LandscapeItemVerification, prior_assessments=accepted,
                          remaining_items=remaining[index:]).model_validate(raw)
        with pytest.raises(LandscapeBudgetExceeded):
            b.check_response('LandscapeItemVerification', padded(raw, len(str(encode_assessment(raw))) + 1),
                             prior_assessments=accepted, landscape=None, remaining_items=remaining[index:])
        accepted.append(stored(raw, role))
    assert len(str(assessment_payload(accepted))) == 2500 - (1 if role else 0)


def test_infeasible_sequence_fails_before_provider():
    svc, provider, failures, events = service([])
    output = landscape()
    output.items *= 49  # Explicit impossible mechanical sequence, not historical output.
    with pytest.raises(Exception) as caught:
        verify(svc, output)
    assert getattr(caught.value, 'code', None) == 'landscape_context_budget_exceeded'
    assert provider.calls == []
    assert failures and failures[-1][2] == 1


def test_nested_strings_and_lists_have_shared_quota_guidance():
    b = budget()
    a = b.assessment_allocation([], items(4))
    schema = b.response_schema(LandscapeItemVerification, remaining_items=items(4)).model_json_schema()
    for key in ['flagged_statements', 'qualifications', 'evidence_refs']:
        assert schema['properties'][key]['items']['maxLength'] <= a.current_response_allowance
        assert schema['properties'][key]['maxItems'] <= a.current_response_allowance
    assert 'all fields together' in b.instructions('LandscapeItemVerification', remaining_items=items(4))


@pytest.mark.parametrize('support', ['supported', 'unsupported', 'insufficient_evidence'])
def test_qualified_positive_and_negative_responses_are_not_truncated(support):
    raw = item_verification(support_verdict=support,
        flagged_statements=[] if support == 'supported' else ['Empirical effect is unestablished.'],
        qualifications=['Synthetic; physical conformity unverified.', 'Across-run independence unestablished.'])
    validated = budget().response_schema(LandscapeItemVerification, remaining_items=items(4)).model_validate(raw)
    assert validated.model_dump() == raw


def test_exact_python_accounting_handles_escaping_unicode_and_server_booleans():
    raw = item_verification(qualifications=['μ→合成\x00\n\r\t\\\'"'])
    prior = [stored(raw)]
    b = budget()
    a = b.assessment_allocation(prior, items(3))
    assert a.immutable_prior_chars == len(str(assessment_payload(prior)))
    assert a.immutable_prior_chars != len(json.dumps(assessment_payload(prior), ensure_ascii=False))
    combined = [*assessment_payload(prior), encode_assessment({**raw, 'server_direct_conflict_role': False})]
    assert len(str(combined)) == a.immutable_prior_chars + len(str(encode_assessment(raw))) + a.server_overhead_chars + 2
    assert len(str([*assessment_payload(prior), encode_assessment({**raw, 'server_direct_conflict_role': True})])) == len(str(combined)) - 1
    b.check_response('LandscapeItemVerification', raw, prior_assessments=prior,
                     landscape=None, remaining_items=items(3))


def test_maximum_feasible_sequence_and_unchanged_generation_guidance():
    b = budget()
    maximum = b.response_schema(LandscapeOutput).model_json_schema()['properties']['items']['maxItems']
    assert maximum == 6  # Existing short-ID generation guidance is unchanged.
    # Encoding makes six mechanically feasible; generation guidance stays six.
    assert b.assessment_allocation([], items(maximum)).minimum_required_chars < 2500
    accepted, remaining = [], items(maximum - 1)
    for index in range(maximum - 1):
        raw = item_verification(qualifications=['Synthetic only; no measured effect.'])
        b.response_schema(LandscapeItemVerification, prior_assessments=accepted,
                          remaining_items=remaining[index:]).model_validate(raw)
        accepted.append(stored(raw))
    b.check_assessments(accepted)


def test_maximum_feasible_pipeline_keeps_all_items_through_summary_repair():
    output = landscape()
    output.items = [deepcopy(output.items[0]) for _ in range(5)]
    assessment = item_verification(qualifications=['Synthetic; no measured effect.'])
    negative = summary_verification(verdict='unsupported_major_statement',
        flagged_statements=['Summary requires the physical conformity limitation.'],
        qualifications=['Physical conformity remains unverified.'])
    repair = {'summary': 'Synthetic schedules only; physical conformity remains unverified.',
              'uncertainty': output.uncertainty}
    svc, provider, failures, _ = service([*[deepcopy(assessment) for _ in range(5)], negative,
                                         repair, summary_verification()])
    result, accepted, summaries, attempts = verify(svc, output)
    assert not failures and len(provider.calls) == 8 and attempts == 9
    assert len(result.items) == len(accepted) == 5 and len(summaries) == 2
    assert summaries[0]['flagged_statements'] == negative['flagged_statements']
    assert all(a['qualifications'] == assessment['qualifications'] for a in accepted)
    assert provider.calls[-1][2]['evidence'] == provider.calls[-3][2]['evidence']
    assert all(a['support_excerpts'][0]['excerpt'] == context()['evidence'][0]['source_text'] for a in accepted)


def test_conflict_allocation_preserves_two_distinct_references_and_comparability():
    proposal = items(1)[0]
    proposal.update(category='conflict', claim_ids=['c1', 'c2'], evidence_ids=['e1', 'e2'])
    raw = item_verification(verdict='direct_conflict', asserts_direct_conflict=True,
                           dimensions={key: 'comparable' for key in item_verification()['dimensions']},
                           evidence_refs=['e1', 'e2'], qualifications=['Only within stated comparable conditions.'])
    b = budget()
    validated = b.response_schema(LandscapeItemVerification, remaining_items=[proposal, *items(3)]).model_validate(raw)
    svc, _, _, _ = service([])
    sources = context()
    bound = svc._bind_landscape_verification(validated, {e['id']: e for e in sources['evidence']},
                                            {'e1', 'e2'}, {'c1': {'e1', 'challenge'}, 'c2': {'e2'}})
    assert bound['evidence_refs'] == ['e1', 'e2']
    assert len(bound['support_excerpts']) == 2
    assert svc._direct_conflict_comparable(validated, sources['claims'])
    with pytest.raises(ValueError):
        svc._bind_landscape_verification(LandscapeItemVerification.model_validate({**raw, 'evidence_refs': ['e1']}),
            {e['id']: e for e in sources['evidence']}, {'e1', 'e2'}, {'c1': {'e1'}, 'c2': {'e2'}})


def test_conflict_floor_reserves_distinct_source_matching_for_every_claim():
    sources = context()
    sources['claims'][0]['evidence_for_ids'] = ['e1', 'e2']
    sources['claims'][0]['evidence_against_ids'] = []
    sources['claims'][1]['evidence_for_ids'] = ['e1', 'e2']
    sources['claims'].append({'id': 'c3', 'text': 'Independent synthetic clause.', 'claim_type': 'finding',
                              'evidence_for_ids': ['e2', 'challenge'], 'evidence_against_ids': []})
    b = LandscapeBudget(sources, 30000)
    proposal = items(1)[0]
    proposal.update(category='conflict', claim_ids=['c1', 'c2', 'c3'], evidence_ids=['e1', 'e2'])
    explicit = {**proposal, 'evidence_ids': ['e1', 'e2', 'challenge']}
    assert b._assessment_floor(proposal) == b._assessment_floor(explicit)
    prior = [stored(item_verification()) for _ in range(3)]
    allocation = b.assessment_allocation(prior, [proposal])
    raw = padded(item_verification(verdict='direct_conflict', asserts_direct_conflict=True,
                    evidence_refs=['e1', 'e2', 'challenge']), allocation.current_response_allowance)
    b.response_schema(LandscapeItemVerification, prior_assessments=prior, remaining_items=[proposal]).model_validate(raw)
    assert len(str([*assessment_payload(prior), encode_assessment({**raw, 'server_direct_conflict_role': False})])) == 2500


def test_infeasible_scientific_reverification_preserves_repair_history_and_attempts():
    initial = landscape()
    repaired = initial.model_dump()
    repaired['items'][0]['text'] = 'Qualified replacement; physical conformity is required.'
    repaired['items'] *= 6  # Same count retained; verbose reverification cannot fit its allocation.
    negative = item_verification(support_verdict='unsupported', flagged_statements=['Missing physical condition.'])
    verbose = item_verification(qualifications=['Material condition.' * 200])
    svc, provider, failures, _ = service([negative, repaired, verbose, verbose])
    with pytest.raises(ServiceError) as caught:
        verify(svc, initial)
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert caught.value.details['repair_attempted'] is True
    assert len(provider.calls) == 4 and failures[-1][2] == 5


def test_structured_repair_keeps_quota_prior_and_sources_immutable():
    output = landscape()
    output.items *= 4
    quota = budget().assessment_allocation([], [i.model_dump() for i in output.items]).current_response_allowance
    verbose = padded(item_verification(), quota + 30)
    svc, provider, failed, _ = service([verbose, item_verification(), *[item_verification() for _ in range(3)], summary_verification()])
    _, accepted, _, attempts = verify(svc, output)
    assert not failed and len(accepted) == 4 and attempts == 7
    first, repair = provider.calls[:2]
    assert first[2] == repair[2] and first[1] == repair[1]
    allowances = [re.search(r'current_response_allowance=(\d+)', p[0])[1] for p in provider.calls[:2]]
    assert allowances == [str(quota)] * 2
    assert 'required_reduction_chars=30' in repair[0]
    assert verbose['justification'] not in repair[0]


def test_scientific_repair_resets_allocations_without_count_or_source_change():
    output = landscape()
    output.items = [deepcopy(output.items[0]) for _ in range(4)]
    for index, item in enumerate(output.items):
        item.text += f' Synthetic clause {index}.'
    repaired = output.model_dump()
    repaired['items'][0]['text'] = 'Physical conformity remains required; empirical effect is unestablished.'
    negative = item_verification(support_verdict='unsupported', flagged_statements=['Missing qualification.'],
                                 qualifications=['Physical conformity required.'])
    svc, provider, failed, _ = service([negative, repaired, *[item_verification() for _ in range(4)], summary_verification()])
    result, accepted, _, attempts = verify(svc, output)
    assert not failed and len(result.items) == len(accepted) == 4 and attempts == 8
    before, after = provider.calls[0], provider.calls[2]
    assert 'immutable_prior_chars=2' in before[0] and 'immutable_prior_chars=2' in after[0]
    assert before[2]['claims'] == after[2]['claims'] and before[2]['evidence'] == after[2]['evidence']
    assert provider.calls[1][2]['rejected_conflict']['flagged_statements'] == negative['flagged_statements']


def test_synthetic_seven_http200_calls_preserve_failure_order_and_count():
    """Synthetic size reconstruction only; historical individual sizes remain unknown."""
    output = landscape()
    output.items *= 4
    accepted = [stored(item_verification()) for _ in range(3)]
    b = budget()
    a = b.assessment_allocation(accepted, items(1))
    prefix = a.immutable_prior_chars + a.server_overhead_chars + a.list_overhead_chars
    initial = padded(item_verification(), 3040 - prefix)
    repaired = padded(item_verification(), 2958 - prefix)
    responses = [output.model_dump(), item_verification(), item_verification(evidence_refs=['challenge']),
                 item_verification(), item_verification(), initial, repaired]
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'model': 'qwen3.8-max', 'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(responses[len(calls) - 1])}}]})
    svc, _, failures, events = service([])
    svc.provider = DashScopeLLMProvider('synthetic-mock-key', 'qwen3.8-max', 120, 2,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    sources = context()
    claims, evidence = {'c1', 'c2'}, {'e1', 'e2', 'challenge'}
    provenance = {'c1': {'e1', 'challenge'}, 'c2': {'e2'}}
    generated, attempts = svc._generate('run', LANDSCAPE_PROMPT, LandscapeOutput, sources,
        lambda value: svc._validate_landscape_output(value, claims, evidence, provenance),
        landscape_budget=b, trace_stage='landscape_generation')
    with pytest.raises(ServiceError) as caught:
        svc._verify_landscape('run', generated, sources, claims, evidence, provenance, attempts)
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert len(calls) == failures[-1][2] == 7
    sizes = [e['measured_size'] for e in events if e.get('layer') == 'output_budget' and e.get('outcome') == 'fail']
    assert sizes == [3040, 2958]
    rounds = [e for e in events if e.get('layer') == 'provider_round']
    assert [e['provider_role'] for e in rounds] == ['initial_structured_generation', 'conflict_verification',
        'conflict_verification', 'structured_repair', 'conflict_verification', 'conflict_verification', 'structured_repair']
    assert any(e.get('validation_code') == 'landscape_support_wrong_source' for e in events)
    assert all('LandscapeSummaryVerification' not in str(c['response_format']) for c in calls)
    assert caught.value.details['current_candidate_chars'] == len(str(encode_assessment(repaired))) < 2958
    assert initial['justification'] not in json.dumps(events)


@pytest.mark.parametrize('scenario', ['normal', 'structured_repair', 'item_repair', 'summary_repair', 'both_repairs', 'maximum_both_repairs'])
def test_corrected_scope_context_measurements(monkeypatch, scenario):
    import test_landscape_budget as original
    from test_landscape_corrected_budget import corrected_sources, corrected_output
    observed = []
    original_service = original.service
    def record_service(*args):
        result = original_service(*args)
        observed.append(result[1])
        return result
    monkeypatch.setattr(original, 'service', record_service)
    monkeypatch.setattr(original, 'original_sources', corrected_sources)
    monkeypatch.setattr(original, 'adequate_output', corrected_output)
    if scenario == 'maximum_both_repairs':
        original.test_limit_sized_real_builders_complete_both_repairs_and_reverification()
    else:
        original.test_original_scope_complete_workflow_and_repairs(scenario)
    calls = observed[0].calls
    record = {'scenario': scenario, 'synthetic_deterministic_provider': True,
              'output_allowance': LandscapeBudget(corrected_sources(), 30000).output_chars,
              'call_count': len(calls), 'maximum_context_chars': max(len(str(c[2])) for c in calls),
              'calls': [{'schema': c[1], 'context_chars': len(str(c[2]))} for c in calls]}
    assert record['output_allowance'] == 4627 and record['maximum_context_chars'] <= 30000
    if os.environ.get('TRANSPORT_DIAGNOSTIC_OUTPUT'):
        (Path(os.environ['TRANSPORT_DIAGNOSTIC_OUTPUT']) / ('context-' + scenario + '.json')).write_text(
            json.dumps(record, indent=2) + '\n', encoding='utf8')
