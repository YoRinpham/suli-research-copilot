"""Post-M6 cumulative context contract; no live scientific judgments."""

import copy
import json
import re
from pathlib import Path

import pytest
import httpx

from app.prompts.scientific import LANDSCAPE_PROMPT, LANDSCAPE_BUDGET_CONTRACT
from app.services.errors import ServiceError
from app.services.landscape_budget import LandscapeBudget, LandscapeBudgetExceeded, assessment_payload, encode_assessment
from app.services.scientific_schemas import LandscapeOutput
from app.providers.llm import DashScopeLLMProvider
from test_landscape_canonical_verification import (
    context, item_verification, landscape, service, summary_verification, verify,
)


def test_budget_reserves_sources_assessments_and_repairs():
    sources = context()
    budget = LandscapeBudget(sources, 30000)
    assert budget.output_chars + budget.source_chars + budget.assessment_chars + budget.critique_chars <= 30000
    assert set(budget.summary_sources['claims']) == {c['id'] for c in sources['claims']}
    assert budget.summary_sources['evidence'] == {e['id']: e['source_text'] for e in sources['evidence']}


@pytest.mark.parametrize('field', ['text', 'uncertainty'])
def test_generated_representation_growth_is_rejected(field):
    sources = context()
    budget = LandscapeBudget(sources, 30000)
    output = landscape().model_dump()
    output['items'][0][field] = '\x00' * budget.output_chars
    with pytest.raises(LandscapeBudgetExceeded) as caught:
        budget.check_output(output)
    assert caught.value.measured > caught.value.budget


@pytest.mark.parametrize('field', ['qualifications', 'flagged_statements', 'justification'])
def test_verifier_overflow_has_sanitized_specific_failure(field):
    response = item_verification(**{field: ['sensitive' * 500] if field != 'justification' else '\x00' * 900})
    subject, provider, failed, events = service([response, response])
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert caught.value.details['repair_attempted'] is True
    assert caught.value.details['persistence_occurred'] is False
    assert 'sensitive' not in json.dumps(events) + str(caught.value.details)
    assert len(provider.calls) == 2 and failed[-1][1] == caught.value.code


def test_cumulative_assessments_cannot_bypass_contract():
    budget = LandscapeBudget(context(), 30000)
    response = item_verification(justification='x' * 600)
    budget.check_assessments([response])
    with pytest.raises(LandscapeBudgetExceeded):
        budget.check_assessments([response] * 4)


@pytest.mark.parametrize('invalid_field', ['item_count', 'summary', 'uncertainty', 'item_text'])
def test_response_shape_limits_cannot_bypass_generation_budget(invalid_field):
    output = landscape().model_dump()
    if invalid_field == 'item_count':
        output['items'] *= 51
    elif invalid_field == 'item_text':
        output['items'][0]['text'] = 'x' * 4001
    else:
        output[invalid_field] = 'x' * (8001 if invalid_field == 'summary' else 2001)
    subject, provider, failed, _ = service([output, output])
    with pytest.raises(ServiceError) as caught:
        subject._generate('run', LANDSCAPE_PROMPT, LandscapeOutput, context(), lambda result: None,
                          landscape_budget=LandscapeBudget(context(), 30000), trace_stage='landscape_generation')
    assert caught.value.code in {'structured_output_invalid', 'landscape_context_budget_exceeded'}
    assert len(provider.calls) == 2 and failed[-1][-1] == 2


def test_qualification_collection_overflow_is_rejected_before_next_stage():
    response = item_verification(qualifications=['Required condition.'] * 500)
    subject, provider, failed, _ = service([response, response])
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert len(provider.calls) == 2
    assert all(call[1] == 'LandscapeItemVerification' for call in provider.calls)


