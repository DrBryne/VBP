# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import functools
import logging
import os
from collections.abc import Callable
from typing import Any

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Status, StatusCode

# Global tracer instance for the VBP application
tracer = trace.get_tracer("vbp_workflow")

def track_telemetry_span(span_name: str):
    """
    Creates a sub-span in the Cloud Trace waterfall for a function.
    Uses start_span directly without attaching to context to avoid race conditions 
    in high-concurrency async environments.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            # We use start_span which is purely additive and doesn't manipulate 
            # the active context stack, making it safe for high concurrency.
            span = tracer.start_span(span_name)
            if kwargs.get('uri'):
                span.set_attribute("vbp.uri", kwargs.get('uri'))
            elif args and isinstance(args[0], str) and args[0].startswith("gs://"):
                span.set_attribute("vbp.uri", args[0])

            try:
                result = await func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))
                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            finally:
                span.end()

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            with tracer.start_as_current_span(span_name) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator

def setup_telemetry() -> str | None:
    """Configure OpenTelemetry and GenAI telemetry with GCS upload and Cloud Trace/Logging."""
    os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")

    env_name = "cloud" if os.environ.get("AGENT_ENGINE_ID") else "local"

    resource = Resource.create({
        "service.name": "vbp_workflow",
        "service.namespace": "vbp",
        "vbp.environment": env_name,
    })

    # Initialize providers
    tracer_provider = TracerProvider(resource=resource)
    logger_provider = LoggerProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)
    set_logger_provider(logger_provider)

    # 1. Configure Cloud/Local Exporters
    if os.environ.get("AGENT_ENGINE_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT"):
        try:
            from opentelemetry.exporter.cloud_logging import CloudLoggingExporter
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            tracer_provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter()))
            logger_provider.add_log_record_processor(BatchLogRecordProcessor(CloudLoggingExporter()))

            logging.info("OpenTelemetry Cloud Trace and Cloud Logging initialized.")
        except ImportError:
            logging.warning("Cloud Exporters not found. Falling back to local logging.")
            tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            logger_provider.add_log_record_processor(BatchLogRecordProcessor(ConsoleLogExporter()))
    else:
        # Local development fallback
        tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(ConsoleLogExporter()))
        logging.info("OpenTelemetry Console Trace and Console Logging initialized.")

    # Attach OTel LoggingHandler to the root python logger
    handler = LoggingHandler(logger_provider=logger_provider)
    logging.getLogger().addHandler(handler)

    # 2. Existing GenAI metadata logging setup
    bucket = os.environ.get("LOGS_BUCKET_NAME")
    capture_content = os.environ.get(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "NO_CONTENT"
    ).upper()

    if bucket and capture_content in ("SPAN_ONLY", "EVENT_ONLY", "SPAN_AND_EVENT"):
        logging.info(
            f"Prompt-response logging enabled - mode: {capture_content} (metadata and content)"
        )
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = capture_content
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT", "jsonl")
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", "upload")
        os.environ.setdefault(
            "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental"
        )
        commit_sha = os.environ.get("COMMIT_SHA", "dev")
        os.environ.setdefault(
            "OTEL_RESOURCE_ATTRIBUTES",
            f"service.namespace=vbp,service.version={commit_sha}",
        )
        path = os.environ.get("GENAI_TELEMETRY_PATH", "completions")
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
            f"gs://{bucket}/{path}",
        )
    else:
        logging.info(
            "Prompt-response logging disabled (set LOGS_BUCKET_NAME=gs://your-bucket and OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT to enable)"
        )

    return bucket
