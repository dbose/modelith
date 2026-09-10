"""Append-only audit log of write/administrative events (spec §20).

Git records every *model* change. It does not record the *administrative* history a
compliance reviewer asks for: who was let in, what a request tried to do and whether
it was allowed or REFUSED (a denied write never becomes a commit, so git cannot show
it), a decision verdict. That trail is this module's whole job.

OpenTelemetry-native: an audit event is emitted once and each configured exporter
receives it —

  * OTLP (opentelemetry-exporter-otlp-proto-http) when the standard
    OTEL_EXPORTER_OTLP_ENDPOINT / …_LOGS_ENDPOINT is set, so the trail lands in
    whatever collector/SIEM the enterprise already runs (Splunk, Datadog, Grafana,
    an OpenTelemetry Collector) with no Modelith-specific integration; and
  * a local JSONL file when MDL_AUDIT_LOG is set — the always-available fallback and
    the source for `GET /api/audit` and the CLI.

Both are optional. With neither configured, audit is OFF (solo users get nothing to
set up and no stray file). If the OTel packages are absent, audit degrades to the
JSONL writer alone — never an import error. Export is best-effort and never fails a
request: a full disk or an unreachable collector is logged to stderr, not surfaced.

The record names an action and its target, never its contents (§20.6): no
definitions, no attribute values, no payloads. The "what" is in git for whoever has
repo access; the audit trail answers who/when/allowed for people who may not.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# env vars (Modelith-specific; OTLP uses the OTel spec's own OTEL_* vars)
ENV_AUDIT_LOG = "MDL_AUDIT_LOG"
ENV_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENV_OTLP_LOGS_ENDPOINT = "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"

_VALID_OUTCOMES = ("ok", "denied", "error")


@dataclass(frozen=True)
class AuditEvent:
    ts: str  # UTC ISO-8601, ms precision
    identity: str
    source: str  # proxy | git | anonymous
    op: str  # command | propose | commit | discard | verdict
    target: str
    outcome: str  # ok | denied | error
    detail: str | None = None

    def to_json(self) -> dict:
        d = {
            "ts": self.ts,
            "identity": self.identity,
            "source": self.source,
            "op": self.op,
            "target": self.target,
            "outcome": self.outcome,
        }
        if self.detail:
            d["detail"] = self.detail
        return d


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{datetime.now(UTC).microsecond // 1000:03d}Z"
    )


class AuditSink:
    """Resolved once at create_app time. Immutable configuration; the write lock
    guards concurrent appends to the JSONL file (uvicorn serves handlers in a
    threadpool)."""

    def __init__(
        self,
        *,
        log_path: Path | None,
        otel_endpoint: str | None,
        resource_attrs: Mapping[str, str] | None = None,
    ) -> None:
        self._log_path = log_path
        self._lock = threading.Lock()
        self._otel_logger = None  # lazily-built OTel logger, or None
        if log_path is not None:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:  # a bad path must not crash startup
                print(f"[audit] cannot create {log_path.parent}: {e}", file=sys.stderr)
                self._log_path = None
        if otel_endpoint:
            self._otel_logger = _try_build_otel_logger(resource_attrs or {})

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        resource_attrs: Mapping[str, str] | None = None,
    ) -> AuditSink:
        env = os.environ if env is None else env
        raw = (env.get(ENV_AUDIT_LOG) or "").strip()
        log_path = Path(raw).expanduser() if raw else None
        endpoint = (
            env.get(ENV_OTLP_LOGS_ENDPOINT) or env.get(ENV_OTLP_ENDPOINT) or ""
        ).strip() or None
        return cls(log_path=log_path, otel_endpoint=endpoint, resource_attrs=resource_attrs)

    @property
    def enabled(self) -> bool:
        return self._log_path is not None or self._otel_logger is not None

    @property
    def log_path(self) -> Path | None:
        return self._log_path

    def record(
        self,
        op: str,
        *,
        identity: str,
        source: str,
        target: str,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        """Emit one audit event to every configured exporter. Best-effort: any
        failure is logged to stderr and swallowed, so an audit outage never takes
        down a write."""
        if not self.enabled:
            return
        if outcome not in _VALID_OUTCOMES:
            outcome = "error"
        ev = AuditEvent(
            ts=_now_iso(),
            identity=identity or "",
            source=source or "anonymous",
            op=op,
            target=target or "",
            outcome=outcome,
            detail=detail,
        )
        if self._log_path is not None:
            self._append_jsonl(ev)
        if self._otel_logger is not None:
            self._emit_otel(ev)

    def _append_jsonl(self, ev: AuditEvent) -> None:
        try:
            line = json.dumps(ev.to_json(), separators=(",", ":"))
            with self._lock, self._log_path.open("a", encoding="utf-8") as f:  # type: ignore[union-attr]
                f.write(line + "\n")
        except OSError as e:
            print(f"[audit] append failed: {e}", file=sys.stderr)

    def _emit_otel(self, ev: AuditEvent) -> None:
        try:
            _otel_emit(self._otel_logger, ev)
        except Exception as e:  # noqa: BLE001 — telemetry must never break a write
            print(f"[audit] otel emit failed: {e}", file=sys.stderr)

    def read(
        self, *, limit: int = 100, op: str | None = None, identity: str | None = None
    ) -> list[dict]:
        """Most recent records from the JSONL file (newest first), filterable. The
        OTLP collector/SIEM is the real query surface when OTLP is on; this is the
        local convenience for the endpoint and the CLI."""
        if self._log_path is None or not self._log_path.exists():
            return []
        try:
            lines = self._log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for ln in reversed(lines):
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if op and rec.get("op") != op:
                continue
            if identity and rec.get("identity") != identity:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out


# --- OpenTelemetry, entirely optional --------------------------------------------
#
# Imported lazily and guarded so the absence of the packages degrades to JSONL-only
# rather than raising. Kept in module-level helpers (not methods) so the SDK types
# never appear in AuditSink's signatures.

_OTEL_SEVERITY = {"ok": "INFO", "denied": "WARN", "error": "ERROR"}


def _try_build_otel_logger(resource_attrs: Mapping[str, str]):
    """Build an OTel logs LoggerProvider with an OTLP exporter, or return None if the
    packages are not installed. The endpoint + headers + protocol come from the OTel
    spec's own OTEL_EXPORTER_OTLP_* env vars, honoured by the exporter itself."""
    try:
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        try:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                OTLPLogExporter,
            )
        except ImportError:  # grpc variant, if that is what is installed
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (  # type: ignore
                OTLPLogExporter,
            )
    except ImportError:
        print(
            "[audit] OTEL endpoint set but opentelemetry packages are not installed; "
            "install modelith[audit] for OTLP export. Falling back to the file sink.",
            file=sys.stderr,
        )
        return None

    attrs = {"service.name": "modelith", **dict(resource_attrs)}
    provider = LoggerProvider(resource=Resource.create(attrs))
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    return provider.get_logger("mdl.audit")


