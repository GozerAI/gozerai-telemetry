"""Tests for distributed tracing."""

import threading
import time

from gozerai_telemetry.tracing import Span, Tracer, span


class TestSpan:
    def test_attributes(self):
        s = Span(name="test", trace_id="abc", span_id="123")
        s.set_attribute("key", "value")
        assert s.attributes["key"] == "value"

    def test_events(self):
        s = Span(name="test", trace_id="abc", span_id="123")
        s.add_event("checkpoint", step=1)
        assert len(s.events) == 1
        assert s.events[0].name == "checkpoint"
        assert s.events[0].attributes["step"] == 1

    def test_error(self):
        s = Span(name="test", trace_id="abc", span_id="123")
        s.set_error(ValueError("bad input"))
        assert s.status == "error"
        assert s.attributes["error.type"] == "ValueError"
        assert s.attributes["error.message"] == "bad input"

    def test_duration(self):
        s = Span(name="test", trace_id="abc", span_id="123")
        s.start_time = 100.0
        s.end_time = 100.5
        assert s.duration_ms == 500.0

    def test_to_dict(self):
        s = Span(name="test", trace_id="abc", span_id="123", service_name="svc")
        s.end()
        d = s.to_dict()
        assert d["trace_id"] == "abc"
        assert d["name"] == "test"
        assert d["service"] == "svc"
        assert d["end_time"] is not None

    def test_duration_no_end(self):
        s = Span(name="test", trace_id="abc", span_id="123")
        s.start_time = time.time() - 0.1  # 100ms ago
        # duration_ms should use current time since end() not called
        assert s.end_time is None
        assert s.duration_ms >= 100.0  # at least 100ms

    def test_multiple_events(self):
        s = Span(name="test", trace_id="abc", span_id="123")
        for i in range(5):
            s.add_event(f"event_{i}", index=i)
        assert len(s.events) == 5
        # Order preserved
        for i in range(5):
            assert s.events[i].name == f"event_{i}"
            assert s.events[i].attributes["index"] == i

    def test_to_dict_with_events(self):
        s = Span(name="test", trace_id="abc", span_id="123")
        s.add_event("start", phase="init")
        s.add_event("end", phase="done")
        s.end()
        d = s.to_dict()
        assert len(d["events"]) == 2
        assert d["events"][0]["name"] == "start"
        assert d["events"][0]["attrs"]["phase"] == "init"
        assert d["events"][1]["name"] == "end"

    def test_set_attribute_overwrite(self):
        s = Span(name="test", trace_id="abc", span_id="123")
        s.set_attribute("key", "first")
        s.set_attribute("key", "second")
        assert s.attributes["key"] == "second"


class TestTracer:
    def test_span_records(self):
        tracer = Tracer("myservice")
        with tracer.span("operation") as s:
            s.set_attribute("key", "val")
        assert len(tracer.get_completed()) == 1
        assert tracer.get_completed()[0].name == "operation"

    def test_nested_spans_share_trace_id(self):
        tracer = Tracer("myservice")
        with tracer.span("parent") as parent:
            with tracer.span("child") as child:
                pass
        completed = tracer.get_completed()
        assert len(completed) == 2
        assert completed[0].trace_id == completed[1].trace_id
        # Child's parent should be the parent span
        child_span = [s for s in completed if s.name == "child"][0]
        parent_span = [s for s in completed if s.name == "parent"][0]
        assert child_span.parent_span_id == parent_span.span_id

    def test_span_error_propagation(self):
        tracer = Tracer("myservice")
        try:
            with tracer.span("failing") as s:
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        completed = tracer.get_completed()
        assert completed[0].status == "error"
        assert completed[0].attributes["error.message"] == "boom"

    def test_max_spans_eviction(self):
        tracer = Tracer("myservice", max_spans=5)
        for i in range(10):
            with tracer.span(f"op_{i}"):
                pass
        assert len(tracer.get_completed()) == 5
        assert tracer.get_completed()[0].name == "op_5"

    def test_clear(self):
        tracer = Tracer("myservice")
        with tracer.span("op"):
            pass
        tracer.clear()
        assert len(tracer.get_completed()) == 0

    def test_get_traces_groups_by_trace_id(self):
        tracer = Tracer("myservice")
        with tracer.span("parent"):
            with tracer.span("child"):
                pass
        traces = tracer.get_traces()
        assert len(traces) == 1  # All in one trace

    def test_initial_attributes(self):
        tracer = Tracer("myservice")
        with tracer.span("op", source="github", count=5) as s:
            pass
        assert tracer.get_completed()[0].attributes["source"] == "github"

    def test_deeply_nested_spans(self):
        tracer = Tracer("myservice")
        depth = 10
        spans = []

        # Build nested spans iteratively using context managers
        import contextlib
        with contextlib.ExitStack() as stack:
            for i in range(depth):
                s = stack.enter_context(tracer.span(f"level_{i}"))
                spans.append(s)

        completed = tracer.get_completed()
        assert len(completed) == depth

        # All share the same trace_id
        trace_ids = {s.trace_id for s in completed}
        assert len(trace_ids) == 1

        # Each span (except root) has correct parent
        span_map = {s.name: s for s in completed}
        for i in range(1, depth):
            child = span_map[f"level_{i}"]
            parent = span_map[f"level_{i - 1}"]
            assert child.parent_span_id == parent.span_id

        # Root has no parent
        assert span_map["level_0"].parent_span_id is None

    def test_span_service_name_propagated(self):
        tracer = Tracer("my_svc")
        with tracer.span("op1") as s1:
            with tracer.span("op2") as s2:
                pass
        for s in tracer.get_completed():
            assert s.service_name == "my_svc"

    def test_concurrent_spans(self):
        tracer = Tracer("conc_svc")
        barrier = threading.Barrier(5)

        def create_span(idx):
            barrier.wait()
            with tracer.span(f"thread_{idx}"):
                pass

        threads = [threading.Thread(target=create_span, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        completed = tracer.get_completed()
        assert len(completed) == 5
        names = {s.name for s in completed}
        assert names == {f"thread_{i}" for i in range(5)}

    def test_get_traces_multiple_traces(self):
        tracer = Tracer("multi_trace")
        # Create separate (non-nested) spans — each gets its own trace_id
        with tracer.span("a"):
            pass
        with tracer.span("b"):
            pass
        with tracer.span("c"):
            pass

        traces = tracer.get_traces()
        assert len(traces) == 3  # Each is its own trace


class TestStandaloneSpan:
    def test_convenience_span(self):
        with span("quick_op", service="test") as s:
            s.set_attribute("done", True)
        assert s.status == "ok"
        assert s.end_time is not None
