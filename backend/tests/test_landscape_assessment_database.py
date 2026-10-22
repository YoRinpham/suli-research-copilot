"""Disposable database proofs for cumulative quota exhaustion and preflight."""
from copy import deepcopy
import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.database import get_engine
from app.persistence.models import Claim, Evidence, GenerationRun, LandscapeItem, ResearchLandscape
from app.services.landscape_budget import LandscapeBudget
from app.services.scientific_schemas import LandscapeOutput
from test_landscape_corrected_database import corrected_live
from test_m3_database import live_m3
from test_landscape_canonical_verification import item_verification
from test_landscape_assessment_allocation import padded
from test_landscape_corrected_budget import corrected_output

pytestmark = pytest.mark.skipif(os.getenv('RUN_DB_INTEGRATION') != '1', reason='Requires isolated PostgreSQL')


@pytest.mark.parametrize('mode', ['two_exhausted_rounds', 'infeasible_before_verification'])
def test_assessment_failure_creates_failed_run_and_no_partial_objects(corrected_live, mode):
    client, provider, project, context = corrected_live
    output = corrected_output(context).model_dump()
    if mode == 'two_exhausted_rounds':
        allocation = LandscapeBudget(context, 30000).assessment_allocation([], output['items'])
        raw = padded(item_verification(evidence_refs=output['items'][0]['evidence_ids']),
                     allocation.current_response_allowance + 1)
        provider.verification_responses = [raw, deepcopy(raw)]
        expected_attempts = 3
    else:
        # Explicit overlapping reference-heavy synthetic layout, not discarded
        # production items. Full approved UUIDs and Claim relationships remain.
        output['items'] = []
        for index in range(5):
            chosen = [context['claims'][(index*4+j)%len(context['claims'])] for j in range(9)]
            output['items'].append({'category': 'method', 'text': 'Fictional conditions remain unverified.',
                'uncertainty': 'No measured results.', 'claim_ids': [c['id'] for c in chosen],
                'evidence_ids': sorted({e for c in chosen for e in c['evidence_for_ids'] + c['evidence_against_ids']})})
        output['summary'] = 'Fictional corpus only.'
        LandscapeBudget(context, 30000).response_schema(LandscapeOutput).model_validate(output)
        provider.responses = [output]
        expected_attempts = 1
    response = client.post(f'/api/v1/projects/{project}/landscapes/generate', json={
        'claim_ids': [c['id'] for c in context['claims']], 'expected_question_revision': 1})
    assert response.status_code == 422, response.text
    error = response.json()['error']
    assert error['code'] == 'landscape_context_budget_exceeded'
    assert error['details']['persistence_occurred'] is False
    assert len(provider.contexts) == expected_attempts
    with Session(get_engine()) as session:
        run = session.get(GenerationRun, error['details']['generation_run_id'])
        assert run.status == 'failed' and run.error_type == error['code']
        assert run.attempt_count == expected_attempts
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project)))
        assert not list(session.scalars(select(LandscapeItem).join(ResearchLandscape).where(ResearchLandscape.project_id == project)))
        assert all(session.get(Claim, c['id']).selection_status == 'selected' for c in context['claims'])
        assert all(session.get(Evidence, e['id']).selection_status == 'selected' for e in context['evidence'])
