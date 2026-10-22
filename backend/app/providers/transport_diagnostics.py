"""Bounded, capture-sanitized observations from supported HTTPCore tracing."""

import asyncio
import errno
import ssl
from datetime import datetime, timezone
from time import monotonic_ns
from typing import Literal
from uuid import uuid4

import httpcore
import httpx
from pydantic import BaseModel, ConfigDict, Field

MAX_EVENTS = 64
MAX_CAUSES = 4
MAX_COUNTER = 1_000_000
Phase = Literal['tcp', 'tls', 'send_headers', 'send_body', 'receive_headers',
                'receive_body', 'response_cleanup', 'unknown']
Traffic = Literal['connect', 'provider', 'unknown']
Cause = Literal['certificate_verification', 'tls_error', 'connect_timeout', 'read_timeout',
                'write_timeout', 'pool_timeout', 'connect_error', 'remote_protocol',
                'local_protocol', 'read_error', 'write_error', 'proxy_error',
                'cancelled', 'timeout', 'os_error', 'unknown']
Outcome = Literal['transport_complete', 'outer_deadline', 'phase_timeout',
                  'caller_cancelled', 'transport_error']

class StrictRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class CauseRecord(StrictRecord):
    category: Cause
    errno: int | None = Field(default=None, ge=-2147483648, le=2147483647)
    winerror: int | None = Field(default=None, ge=0, le=2147483647)

class PhaseRecord(StrictRecord):
    seq: int = Field(ge=1, le=MAX_COUNTER)
    phase: Phase
    state: Literal['started', 'complete', 'failed']
    traffic: Traffic
    elapsed_ms: int = Field(ge=0, le=9223372036854775807)
    causes: list[CauseRecord] = Field(max_length=MAX_CAUSES)
    status_code: int | None = Field(default=None, ge=100, le=599)

class AttemptRecord(StrictRecord):
    version: Literal[1] = 1
    logical_call_id: str = Field(pattern=r'^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$', max_length=36)
    attempt: int = Field(ge=1, le=MAX_COUNTER)
    start_utc: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$', max_length=32)
    end_utc: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$', max_length=32)
    start_monotonic_ns: int = Field(ge=0, le=9223372036854775807)
    end_monotonic_ns: int = Field(ge=0, le=9223372036854775807)
    elapsed_ms: int = Field(ge=0, le=9223372036854775807)
    outcome: Outcome
    deadline_expired: bool
    last_phase: Phase
    last_io_phase: Phase
    last_io_traffic: Traffic
    tunnel_state: Literal['accepted', 'rejected', 'unknown']
    owned_client_state: Literal['closed', 'unknown', 'not_owned']
    events: list[PhaseRecord] = Field(max_length=MAX_EVENTS)
    terminal_causes: list[CauseRecord] = Field(max_length=MAX_CAUSES)
    dropped_events: int = Field(ge=0, le=MAX_COUNTER)
    ignored_events: int = Field(ge=0, le=MAX_COUNTER)
    unobserved_phases: list[Phase] = Field(max_length=7)
    unobserved_provider_phases: list[Phase] = Field(max_length=5)
    unsupported_phases: list[Literal['dns', 'pool_wait', 'socket_close']] = Field(max_length=3)

_PHASES = {
    'connection.connect_tcp': 'tcp', 'connection.start_tls': 'tls', 'proxy.start_tls': 'tls',
    'http11.send_request_headers': 'send_headers', 'http11.send_request_body': 'send_body',
    'http11.receive_response_headers': 'receive_headers', 'http11.receive_response_body': 'receive_body',
    'http11.response_closed': 'response_cleanup',
}
_EVENTS = {name + '.' + state: (phase, state)
           for name, phase in _PHASES.items() for state in ('started', 'complete', 'failed')}
_ERRNOS = frozenset(errno.errorcode) | {1, 2, 5, 8}
_WINERRORS = frozenset({2, 5, 64, 121, 995, 10013, 10051, 10053, 10054, 10060, 10061, 10065, 11001})

def cause_category(exc: BaseException) -> Cause:
    for types, category in (
        ((ssl.SSLCertVerificationError,), 'certificate_verification'),
        ((ssl.SSLError,), 'tls_error'),
        ((httpx.ConnectTimeout, httpcore.ConnectTimeout), 'connect_timeout'),
        ((httpx.ReadTimeout, httpcore.ReadTimeout), 'read_timeout'),
        ((httpx.WriteTimeout, httpcore.WriteTimeout), 'write_timeout'),
        ((httpx.PoolTimeout, httpcore.PoolTimeout), 'pool_timeout'),
        ((httpx.ConnectError, httpcore.ConnectError), 'connect_error'),
        ((httpx.RemoteProtocolError, httpcore.RemoteProtocolError), 'remote_protocol'),
        ((httpx.LocalProtocolError, httpcore.LocalProtocolError), 'local_protocol'),
        ((httpx.ReadError, httpcore.ReadError), 'read_error'),
        ((httpx.WriteError, httpcore.WriteError), 'write_error'),
        ((httpx.ProxyError, httpcore.ProxyError), 'proxy_error'),
        ((asyncio.CancelledError,), 'cancelled'), ((TimeoutError,), 'timeout'),
        ((OSError,), 'os_error'),
    ):
        if isinstance(exc, types):
            return category
    return 'unknown'