def test_transmitted_budget_template_and_policy_are_part_of_prompt_hash():
    budget = LandscapeBudget(context(), 30000)
    assert LANDSCAPE_PROMPT.bundle['cumulative_budget_contract'] == LANDSCAPE_BUDGET_CONTRACT
    assert budget.instructions() == LANDSCAPE_BUDGET_CONTRACT.format(
        output_chars=budget.output_chars, assessment_chars=budget.assessment_chars, critique_chars=budget.critique_chars,
        response_allowance=budget.output_chars)
    assert 'assessment=2500;critique=1500;minimum_output=4000;maximum_output=12000' == LANDSCAPE_PROMPT.bundle['cumulative_budget_policy']


def test_infeasible_sources_are_rejected_before_dispatch():
    sources = context()
    sources['evidence'][0]['source_text'] = 'x' * 27000
    with pytest.raises(ServiceError) as caught:
        LandscapeBudget(sources, 30000)
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert caught.value.details['repair_attempted'] is False
    assert caught.value.details['generated_output_chars'] == 0


def test_context_error_separates_actual_generated_contribution():
    budget = LandscapeBudget(context(), 30000)
    oversized = {**context(), 'summary': 'x' * 30000}
    with pytest.raises(LandscapeBudgetExceeded) as caught:
        budget.check_context('summary_verification', oversized)
    assert caught.value.generated_chars == len(str({'summary': oversized['summary']})) - 2
    assert caught.value.generated_chars < caught.value.measured


def test_later_verifier_and_structured_repair_receive_remaining_allowance():
    sources = context()
    initial = landscape()
    initial.items *= 2
    first = item_verification(justification='a' * 900, qualifications=['b' * 500])
    subject, provider, failed, _ = service([first, item_verification(), item_verification(), summary_verification()])
    verify(subject, initial, sources)
    assert not failed and len(provider.calls) == 4
    first_prompt, repaired_prompt, second_prompt = [call[0] for call in provider.calls[:3]]
    allowances = [int(re.search(r'response allowance is <= (\d+)', p)[1])
                  for p in (first_prompt, repaired_prompt, second_prompt)]
    assert 0 < allowances[0] < allowances[2] < 2500
    assert allowances[0] == allowances[1]
    # The verbose first response is rejected; its structured repair has the same quota.
    budget = LandscapeBudget(sources, 30000)
    budget.check_assessments([first, item_verification()])


