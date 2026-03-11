"""Tests for distributed tracing."""

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


class TestStandaloneSpan:
    def test_convenience_span(self):
        with span("quick_op", service="test") as s:
            s.set_attribute("done", True)
        assert s.status == "ok"
        assert s.end_time is not None
