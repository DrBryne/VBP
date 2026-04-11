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

import functools
import logging
import os
import asyncio
from typing import Any, Callable
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.resources import Resource

# Global tracer instance for the VBP application
tracer = trace.get_tracer("vbp_workflow")

def track_telemetry_span(span_name: str):
    """
    Creates a sub-span in the Cloud Trace waterfall for a function.
    Automatically captures exceptions and sets the span status.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            # Local console print for immediate insight
            print(f"[Telemetry Start] {span_name}")
            start_time = asyncio.get_event_loop().time()
            
            with tracer.start_as_current_span(span_name) as span:
                if kwargs.get('uri'):
                    span.set_attribute("vbp.uri", kwargs.get('uri'))
                elif args and isinstance(args[0], str) and args[0].startswith("gs://"):
                    span.set_attribute("vbp.uri", args[0])
                
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    elapsed = asyncio.get_event_loop().time() - start_time
                    print(f"[Telemetry End] {span_name} ({elapsed:.2f}s)")
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    print(f"[Telemetry ERROR] {span_name} failed: {e}")
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            print(f"[Telemetry Start] {span_name}")
            import time
            start_time = time.perf_counter()
            
            with tracer.start_as_current_span(span_name) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    elapsed = time.perf_counter() - start_time
                    print(f"[Telemetry End] {span_name} ({elapsed:.2f}s)")
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    print(f"[Telemetry ERROR] {span_name} failed: {e}")
                    raise

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator

def setup_telemetry() -> str | None:
    """Configure OpenTelemetry and GenAI telemetry with GCS upload and Cloud Trace."""
    os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")

    # 1. Initialize Cloud Trace if running in a managed environment
    if os.environ.get("AGENT_ENGINE_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT"):
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            resource = Resource.create({
                "service.name": "vbp_workflow",
                "service.namespace": "vbp",
            })

            provider = TracerProvider(resource=resource)
            exporter = CloudTraceSpanExporter()
            processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(processor)
            trace.set_tracer_provider(provider)
            logging.info("OpenTelemetry Cloud Trace initialized.")
        except ImportError:
            logging.warning("CloudTraceSpanExporter not found. Falling back to local logging.")
    else:
        # Local development fallback: SimpleSpanProcessor for immediate visibility
        provider = TracerProvider()
        processor = SimpleSpanProcessor(ConsoleSpanExporter())
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        logging.info("OpenTelemetry Console Trace initialized (SimpleSpanProcessor).")

    # 2. Existing GenAI metadata logging setup
    bucket = os.environ.get("LOGS_BUCKET_NAME")
    capture_content = os.environ.get(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false"
    )
    if bucket and capture_content != "false":
        logging.info(
            "Prompt-response logging enabled - mode: NO_CONTENT (metadata only, no prompts/responses)"
        )
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
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
