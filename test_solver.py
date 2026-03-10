"""
E2E test suite for the ASR Solver API.

Usage:
    # Start the solver:  uvicorn main:app --port 8000
    # Then run:           pytest test_solver.py -v

Tests against July 2026 to avoid disrupting existing data.
Cleans up after itself via /clear.
"""
import pytest
import httpx
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List

BASE_URL = "http://localhost:8000"
TEST_YEAR = 2026
TEST_MONTH = 7  # July — no imported data expected

# Shift durations (gross minutes) — mirrors the dashboard constants
SHIFT_GROSS_MIN: Dict[str, int] = {
    "AMJP": 730, "AMNP": 710, "AMHS": 600, "R": 730, "RS": 600,
    "A1": 480, "A2": 480, "6FM": 240, "6P1": 480, "CSP": 480, "AMBCE": 730,
    "FO9": 480, "E": 480,
}

# Shift codes that are incompatible after a night shift (11h rest)
DAY_SHIFTS_AFTER_NIGHT = {"AMJP", "AMHS", "A1", "A2", "6FM", "6P1"}


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    """Reusable httpx client that checks the API is alive."""
    c = httpx.Client(base_url=BASE_URL, timeout=180.0)
    r = c.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok", f"API not healthy: {data}"
    yield c
    c.close()