@pytest.mark.parametrize('field', ['qualifications', 'justification'])
def test_real_adapter_rejects_schema_valid_representation_overflow(field):
    calls = []
    value = item_verification(**{field: ['Sensitive' * 500] if field == 'qualifications' else '\x00' * 900})
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'model': 'qwen3.8-max', 'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(value)}}]})
    subject, _, failed, events = service([])
    subject.provider = DashScopeLLMProvider('mock-key', 'qwen3.8-max', 60, 2,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert len(calls) == 2 and failed[-1][-1] == 3
    assert 'Sensitive' not in json.dumps(events)


def test_summary_repair_cannot_expand_past_remaining_output_budget():
    negative = summary_verification(verdict='unsupported_major_statement', flagged_statements=['Unsupported assertion'])
    subject, provider, failed, _ = service([item_verification(), negative,
        {'summary': '\x00' * 7999, 'uncertainty': 'Unresolved.'}])
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert caught.value.details['repair_attempted'] is True
    assert len(provider.calls) == 3


def test_original_scope_has_nontrivial_budget_and_exact_sources():
    sources = original_sources()
    budget = LandscapeBudget(sources, 30000)
    assert len(sources['claims']) == 17 and len(sources['evidence']) == 18
    assert budget.output_chars >= 4000
    assert len(str(sources)) == 24346


def original_sources():
    path = Path(__file__).parent / 'fixtures/landscape_budget_original_scope.json'
    return json.loads(path.read_text(encoding='utf8'))


def adequate_output(sources):
    groups = [
        (['082a9b10', '0e656b6d', '331741e8', '45d27dff', 'dd6ad5be', 'ed274f3d'], 'method',
         'One fresh aliquot in one uninterrupted five-minute run is the unit; within-run samples are repeated measurements. '
         'A commands continuous clockwise 30 rpm from 0 to 300 s; B commands clockwise 60 rpm during [0,30), '
         '[60,90), [120,150), [180,210), [240,270), stopping otherwise. The immersed paddle stays in place. '
         'Physical transitions must reach target within +/-2 rpm, or stationary, within one second; command timing is separate. '
         'Feedback must distinguish measured, estimated and commanded speed. Logs and ratings do not establish actual '
         'speed, transitions or revolutions; nominal rotation totals imply no equal work or energy. The methods also differ '
         'in instantaneous speed and uninterrupted duration: intermittency alone cannot be isolated.'),
        (['047581b3', '155c6630', '54ae9869', '5ba819de', '5d0918a1', 'a60fb791', 'da6dcb29', 'e0dbd49e'], 'metric',
         'S=T0-T300 uses recorded 0.1 C temperatures without transformation, clipping or averaging. T0 is 44.8-45.2 C, '
         'recorded within ten seconds of bath removal with stationary paddle, defining zero; valid-score bounds T0-60 to T0 '
         'are validity limits, not predictions. T300 is recorded within +/-1 s, before A stops and with B stationary. '
         'Invalid, missing, nonfinite, out-of-range or mistimed endpoints give no score; '
         'no substitution, interpolation or imputation. Probe tip: 15+/-1 mm above base, 20+/-1 mm from shaft; resolution '
         '0.1 C, rated accuracy +/-0.2 C over 20-50 C, 90% response <=5 s. Reference: resolution <=0.1 C and documented '
         'accuracy +/-0.1 C over 20-50 C; resolution alone is insufficient. Every paired check at 21-23 C and 44-46 C '
         'must agree within 0.2 C; three pairs span ten seconds, each reference stays in band and each probe/reference '
         'range is <=0.1 C. Agreement is limited to check conditions, '
         'not absolute accuracy, stirring response, full range or repeated-run reliability. Lag, calibration, timing, vessel '
         'history, moisture, ambient drift and water loss may affect outcomes. Score, instrument eligibility and motor conformity are distinct.'),
        (['75c19471', 'f2600337', 'f6ed05bc'], 'finding',
         'All settings are proposed design choices in a fictional, nonempirical document, not discoveries or observed values. '
         'The score-distribution comparison and repeatability question remain unresolved within this corpus: no preferred '
         'direction, significance, superiority or global novelty. No across-run aggregation is defined; independence of runs is not established.'),
    ]
    items = []
    for prefixes, category, text in groups:
        chosen = [c for c in sources['claims'] if c['id'][:8] in prefixes]
        items.append({'category': category, 'text': text,
                      'uncertainty': 'Procedural specifications only; preserve source conditions.',
                      'claim_ids': [c['id'] for c in chosen],
                      'evidence_ids': sorted({e for c in chosen for e in c['evidence_for_ids'] + c['evidence_against_ids']})})
    return LandscapeOutput.model_validate({'summary': 'Fictional protocol: commanded stirring schedules and S=T0-T300. No measured effect; comparison unresolved.',
                                          'uncertainty': 'Validity and motor conformity unverified; run independence not established.',
                                          'items': items})


@pytest.mark.parametrize('scenario', ['normal', 'structured_repair', 'item_repair', 'summary_repair', 'both_repairs'])
def test_original_scope_complete_workflow_and_repairs(scenario):
    sources = original_sources()
    budget = LandscapeBudget(sources, 30000)
    output = adequate_output(sources)
    budget.check_output(output.model_dump())
    assert {c for item in output.items for c in item.claim_ids} == {c['id'] for c in sources['claims']}
    assert {e for item in output.items for e in item.evidence_ids} == {e['id'] for e in sources['evidence']}
    assessments = [item_verification(evidence_refs=item.evidence_ids,
                                    justification='Procedural scope only; no empirical effects.',
                                    qualifications=['Source conditions and unresolved status retained.']) for item in output.items]
    summary = summary_verification(evidence_refs=[sources['evidence'][0]['id']])
    responses = [output.model_dump()]
    if scenario == 'structured_repair':
        invalid = output.model_dump()
        invalid['items'][0]['claim_ids'][0] = 'foreign-source'
        responses.insert(0, invalid)
    if scenario in ('item_repair', 'both_repairs'):
        repaired = output.model_copy(deep=True)
        repaired.items[0].text += ' Actual adherence is not demonstrated.'
        responses += [item_verification(support_verdict='unsupported', evidence_refs=output.items[0].evidence_ids,
            flagged_statements=['Motor conformity must remain qualified.']), repaired.model_dump()]
        output = repaired
    responses += assessments
    if scenario in ('summary_repair', 'both_repairs'):
        responses += [summary_verification(verdict='unsupported_major_statement',
                         evidence_refs=[sources['evidence'][0]['id']], flagged_statements=['Retain unresolved status.']),
                      {'summary': output.summary + ' Repeatability is unresolved.', 'uncertainty': output.uncertainty}]
    responses += [summary]
    subject, provider, failed, events = service(responses)
    claims = {c['id'] for c in sources['claims']}
    evidence = {e['id'] for e in sources['evidence']}
    provenance = {c['id']: set(c['evidence_for_ids'] + c['evidence_against_ids']) for c in sources['claims']}
    generated, attempts = subject._generate('run', LANDSCAPE_PROMPT, LandscapeOutput, sources,
        lambda result: subject._validate_landscape_output(result, claims, evidence, provenance),
        landscape_budget=budget, trace_stage='landscape_generation')
    result, items, summaries, attempts = subject._verify_landscape('run', generated, sources, claims, evidence, provenance, attempts)
    assert not failed and attempts == len(provider.calls)
    assert all(len(str(call[2])) <= 30000 for call in provider.calls)
    canonical = {e['id']: e for e in sources['evidence']}
    for assessment in items + summaries:
        for passage in assessment['support_excerpts']:
            assert passage['excerpt'] == canonical[passage['evidence_id']]['source_text']
            assert passage['page_number'] == canonical[passage['evidence_id']]['page_number']
    # Every material summary proposition retains access to full original source text.
    summary_contexts = [call[2] for call in provider.calls if call[1] == 'LandscapeSummaryVerification']
    assert all(call['evidence'] ==
               {e['id']: e['source_text'] for e in sources['evidence']} for call in summary_contexts)


def test_adverse_maximum_accepted_payloads_reserve_repair_and_escaping():
    sources = original_sources()
    budget = LandscapeBudget(sources, 30000)
    output = adequate_output(sources).model_dump()
    remaining = budget.output_chars - len(str(output))
    output['summary'] += ' ' * remaining
    budget.check_output(output)
    assessments = [item_verification(qualifications=['x' * (budget.assessment_chars - 25)])]
    while len(str(assessment_payload(assessments))) > budget.assessment_chars:
        assessments[0]['qualifications'][0] = assessments[0]['qualifications'][0][:-1]
    budget.check_assessments(assessments)
    critique = {'flagged_statements': ['\x00' * 100], 'qualifications': ['Source limits.']}
    budget.check_critique(critique)
    built = {**budget.summary_sources, 'summary': output['summary'], 'uncertainty': output['uncertainty'],
             'accepted_items': output['items'], 'item_assessments': assessment_payload(assessments), **critique}
    budget.check_context('summary_verification.repair', built)
    assert len(str(built)) <= 30000
    with pytest.raises(LandscapeBudgetExceeded):
        budget.check_output({**output, 'summary': output['summary'] + '\x00'})


def test_limit_sized_real_builders_complete_both_repairs_and_reverification():
    sources = original_sources()
    budget = LandscapeBudget(sources, 30000)
    output = adequate_output(sources).model_dump()
    output['summary'] += ' ' * (budget.output_chars - len(str(output)) - 1) + '.'
    assert len(str(output)) == budget.output_chars
    first = output['items'][0]
    rejected = item_verification(support_verdict='unsupported', evidence_refs=first['evidence_ids'],
                                 flagged_statements=['Retain limits.'])
    critique = {'rejected_conflict': {'text': first['text'], 'category': first['category'],
        'verdict': rejected['verdict'], 'support_verdict': rejected['support_verdict'],
        'flagged_statements': rejected['flagged_statements'], 'qualifications': rejected['qualifications'],
        'asserts_direct_conflict': False, 'comparison_dimensions': rejected['dimensions']}}
    current_quota = budget.assessment_allocation([], output['items']).current_response_allowance
    # The separate verbose scientific-repair critique still has its own 1500
    # boundary. A larger encoded quota does not authorize exceeding that cap.
    padding = min(current_quota - len(str(encode_assessment(rejected))),
                  budget.critique_chars - len(str(critique)))
    assert padding > 0
    rejected['flagged_statements'][0] += ' ' * (padding - 1) + '.'
    assert len(str(encode_assessment(rejected))) <= current_quota
    assert len(str(critique)) == budget.critique_chars
    repaired = copy.deepcopy(output)
    repaired['items'][0]['text'] += ' Motor adherence remains unverified.'
    repaired['summary'] = adequate_output(sources).summary
    repaired['summary'] += ' ' * (budget.output_chars - len(str(repaired)) - 1) + '.'
    assert len(str(repaired)) == budget.output_chars
    assessments = [item_verification(evidence_refs=item['evidence_ids'],
        justification='Mock support judgment for bounded procedural content.',
        qualifications=['Synthetic only; all material conditions remain required.']) for item in repaired['items']]
    stored_assessments = []
    for index, assessment in enumerate(assessments):
        quota = budget.assessment_allocation(stored_assessments, repaired['items'][index:]).current_response_allowance
        padding = quota - len(str(encode_assessment(assessment)))
        assert padding > 0
        assessment['qualifications'][0] += ' ' * (padding - 1) + '.'
        stored_assessments.append({**assessment, 'server_direct_conflict_role': False})
    assert len(str(assessment_payload([{**a, 'server_direct_conflict_role': False} for a in assessments]))) == budget.assessment_chars
    summary_negative = summary_verification(verdict='unsupported_major_statement',
        evidence_refs=[sources['evidence'][0]['id']], flagged_statements=['Restore qualification.'])
    summary_negative['flagged_statements'][0] += ' ' * (budget.critique_chars - len(str(summary_negative)) - 1) + '.'
    assert len(str(summary_negative)) == budget.critique_chars
    summary_repair = {'summary': repaired['summary'], 'uncertainty': repaired['uncertainty']}
    subject, provider, failed, _ = service([output, rejected, repaired, *assessments,
        summary_negative, summary_repair, summary_verification(evidence_refs=[sources['evidence'][0]['id']])])
    claims = {c['id'] for c in sources['claims']}
    evidence = {e['id'] for e in sources['evidence']}
    provenance = {c['id']: set(c['evidence_for_ids'] + c['evidence_against_ids']) for c in sources['claims']}
    generated, attempts = subject._generate('run', LANDSCAPE_PROMPT, LandscapeOutput, sources,
        lambda value: subject._validate_landscape_output(value, claims, evidence, provenance), landscape_budget=budget)
    result, items, summaries, attempts = subject._verify_landscape('run', generated, sources, claims, evidence, provenance, attempts)
    assert not failed and len(provider.calls) == attempts == 9
    assert len(str(result.model_dump())) == budget.output_chars
    assert all(len(str(call[2])) <= 30000 for call in provider.calls)
    assert [call[1] for call in provider.calls][-3:] == ['LandscapeSummaryVerification', 'SummaryRepair', 'LandscapeSummaryVerification']
