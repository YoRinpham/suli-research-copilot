"""Proposed real provider capture path: synthetic faults and loopback HTTP/TLS."""
import asyncio
import json
import os
from time import perf_counter_ns
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from app.providers.llm import DashScopeLLMProvider, LLMProviderError, LLMTimeout, trace_provider_calls

OUT = Path(__file__).resolve().parent
SECRET = 'SENTINEL_PRIVATE_TOKEN_97'

@pytest.fixture(scope='module', autouse=True)
def artifact_directory(tmp_path_factory):
    global OUT
    OUT = Path(os.environ['TRANSPORT_DIAGNOSTIC_OUTPUT']) if os.getenv('TRANSPORT_DIAGNOSTIC_OUTPUT') else tmp_path_factory.mktemp('transport-artifacts')

class Answer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: int

def response():
    return {'model': 'qwen3.8-max', 'choices': [{'finish_reason': 'stop', 'message': {'content': '{"value":7}'}}]}

def provider(handler, retries=2, timeout=120):
    return DashScopeLLMProvider(SECRET, 'qwen3.8-max', timeout, retries,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False), reasoning_effort='low')

def diagnostics(events):
    return [e['transport_diagnostic'] for e in events if e['layer'] == 'transport']

def test_capture_path_success_and_unchanged_parsing():
    events = []
    p = provider(lambda r: httpx.Response(200, json=response()))
    with trace_provider_calls(events.append):
        result = p.generate_structured(SECRET, Answer, {'context': SECRET})
    assert result.output == Answer(value=7) and result.attempt_count == 1
    assert len(diagnostics(events)) == 1
    d = diagnostics(events)[0]
    assert d['outcome'] == 'transport_complete'
    assert d['last_io_phase'] == 'unknown' and d['unobserved_phases']
    assert len(d['logical_call_id']) == 36 and d['elapsed_ms'] >= 0
    assert SECRET not in json.dumps(d)

@pytest.mark.parametrize('exception,category,outcome', [
    (httpx.ConnectError, 'provider_connect_error', 'transport_error'),
    (httpx.RemoteProtocolError, 'provider_protocol_error', 'transport_error'),
    (httpx.ReadTimeout, 'provider_read_timeout', 'phase_timeout'),
    (httpx.ConnectTimeout, 'provider_connect_timeout', 'phase_timeout'),
    (httpx.WriteTimeout, 'provider_write_timeout', 'phase_timeout'),
    (httpx.PoolTimeout, 'provider_pool_timeout', 'phase_timeout'),
])
def test_synthetic_exception_semantics(exception, category, outcome):
    def fail(request):
        raise exception(SECRET, request=request)
    events = []
    with trace_provider_calls(events.append), pytest.raises(LLMProviderError) as caught:
        provider(fail).generate_structured(SECRET, Answer, {'x': SECRET})
    assert caught.value.category == category and caught.value.attempt_count == 3
    assert [d['attempt'] for d in diagnostics(events)] == [1, 2, 3]
    assert all(d['outcome'] == outcome for d in diagnostics(events))
    assert len({d['logical_call_id'] for d in diagnostics(events)}) == 1
    assert SECRET not in json.dumps(diagnostics(events))

def test_retry_identical_payload_fresh_owned_clients_and_exact_profile(monkeypatch):
    import app.providers.llm as module
    actual_client = httpx.AsyncClient
    clients, payloads, kwargs_seen = [], [], []
    def handle(request):
        payloads.append(request.content)
        if len(payloads) == 1:
            raise httpx.RemoteProtocolError(SECRET)
        return httpx.Response(200, json=response())
    def factory(**kwargs):
        kwargs_seen.append(kwargs)
        client = actual_client(transport=httpx.MockTransport(handle), trust_env=False)
        clients.append(client)
        return client
    monkeypatch.setattr(module.httpx, 'AsyncClient', factory)
    p = DashScopeLLMProvider(SECRET, 'qwen3.8-max', 120, 2, reasoning_effort='low')
    events = []
    with trace_provider_calls(events.append):
        result = p.generate_structured(SECRET, Answer, {'x': SECRET})
    assert result.attempt_count == 2
    assert len(clients) == 2 and clients[0] is not clients[1] and all(c.is_closed for c in clients)
    assert kwargs_seen == [{}, {}] and payloads[0] == payloads[1]
    assert p.timeout_seconds == 120 and p.retry_count == 2
    body = json.loads(payloads[0])
    assert body['reasoning_effort'] == 'low' and body['response_format']['type'] == 'json_schema'
    assert all(d['owned_client_state'] == 'closed' for d in diagnostics(events))

