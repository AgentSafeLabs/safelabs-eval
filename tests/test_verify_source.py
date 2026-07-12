"""
tests/test_verify_source.py

Regression tests for safelabs/verify_source.py.

These tests exist because of two real, verified bugs found during
Paper B infra verification (July 2026):

1. audit_editable_installs.sh used import names instead of pip
   distribution names when calling `pip show`, causing it to silently
   report "not installed" for a genuinely stale, non-editable install.
2. verify_source.py's original _expected_repo_root() derived the
   expected path from this file's OWN __file__. When a non-editable
   install shadows the whole safelabs package, verify_source.py gets
   copied into site-packages too, so its own notion of "expected root"
   got shadowed along with it -- the comparison became
   self-consistent-but-wrong instead of catching the mismatch.

Both were only caught by manually running a deliberate negative test
from a neutral working directory (cwd shadowing masked the bug on the
first two manual attempts -- running the check from inside a
directory containing a local safelabs/ folder lets Python's cwd-first
import resolution silently bypass whatever is actually installed,
regardless of install state).

These tests encode that negative test permanently, using monkeypatched
module objects rather than real pip install/uninstall cycles, so they
run fast, don't mutate the real environment, and can't be accidentally
fooled by cwd the way the manual terminal checks were. A slower,
optional end-to-end test that performs a real pip install/uninstall
cycle is included at the bottom, marked so it's opt-in only (it
mutates the actual venv and should not run by default in CI).
"""

import os
import sys
import types
import subprocess
import shutil
import pytest

from safelabs.verify_source import (
    assert_running_from_source,
    verified_source,
    _is_site_packages_path,
)


# ---------------------------------------------------------------------------
# Fast, isolated unit tests -- no real pip install/uninstall, no real
# filesystem changes beyond pytest's own tmp_path. These monkeypatch
# sys.modules with a fake module object pointing __file__ at whatever
# path we want to test.
# ---------------------------------------------------------------------------

def _install_fake_module(monkeypatch, package_name, fake_file_path):
    """
    Insert a fake module into sys.modules with the given __file__, so
    assert_running_from_source() resolves against a path we control
    without touching the real installed package at all.
    """
    fake_module = types.ModuleType(package_name)
    fake_module.__file__ = fake_file_path
    monkeypatch.setitem(sys.modules, package_name, fake_module)


class TestIsSitePackagesPath:
    """Direct tests of the independent site-packages check."""

    def test_detects_site_packages_in_venv(self):
        path = "/Users/x/project/.venv/lib/python3.12/site-packages/safelabs"
        assert _is_site_packages_path(path) is True

    def test_detects_site_packages_system_install(self):
        path = "/usr/local/lib/python3.12/site-packages/safelabs"
        assert _is_site_packages_path(path) is True

    def test_does_not_flag_real_source_tree(self):
        path = "/Users/x/project/safelabs-eval/safelabs"
        assert _is_site_packages_path(path) is False

    def test_does_not_false_positive_on_similar_substring(self):
        # A directory name that merely CONTAINS the substring
        # "site-packages" should not trigger a false positive -- the
        # check must match on path components, not substring search.
        path = "/Users/x/my-site-packages-backup/safelabs"
        assert _is_site_packages_path(path) is False


