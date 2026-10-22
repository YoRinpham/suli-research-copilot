"""Gap-only bounded capture boundary; observations never decide science or traffic."""
import asyncio
from contextvars import ContextVar
from functools import wraps
import json
import logging
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from app.providers.transport_diagnostics import AttemptRecord

MAX_EVENTS = 512
MAX_COUNT = 1_000_000
UUID_PATTERN = r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$"
Category = Literal[
    "unknown", "source_lineage", "source_authority", "candidate_capacity",
    "advisory_source_reference", "schema_invalid", "domain_invalid", "pre_result_value_error",
    "response_json", "envelope", "output_json", "incomplete_response", "invalid_content_type",
    "model_identity", "provider_connect_error", "provider_network_error", "provider_protocol_error",
    "provider_total_timeout", "provider_connect_timeout", "provider_read_timeout",
    "provider_write_timeout", "provider_pool_timeout", "provider_timeout", "provider_auth_missing",
    "provider_http_429", "provider_http_500", "provider_http_502", "provider_http_503", "provider_http_504",
    "provider_http_401", "provider_http_403", "provider_model_mismatch", "structured_output_invalid",
    "validation_error", "llm_failed", "database_failed", "invalid_state", "cancelled",
]


class GapEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    project_id: str = Field(pattern=UUID_PATTERN, max_length=36)
    generation_run_id: str | None = Field(default=None, pattern=UUID_PATTERN, max_length=36)
    sequence: int = Field(ge=1, le=MAX_EVENTS + 1)
    stage: Literal["gap_execution", "gap_generation", "gap_assessment"]
    candidate_index: int | None = Field(default=None, ge=0, le=9)
    repair_round: int | None = Field(default=None, ge=0, le=1)
    logical_invocation_id: str | None = Field(default=None, pattern=UUID_PATTERN, max_length=36)
    adapter_call_id: str | None = Field(default=None, pattern=UUID_PATTERN, max_length=36)
    layer: Literal["adapter", "transport", "provider", "decode", "pydantic", "domain_validation",
                   "capacity", "repair", "provider_round", "stage_outcome", "persistence", "capture"]
    outcome: Literal["start", "pass", "fail", "retry", "scheduled", "invoked", "attempt_started", "summary"]
    category: Category = "unknown"
    error_path: str | None = Field(default=None, max_length=120)
    validation_code: Literal["missing", "extra_forbidden", "literal_error", "string_type", "list_type",
                             "dict_type", "model_type", "too_short", "too_long", "value_error",
                             "string_too_short", "string_too_long", "gap_foreign_landscape_item",
                             "gap_incomplete_lineage", "gap_evidence_claim_mismatch",
                             "gap_advisory_source_reference", "gap_candidate_capacity", "unknown"] | None = None
    attempt: int | None = Field(default=None, ge=1, le=3)
    duration_ms: int | None = Field(default=None, ge=0, le=9223372036854775807)
    context_chars: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    candidate_allowance: int | None = Field(default=None, ge=-MAX_COUNT, le=MAX_COUNT)
    measured_size: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    limit: int | None = Field(default=None, ge=1, le=MAX_COUNT)
    legacy_cumulative_attempts: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    provider_attempts: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    logical_invocations: int = Field(ge=0, le=MAX_COUNT)
    adapter_invocations: int = Field(ge=0, le=MAX_COUNT)
    structured_repairs: int = Field(ge=0, le=MAX_COUNT)
    retry_decisions: int = Field(ge=0, le=MAX_COUNT)
    adapter_request_attempts: int = Field(ge=0, le=MAX_COUNT)
    transport_records: int = Field(ge=0, le=MAX_COUNT)
    http_status: int | None = Field(default=None, ge=100, le=599)
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$", max_length=64)
    response_length: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    transport_diagnostic: AttemptRecord | None = None
    dropped_events: int = Field(ge=0, le=MAX_COUNT)
    invalid_events: int = Field(ge=0, le=MAX_COUNT)
    sink_failures: int = Field(ge=0, le=MAX_COUNT)
    capture_complete: bool
    remote_authenticated_receipt: Literal["unknown"] = "unknown"


_ACTIVE: ContextVar["GapCapture | None"] = ContextVar("gap_capture", default=None)
_LOG = logging.getLogger("uvicorn.error")
_FIELDS = frozenset(GapEvent.model_fields)
_CATEGORIES = frozenset(Category.__args__)
_SCALAR_FIELDS = "title|description|gap_type|why_it_matters|uncertainty|novelty_scope"
_LIST_FIELDS = "assumptions|landscape_item_ids|claim_ids|evidence_for_ids|evidence_against_ids"
_PATH = re.compile(rf"^(gaps(?:\.[0-9])?(?:\.(?:{_SCALAR_FIELDS})|\.(?:{_LIST_FIELDS})(?:\.\d{{1,4}})?)?|"
                   r"verdict|justification|risk_flags(?:\.\d{1,4})?|source_ids(?:\.\d{1,4})?)$")