@pytest.mark.parametrize('sink_kind', ['runtime', 'cancelled'])
def test_diagnostic_sink_failure_preserves_original_failure_and_success(sink_kind):
    def sink(event):
        if sink_kind == 'cancelled':
            raise asyncio.CancelledError(SECRET)
        raise RuntimeError(SECRET)
    def fail(request):
        raise httpx.RemoteProtocolError(SECRET)
    with trace_provider_calls(sink), pytest.raises(LLMProviderError) as caught:
        provider(fail).generate_structured(SECRET, Answer, {})
    assert caught.value.category == 'provider_protocol_error' and caught.value.attempt_count == 3
    with trace_provider_calls(sink):
        assert provider(lambda r: httpx.Response(200, json=response())).generate_structured(SECRET, Answer, {}).output.value == 7

def test_synthetic_outer_deadline_during_body_cleanup_and_cancel(monkeypatch):
    import app.providers.llm as module
    actual_client = httpx.AsyncClient
    clients = []
    class Slow(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.01)
                yield b' '
        async def aclose(self):
            self.closed = True
    streams = []
    def handler(request):
        stream = Slow()
        streams.append(stream)
        return httpx.Response(200, stream=stream)
    def factory():
        c = actual_client(transport=httpx.MockTransport(handler), trust_env=False)
        clients.append(c)
        return c
    monkeypatch.setattr(module.httpx, 'AsyncClient', factory)
    p = DashScopeLLMProvider(SECRET, 'qwen3.8-max', .05, 2)
    events = []
    with trace_provider_calls(events.append), pytest.raises(LLMTimeout) as caught:
        p.generate_structured(SECRET, Answer, {})
    assert caught.value.category == 'provider_total_timeout' and caught.value.attempt_count == 3
    assert all(d['deadline_expired'] and d['outcome'] == 'outer_deadline' for d in diagnostics(events))
    assert all(c.is_closed for c in clients) and all(s.closed for s in streams)
    async def cancel():
        with trace_provider_calls(events.append):
            task = asyncio.create_task(p._post({'dummy': SECRET}))
            await asyncio.sleep(.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(cancel())
    assert diagnostics(events)[-1]['outcome'] == 'caller_cancelled'
    assert not diagnostics(events)[-1]['deadline_expired']
    assert clients[-1].is_closed and streams[-1].closed

def test_concurrent_context_isolation():
    async def run():
        async def handler(request):
            await asyncio.sleep(.01)
            return httpx.Response(200, json=response())
        async def one():
            events = []
            p = provider(handler)
            with trace_provider_calls(events.append):
                await p._post({'dummy': SECRET})
            return diagnostics(events)
        return await asyncio.gather(one(), one())
    left, right = asyncio.run(run())
    assert len(left) == len(right) == 1
    assert left[0]['logical_call_id'] != right[0]['logical_call_id']

def test_synthetic_bounds_allowlist_causes_and_capture_redaction():
    import errno
    import httpcore
    from app.providers.transport_diagnostics import TransportAttempt, MAX_EVENTS, MAX_CAUSES, AttemptRecord
    d = TransportAttempt()
    chain = OSError(errno.ECONNRESET, SECRET)
    chain.__cause__ = chain
    for _ in range(200):
        d.capture('http11.receive_response_headers.started', {'request': httpcore.Request('POST', 'https://signed.invalid/?token=' + SECRET, headers={'Authorization': SECRET}, content=SECRET.encode())})
        d.capture('http11.receive_response_headers.failed', {'exception': chain, 'headers': SECRET, 'socket': SECRET, 'return_value': SECRET})
    d.capture(SECRET, {'exception': RuntimeError(SECRET)})
    record = d.finish('transport_error', False, 'not_owned')
    assert len(record['events']) == MAX_EVENTS and record['dropped_events'] > 0
    assert [e['seq'] for e in record['events']] == list(range(1, MAX_EVENTS + 1))
    assert all(len(e['causes']) <= MAX_CAUSES for e in record['events'])
    assert record['events'][1]['causes'][0]['errno'] == errno.ECONNRESET
    assert 'pool_wait' in record['unsupported_phases']
    assert SECRET not in json.dumps(record) and len(json.dumps(record)) < 32768
    AttemptRecord.model_validate(record)
    with pytest.raises(Exception):
        AttemptRecord.model_validate({**record, 'raw': SECRET})

def test_service_allowlist_and_run_stage_role_correlation():
    from app.config.settings import get_settings
    from app.prompts.scientific import LANDSCAPE_PROMPT
    from app.services.scientific import ScientificService
    events = []
    subject = ScientificService(get_settings(), provider(lambda r: httpx.Response(200, json=response())), trace_sink=events.append)
    result, count = subject._generate('00000000-0000-4000-8000-000000000001', LANDSCAPE_PROMPT, Answer, {}, lambda v: v,
        trace_stage='landscape_generation')
    assert result.value == 7 and count == 1
    event = next(e for e in events if e['layer'] == 'transport')
    assert event['generation_run_id'] == '00000000-0000-4000-8000-000000000001'
    assert event['stage'] == 'landscape_generation' and event['provider_role'] == 'initial_structured_generation'
    assert event['logical_call_id'] == event['transport_diagnostic']['logical_call_id']

@pytest.mark.parametrize('failure_point', ['construct', 'capture', 'finish'])
def test_collector_internal_failure_does_not_change_results(monkeypatch, failure_point):
    import app.providers.llm as module
    from app.providers.transport_diagnostics import TransportAttempt
    def fail(*args, **kwargs):
        raise RuntimeError(SECRET)
    if failure_point == 'construct':
        monkeypatch.setattr(module, 'TransportAttempt', fail)
    else:
        monkeypatch.setattr(TransportAttempt, failure_point, fail)
    events = []
    async def success(request):
        callback = request.extensions.get('trace')
        if callback is not None:
            await callback('connection.connect_tcp.started', {'raw': SECRET})
        return httpx.Response(200, json=response())
    with trace_provider_calls(events.append):
        assert provider(success).generate_structured(SECRET, Answer, {}).output.value == 7
    def disconnect(request):
        raise httpx.RemoteProtocolError(SECRET)
    with trace_provider_calls(events.append), pytest.raises(LLMProviderError) as caught:
        provider(disconnect).generate_structured(SECRET, Answer, {})
    assert caught.value.category == 'provider_protocol_error' and caught.value.attempt_count == 3

def test_cleanup_failure_propagates_and_is_not_a_hidden_success(monkeypatch):
    import app.providers.llm as module
    actual = httpx.AsyncClient
    class CleanupFailure(actual):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            raise RuntimeError(SECRET)
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda: CleanupFailure(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response())), trust_env=False))
    p = DashScopeLLMProvider(SECRET, 'qwen3.8-max', 120, 2)
    events = []
    with trace_provider_calls(events.append), pytest.raises(RuntimeError):
        p.generate_structured(SECRET, Answer, {})
    assert len(diagnostics(events)) == 1 and diagnostics(events)[0]['outcome'] == 'transport_error'
    assert SECRET not in json.dumps(diagnostics(events))

