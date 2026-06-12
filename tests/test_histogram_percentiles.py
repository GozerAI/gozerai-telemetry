"""Tests for Histogram percentile queries."""

import pytest

from gozerai_telemetry.metrics import Histogram


class TestHistogramPercentile:
    def test_get_percentile_p50(self):
        h = Histogram("test", buckets=(1.0, 5.0, 10.0))
        for v in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
            h.observe(v)
        p50 = h.get_percentile(50)
        assert 5.0 <= p50 <= 6.0  # median of 1..10

    def test_get_percentile_p0(self):
        h = Histogram("test", buckets=(1.0, 5.0, 10.0))
        for v in [10, 20, 30]:
            h.observe(v)
        assert h.get_percentile(0) == 10.0

    def test_get_percentile_p100(self):
        h = Histogram("test", buckets=(1.0, 5.0, 10.0))
        for v in [10, 20, 30]:
            h.observe(v)
        assert h.get_percentile(100) == 30.0

    def test_get_percentile_single_observation(self):
        h = Histogram("test", buckets=(1.0,))
        h.observe(42.0)
        assert h.get_percentile(50) == 42.0
        assert h.get_percentile(0) == 42.0
        assert h.get_percentile(100) == 42.0

    def test_get_percentile_two_observations(self):
        h = Histogram("test", buckets=(10.0,))
        h.observe(0.0)
        h.observe(10.0)
        assert h.get_percentile(0) == 0.0
        assert h.get_percentile(50) == 5.0
        assert h.get_percentile(100) == 10.0

    def test_get_percentile_linear_interpolation(self):
        h = Histogram("test", buckets=(100.0,))
        # Values: 0, 10, 20, 30, 40
        for v in [0, 10, 20, 30, 40]:
            h.observe(v)
        # p25: rank = 0.25 * 4 = 1.0, so index 1 = 10.0
        assert h.get_percentile(25) == 10.0
        # p75: rank = 0.75 * 4 = 3.0, so index 3 = 30.0
        assert h.get_percentile(75) == 30.0

    def test_get_percentile_interpolation_fractional(self):
        h = Histogram("test", buckets=(100.0,))
        # Values: 0, 10, 20
        for v in [0, 10, 20]:
            h.observe(v)
        # p50: rank = 0.5 * 2 = 1.0, index 1 = 10.0
        assert h.get_percentile(50) == 10.0
        # p25: rank = 0.25 * 2 = 0.5, lerp(0, 10, 0.5) = 5.0
        assert h.get_percentile(25) == 5.0

    def test_get_percentile_out_of_range_low(self):
        h = Histogram("test", buckets=(1.0,))
        h.observe(1.0)
        with pytest.raises(ValueError, match="between 0 and 100"):
            h.get_percentile(-1)

    def test_get_percentile_out_of_range_high(self):
        h = Histogram("test", buckets=(1.0,))
        h.observe(1.0)
        with pytest.raises(ValueError, match="between 0 and 100"):
            h.get_percentile(101)

    def test_get_percentile_no_observations(self):
        h = Histogram("test", buckets=(1.0,))
        with pytest.raises(ValueError, match="No observations"):
            h.get_percentile(50)

    def test_get_percentile_with_labels(self):
        h = Histogram("test", buckets=(100.0,))
        for v in [1, 2, 3]:
            h.observe(v, endpoint="/api")
        for v in [10, 20, 30]:
            h.observe(v, endpoint="/health")
        p50_api = h.get_percentile(50, endpoint="/api")
        p50_health = h.get_percentile(50, endpoint="/health")
        assert p50_api == 2.0
        assert p50_health == 20.0

    def test_get_percentile_no_observations_for_label(self):
        h = Histogram("test", buckets=(100.0,))
        h.observe(1.0, endpoint="/api")
        with pytest.raises(ValueError, match="No observations"):
            h.get_percentile(50, endpoint="/missing")


