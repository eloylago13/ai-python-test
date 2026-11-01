from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Counters are intentionally low-cardinality. Route templates are used for the
# HTTP histogram instead of raw paths so request IDs do not explode the metric
# cardinality.
REQUESTS_CREATED = Counter(
    "notification_requests_created",
    "Notification requests accepted by the API.",
)
REQUESTS_PROCESSED = Counter(
    "notification_requests_processed",
    "Notification requests that reached a terminal state.",
    ["result"],
)
EXTRACTION_FAILURES = Counter(
    "notification_extraction_failures",
    "Extraction failures by error code.",
    ["code"],
)
DELIVERY_FAILURES = Counter(
    "notification_delivery_failures",
    "Delivery failures by error code.",
    ["code"],
)
EXTRACTION_SOURCES = Counter(
    "notification_extraction_sources",
    "Successful extractions by source.",
    ["source"],
)
PROVIDER_RETRIES = Counter(
    "notification_provider_retries",
    "Provider retry attempts by operation.",
    ["operation"],
)
QUEUE_DEPTH = Gauge(
    "notification_queue_depth",
    "Current number of items waiting in a pipeline queue.",
    ["queue"],
)
HTTP_DURATION = Histogram(
    "notification_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["method", "route", "status"],
)
