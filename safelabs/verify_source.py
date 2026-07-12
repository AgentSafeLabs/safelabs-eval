"""
verify_source.py

Drop this file into safelabs/ (or the equivalent top-level package dir
in agentdojo-x / safelabs-platform) and import it at the top of any
entrypoint that computes verdicts or produces a number that might end up
in a paper, blog post, or report: runner.py, cli.py, any results-report
scripts.

This exists because a stale, non-editable safelabs-eval install in
.venv/site-packages silently shadowed the real source tree twice during
Paper A's data reconstruction, producing wrong numbers that were only
caught by manual 30/30 self-consistency checks. This makes that check
automatic and loud instead of something a human has to remember to do.

Usage, at the top of an entrypoint script:

    from safelabs.verify_source import assert_running_from_source
    assert_running_from_source()

Or as a decorator on a specific function that computes scored output:

    from safelabs.verify_source import verified_source

    @verified_source
    def run_eval(...):
        ...
"""

import os
import sys
import functools
import warnings


def _expected_repo_root() -> str:
    """
    The repo root is assumed to be the parent directory of the package
    directory this file lives in. Override via the SAFELABS_REPO_ROOT
    env var if a script needs to run from an unusual location (e.g. CI
    checking out to a non-standard path).
    """
    override = os.environ.get("SAFELABS_REPO_ROOT")
    if override:
        return os.path.realpath(override)
    this_file = os.path.realpath(__file__)
    package_dir = os.path.dirname(this_file)
    repo_root = os.path.dirname(package_dir)
    return repo_root


def assert_running_from_source(package_name: str = "safelabs") -> None:
    """
    Raises RuntimeError if the given package was NOT imported from the
    current repo's source tree — e.g. it was imported from a stale
    .venv/site-packages build instead.

    This is deliberately a hard failure (raise), not a warning: a script
    computing verdicts for a paper or public report should refuse to run
    rather than silently produce numbers from unknown code.
    """
    try:
        module = sys.modules.get(package_name)
        if module is None:
            module = __import__(package_name)
        actual_path = os.path.realpath(os.path.dirname(module.__file__))
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            f"verify_source: could not import '{package_name}' to check "
            f"its location — {exc}"
        ) from exc

    expected_root = _expected_repo_root()

    if not actual_path.startswith(expected_root):
        raise RuntimeError(
            f"\n\n*** SOURCE MISMATCH DETECTED ***\n"
            f"'{package_name}' was imported from:\n"
            f"    {actual_path}\n"
            f"but the expected source tree root is:\n"
            f"    {expected_root}\n\n"
            f"This means you are almost certainly running against a "
            f"stale, non-editable installed build (e.g. .venv/site-packages) "
            f"rather than your current source tree. Any fixes you've made "
            f"since that build was created will NOT be reflected in the "
            f"output of this script.\n\n"
            f"Fix with:\n"
            f"    pip uninstall -y {package_name}\n"
            f"    pip install -e . --break-system-packages\n"
        )


def verified_source(func=None, *, package_name: str = "safelabs"):
    """
    Decorator version — checks source location once, at first call,
    before running the wrapped function. Use on any function whose
    output might be cited in a paper, blog post, or report.
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            assert_running_from_source(package_name=package_name)
            return f(*args, **kwargs)
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


if __name__ == "__main__":
    # Allow `python -m safelabs.verify_source` as a quick manual check.
    pkg = sys.argv[1] if len(sys.argv) > 1 else "safelabs"
    try:
        assert_running_from_source(package_name=pkg)
        print(f"OK — '{pkg}' is running from source: {_expected_repo_root()}")
    except RuntimeError as e:
        print(e)
        sys.exit(1)