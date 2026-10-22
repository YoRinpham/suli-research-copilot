"""Request-local output schemas, using the exact approved synthetic scope."""
import json

import httpx
import pytest
from pydantic import ValidationError

from app.prompts.scientific import LANDSCAPE_PROMPT
from app.providers.llm import DashScopeLLMProvider
from app.services.errors import ServiceError
from app.services.landscape_budget import LandscapeBudget
from app.services.scientific_schemas import LandscapeOutput, SummaryRepair
from test_landscape_corrected_budget import corrected_sources, corrected_output
from test_landscape_canonical_verification import service


def sized_output(size):
    value = corrected_output(corrected_sources()).model_dump()
    value['summary'] += 'x' * (size - len(str(value)))
    assert len(str(value)) == size
    return value


def generate(subject):
    sources = corrected_sources()
    return subject._generate('run', LANDSCAPE_PROMPT, LandscapeOutput, sources,
        lambda output: subject._validate_landscape_output(output,
            {c['id'] for c in sources['claims']}, {e['id'] for e in sources['evidence']},
            {c['id']: set(c['evidence_for_ids'] + c['evidence_against_ids']) for c in sources['claims']}),
        trace_stage='landscape_generation', landscape_budget=LandscapeBudget(sources, 30000))


@pytest.mark.parametrize('size', [4627, 4628, 5142, 5506])
def test_provider_schema_enforces_cumulative_representation(size):
    budget = LandscapeBudget(corrected_sources(), 30000)
    assert budget.output_chars == 4627
    schema = budget.response_schema(LandscapeOutput)
    value = sized_output(size)
    # Historical/human schemas remain readable, even when the new request rejects.
    LandscapeOutput.model_validate(value)
    if size <= 4627:
        assert schema.model_validate(value).model_dump() == value
    else:
        with pytest.raises(ValidationError):
            schema.model_validate(value)
    assert schema.model_json_schema()['properties']['summary']['maxLength'] <= 4627


def test_budget_repair_receives_actual_excess_without_private_text():
    subject, provider, failed, events = service([sized_output(5142), sized_output(4627)])
    output, attempts = generate(subject)
    assert attempts == 2 and not failed
    assert len(str(output.model_dump())) == 4627
    repair = provider.calls[1][0]
    assert 'measured=5142' in repair and 'allowance=4627' in repair and 'excess=515' in repair
    assert 'Reduce' in repair
    assert provider.calls[0][2] == provider.calls[1][2] == corrected_sources()
    assert 'x' * 20 not in repair and 'x' * 20 not in json.dumps(events)
    accepted = [e for e in events if e.get('layer') == 'output_budget' and e.get('outcome') == 'pass']
    assert len(accepted) == 1 and accepted[0]['generated_output_chars'] == 4627
    assert accepted[0]['context_chars'] == 25522 and accepted[0]['repair_attempted']


def test_real_adapter_budget_repair_keeps_transport_counts_and_specific_error():
    requests = []
    responses = [sized_output(5142), sized_output(5506)]
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'model': 'qwen3.8-max', 'choices': [
            {'finish_reason': 'stop', 'message': {'content': json.dumps(responses.pop(0))}}]})
    subject, _, failed, events = service([])
    subject.provider = DashScopeLLMProvider('mock-key', 'qwen3.8-max', 120, 2,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)), reasoning_effort='low')
    with pytest.raises(ServiceError) as caught:
        generate(subject)
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert caught.value.details['measured_size'] == 5506
    assert caught.value.details['size_budget'] == 4627
    assert failed[-1][-1] == 2 and len(requests) == 2
    assert all(r['response_format']['json_schema']['schema']['properties']['summary']['maxLength'] <= 4627
               for r in requests)
    assert 'measured=5142' in requests[1]['messages'][0]['content']
    assert all(r['reasoning_effort'] == 'low' for r in requests)
    assert not any(e.get('stage', '').startswith('conflict_verification') for e in events)
    rounds = [e for e in events if e.get('layer') == 'provider_round']
    assert len(rounds) == 2 and sum(e['provider_attempts'] for e in rounds) == 2


def test_summary_schema_checks_whole_candidate_and_exact_remaining_allowance():
    budget = LandscapeBudget(corrected_sources(), 30000)
    value = sized_output(4627)
    schema = budget.response_schema(SummaryRepair, landscape=value)
    response = {key: value[key] for key in ('summary', 'uncertainty')}
    schema.model_validate(response)
    response['uncertainty'] += 'x'
    with pytest.raises(ValidationError):
        schema.model_validate(response)


def test_escape_expansion_cannot_bypass_provider_schema():
    budget = LandscapeBudget(corrected_sources(), 30000)
    value = corrected_output(corrected_sources()).model_dump()
    value['uncertainty'] = '\x00' * 400
    assert len(value['uncertainty']) < 2000
    with pytest.raises(ValidationError):
        budget.response_schema(LandscapeOutput).model_validate(value)


def test_bounded_item_count_preserves_historical_readability_and_service_boundary():
    budget = LandscapeBudget(corrected_sources(), 30000)
    maximum = budget.response_schema(LandscapeOutput).model_json_schema()['properties']['items']['maxItems']
    assert maximum == 5  # Exact UUID authority + minimum independent-assessment footprint.
    value = {'summary': 'Synthetic; unresolved.', 'uncertainty': 'No observations.', 'items': [
        {'category': 'method', 'text': 'Command only.', 'uncertainty': 'Unverified.',
         'claim_ids': [corrected_sources()['claims'][0]['id']],
         'evidence_ids': [corrected_sources()['claims'][0]['evidence_for_ids'][0]]}] * (maximum + 1)}
    assert len(str(value)) < budget.output_chars
    LandscapeOutput.model_validate(value)
    subject, provider, failed, _ = service([value, value])
    with pytest.raises(ServiceError) as caught:
        generate(subject)
    assert caught.value.code == 'structured_output_invalid'
    assert len(provider.calls) == 2 and failed[-1][-1] == 2


def test_transport_retry_budget_error_counts_actual_attempts():
    calls = []
    responses = [httpx.Response(503), httpx.Response(200, json={
        'model': 'qwen3.8-max', 'choices': [{'finish_reason': 'stop',
        'message': {'content': json.dumps(sized_output(5506))}}]})]
    def respond(request):
        calls.append(request)
        return responses.pop(0)
    subject, _, failed, _ = service([])
    subject.provider = DashScopeLLMProvider('mock-key', 'qwen3.8-max', 120, 2,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    sources = corrected_sources()
    with pytest.raises(ServiceError) as caught:
        subject._generate('run', LANDSCAPE_PROMPT, LandscapeOutput, sources, lambda output: None,
                          max_calls=1, landscape_budget=LandscapeBudget(sources, 30000),
                          trace_stage='landscape_generation')
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert len(calls) == 2 and failed[-1][-1] == 2


def test_summary_nondictionary_is_a_structure_failure():
    sources = corrected_sources()
    subject, _, failed, _ = service([[], []])
    with pytest.raises(ServiceError) as caught:
        subject._generate('run', LANDSCAPE_PROMPT, SummaryRepair, sources, lambda output: None,
            landscape_budget=LandscapeBudget(sources, 30000), landscape_output=corrected_output(sources),
            trace_stage='summary_verification.repair')
    assert caught.value.code == 'structured_output_invalid' and failed[-1][-1] == 2
