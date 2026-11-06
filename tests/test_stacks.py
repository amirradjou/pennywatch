import tomllib

from pennywatch.stacks import Stack, exact_pins, locked_versions, project_file

DEV_REQUIRES = [
    "pennylane==0.46.0-dev142",
    "pennylane-lightning==0.46.0-dev34",
    "pennylane-lightning-kokkos==0.46.0-dev34",
    "jax==0.7.1",
    "numpy>2.0.0",
    "scipy-openblas32<0.3.33,>=0.3.26",
]


def test_exact_pins_keep_only_exact_requirements_and_normalise_dev_versions():
    assert exact_pins(DEV_REQUIRES) == {
        "pennylane": "0.46.0.dev142",
        "pennylane-lightning": "0.46.0.dev34",
        "pennylane-lightning-kokkos": "0.46.0.dev34",
        "jax": "0.7.1",
    }


def test_a_dev_version_means_testpypi():
    assert Stack("0.16.0.dev134").is_dev
    assert not Stack("0.15.0").is_dev


def test_stable_stack_uses_only_pypi():
    project = tomllib.loads(project_file(Stack("0.15.0"), {}))
    assert project["project"]["dependencies"] == ["pennylane-catalyst==0.15.0"]
    assert "tool" not in project


def test_only_the_three_named_packages_may_come_from_testpypi():
    project = tomllib.loads(project_file(Stack("0.16.0.dev134"), exact_pins(DEV_REQUIRES)))
    assert set(project["tool"]["uv"]["sources"]) == {
        "pennylane",
        "pennylane-lightning",
        "pennylane-catalyst",
    }
    index = project["tool"]["uv"]["index"][0]
    assert index["explicit"] is True
    assert index["url"] == "https://test.pypi.org/simple/"


def test_dev_stack_names_catalysts_pins_and_drops_kokkos():
    project = tomllib.loads(project_file(Stack("0.16.0.dev134"), exact_pins(DEV_REQUIRES)))
    assert project["project"]["dependencies"] == [
        "pennylane-catalyst==0.16.0.dev134",
        "pennylane==0.46.0.dev142",
        "pennylane-lightning==0.46.0.dev34",
    ]
    assert any("kokkos" in item for item in project["tool"]["uv"]["override-dependencies"])
    assert not any("kokkos" in item for item in project["project"]["dependencies"])


def test_locked_versions(tmp_path):
    lock = tmp_path / "pylock.toml"
    lock.write_text(
        '[[packages]]\nname = "a"\nversion = "1"\n[[packages]]\nname = "b"\nversion = "2"\n'
    )
    assert locked_versions(lock) == {"a": "1", "b": "2"}