class TestHistogramGetPercentiles:
    def test_get_percentiles_returns_p50_p95_p99(self):
        h = Histogram("test", buckets=(1000.0,))
        for v in range(100):
            h.observe(v)
        result = h.get_percentiles()
        assert "p50" in result
        assert "p95" in result
        assert "p99" in result

    def test_get_percentiles_values_ordered(self):
        h = Histogram("test", buckets=(1000.0,))
        for v in range(1000):
            h.observe(v)
        result = h.get_percentiles()
        assert result["p50"] <= result["p95"] <= result["p99"]

    def test_get_percentiles_approximate_values(self):
        h = Histogram("test", buckets=(1000.0,))
        for v in range(100):
            h.observe(v)
        result = h.get_percentiles()
        # p50 should be around 49-50
        assert 48 <= result["p50"] <= 51
        # p95 should be around 94-95
        assert 93 <= result["p95"] <= 96
        # p99 should be around 98-99
        assert 97 <= result["p99"] <= 100

    def test_get_percentiles_with_labels(self):
        h = Histogram("test", buckets=(1000.0,))
        for v in range(100):
            h.observe(v, endpoint="/api")
        result = h.get_percentiles(endpoint="/api")
        assert "p50" in result
        assert result["p50"] <= result["p95"] <= result["p99"]

    def test_get_percentiles_no_observations(self):
        h = Histogram("test", buckets=(1.0,))
        with pytest.raises(ValueError):
            h.get_percentiles()


class TestHistogramObservationsBounded:
    def test_max_observations_default(self):
        h = Histogram("test", buckets=(100.0,))
        assert h._max_observations == 10_000

    def test_max_observations_custom(self):
        h = Histogram("test", buckets=(100.0,), max_observations=100)
        assert h._max_observations == 100

    def test_observations_fifo_bounded(self):
        h = Histogram("test", buckets=(100.0,), max_observations=5)
        for v in range(10):
            h.observe(v)
        key = ()
        # Should only retain last 5: [5, 6, 7, 8, 9]
        assert len(h._observations[key]) == 5
        assert list(h._observations[key]) == [5, 6, 7, 8, 9]

    def test_percentile_uses_recent_observations(self):
        h = Histogram("test", buckets=(100.0,), max_observations=5)
        # Add 10 values, only last 5 kept
        for v in range(10):
            h.observe(v)
        # Observations are [5, 6, 7, 8, 9]
        assert h.get_percentile(0) == 5.0
        assert h.get_percentile(100) == 9.0

    def test_bucket_counts_not_bounded(self):
        """Bucket counts should reflect ALL observations, not just the retained ones."""
        h = Histogram("test", buckets=(5.0, 10.0), max_observations=3)
        for v in range(10):
            h.observe(v)
        key = ()
        # Total count should be 10 (all observations)
        assert h._totals[key] == 10
        # Sum should be 0+1+...+9 = 45
        assert h._sums[key] == 45.0

    def test_large_observation_set(self):
        h = Histogram("test", buckets=(1000.0,), max_observations=1000)
        for v in range(5000):
            h.observe(v)
        key = ()
        assert len(h._observations[key]) == 1000
        assert h._totals[key] == 5000
        # Last 1000 values: 4000..4999
        assert h.get_percentile(0) == 4000.0
        assert h.get_percentile(100) == 4999.0


class TestHistogramBucketAndPercentileCoexistence:
    def test_buckets_still_work(self):
        """Existing bucket-based counting should be unaffected."""
        h = Histogram("duration", buckets=(0.1, 0.5, 1.0))
        h.observe(0.05)
        h.observe(0.3)
        h.observe(0.8)
        key = ()
        assert h._totals[key] == 3
        assert h._sums[key] == pytest.approx(0.05 + 0.3 + 0.8)

    def test_prometheus_export_unaffected(self):
        """Prometheus export should still work with observations stored."""
        h = Histogram("req_duration", "Request duration", buckets=(0.1, 1.0))
        h.observe(0.05)
        h.observe(0.5)
        text = h.to_prometheus()
        assert "req_duration_bucket" in text
        assert "req_duration_sum" in text
        assert "req_duration_count" in text

    def test_time_context_manager_records_observation(self):
        h = Histogram("op_duration", buckets=(10.0,))
        with h.time():
            pass
        key = ()
        assert len(h._observations[key]) == 1
        assert h.get_percentile(50) > 0

    def test_concurrent_observe_and_percentile(self):
        """Thread safety: observe and get_percentile concurrently."""
        import threading

        h = Histogram("conc", buckets=(1000.0,), max_observations=1000)
        errors = []

        def writer():
            for v in range(500):
                h.observe(v)

        def reader():
            try:
                for _ in range(100):
                    try:
                        h.get_percentile(50)
                    except ValueError:
                        pass  # No observations yet, that's fine
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert h._totals[()] == 2000  # 4 writers * 500
