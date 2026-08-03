"""Prometheus metrics.

Label cardinality is kept deliberately low: no user ids, no project ids, no
filenames — only bounded enumerations (tool, stage, provider, status).
"""

from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import REGISTRY as DEFAULT_REGISTRY

from picglot.core.config import settings

registry: CollectorRegistry = DEFAULT_REGISTRY

http_requests_total = Counter(
    "picglot_http_requests_total",
    "HTTP requests handled",
    ["method", "route", "status"],
)
http_request_duration = Histogram(
    "picglot_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
jobs_total = Counter(
    "picglot_jobs_total",
    "Jobs by type and terminal status",
    ["type", "status"],
)
job_duration = Histogram(
    "picglot_job_duration_seconds",
    "End to end job duration",
    ["type"],
    buckets=(1, 2.5, 5, 10, 20, 40, 80, 160, 320, 640),
)
job_stage_duration = Histogram(
    "picglot_job_stage_duration_seconds",
    "Duration of a single pipeline stage",
    ["stage"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
pages_processed_total = Counter(
    "picglot_pages_processed_total",
    "Pages pushed through the pipeline",
    ["tool"],
)
provider_calls_total = Counter(
    "picglot_provider_calls_total",
    "Provider invocations",
    ["kind", "provider", "status"],
)
provider_latency = Histogram(
    "picglot_provider_latency_seconds",
    "Provider call latency",
    ["kind", "provider"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
provider_cost_usd = Counter(
    "picglot_provider_cost_usd_total",
    "Estimated provider spend",
    ["kind", "provider"],
)
provider_daily_cost_usd = Gauge(
    "picglot_provider_daily_cost_usd",
    "Estimated provider spend today",
    ["kind", "provider"],
)
provider_daily_cost_limit_usd = Gauge(
    "picglot_provider_daily_cost_limit_usd",
    "Configured daily spend cap",
    ["kind", "provider"],
)
provider_circuit_open = Gauge(
    "picglot_provider_circuit_open",
    "1 when the circuit breaker for a provider is open",
    ["kind", "provider"],
)
queue_depth = Gauge(
    "picglot_queue_depth",
    "Queued jobs awaiting a worker",
    ["queue"],
)
worker_heartbeat_age = Gauge(
    "picglot_worker_heartbeat_age_seconds",
    "Seconds since the most recent worker heartbeat",
)
credits_consumed_total = Counter(
    "picglot_credits_consumed_total",
    "Credits debited",
    ["reason"],
)
credits_refunded_total = Counter(
    "picglot_credits_refunded_total",
    "Credits refunded",
    ["reason"],
)
translation_characters_total = Counter(
    "picglot_translation_characters_total",
    "Characters submitted for translation",
    ["provider"],
)
webhook_delivery_failures_total = Counter(
    "picglot_webhook_delivery_failures_total",
    "Outbound webhook deliveries that failed",
    ["event"],
)
payment_webhook_failures_total = Counter(
    "picglot_payment_webhook_failures_total",
    "Inbound payment webhooks rejected or failed",
    ["provider", "reason"],
)
rate_limit_hits_total = Counter(
    "picglot_rate_limit_hits_total",
    "Requests rejected by rate limiting",
    ["scope"],
)
db_up = Gauge("picglot_db_up", "1 when the database responds to a health probe")
storage_used_bytes = Gauge("picglot_storage_used_bytes", "Bytes stored in object storage")
storage_capacity_bytes = Gauge("picglot_storage_capacity_bytes", "Configured storage capacity")
cache_hits_total = Counter("picglot_cache_hits_total", "Cache hits", ["cache"])
cache_misses_total = Counter("picglot_cache_misses_total", "Cache misses", ["cache"])


@contextmanager
def observe_stage(stage: str):
    started = perf_counter()
    try:
        yield
    finally:
        job_stage_duration.labels(stage=stage).observe(perf_counter() - started)


@contextmanager
def observe_provider(kind: str, provider: str):
    started = perf_counter()
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        elapsed = perf_counter() - started
        provider_latency.labels(kind=kind, provider=provider).observe(elapsed)
        provider_calls_total.labels(kind=kind, provider=provider, status=status).inc()


def render() -> bytes:
    return generate_latest(registry)


def enabled() -> bool:
    return settings.metrics_enabled


CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def snapshot() -> dict[str, Any]:
    """Small JSON view used by the admin dashboard and the status page."""
    values: dict[str, Any] = {}
    for metric in registry.collect():
        for sample in metric.samples:
            if sample.name.endswith(("_created", "_bucket")):
                continue
            key = sample.name
            if sample.labels:
                key += "{" + ",".join(f"{k}={v}" for k, v in sorted(sample.labels.items())) + "}"
            values[key] = sample.value
    return values