def safe_causes(exc: object) -> list[dict[str, object]]:
    records = []
    seen = set()
    while isinstance(exc, BaseException) and id(exc) not in seen and len(records) < MAX_CAUSES:
        seen.add(id(exc))
        error_number = getattr(exc, 'errno', None) if isinstance(exc, OSError) else None
        win_number = getattr(exc, 'winerror', None) if isinstance(exc, OSError) else None
        records.append(CauseRecord(category=cause_category(exc),
            errno=error_number if type(error_number) is int and error_number in _ERRNOS else None,
            winerror=win_number if type(win_number) is int and win_number in _WINERRORS else None).model_dump())
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return records

class TransportAttempt:
    """Never retain trace info, exception objects, request objects or response tuples."""

    def __init__(self, logical_call_id: str | None = None, attempt: int = 1) -> None:
        self.logical_call_id = logical_call_id or str(uuid4())
        self.attempt = attempt
        self.start_utc = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        self.started = monotonic_ns()
        self.events: list[dict[str, object]] = []
        self.sequence = self.dropped = self.ignored = 0
        self.last_phase: Phase = 'unknown'
        self.last_io_phase: Phase = 'unknown'
        self.last_io_traffic: Traffic = 'unknown'
        self.http_traffic: Traffic = 'unknown'
        self.tunnel_state = 'unknown'
        self.observed: set[str] = set()
        self.provider_observed: set[str] = set()

    async def trace(self, name: str, info: dict[str, object]) -> None:
        # No awaits: capture cannot suspend cancellation or add an I/O dependency.
        try:
            self.capture(name, info)
        except (Exception, asyncio.CancelledError):
            self.ignored = min(MAX_COUNTER, self.ignored + 1)

    def capture(self, name: str, info: dict[str, object]) -> None:
        if type(name) is not str or len(name) > 80 or name not in _EVENTS:
            self.ignored = min(MAX_COUNTER, self.ignored + 1)
            return
        phase, state = _EVENTS[name]
        traffic: Traffic = 'unknown'
        if name.startswith('http11.'):
            if state == 'started':
                request = info.get('request')
                if isinstance(request, httpcore.Request):
                    self.http_traffic = ('connect' if request.method == b'CONNECT' else
                                         'provider' if request.method == b'POST' else 'unknown')
                else:
                    self.http_traffic = 'unknown'
            traffic = self.http_traffic
        elif name.startswith('proxy.start_tls'):
            traffic = 'provider'
        status = None
        if phase == 'receive_headers' and state == 'complete':
            value = info.get('return_value')
            if type(value) is tuple and len(value) == 4 and type(value[1]) is int and 100 <= value[1] <= 599:
                status = value[1]
                if traffic == 'connect':
                    self.tunnel_state = 'accepted' if 200 <= status < 300 else 'rejected'
        self.last_phase = phase
        if phase != 'response_cleanup':
            self.last_io_phase, self.last_io_traffic = phase, traffic
        self.observed.add(phase)
        if traffic == 'provider':
            self.provider_observed.add(phase)
        self.sequence = min(MAX_COUNTER, self.sequence + 1)
        if len(self.events) >= MAX_EVENTS:
            self.dropped = min(MAX_COUNTER, self.dropped + 1)
            return
        self.events.append(PhaseRecord(seq=self.sequence, phase=phase, state=state, traffic=traffic,
            elapsed_ms=max(0, (monotonic_ns() - self.started) // 1_000_000),
            causes=safe_causes(info.get('exception')) if state == 'failed' else [], status_code=status).model_dump())

    def finish(self, outcome: Outcome, deadline_expired: bool,
               owned_client_state: Literal['closed', 'unknown', 'not_owned'],
               exc: BaseException | None = None) -> dict[str, object]:
        ended = monotonic_ns()
        return AttemptRecord(logical_call_id=self.logical_call_id, attempt=self.attempt,
            start_utc=self.start_utc, end_utc=datetime.now(timezone.utc).isoformat(timespec='microseconds'),
            start_monotonic_ns=self.started, end_monotonic_ns=ended,
            elapsed_ms=max(0, (ended - self.started) // 1_000_000), outcome=outcome,
            deadline_expired=deadline_expired, last_phase=self.last_phase, last_io_phase=self.last_io_phase,
            last_io_traffic=self.last_io_traffic, tunnel_state=self.tunnel_state,
            owned_client_state=owned_client_state, events=self.events, terminal_causes=safe_causes(exc),
            dropped_events=self.dropped, ignored_events=self.ignored,
            unobserved_phases=[p for p in ('tcp', 'tls', 'send_headers', 'send_body', 'receive_headers',
                                          'receive_body', 'response_cleanup') if p not in self.observed],
            unobserved_provider_phases=[p for p in ('send_headers', 'send_body', 'receive_headers',
                                                   'receive_body', 'response_cleanup') if p not in self.provider_observed],
            unsupported_phases=['dns', 'pool_wait', 'socket_close']).model_dump()
