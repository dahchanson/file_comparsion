"""Unit tests for display/progress.py."""

from pathlib import Path

from fcompare.display.progress import NoOpProgressReporter, ProgressReporter, make_reporter
from fcompare.engine.models import FileRecord


def make_record(path: str = "/a/f.txt", size: int = 1024) -> FileRecord:
    return FileRecord(path=Path(path), rel_path=Path("f.txt"), size=size, mtime=0.0)


# ---------------------------------------------------------------------------
# NoOpProgressReporter — must be fully silent and never raise
# ---------------------------------------------------------------------------


class TestNoOpProgressReporter:
    def test_walk_phase_yields_callable(self) -> None:
        rep = NoOpProgressReporter()
        with rep.walk_phase("Scanning…") as tick:
            assert callable(tick)

    def test_walk_phase_tick_accepts_file_record(self) -> None:
        rep = NoOpProgressReporter()
        with rep.walk_phase("Scanning…") as tick:
            tick(make_record())  # must not raise

    def test_hash_phase_returns_callable(self) -> None:
        rep = NoOpProgressReporter()
        cb = rep.hash_phase(total_files=10, total_bytes=10240)
        assert callable(cb)

    def test_hash_phase_callback_accepts_file_record(self) -> None:
        rep = NoOpProgressReporter()
        cb = rep.hash_phase(total_files=1, total_bytes=1024)
        cb(make_record())  # must not raise

    def test_print_filter_summary_does_not_raise(self) -> None:
        rep = NoOpProgressReporter()
        rep.print_filter_summary(total=100, candidates=10)  # must not raise

    def test_stop_does_not_raise(self) -> None:
        rep = NoOpProgressReporter()
        rep.stop()  # must not raise

    def test_multiple_walk_phases_independent(self) -> None:
        rep = NoOpProgressReporter()
        with rep.walk_phase("Left…") as tick_left:
            tick_left(make_record("/a/f.txt"))
        with rep.walk_phase("Right…") as tick_right:
            tick_right(make_record("/b/f.txt"))


# ---------------------------------------------------------------------------
# ProgressReporter — verify callback wiring without asserting on Rich internals
# ---------------------------------------------------------------------------


class TestProgressReporter:
    def test_walk_phase_tick_callable_does_not_raise(self) -> None:
        rep = ProgressReporter()
        try:
            with rep.walk_phase("Scanning…") as tick:
                for i in range(5):
                    tick(make_record(f"/a/f{i}.txt"))
        finally:
            rep.stop()

    def test_hash_phase_callback_does_not_raise(self) -> None:
        rep = ProgressReporter()
        try:
            cb = rep.hash_phase(total_files=3, total_bytes=3072)
            for i in range(3):
                cb(make_record(f"/a/f{i}.txt", size=1024))
        finally:
            rep.stop()

    def test_hash_phase_zero_total_bytes_does_not_raise(self) -> None:
        rep = ProgressReporter()
        try:
            cb = rep.hash_phase(total_files=0, total_bytes=0)
            cb(make_record(size=0))
        finally:
            rep.stop()

    def test_print_filter_summary_does_not_raise(self) -> None:
        rep = ProgressReporter()
        try:
            rep.print_filter_summary(total=1000, candidates=50)
        finally:
            rep.stop()

    def test_multiple_walk_phases_sequential(self) -> None:
        rep = ProgressReporter()
        try:
            with rep.walk_phase("Left…") as tick:
                tick(make_record("/a/f.txt"))
            with rep.walk_phase("Right…") as tick:
                tick(make_record("/b/f.txt"))
        finally:
            rep.stop()


# ---------------------------------------------------------------------------
# make_reporter factory
# ---------------------------------------------------------------------------


class TestMakeReporter:
    def test_verbose_true_returns_progress_reporter(self) -> None:
        rep = make_reporter(verbose=True)
        try:
            assert isinstance(rep, ProgressReporter)
        finally:
            rep.stop()

    def test_verbose_false_returns_noop_reporter(self) -> None:
        rep = make_reporter(verbose=False)
        assert isinstance(rep, NoOpProgressReporter)

    def test_noop_reporter_satisfies_same_interface(self) -> None:
        rep = make_reporter(verbose=False)
        with rep.walk_phase("Scanning…") as tick:
            tick(make_record())
        cb = rep.hash_phase(total_files=1, total_bytes=1024)
        cb(make_record())
        rep.print_filter_summary(total=10, candidates=2)
        rep.stop()