def _otel_emit(logger, ev: AuditEvent) -> None:
    """Emit one AuditEvent as an OTel LogRecord, stamped with the current trace
    context when there is one."""
    import time

    from opentelemetry import trace
    from opentelemetry._logs import SeverityNumber

    # The LogRecord to emit lives in the API package (opentelemetry._logs) in current
    # SDKs; older SDKs exposed it under opentelemetry.sdk._logs. Try both so a range
    # of installed versions works.
    try:
        from opentelemetry._logs import LogRecord
    except ImportError:  # pragma: no cover - very old SDKs
        from opentelemetry.sdk._logs import LogRecord  # type: ignore

    sev_name = _OTEL_SEVERITY.get(ev.outcome, "INFO")
    sev_num = getattr(SeverityNumber, sev_name, SeverityNumber.INFO)
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    attributes = {
        "mdl.audit.identity": ev.identity,
        "mdl.audit.source": ev.source,
        "mdl.audit.op": ev.op,
        "mdl.audit.target": ev.target,
        "mdl.audit.outcome": ev.outcome,
    }
    if ev.detail:
        attributes["mdl.audit.detail"] = ev.detail
    record = LogRecord(
        timestamp=time.time_ns(),
        trace_id=ctx.trace_id if ctx and ctx.is_valid else 0,
        span_id=ctx.span_id if ctx and ctx.is_valid else 0,
        trace_flags=ctx.trace_flags if ctx and ctx.is_valid else None,
        severity_text=sev_name,
        severity_number=sev_num,
        body="audit",
        attributes=attributes,
    )
    logger.emit(record)