def safe_path(path, validation_code):
    if validation_code == "extra_forbidden":
        return "<extra_field>"
    if type(path) is str and len(path) <= 120 and _PATH.fullmatch(path):
        return path
    return "<unknown_field>"


class GapCapture:
    def __init__(self, project_id, sink):
        self.project_id, self.sink = project_id, sink
        self.run_id = None
        self.stage, self.index, self.repair, self.invocation = "gap_execution", None, None, None
        self.events = self.dropped = self.invalid = self.sink_failures = 0
        self.logical = self.adapters = self.repairs = self.retries = self.requests = self.transports = 0
        self.legacy = None
        self.complete = True

    def emit(self, fields, *, summary=False):
        # This is the only Gap boundary into either logging or an external sink.
        try:
            if self.events >= MAX_EVENTS and not summary:
                self.dropped = min(MAX_COUNT, self.dropped + 1)
                self.complete = False
                return
            if type(fields) is not dict:
                raise ValueError()
            approved = {k: v for k, v in fields.items() if type(k) is str and k in _FIELDS}
            category = fields.get("category", fields.get("decode_category", "unknown"))
            if category == "unknown":
                category = {"IncompleteResponse": "incomplete_response", "InvalidContentType": "invalid_content_type", "ModelIdentityMismatch": "model_identity"}.get(fields.get("error_type"), "unknown")
                if fields.get("outcome") == "fail" and fields.get("layer") in {"pydantic", "domain_validation"}:
                    category = "schema_invalid" if fields["layer"] == "pydantic" else "domain_invalid"
            approved["category"] = category if type(category) is str and category in _CATEGORIES else "unknown"
            if "error_path" in fields:
                approved["error_path"] = safe_path(fields["error_path"], fields.get("validation_code", fields.get("error_type")))
            code = fields.get("validation_code", fields.get("error_type"))
            if code is not None:
                approved["validation_code"] = code if code in GapEvent.model_fields["validation_code"].annotation.__args__[0].__args__ else "unknown"
            if "cumulative_attempts" in fields:
                approved["legacy_cumulative_attempts"] = fields["cumulative_attempts"]
            if "logical_call_id" in fields:
                approved["adapter_call_id"] = fields["logical_call_id"]
            record = fields.get("transport_diagnostic")
            if record is not None:
                # Force validation of nested fields; typed model instances must not
                # bypass literal allowlists through Pydantic's instance shortcut.
                approved["transport_diagnostic"] = AttemptRecord.model_validate_json(json.dumps(record, allow_nan=False))
                approved["adapter_call_id"] = approved["transport_diagnostic"].logical_call_id
            requests = min(MAX_COUNT, self.requests + (fields.get("layer") == "adapter" and fields.get("outcome") == "attempt_started"))
            adapters = min(MAX_COUNT, self.adapters + (fields.get("layer") == "adapter" and fields.get("outcome") == "start"))
            retries = min(MAX_COUNT, self.retries + (fields.get("layer") == "provider" and fields.get("outcome") == "retry"))
            transports = min(MAX_COUNT, self.transports + (record is not None))
            complete = self.complete and not (record is not None and (record.get("dropped_events") or record.get("ignored_events")))
            legacy = approved.get("legacy_cumulative_attempts", self.legacy)
            index = fields.get("candidate_index", self.index)
            match = re.match(r"^gaps\.([0-9])(?:\.|$)", approved.get("error_path", ""))
            if index is None and self.stage == "gap_generation" and match is not None:
                index = int(match.group(1))
            approved.update(project_id=self.project_id, generation_run_id=self.run_id,
                sequence=min(MAX_EVENTS + 1, self.events + 1), stage=self.stage,
                candidate_index=index, repair_round=fields.get("repair_round", self.repair),
                logical_invocation_id=self.invocation, logical_invocations=self.logical,
                adapter_request_attempts=requests, transport_records=transports, adapter_invocations=adapters,
                structured_repairs=self.repairs, retry_decisions=retries, legacy_cumulative_attempts=legacy,
                dropped_events=self.dropped, invalid_events=self.invalid, sink_failures=self.sink_failures,
                capture_complete=bool(complete and requests == transports and self.logical == adapters),
                remote_authenticated_receipt="unknown")
            event = GapEvent.model_validate(approved).model_dump(mode="json", exclude_none=True)
            self.requests, self.adapters, self.transports, self.retries = requests, adapters, transports, retries
            self.legacy, self.complete = legacy, complete
            if not summary:
                self.events += 1
        except (Exception, asyncio.CancelledError):
            self.invalid = min(MAX_COUNT, self.invalid + 1)
            self.complete = False
            return
        try:
            if self.sink is not None:
                self.sink(event)
            else:
                _LOG.info("gap_trace %s", json.dumps(event, sort_keys=True))
        except (Exception, asyncio.CancelledError):
            self.sink_failures = min(MAX_COUNT, self.sink_failures + 1)
            self.complete = False