def test_service_diagnostic_sink_failure_preserves_transport_error_and_failure_callback():
    from app.config.settings import get_settings
    from app.prompts.scientific import LANDSCAPE_PROMPT
    from app.services.scientific import ScientificService
    from app.services.errors import ServiceError
    seen, failed = [], []
    def sink(event):
        if event['layer'] == 'transport':
            raise RuntimeError(SECRET)
        seen.append(event)
    def disconnect(request):
        raise httpx.RemoteProtocolError(SECRET)
    subject = ScientificService(get_settings(), provider(disconnect), trace_sink=sink)
    subject._failed = lambda *args: failed.append(args)
    with pytest.raises(ServiceError) as caught:
        subject._generate('00000000-0000-4000-8000-000000000001', LANDSCAPE_PROMPT, Answer, {}, lambda v: v,
                          trace_stage='landscape_generation')
    assert caught.value.details['provider_error_category'] == 'provider_protocol_error'
    assert failed and failed[-1][1:3] == ('provider_protocol_error', 3)
    assert not any(e.get('layer') == 'repair' for e in seen)

def test_cause_depth_unknown_numbers_metadata_and_cancellation_safe_callback():
    from app.providers.transport_diagnostics import TransportAttempt
    errors = [OSError(987654321, SECRET) for _ in range(9)]
    for a, b in zip(errors, errors[1:]):
        a.__cause__ = b
    errors[0].winerror = 987654321
    d = TransportAttempt()
    asyncio.run(d.trace('connection.connect_tcp.failed', {'exception': errors[0], 'raw': SECRET}))
    result = d.finish('transport_error', False, 'unknown', errors[0])
    assert len(result['terminal_causes']) == 4
    assert all(c['errno'] is None and c['winerror'] is None for c in result['terminal_causes'])
    assert SECRET not in json.dumps(result)

