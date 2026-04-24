import asyncio
import functools

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

# 1. Setup
provider = TracerProvider()
processor = SimpleSpanProcessor(ConsoleSpanExporter())
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("test_tracer")

# 2. Decorator
def track_test_span(name: str):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(name):
                return await func(*args, **kwargs)
        return wrapper
    return decorator

# 3. Target
@track_test_span("Test: HelloWorld")
async def hello():
    print("Hello from function!")
    await asyncio.sleep(0.1)

if __name__ == "__main__":
    print("Starting OTel Test...")
    asyncio.run(hello())
    print("OTel Test Finished.")
