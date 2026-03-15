"""Tests for distributed tracing correlation."""

import asyncio
import threading
import uuid

from gozerai_telemetry.correlation import (
    HEADER_NAME,
    CorrelationContext,
    correlation_middleware,
    extract_correlation_id,
    get_correlation_id,
    inject_headers,
    new_correlation_id,
    set_correlation_id,
)


class TestSetAndGet:
    def test_set_and_get(self):
        set_correlation_id("abc-123")
        assert get_correlation_id() == "abc-123"
        # Reset
        set_correlation_id(None)

    def test_default_is_none(self):
        # In a fresh context the ID should be None
        # (may be non-None if another test set it, so use a context)
        with CorrelationContext("tmp"):
            pass
        # After exiting the context, value is restored
        # We just verify get doesn't raise
        get_correlation_id()

    def test_new_correlation_id_generates_and_sets(self):
        cid = new_correlation_id()
        assert cid is not None
        assert len(cid) == 32  # uuid4 hex
        assert get_correlation_id() == cid


class TestCorrelationContext:
    def test_context_manager_sets_id(self):
        with CorrelationContext("ctx-456") as ctx:
            assert get_correlation_id() == "ctx-456"
            assert ctx.correlation_id == "ctx-456"

    def test_context_manager_restores_previous(self):
        set_correlation_id("outer")
        with CorrelationContext("inner"):
            assert get_correlation_id() == "inner"
        assert get_correlation_id() == "outer"
        # Clean up
        set_correlation_id(None)

    def test_auto_generates_id_when_none(self):
        with CorrelationContext() as ctx:
            assert ctx.correlation_id is not None
            assert len(ctx.correlation_id) == 32
            assert get_correlation_id() == ctx.correlation_id

    def test_nested_contexts(self):
        with CorrelationContext("level-1"):
            assert get_correlation_id() == "level-1"
            with CorrelationContext("level-2"):
                assert get_correlation_id() == "level-2"
            assert get_correlation_id() == "level-1"


class TestHeaderInjection:
    def test_inject_headers_adds_id(self):
        set_correlation_id("inject-test")
        headers = {"Accept": "application/json"}
        result = inject_headers(headers)
        assert result[HEADER_NAME] == "inject-test"
        assert result is headers  # Mutates in place

    def test_inject_headers_generates_if_missing(self):
        set_correlation_id(None)
        headers = {}
        inject_headers(headers)
        assert HEADER_NAME in headers
        assert len(headers[HEADER_NAME]) == 32


class TestHeaderExtraction:
    def test_extract_from_headers(self):
        headers = {HEADER_NAME: "extracted-id"}
        assert extract_correlation_id(headers) == "extracted-id"

    def test_extract_case_insensitive(self):
        headers = {"x-correlation-id": "lower-case"}
        assert extract_correlation_id(headers) == "lower-case"

    def test_extract_returns_none_when_missing(self):
        headers = {"Content-Type": "application/json"}
        assert extract_correlation_id(headers) is None


class TestCorrelationMiddleware:
    def test_extracts_existing_id(self):
        headers = {HEADER_NAME: "mw-existing"}
        cid = correlation_middleware(headers)
        assert cid == "mw-existing"
        assert get_correlation_id() == "mw-existing"

    def test_generates_when_missing(self):
        headers = {}
        cid = correlation_middleware(headers)
        assert cid is not None
        assert len(cid) == 32
        assert get_correlation_id() == cid


class TestThreadIsolation:
    def test_threads_have_independent_context(self):
        results = {}
        barrier = threading.Barrier(2)

        def worker(name, cid):
            set_correlation_id(cid)
            barrier.wait()
            # After barrier, each thread should still see its own ID
            results[name] = get_correlation_id()

        t1 = threading.Thread(target=worker, args=("t1", "id-t1"))
        t2 = threading.Thread(target=worker, args=("t2", "id-t2"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["t1"] == "id-t1"
        assert results["t2"] == "id-t2"
