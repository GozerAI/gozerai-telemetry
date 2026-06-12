"""Tests for async tracing support."""

import asyncio

import pytest

from gozerai_telemetry.tracing import Span, Tracer, _current_span


pytestmark = pytest.mark.asyncio


class TestAsyncSpanBasic:
    async def test_creates_span(self):
        tracer = Tracer("test-svc")
        async with tracer.async_span("op") as s:
            assert isinstance(s, Span)
            assert s.name == "op"
            assert s.service_name == "test-svc"

    async def test_span_recorded_on_exit(self):
        tracer = Tracer("test-svc")
        async with tracer.async_span("op") as s:
            pass
        completed = tracer.get_completed()
        assert len(completed) == 1
        assert completed[0] is s
        assert s.end_time is not None

    async def test_span_attributes(self):
        tracer = Tracer("svc")
        async with tracer.async_span("op", region="us-east") as s:
            s.set_attribute("extra", 42)
        assert s.attributes["region"] == "us-east"
        assert s.attributes["extra"] == 42

    async def test_span_events(self):
        tracer = Tracer("svc")
        async with tracer.async_span("op") as s:
            s.add_event("checkpoint", step=1)
        assert len(s.events) == 1
        assert s.events[0].name == "checkpoint"

    async def test_span_status_ok(self):
        tracer = Tracer("svc")
        async with tracer.async_span("op") as s:
            pass
        assert s.status == "ok"


class TestAsyncSpanErrorHandling:
    async def test_error_sets_status(self):
        tracer = Tracer("svc")
        with pytest.raises(ValueError, match="boom"):
            async with tracer.async_span("op") as s:
                raise ValueError("boom")
        assert s.status == "error"
        assert s.attributes["error.type"] == "ValueError"
        assert s.attributes["error.message"] == "boom"

    async def test_error_still_records(self):
        tracer = Tracer("svc")
        with pytest.raises(RuntimeError):
            async with tracer.async_span("op"):
                raise RuntimeError("fail")
        assert len(tracer.get_completed()) == 1

    async def test_error_still_ends_span(self):
        tracer = Tracer("svc")
        with pytest.raises(RuntimeError):
            async with tracer.async_span("op") as s:
                raise RuntimeError("fail")
        assert s.end_time is not None


class TestAsyncSpanContextPropagation:
    async def test_sets_current_span(self):
        tracer = Tracer("svc")
        async with tracer.async_span("outer") as s:
            assert _current_span.get() is s
        assert _current_span.get() is None

    async def test_nested_spans_share_trace_id(self):
        tracer = Tracer("svc")
        async with tracer.async_span("outer") as outer:
            async with tracer.async_span("inner") as inner:
                pass
        assert inner.trace_id == outer.trace_id
        assert inner.parent_span_id == outer.span_id

    async def test_nested_restores_parent(self):
        tracer = Tracer("svc")
        async with tracer.async_span("outer") as outer:
            async with tracer.async_span("inner"):
                pass
            assert _current_span.get() is outer
        assert _current_span.get() is None

    async def test_root_span_has_no_parent(self):
        tracer = Tracer("svc")
        async with tracer.async_span("root") as s:
            pass
        assert s.parent_span_id is None

    async def test_three_level_nesting(self):
        tracer = Tracer("svc")
        async with tracer.async_span("l1") as l1:
            async with tracer.async_span("l2") as l2:
                async with tracer.async_span("l3") as l3:
                    pass
        assert l1.parent_span_id is None
        assert l2.parent_span_id == l1.span_id
        assert l3.parent_span_id == l2.span_id
        assert l1.trace_id == l2.trace_id == l3.trace_id


class TestAsyncSpanDuration:
    async def test_duration_positive(self):
        tracer = Tracer("svc")
        async with tracer.async_span("op") as s:
            await asyncio.sleep(0.01)
        assert s.duration_ms > 0

    async def test_to_dict_after_end(self):
        tracer = Tracer("svc")
        async with tracer.async_span("op") as s:
            pass
        d = s.to_dict()
        assert d["name"] == "op"
        assert d["end_time"] is not None
        assert d["duration_ms"] > 0


class TestAsyncSpanWithSyncSpan:
    """Async and sync spans should share the same context."""

    async def test_async_under_sync(self):
        tracer = Tracer("svc")
        with tracer.span("sync-outer") as outer:
            async with tracer.async_span("async-inner") as inner:
                pass
        assert inner.parent_span_id == outer.span_id
        assert inner.trace_id == outer.trace_id

    async def test_sync_under_async(self):
        tracer = Tracer("svc")
        async with tracer.async_span("async-outer") as outer:
            with tracer.span("sync-inner") as inner:
                pass
        assert inner.parent_span_id == outer.span_id
        assert inner.trace_id == outer.trace_id


class TestAsyncSpanMaxSpans:
    async def test_respects_max_spans(self):
        tracer = Tracer("svc", max_spans=3)
        for i in range(5):
            async with tracer.async_span(f"op-{i}"):
                pass
        assert len(tracer.get_completed()) == 3

    async def test_keeps_most_recent(self):
        tracer = Tracer("svc", max_spans=2)
        for i in range(4):
            async with tracer.async_span(f"op-{i}"):
                pass
        names = [s.name for s in tracer.get_completed()]
        assert names == ["op-2", "op-3"]


class TestAsyncSpanConcurrency:
    async def test_concurrent_tasks_independent_context(self):
        """Each async task should get its own span context."""
        tracer = Tracer("svc")
        results = {}

        async def work(name: str):
            async with tracer.async_span(name) as s:
                await asyncio.sleep(0.01)
                results[name] = _current_span.get()
                assert _current_span.get() is s

        await asyncio.gather(work("a"), work("b"), work("c"))

        # Each task had its own span
        assert results["a"].name == "a"
        assert results["b"].name == "b"
        assert results["c"].name == "c"
        assert len(tracer.get_completed()) == 3

    async def test_concurrent_nested_spans(self):
        tracer = Tracer("svc")

        async def nested_work(prefix: str):
            async with tracer.async_span(f"{prefix}-outer") as outer:
                async with tracer.async_span(f"{prefix}-inner") as inner:
                    await asyncio.sleep(0.01)
                    assert inner.parent_span_id == outer.span_id

        await asyncio.gather(nested_work("a"), nested_work("b"))
        assert len(tracer.get_completed()) == 4


class TestAsyncSpanGetTraces:
    async def test_groups_by_trace_id(self):
        tracer = Tracer("svc")
        async with tracer.async_span("root") as root:
            async with tracer.async_span("child"):
                pass
        traces = tracer.get_traces()
        assert root.trace_id in traces
        assert len(traces[root.trace_id]) == 2
