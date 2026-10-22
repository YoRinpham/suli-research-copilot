"""Permanent figure deletion is scoped to one artifact and its controlled file."""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import get_settings
from app.services.errors import ServiceError
from app.services.m5 import M5Service
from app.services.m5_storage import M5Storage


def test_delete_route_passes_exact_project_figure_and_revision(monkeypatch):
    from app.api import m5
    from app.main import create_app
    from fastapi.testclient import TestClient

    calls = []
    def delete(*args):
        calls.append(args)
        return {'deleted_figure_id': args[1]}
    monkeypatch.setattr(m5, 'service', lambda request: SimpleNamespace(delete_figure=delete))
    with TestClient(create_app(get_settings())) as client:
        response = client.post('/api/v1/projects/project/figures/figure/delete',
                               json={'expected_question_revision': 2})
    assert response.status_code == 200
    assert calls == [('project', 'figure', 2)]


class FigureSession:
    def __init__(self, artifact):
        self.artifact = artifact
        self.deleted = []
        self.committed = False
        self.fail_commit = False

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def scalar(self, query):
        params = query.compile().params
        return self.artifact if params.get('id_1') == 'figure' and params.get('project_id_1') == 'project' else None

    def delete(self, artifact): self.deleted.append(artifact)

    def commit(self):
        if self.fail_commit: raise SQLAlchemyError('database failure')
        self.committed = True


@pytest.fixture
def deletion_case(tmp_path, monkeypatch):
    storage = M5Storage(tmp_path)
    key, _ = storage.save('figures', b'PNG', 'png')
    retained_key, retained_hash = storage.save('figures', b'RETAINED PNG', 'png')
    session = FigureSession(SimpleNamespace(id='figure', storage_key=key))
    service = M5Service(get_settings(), None, lambda: session)
    service.storage = storage

    def current_project(session, project, revision=None, **kwargs):
        if revision != 1: raise ServiceError(409, 'stale_revision', 'stale revision', 'figure_delete')

    monkeypatch.setattr(service, '_project', current_project)
    return service, session, storage, key, retained_key, retained_hash


def test_delete_only_requested_figure_and_file(deletion_case):
    service, session, storage, key, retained_key, retained_hash = deletion_case
    result = service.delete_figure('project', 'figure', 1)
    assert result == {'deleted_figure_id': 'figure'}
    assert not storage._path(key).exists()
    assert storage.read(retained_key, retained_hash) == b'RETAINED PNG'
    assert session.deleted == [session.artifact] and session.committed


@pytest.mark.parametrize('project,revision,code', [('foreign', 1, 'not_found'), ('project', 2, 'stale_revision')])
def test_delete_rejects_foreign_project_or_stale_revision(deletion_case, project, revision, code):
    service, session, storage, key, *_ = deletion_case
    with pytest.raises(ServiceError) as caught:
        service.delete_figure(project, 'figure', revision)
    assert caught.value.code == code
    assert storage._path(key).exists() and not session.deleted and not session.committed


def test_file_delete_failure_does_not_remove_record(deletion_case, monkeypatch):
    service, session, storage, key, *_ = deletion_case
    monkeypatch.setattr(storage, 'discard_figure', lambda key: (_ for _ in ()).throw(PermissionError('locked')))
    with pytest.raises(ServiceError) as caught:
        service.delete_figure('project', 'figure', 1)
    assert caught.value.code == 'figure_delete_failed'
    assert storage._path(key).exists() and not session.deleted and not session.committed


def test_metadata_failure_is_explicit_and_retry_handles_already_deleted_file(deletion_case):
    service, session, storage, key, *_ = deletion_case
    session.fail_commit = True
    with pytest.raises(ServiceError, match='retry deletion'):
        service.delete_figure('project', 'figure', 1)
    assert not storage._path(key).exists() and not session.committed
    session.fail_commit = False
    assert service.delete_figure('project', 'figure', 1)['deleted_figure_id'] == 'figure'
    assert session.committed


def test_figure_delete_never_unlinks_dataset_or_escaped_path(tmp_path, monkeypatch):
    storage = M5Storage(tmp_path)
    key, digest = storage.save('datasets', b'x\n1\n', 'csv')
    with pytest.raises(ValueError): storage.discard_figure(key)
    with pytest.raises(ValueError): storage.discard_figure('figures/../outside.png')
    monkeypatch.setattr(storage, '_path', lambda key: tmp_path / 'datasets' / 'protected.csv')
    with pytest.raises(ValueError): storage.discard_figure('figures/linked.png')
    monkeypatch.undo()
    assert storage.read(key, digest) == b'x\n1\n'