def safe_emit(capture, fields, *, summary=False):
    try:
        capture.emit(fields, summary=summary)
    except (Exception, asyncio.CancelledError):
        try:
            capture.complete = False
        except (Exception, asyncio.CancelledError):
            pass


def gap_provider_event(fields, repair_round):
    capture = _ACTIVE.get()
    if capture is not None:
        safe_emit(capture, {**fields, "repair_round": repair_round} if type(fields) is dict else fields)


def gap_event(**fields):
    capture = _ACTIVE.get()
    if capture is not None:
        safe_emit(capture, fields)


def bind_gap_run(run_id):
    capture = _ACTIVE.get()
    if capture is not None:
        try:
            capture.run_id = run_id
        except (Exception, asyncio.CancelledError):
            capture.complete = False


def gap_invocation(repair_round, attempts):
    capture = _ACTIVE.get()
    if capture is not None:
        try:
            capture.repair, capture.invocation = repair_round, str(uuid4())
            capture.logical = min(MAX_COUNT, capture.logical + 1)
            capture.repairs = min(MAX_COUNT, capture.repairs + (repair_round == 1))
            gap_event(layer="adapter", outcome="invoked", legacy_cumulative_attempts=attempts)
        except (Exception, asyncio.CancelledError):
            capture.complete = False


def failure_category(error):
    try:
        return "cancelled" if isinstance(error, asyncio.CancelledError) else getattr(error, "code", "unknown")
    except (Exception, asyncio.CancelledError):
        return "unknown"


def gap_capacity(context, candidate, index, error=None):
    try:
        if error is None:
            measured, limit, allowance = len(str(context.assessment_context(candidate))), context.limit, context.candidate_budget
        else:
            measured, limit, allowance = error.measured_chars, error.limit, error.candidate_limit
        gap_event(layer="capacity", outcome="fail" if error else "pass", category="candidate_capacity",
                  candidate_index=index, measured_size=measured, limit=limit, candidate_allowance=allowance,
                  **({"validation_code": "gap_candidate_capacity"} if error else {}))
    except (Exception, asyncio.CancelledError):
        capture = _ACTIVE.get()
        if capture is not None:
            capture.complete = False


def diagnose_gap(method):
    @wraps(method)
    def wrapped(self, project_id, revision, input_id, stage, *args, **kwargs):
        try:
            capture = GapCapture(project_id, self.trace_sink) if stage == "gap" else None
        except (Exception, asyncio.CancelledError):
            capture = None
        token = _ACTIVE.set(capture)
        try:
            result = method(self, project_id, revision, input_id, stage, *args, **kwargs)
            gap_event(layer="stage_outcome", outcome="pass")
            return result
        except BaseException as error:
            category = failure_category(error)
            gap_event(layer="stage_outcome", outcome="fail", category=category)
            raise
        finally:
            if capture is not None:
                safe_emit(capture, {"layer": "capture", "outcome": "summary"}, summary=True)
            _ACTIVE.reset(token)
    return wrapped


def diagnose_gap_call(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        capture = _ACTIVE.get()
        stage = kwargs.get("trace_stage") or ""
        if capture is None or type(stage) is not str or not re.fullmatch(r"gap_generation|gap_assessment\[[0-9]\]", stage):
            return method(self, *args, **kwargs)
        previous = capture.stage, capture.index, capture.repair, capture.invocation
        capture.stage = "gap_assessment" if stage.startswith("gap_assessment[") else "gap_generation"
        capture.index = int(stage.split("[")[1][:-1]) if capture.stage == "gap_assessment" else None
        try:
            result = method(self, *args, **kwargs)
            gap_event(layer="stage_outcome", outcome="pass")
            return result
        except BaseException as error:
            category = failure_category(error)
            gap_event(layer="stage_outcome", outcome="fail", category=category)
            raise
        finally:
            capture.stage, capture.index, capture.repair, capture.invocation = previous
    return wrapped
