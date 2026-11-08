import pytest

from pennywatch import container


def test_available_memory_is_read_in_gib(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 32000000 kB\nMemAvailable: 8388608 kB\n")
    assert container.available_memory_gib(meminfo) == 8.0


def test_a_kernel_without_the_figure_is_an_error(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 32000000 kB\n")
    with pytest.raises(ValueError, match="MemAvailable"):
        container.available_memory_gib(meminfo)


def test_a_job_starts_straight_away_when_memory_is_free():
    naps = []
    container.wait_for_memory(read=lambda: 8.0, sleep=naps.append)
    assert naps == []


def test_a_job_waits_until_memory_is_free():
    readings = iter([1.0, 3.9, 4.0])
    naps = []
    container.wait_for_memory(read=lambda: next(readings), sleep=naps.append)
    assert naps == [container.MEMORY_POLL_S] * 2


def test_a_job_gives_up_after_the_limit_and_says_why():
    clock = iter([0.0, 1.0, container.MEMORY_WAIT_LIMIT_S + 1.0])
    with pytest.raises(RuntimeError, match=r"only 1.5 GiB"):
        container.wait_for_memory(read=lambda: 1.5, sleep=lambda _: None, now=lambda: next(clock))


def test_the_other_backends_get_exactly_the_environment_they_had(tmp_path):
    plain = container.sandbox_args(0, tmp_path, tmp_path, tmp_path, layout=1)
    assert "PYTHONPATH=/src" in plain
    assert not any("/kokkos" in arg for arg in plain)


def test_only_a_kokkos_run_gets_kokkos_on_its_path(tmp_path):
    args = container.sandbox_args(0, tmp_path, tmp_path, tmp_path, kokkos=tmp_path / "k")
    assert "PYTHONPATH=/kokkos:/src" in args
    assert f"--volume={tmp_path / 'k'}:/kokkos:ro" in args


def test_an_overlay_comes_before_kokkos(tmp_path):
    args = container.sandbox_args(
        0, tmp_path, tmp_path, tmp_path, overlay=tmp_path, kokkos=tmp_path
    )
    assert "PYTHONPATH=/overlay:/kokkos:/src" in args