def test_schema_and_bounded_capture_overhead_artifacts():
    import httpcore
    from app.providers.transport_diagnostics import AttemptRecord, TransportAttempt
    request = httpcore.Request('POST', 'https://signed.invalid/?token=' + SECRET, headers={'Authorization': SECRET}, content=SECRET.encode())
    (OUT / 'transport-schema.json').write_text(json.dumps(AttemptRecord.model_json_schema(), indent=2) + '\n', encoding='utf8')
    start = perf_counter_ns()
    samples = []
    for _ in range(100):
        d = TransportAttempt()
        for _ in range(6):
            d.capture('http11.send_request_headers.started', {'request': request})
            d.capture('http11.send_request_headers.complete', {'return_value': None})
        samples.append(d.finish('transport_complete', False, 'not_owned'))
    elapsed = perf_counter_ns() - start
    metrics = {'iterations': 100, 'events_per_attempt': 12, 'mean_capture_and_schema_microseconds': elapsed / 100_000,
               'maximum_sample_json_bytes': max(len(json.dumps(x).encode()) for x in samples),
               'sink_time_included': False, 'network_time_included': False}
    (OUT / 'overhead.json').write_text(json.dumps(metrics, indent=2) + '\n', encoding='utf8')
    assert metrics['maximum_sample_json_bytes'] < 32768

def test_maximum_record_bound_with_four_causes_per_event():
    import errno
    import ssl
    from app.providers.transport_diagnostics import TransportAttempt
    errors = [ssl.SSLCertVerificationError(1, SECRET) for _ in range(4)]
    for a, b in zip(errors, errors[1:]):
        a.__cause__ = b
    d = TransportAttempt()
    for _ in range(100):
        d.capture('connection.start_tls.failed', {'exception': errors[0], 'raw': SECRET})
    value = d.finish('transport_error', False, 'unknown', errors[0])
    assert len(value['events']) == 64 and all(len(e['causes']) == 4 for e in value['events'])
    assert len(json.dumps(value).encode()) < 65536 and SECRET not in json.dumps(value)
