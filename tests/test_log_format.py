"""Tests for structured JSON logging."""

import io
import json
import logging
import time

from gozerai_telemetry.log_format import StructuredFormatter, get_logger, setup_logging


class TestStructuredFormatter:
    def test_outputs_valid_json(self):
        fmt = StructuredFormatter(service_name="test-svc")
        record = logging.LogRecord(
            name="mylogger", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        line = fmt.format(record)
        parsed = json.loads(line)
        assert isinstance(parsed, dict)

    def test_timestamp_is_iso8601(self):
        fmt = StructuredFormatter(service_name="test-svc")
        record = logging.LogRecord(
            name="mylogger", level=logging.INFO, pathname="", lineno=0,
            msg="check ts", args=(), exc_info=None,
        )
        line = fmt.format(record)
        parsed = json.loads(line)
        ts = parsed["timestamp"]
        # ISO8601 must contain 'T' separator and timezone info
        assert "T" in ts
        assert ts.endswith("+00:00") or ts.endswith("Z")

    def test_service_name_in_output(self):
        fmt = StructuredFormatter(service_name="trendscope")
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="hi", args=(), exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert parsed["service"] == "trendscope"

    def test_log_level_mapping(self):
        fmt = StructuredFormatter(service_name="svc")
        for level_name, level_num in [
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
        ]:
            record = logging.LogRecord(
                name="x", level=level_num, pathname="", lineno=0,
                msg="test", args=(), exc_info=None,
            )
            parsed = json.loads(fmt.format(record))
            assert parsed["level"] == level_name

    def test_message_content(self):
        fmt = StructuredFormatter(service_name="svc")
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="order %s processed", args=("ORD-123",), exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert parsed["message"] == "order ORD-123 processed"

    def test_extra_fields_passed_through(self):
        fmt = StructuredFormatter(service_name="svc")
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="extra test", args=(), exc_info=None,
        )
        record.user_id = "u-42"
        record.request_path = "/api/v1/data"
        parsed = json.loads(fmt.format(record))
        assert parsed["user_id"] == "u-42"
        assert parsed["request_path"] == "/api/v1/data"

    def test_trace_context_when_active(self):
        from gozerai_telemetry.tracing import Tracer

        fmt = StructuredFormatter(service_name="svc")
        tracer = Tracer("svc")
        with tracer.span("test_op") as span:
            record = logging.LogRecord(
                name="x", level=logging.INFO, pathname="", lineno=0,
                msg="inside span", args=(), exc_info=None,
            )
            parsed = json.loads(fmt.format(record))
            assert parsed["trace_id"] == span.trace_id
            assert parsed["span_id"] == span.span_id

    def test_no_trace_context_when_inactive(self):
        fmt = StructuredFormatter(service_name="svc")
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="no span", args=(), exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert "trace_id" not in parsed
        assert "span_id" not in parsed

    def test_exception_info_formatted(self):
        fmt = StructuredFormatter(service_name="svc")
        try:
            raise ValueError("bad value")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="x", level=logging.ERROR, pathname="", lineno=0,
            msg="fail", args=(), exc_info=exc_info,
        )
        parsed = json.loads(fmt.format(record))
        assert "exception" in parsed
        assert parsed["exception"]["type"] == "ValueError"
        assert parsed["exception"]["message"] == "bad value"
        assert isinstance(parsed["exception"]["traceback"], list)

    def test_unicode_in_messages(self):
        fmt = StructuredFormatter(service_name="svc")
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="hello unicode: \u00e9\u00e8\u00ea \u4f60\u597d \U0001f600",
            args=(), exc_info=None,
        )
        line = fmt.format(record)
        parsed = json.loads(line)
        assert "\u00e9" in parsed["message"]
        assert "\u4f60\u597d" in parsed["message"]

    def test_very_long_messages(self):
        fmt = StructuredFormatter(service_name="svc")
        long_msg = "x" * 100_000
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg=long_msg, args=(), exc_info=None,
        )
        line = fmt.format(record)
        parsed = json.loads(line)
        assert len(parsed["message"]) == 100_000

    def test_logger_name_in_output(self):
        fmt = StructuredFormatter(service_name="svc")
        record = logging.LogRecord(
            name="myapp.module.sub", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert parsed["logger"] == "myapp.module.sub"

    def test_no_exception_when_exc_info_none(self):
        fmt = StructuredFormatter(service_name="svc")
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="ok", args=(), exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert "exception" not in parsed

    def test_correlation_id_when_set(self):
        from gozerai_telemetry.correlation import CorrelationContext

        fmt = StructuredFormatter(service_name="svc")
        with CorrelationContext("corr-abc-123"):
            record = logging.LogRecord(
                name="x", level=logging.INFO, pathname="", lineno=0,
                msg="correlated", args=(), exc_info=None,
            )
            parsed = json.loads(fmt.format(record))
            assert parsed["correlation_id"] == "corr-abc-123"

    def test_no_correlation_id_when_unset(self):
        from gozerai_telemetry.correlation import set_correlation_id
        set_correlation_id(None)  # Ensure clean state
        fmt = StructuredFormatter(service_name="svc")
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="no corr", args=(), exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert "correlation_id" not in parsed


class TestSetupLogging:
    def test_configures_root_logger(self):
        stream = io.StringIO()
        root = setup_logging("test-svc", level="DEBUG", stream=stream)
        assert root is logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, StructuredFormatter)
        # Clean up
        root.handlers.clear()

    def test_output_is_json(self):
        stream = io.StringIO()
        setup_logging("test-svc", level="INFO", stream=stream)
        logger = logging.getLogger("test_setup")
        logger.info("hello from setup")
        output = stream.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["message"] == "hello from setup"
        assert parsed["service"] == "test-svc"
        # Clean up
        logging.getLogger().handlers.clear()

    def test_replaces_existing_handlers(self):
        root = logging.getLogger()
        root.addHandler(logging.StreamHandler())
        root.addHandler(logging.StreamHandler())
        assert len(root.handlers) >= 2
        stream = io.StringIO()
        setup_logging("svc", stream=stream)
        assert len(root.handlers) == 1
        root.handlers.clear()

    def test_level_case_insensitive(self):
        stream = io.StringIO()
        root = setup_logging("svc", level="warning", stream=stream)
        assert root.level == logging.WARNING
        root.handlers.clear()


class TestGetLogger:
    def test_returns_named_logger(self):
        logger = get_logger("my.module")
        assert logger.name == "my.module"
        assert isinstance(logger, logging.Logger)

    def test_inherits_root_config(self):
        stream = io.StringIO()
        setup_logging("svc", level="DEBUG", stream=stream)
        logger = get_logger("child.module")
        logger.debug("debug msg")
        output = stream.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["level"] == "DEBUG"
        assert parsed["logger"] == "child.module"
        logging.getLogger().handlers.clear()