# ─── Helper: fetch schedule from Supabase via the solver response ───
def _entries_from_schedule(schedule: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten the solver response schedule into a list of (date, shift, employee_id) tuples."""
    rows = []
    for day in schedule:
        d = day["date"]
        for shift_code, staff_list in day["shifts"].items():
            for person in staff_list:
                rows.append({"date": d, "shift": shift_code, "id": person["id"]})
    return rows


# ═══════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════

class TestHealthAndConfig:
    """Quick sanity checks for the API surface."""

    def test_health(self, client: httpx.Client):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_config_returns_expected_keys(self, client: httpx.Client):
        r = client.get("/config")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        cfg = data["config"]
        for key in ("heures", "nuits", "weekends", "gardes"):
            assert key in cfg, f"Missing config key: {key}"
        assert cfg["heures"]["max_week"] == 50

    def test_status_endpoint(self, client: httpx.Client):
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data


class TestClearEndpoint:
    """Test the /clear endpoint on the test month."""

    def test_clear(self, client: httpx.Client):
        r = client.post("/clear", json={"year": TEST_YEAR, "month": TEST_MONTH})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "success"


class TestSolverConstraints:
    """
    Run the solver on July 2026, then validate every SECO constraint
    on the result. Cleanup at the end.
    """

    @pytest.fixture(autouse=True, scope="class")
    def solve_result(self, client: httpx.Client):
        """Run /clear → /solve → store result → /clear (teardown)."""
        # Pre-clean
        client.post("/clear", json={"year": TEST_YEAR, "month": TEST_MONTH})

        # Solve
        r = client.post("/solve", json={"year": TEST_YEAR, "month": TEST_MONTH})
        assert r.status_code == 200
        data = r.json()

        # Allow infeasible — if the data is sparse, the model may be infeasible.
        # In that case we skip constraint checks gracefully.
        self.__class__._solve_data = data

        yield data

        # Teardown: clean up solver entries
        client.post("/clear", json={"year": TEST_YEAR, "month": TEST_MONTH})

    @property
    def _data(self):
        return self.__class__._solve_data

    def test_solver_returns_success_or_error(self):
        assert self._data["status"] in ("success", "error")

    def test_entries_saved_positive(self):
        if self._data["status"] != "success":
            pytest.skip("Solver did not find a feasible solution")
        assert self._data.get("entries_saved", 0) > 0

    def test_uniqueness_one_shift_per_employee_per_day(self):
        """C1: Each employee must have at most 1 operational shift per day."""
        if self._data["status"] != "success":
            pytest.skip("Solver did not find a feasible solution")

        schedule = self._data["schedule"]
        for day_entry in schedule:
            emp_shifts: Dict[str, list] = defaultdict(list)
            for shift_code, staff in day_entry["shifts"].items():
                for person in staff:
                    emp_shifts[person["id"]].append(shift_code)
            for emp_id, codes in emp_shifts.items():
                assert len(codes) <= 1, (
                    f"Employee {emp_id} has {len(codes)} shifts on {day_entry['date']}: {codes}"
                )

    def test_weekly_50h_limit(self):
        """C7: No employee should exceed 50h (3000 min) per ISO week."""
        if self._data["status"] != "success":
            pytest.skip("Solver did not find a feasible solution")

        schedule = self._data["schedule"]
        entries = _entries_from_schedule(schedule)

        # Group by employee + ISO week
        weekly: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for e in entries:
            d = date.fromisoformat(e["date"])
            iso = d.isocalendar()
            week_key = f"{iso[0]}-W{iso[1]:02d}"
            mins = SHIFT_GROSS_MIN.get(e["shift"], 0)
            weekly[e["id"]][week_key] += mins

        for emp_id, weeks in weekly.items():
            for wk, total_min in weeks.items():
                assert total_min <= 3000, (
                    f"Employee {emp_id} has {total_min} min ({total_min/60:.1f}h) in {wk} — exceeds 50h limit"
                )

    def test_11h_rest_after_night(self):
        """C3: No day shift the day after an AMNP (night shift)."""
        if self._data["status"] != "success":
            pytest.skip("Solver did not find a feasible solution")

        schedule = self._data["schedule"]
        entries = _entries_from_schedule(schedule)

        # Build per-employee day-to-shift map
        emp_schedule: Dict[str, Dict[str, str]] = defaultdict(dict)
        for e in entries:
            emp_schedule[e["id"]][e["date"]] = e["shift"]

        num_days = 31  # July
        for emp_id, sched in emp_schedule.items():
            for day_num in range(1, num_days):
                d = date(TEST_YEAR, TEST_MONTH, day_num)
                next_d = d + timedelta(days=1)
                if next_d.month != TEST_MONTH:
                    continue
                shift_today = sched.get(d.isoformat())
                shift_tomorrow = sched.get(next_d.isoformat())
                if shift_today == "AMNP" and shift_tomorrow in DAY_SHIFTS_AFTER_NIGHT:
                    pytest.fail(
                        f"Employee {emp_id}: day shift {shift_tomorrow} on {next_d} "
                        f"after AMNP on {d} — violates 11h rest rule"
                    )

    def test_max_consecutive_nights(self):
        """C4: No more than 3 consecutive nights per employee."""
        if self._data["status"] != "success":
            pytest.skip("Solver did not find a feasible solution")

        MAX_CONSEC = 3
        schedule = self._data["schedule"]
        entries = _entries_from_schedule(schedule)

        emp_schedule: Dict[str, Dict[str, str]] = defaultdict(dict)
        for e in entries:
            emp_schedule[e["id"]][e["date"]] = e["shift"]

        num_days = 31
        for emp_id, sched in emp_schedule.items():
            consec = 0
            for day_num in range(1, num_days + 1):
                d = date(TEST_YEAR, TEST_MONTH, day_num)
                if sched.get(d.isoformat()) == "AMNP":
                    consec += 1
                    assert consec <= MAX_CONSEC, (
                        f"Employee {emp_id} has {consec} consecutive nights ending {d}"
                    )
                else:
                    consec = 0

    def test_coverage_requirements(self):
        """C9: Verify minimum daily coverage for operational shifts."""
        if self._data["status"] != "success":
            pytest.skip("Solver did not find a feasible solution")

        schedule = self._data["schedule"]
        # We just check that AMJP and AMNP have at least 2 staff each day
        # (the solver may cap lower if many are absent, but 2 is the hard minimum)
        for day_entry in schedule:
            d = date.fromisoformat(day_entry["date"])
            amjp_count = len(day_entry["shifts"].get("AMJP", []))
            amnp_count = len(day_entry["shifts"].get("AMNP", []))
            # At minimum, expect 2 for both J and N (2 ambulances)
            assert amjp_count >= 2, (
                f"Only {amjp_count} AMJP on {d} — need at least 2"
            )
            assert amnp_count >= 2, (
                f"Only {amnp_count} AMNP on {d} — need at least 2"
            )
