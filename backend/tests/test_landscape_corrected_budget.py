"""Exact human-authorized synthetic 18-Claim scope; deterministic providers only."""
import copy
import json
from pathlib import Path
import pytest
import test_landscape_budget as original
from app.services.landscape_budget import LandscapeBudget
from app.services.errors import ServiceError

C60 = 'fb2b9f13-d30b-4b79-9f16-e9f9f8bb1fcc'
E60 = '94ad89c6-c3ef-4f33-bf18-cdbd74159f8b'

def corrected_sources():
    return json.loads((Path(__file__).parent/'fixtures/landscape_budget_corrected_scope.json').read_text(encoding='utf8'))

_base_output = original.adequate_output

def corrected_output(sources):
    output = _base_output(sources)
    item = output.items[2]
    item.claim_ids.append(C60)
    item.evidence_ids = sorted([*item.evidence_ids, E60])
    item.text += (' Reused apparatus, common sessions and shared preparations may introduce across-run dependence; '
                  'within-run samples are not independent replicates.')
    # Audit this qualification with the metric item's exact C58/E58 source union.
    item.text = item.text.replace('No across-run aggregation is defined; ', '')
    output.items[1].text += ' No across-run aggregation is defined.'
    output.items[0].text = output.items[0].text.replace('Physical transitions must', 'A physical transitions must')
    output.items[0].text += ' B physical conformity timing is outside this source scope.'
    for item in output.items:
        item.uncertainty = 'Fictional specification; no observations.'
    output.uncertainty = 'Conformity unverified; independence unestablished.'
    return output

def test_corrected_scope_budget_preserves_every_source_value():
    sources = corrected_sources()
    prior = original.original_sources()
    assert {c['id'] for c in sources['claims']} == {c['id'] for c in prior['claims']} | {C60}
    assert {e['id'] for e in sources['evidence']} == {e for c in sources['claims'] for e in c['evidence_for_ids'] + c['evidence_against_ids']}
    c60 = next(c for c in sources['claims'] if c['id'] == C60)
    assert c60['human_grounding_status'] == 'supported' and c60['evidence_for_ids'] == [E60]
    e60 = next(e for e in sources['evidence'] if e['id'] == E60)
    assert e60['page_number'] == 10 and e60['paper_id'] == '1483163c-2d2f-4d0f-be72-dcb777b95fd5'
    budget = LandscapeBudget(sources, 30000)
    assert budget.output_chars >= 4000
    unpacked = [{ 'id':cid,'text':row['text'],'claim_type':row['type'],
                  'human_grounding_status':row['grounding'],'uncertainty':row['uncertainty'],
                  'evidence_for_ids':row['supports'],'evidence_against_ids':row['challenges']}
                 for cid,row in budget.summary_sources['claims'].items()]
    assert unpacked == sources['claims']
    assert budget.summary_sources['evidence'] == {e['id']:e['source_text'] for e in sources['evidence']}
    output = corrected_output(sources)
    budget.check_output(output.model_dump())
    assert {c for item in output.items for c in item.claim_ids} == {c['id'] for c in sources['claims']}
    assert {e for item in output.items for e in item.evidence_ids} == set(budget.summary_sources['evidence'])

@pytest.mark.parametrize('scenario', ['normal','structured_repair','item_repair','summary_repair','both_repairs','maximum_both_repairs'])
def test_corrected_scope_real_builders_and_all_repairs(monkeypatch, scenario):
    monkeypatch.setattr(original,'original_sources',corrected_sources)
    monkeypatch.setattr(original,'adequate_output',corrected_output)
    if scenario == 'maximum_both_repairs':
        original.test_limit_sized_real_builders_complete_both_repairs_and_reverification()
    else:
        original.test_original_scope_complete_workflow_and_repairs(scenario)


def test_output_allowance_reserves_full_union_item_verification():
    sources = copy.deepcopy(original.original_sources())
    # Increase only metadata omitted by summary sources; source text stays exact.
    for row in sources['evidence']:
        row['paper_title'] = (row['paper_title'] or '') + ' retained metadata' * 5
    budget = LandscapeBudget(sources,30000)
    item_base = len(str({'item_text':'','item_category':'conflict','item_uncertainty':'',
                         'claims':sources['claims'],'evidence':sources['evidence']}))
    assert 4000 <= budget.output_chars <= 30000-item_base
    assert budget.output_chars < 30000-budget.source_chars-budget.assessment_chars-budget.critique_chars



@pytest.mark.parametrize('overflow', ['output', 'assessment'])
def test_corrected_schema_valid_oversize_is_explicitly_rejected(overflow):
    from app.prompts.scientific import LANDSCAPE_PROMPT
    from app.services.scientific_schemas import LandscapeOutput
    from test_landscape_canonical_verification import service, item_verification
    sources = corrected_sources()
    budget = LandscapeBudget(sources, 30000)
    output = corrected_output(sources)
    ids = {c['id'] for c in sources['claims']}
    evidence = {e['id'] for e in sources['evidence']}
    provenance = {c['id']: set(c['evidence_for_ids'] + c['evidence_against_ids']) for c in sources['claims']}
    if overflow == 'output':
        oversized = output.model_dump()
        oversized['summary'] += 'x' * (budget.output_chars - len(str(oversized)) + 1)
        LandscapeOutput.model_validate(oversized)
        subject, provider, failed, _ = service([oversized, oversized])
        with pytest.raises(ServiceError) as caught:
            subject._generate('run', LANDSCAPE_PROMPT, LandscapeOutput, sources,
                lambda value: subject._validate_landscape_output(value, ids, evidence, provenance),
                landscape_budget=budget, trace_stage='landscape_generation')
    else:
        response = item_verification(evidence_refs=output.items[0].evidence_ids,
                                     qualifications=['Required conditions.'] * 200)
        subject, provider, failed, _ = service([response, response])
        with pytest.raises(ServiceError) as caught:
            subject._verify_landscape('run', output, sources, ids, evidence, provenance, 1)
    assert caught.value.code == 'landscape_context_budget_exceeded'
    assert caught.value.details['repair_attempted'] and caught.value.details['persistence_occurred'] is False
    assert len(provider.calls) == 2 and failed[-1][1] == caught.value.code
