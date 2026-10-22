"""Budget failures use official Run persistence in an isolated PostgreSQL DB."""

import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.database import get_engine
from app.persistence.models import Evidence, GenerationRun, LandscapeItem, ResearchLandscape
from test_m3_database import live_m3
from test_m3_semantic_database import two_claims
from test_landscape_canonical_database import proposal


pytestmark = pytest.mark.skipif(os.getenv('RUN_DB_INTEGRATION') != '1', reason='Requires isolated PostgreSQL')


def test_budget_failure_persists_only_failed_run(live_m3):
    client, provider, _ = live_m3
    project, claims, evidence = two_claims(live_m3, 'Fictional schedule A.', 'Fictional schedule B.')
    oversized = proposal(claims, evidence)
    oversized['items'] = [{**oversized['items'][0], 'text': 'x' * 4000, 'uncertainty': 'y' * 2000}] * 3
    provider.responses = [oversized, oversized]
    response = client.post(f'/api/v1/projects/{project}/landscapes/generate', json={
        'claim_ids': claims, 'expected_question_revision': 1})
    assert response.status_code == 422, response.text
    error = response.json()['error']
    assert error['code'] == 'landscape_context_budget_exceeded'
    assert error['details']['repair_attempted'] is True
    assert error['details']['persistence_occurred'] is False
    with Session(get_engine()) as session:
        run = session.get(GenerationRun, error['details']['generation_run_id'])
        assert run.status == 'failed' and run.attempt_count == 2
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project)))
        assert not list(session.scalars(select(LandscapeItem).join(ResearchLandscape).where(ResearchLandscape.project_id == project)))


def test_historical_large_content_and_old_run_contract_remain_readable(live_m3):
    client, provider, _ = live_m3
    project, claims, evidence = two_claims(live_m3, 'Fictional schedule A.', 'Fictional schedule B.')
    provider.responses = [proposal(claims, evidence)]
    response = client.post(f'/api/v1/projects/{project}/landscapes/generate', json={
        'claim_ids': claims, 'expected_question_revision': 1})
    assert response.status_code == 201, response.text
    landscape_id = response.json()['landscape']['id']
    run_id = response.json()['generation_run_id']
    with Session(get_engine()) as session:
        # An old persisted record may exceed today's generation-only contract.
        session.get(ResearchLandscape, landscape_id).summary = 'h' * 8000
        session.get(GenerationRun, run_id).schema_version = 'm3-landscape-v4'
        session.get(GenerationRun, run_id).prompt_version = 'm3-v5'
        session.commit()
    read = client.get(f'/api/v1/projects/{project}/landscapes/{landscape_id}')
    assert read.status_code == 200 and read.json()['summary'] == 'h' * 8000
    with Session(get_engine()) as session:
        assert session.get(GenerationRun, run_id).schema_version == 'm3-landscape-v4'


def test_infeasible_source_preflight_creates_no_run(live_m3):
    client, provider, _ = live_m3
    project, claims, evidence = two_claims(live_m3, 'Fictional A.', 'Fictional B.')
    with Session(get_engine()) as session:
        for eid in evidence:
            row = session.get(Evidence, eid)
            row.source_text = row.chunk.text = 'x' * 14000
        session.commit()
    provider.contexts.clear()
    response = client.post(f'/api/v1/projects/{project}/landscapes/generate', json={
        'claim_ids': claims, 'expected_question_revision': 1})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'landscape_context_budget_exceeded'
    assert not provider.contexts
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(GenerationRun).where(GenerationRun.project_id == project, GenerationRun.stage == 'landscape')))
