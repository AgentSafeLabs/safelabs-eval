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

--- Fix history ---

v2: The original implementation computed its "expected repo root" from
this file's OWN __file__ path. That's a self-reference bug: when a
non-editable install shadows the whole `safelabs` package, this file
gets copied into site-packages right along with everything else, so
its own notion of "expected root" gets shadowed too. The comparison
then becomes self-consistent-but-wrong (site-packages compared against
site-packages) instead of catching the mismatch — verified broken via
a deliberate negative test (`pip install . --no-deps` followed by
assert_running_from_source(), which incorrectly reported OK).

v2 adds an independent, non-self-referential check: a genuinely
editable install's module __file__ resolves directly into the source
tree and never passes through a `site-packages` directory. That check
doesn't depend on where verify_source.py itself was loaded from, so it
can't be fooled by the same shadowing. The original expected-root
comparison is kept as a second layer, since it can still catch a
source tree that's been moved/duplicated somewhere unexpected — a case
the site-packages check alone wouldn't catch.
"""

import os
import sys
import functools


def _expected_repo_root() -> str:
    """
    The repo root is assumed to be the parent directory of the package
    directory this file lives in. Override via the SAFELABS_REPO_ROOT
    env var if a script needs to run from an unusual location (e.g. CI
    checking out to a non-standard path).

    NOTE: this is derived from THIS file's own __file__, so it is only
    a reliable signal when this file itself is being loaded from the
    real source tree. If the whole package has been shadowed by a
    non-editable install, this function's return value is shadowed too
    — that's exactly why assert_running_from_source() below does NOT
    rely on this function alone; see the site-packages check first.
    """
    override = os.environ.get("SAFELABS_REPO_ROOT")
    if override:
        return os.path.realpath(override)
    this_file = os.path.realpath(__file__)
    package_dir = os.path.dirname(this_file)
    repo_root = os.path.dirname(package_dir)
    return repo_root


def _is_site_packages_path(path: str) -> bool:
    """
    True if any component of `path` is literally 'site-packages'.

    A genuinely editable install (`pip install -e .`) makes the
    importable package resolve directly into the source checkout —
    its __file__ never passes through a site-packages directory, even
    though a .pth/finder mechanism is what makes the import work. A
    non-editable install (`pip install .`) always copies/builds the
    package into site-packages. This makes site-packages membership a
    reliable, independent signal that doesn't depend on where
    verify_source.py itself happened to be loaded from.
    """
    return "site-packages" in os.path.normpath(path).split(os.sep)


def assert_running_from_source(package_name: str = "safelabs") -> None:
    """
    Raises RuntimeError if the given package was NOT imported from the
    current repo's source tree — e.g. it was imported from a stale
    .venv/site-packages build instead.

    This is deliberately a hard failure (raise), not a warning: a script
    computing verdicts for a paper or public report should refuse to run
    rather than silently produce numbers from unknown code.

    Two independent checks are performed:
      1. Is the imported module's path inside a site-packages directory
         at all? (Catches non-editable installs regardless of whether
         this checker's own file has also been shadowed.)
      2. Does the imported module's path fall under this file's expected
         repo root? (Catches a source tree that's been moved, duplicated,
         or is otherwise not where this file assumes it should be.)
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

    # Check 1: site-packages membership. Deliberately NOT derived from
    # this file's own __file__, so it still works even if this checker
    # itself has been shadowed by the same non-editable install it's
    # trying to detect.
    if _is_site_packages_path(actual_path):
        raise RuntimeError(
            f"\n\n*** SOURCE MISMATCH DETECTED ***\n"
            f"'{package_name}' was imported from a site-packages copy:\n"
            f"    {actual_path}\n\n"
            f"This means you are almost certainly running against a "
            f"stale, non-editable installed build rather than your "
            f"current source tree. Any fixes you've made since that "
            f"build was created will NOT be reflected in the output "
            f"of this script.\n\n"
            f"Fix with:\n"
            f"    pip uninstall -y {package_name}\n"
            f"    pip install -e . --break-system-packages\n"
        )

    # Check 2: expected-root comparison. Catches a source tree that's
    # been moved/duplicated somewhere this file doesn't expect, which
    # the site-packages check alone would not catch.
    expected_root = _expected_repo_root()
    if not actual_path.startswith(expected_root):
        raise RuntimeError(
            f"\n\n*** SOURCE MISMATCH DETECTED ***\n"
            f"'{package_name}' was imported from:\n"
            f"    {actual_path}\n"
            f"but the expected source tree root is:\n"
            f"    {expected_root}\n\n"
            f"This means you are almost certainly running against a "
            f"different copy of the source than you expect. If this is "
            f"intentional (e.g. a CI checkout at a non-standard path), "
            f"set the SAFELABS_REPO_ROOT environment variable to the "
            f"correct root and re-run.\n\n"
            f"Otherwise, fix with:\n"
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