class TestAssertRunningFromSourceUnit:
    """
    Fast unit tests using a monkeypatched fake module, simulating both
    the clean (editable, source-tree) case and the broken (shadowed,
    site-packages) case without any real pip install/uninstall.
    """

    def test_passes_when_module_is_in_source_tree(self, monkeypatch, tmp_path):
        fake_repo = tmp_path / "safelabs-eval"
        fake_pkg_dir = fake_repo / "safelabs"
        fake_pkg_dir.mkdir(parents=True)
        fake_file = fake_pkg_dir / "__init__.py"
        fake_file.write_text("")

        monkeypatch.setenv("SAFELABS_REPO_ROOT", str(fake_repo))
        _install_fake_module(monkeypatch, "fake_safelabs_ok", str(fake_file))

        # Should not raise.
        assert_running_from_source(package_name="fake_safelabs_ok")

    def test_raises_when_module_is_in_site_packages(self, monkeypatch, tmp_path):
        fake_venv_site_packages = (
            tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "safelabs"
        )
        fake_venv_site_packages.mkdir(parents=True)
        fake_file = fake_venv_site_packages / "__init__.py"
        fake_file.write_text("")

        _install_fake_module(monkeypatch, "fake_safelabs_shadowed", str(fake_file))

        with pytest.raises(RuntimeError, match=r"SOURCE MISMATCH DETECTED"):
            assert_running_from_source(package_name="fake_safelabs_shadowed")

    def test_raises_even_if_verify_source_itself_is_shadowed(
        self, monkeypatch, tmp_path
    ):
        """
        Regression test for the actual bug that shipped: the original
        implementation derived its "expected root" from verify_source.py's
        OWN __file__, so when the whole package (including this file)
        got copied into site-packages, the check became
        self-consistent-but-wrong.

        Simulate that exact scenario by pointing SAFELABS_REPO_ROOT at
        a site-packages path -- mimicking what _expected_repo_root()
        would have computed on its own if verify_source.py had been
        shadowed too -- and confirm the check still raises. The old,
        buggy implementation would have PASSED (incorrectly) under
        these conditions; the fix must not regress this.
        """
        shadowed_root = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
        shadowed_pkg_dir = shadowed_root / "safelabs"
        shadowed_pkg_dir.mkdir(parents=True)
        fake_file = shadowed_pkg_dir / "__init__.py"
        fake_file.write_text("")

        # Even if SAFELABS_REPO_ROOT itself were (incorrectly) pointed
        # at the shadowed site-packages path -- the exact failure mode
        # that bit the original implementation -- the independent
        # site-packages check must still catch this.
        monkeypatch.setenv("SAFELABS_REPO_ROOT", str(shadowed_root))
        _install_fake_module(
            monkeypatch, "fake_safelabs_self_shadowed", str(fake_file)
        )

        with pytest.raises(RuntimeError, match=r"SOURCE MISMATCH DETECTED"):
            assert_running_from_source(package_name="fake_safelabs_self_shadowed")

    def test_raises_when_package_cannot_be_imported(self, monkeypatch):
        monkeypatch.delitem(
            sys.modules, "definitely_not_a_real_package_xyz", raising=False
        )
        with pytest.raises(RuntimeError, match=r"could not import"):
            assert_running_from_source(
                package_name="definitely_not_a_real_package_xyz"
            )


class TestVerifiedSourceDecorator:
    """The @verified_source decorator should gate the wrapped function."""

    def test_decorator_raises_before_calling_wrapped_function(
        self, monkeypatch, tmp_path
    ):
        fake_venv_site_packages = (
            tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "safelabs"
        )
        fake_venv_site_packages.mkdir(parents=True)
        fake_file = fake_venv_site_packages / "__init__.py"
        fake_file.write_text("")
        _install_fake_module(monkeypatch, "fake_safelabs_decorated", str(fake_file))

        calls = []

        @verified_source(package_name="fake_safelabs_decorated")
        def compute_verdict():
            calls.append(1)
            return "should not reach here"

        with pytest.raises(RuntimeError, match=r"SOURCE MISMATCH DETECTED"):
            compute_verdict()

        assert calls == [], (
            "wrapped function must not run when source verification fails "
            "-- a verdict-computing function must never execute against "
            "unverified source"
        )

    def test_decorator_calls_wrapped_function_when_source_is_clean(
        self, monkeypatch, tmp_path
    ):
        fake_repo = tmp_path / "safelabs-eval"
        fake_pkg_dir = fake_repo / "safelabs"
        fake_pkg_dir.mkdir(parents=True)
        fake_file = fake_pkg_dir / "__init__.py"
        fake_file.write_text("")
        monkeypatch.setenv("SAFELABS_REPO_ROOT", str(fake_repo))
        _install_fake_module(monkeypatch, "fake_safelabs_clean", str(fake_file))

        @verified_source(package_name="fake_safelabs_clean")
        def compute_verdict():
            return 42

        assert compute_verdict() == 42


# ---------------------------------------------------------------------------
# Slow, opt-in end-to-end test: performs a REAL pip install/uninstall
# cycle against the actual repo, matching the exact manual negative
# test run during Phase 0 verification (July 2026). This mutates the
# real venv, takes several seconds, and must NOT run by default in CI
# or on every `pytest` invocation -- opt in explicitly:
#
#   pytest tests/test_verify_source.py -m e2e_pip_cycle
#
# Register the marker in pyproject.toml or pytest.ini, e.g.:
#
#   [tool.pytest.ini_options]
#   markers = ["e2e_pip_cycle: real pip install/uninstall, mutates .venv"]
#
# and by default EXCLUDE it from normal runs, e.g. in CI:
#
#   pytest -m "not e2e_pip_cycle"
# ---------------------------------------------------------------------------

@pytest.mark.e2e_pip_cycle
@pytest.mark.skipif(shutil.which("pip") is None, reason="pip not available on PATH")
def test_real_pip_cycle_detects_non_editable_shadow():
    """
    End-to-end negative test: uninstall, reinstall non-editable
    (--no-deps, no -e), confirm assert_running_from_source() raises
    when invoked as a SEPARATE subprocess run from a neutral cwd, then
    restore the editable install regardless of outcome.

    Run as a subprocess (not an in-process import) deliberately: this
    package is almost certainly already imported in-process by the
    time this test runs (pytest itself imported it to collect these
    tests), and Python will not re-resolve an already-imported module
    just because the underlying install changed underneath it. A
    subprocess is required to get a genuinely fresh import resolution
    -- matching how a real trial-run entrypoint would actually observe
    the shadowed state.

    The subprocess is also deliberately run from a neutral cwd (the
    user's home directory), not the repo root: running from inside a
    directory containing a local safelabs/ folder lets Python's
    cwd-first import resolution silently bypass the installed package
    entirely, which is exactly what masked this bug during the first
    two manual verification attempts in this project's history.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_pip = os.path.join(repo_root, ".venv", "bin", "pip")
    venv_python = os.path.join(repo_root, ".venv", "bin", "python")

    if not (os.path.exists(venv_pip) and os.path.exists(venv_python)):
        pytest.skip(".venv not found at expected repo-local path")

    neutral_cwd = os.path.expanduser("~")

    check_cmd = [
        venv_python,
        "-c",
        "from safelabs.verify_source import assert_running_from_source; "
        "assert_running_from_source(); print('OK: verified source')",
    ]

    try:
        subprocess.run(
            [venv_pip, "uninstall", "-y", "safelabs-eval"],
            cwd=repo_root, capture_output=True, check=False,
        )
        subprocess.run(
            [venv_pip, "install", ".", "--no-deps", "--break-system-packages"],
            cwd=repo_root, capture_output=True, check=True,
        )

        result = subprocess.run(
            check_cmd, cwd=neutral_cwd, capture_output=True, text=True,
        )
        assert result.returncode != 0, (
            "assert_running_from_source() should have raised against a "
            "non-editable install, but it exited 0. stdout was: "
            f"{result.stdout!r}"
        )
        assert "SOURCE MISMATCH DETECTED" in result.stderr

    finally:
        # Always restore, even if the assertion above failed -- never
        # leave the venv in a broken, non-editable state.
        subprocess.run(
            [venv_pip, "uninstall", "-y", "safelabs-eval"],
            cwd=repo_root, capture_output=True, check=False,
        )
        restore = subprocess.run(
            [venv_pip, "install", "-e", ".", "--break-system-packages"],
            cwd=repo_root, capture_output=True, check=True,
        )
        assert restore.returncode == 0, (
            "failed to restore editable install after e2e test -- venv "
            "may be left in a broken state, fix manually with:\n"
            f"  cd {repo_root} && .venv/bin/pip install -e . --break-system-packages"
        )