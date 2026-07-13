#!/usr/bin/env bash
# audit_editable_installs.sh
#
# Checks every repo with a local .venv for the class of bug that hit
# safelabs-eval twice: a stale, non-editable installed package silently
# shadowing the actual source tree, so scripts import old code without
# any error or warning.
#
# TWO checks are performed:
#
#   1. OWN-PACKAGE check: is each repo's own package installed editable
#      into its own venv? (The original check.)
#
#   2. CROSS-REPO DEPENDENCY check: for each repo's venv, is every OTHER
#      project package that is installed there ALSO editable and pointing
#      at that sibling repo's live source tree? This is the check that was
#      missing when a stale safelabs-eval v0.2.0 WHEEL silently shadowed
#      v0.2.1 inside agentdojo-x's venv — the own-package check reported
#      agentdojo-x "OK" (its own package was editable) while its
#      safelabs-eval DEPENDENCY was a frozen snapshot. Own-package
#      editability says nothing about a dependency's editability, so both
#      checks are needed.
#
# Usage: run from the parent directory containing all repos, e.g.:
#   cd ~ && bash audit_editable_installs.sh
#
# Exit status: 0 if every checked package is editable and up to date,
# 1 if any problem is found (so this can be used as a CI/pre-flight gate
# later, not just a manual check).

set -uo pipefail

# Map: repo directory name -> importable package name.
# Parallel indexed arrays, not associative — associative arrays require
# bash 4+, and this script may be invoked as `bash script.sh`, which
# resolves to whatever bash is first in PATH (often macOS's bash 3.2
# at /bin/bash, regardless of the shebang above).
REPO_DIRS=("safelabs-eval" "agentdojo-x" "safelabs-platform")
REPO_PKGS=("safelabs-eval" "agentdojo-x" "safelabs-platform")

FAILED=0

# Canonicalize a directory path (portable; avoids depending on `realpath`,
# which is not guaranteed present on older macOS). Prints nothing if the
# path does not exist as a directory.
abspath() {
  ( cd "$1" 2>/dev/null && pwd -P )
}

# Extract "Editable project location" from a `pip show` blob (empty if the
# package is a non-editable/built install).
editable_location_of() {
  echo "$1" | grep -E "^Editable project location:" | sed 's/^Editable project location: //'
}

for i in "${!REPO_DIRS[@]}"; do
  repo="${REPO_DIRS[$i]}"
  pkg="${REPO_PKGS[$i]}"

  if [ ! -d "${repo}" ]; then
    echo "  SKIP — directory not found at ./${repo}"
    echo ""
    continue
  fi

  if [ ! -x "${repo}/.venv/bin/pip" ]; then
    echo "  SKIP — no .venv/bin/pip found (venv missing or not created yet)"
    echo ""
    continue
  fi

  echo "== ${repo} =="

  # --- Check 1: own package editable in own venv -----------------------------
  #
  # pip show gives the installed Location and whether it's an editable
  # install (editable installs show an "Editable project location" pointing
  # back at the source checkout; non-editable installs point into
  # site-packages with a copied/built snapshot).
  show_output=$("${repo}/.venv/bin/pip" show "${pkg}" 2>&1)

  if echo "${show_output}" | grep -qi "not found"; then
    echo "  [own] SKIP — ${pkg} not installed in this venv"
  else
    location=$(echo "${show_output}" | grep -E "^Location:" | sed 's/^Location: //')
    editable_loc=$(editable_location_of "${show_output}")

    echo "  [own] Location:                  ${location}"
    if [ -n "${editable_loc}" ]; then
      echo "  [own] Editable project location: ${editable_loc}"
      echo "  [own] STATUS: OK — editable install, points at source tree"
    else
      echo "  [own] Editable project location: (none — NOT an editable install)"
      echo "  [own] STATUS: *** PROBLEM *** — stale/built snapshot, not your"
      echo "        live source tree. Code changes will NOT take effect. Run:"
      echo "          cd ${repo} && .venv/bin/pip uninstall -y ${pkg} && .venv/bin/pip install -e . --break-system-packages"
      FAILED=1
    fi
  fi

  # --- Check 2: cross-repo dependencies editable + pointing at siblings ------
  #
  # For every OTHER project package, if it is installed in THIS repo's venv,
  # it must be editable AND its editable location must resolve to that
  # sibling repo's own source tree — not a frozen wheel, and not some other
  # copy.
  for j in "${!REPO_PKGS[@]}"; do
    [ "${j}" -eq "${i}" ] && continue          # skip own package (Check 1)
    dep_dir="${REPO_DIRS[$j]}"
    dep_pkg="${REPO_PKGS[$j]}"

    # Only meaningful if the sibling source dir actually exists locally.
    [ -d "${dep_dir}" ] || continue

    dep_show=$("${repo}/.venv/bin/pip" show "${dep_pkg}" 2>&1)
    if echo "${dep_show}" | grep -qi "not found"; then
      continue   # this repo doesn't depend on ${dep_pkg}; nothing to check
    fi

    dep_editable_loc=$(editable_location_of "${dep_show}")
    if [ -z "${dep_editable_loc}" ]; then
      echo "  [dep] ${dep_pkg}: *** PROBLEM *** — installed but NOT editable"
      echo "        (a frozen/built snapshot is shadowing ${dep_dir}'s live"
      echo "        source in ${repo}'s venv — the exact stale-dependency bug"
      echo "        this check exists for). Fix:"
      echo "          cd ${repo} && .venv/bin/pip install -e ../${dep_dir}"
      FAILED=1
      continue
    fi

    # Editable — but does it point at the sibling's real source tree?
    expected=$(abspath "${dep_dir}")
    actual=$(abspath "${dep_editable_loc}")
    if [ -n "${expected}" ] && [ "${actual}" = "${expected}" ]; then
      echo "  [dep] ${dep_pkg}: OK — editable, points at ./${dep_dir}"
    else
      echo "  [dep] ${dep_pkg}: *** PROBLEM *** — editable, but points at"
      echo "        ${dep_editable_loc}"
      echo "        not this project's ./${dep_dir} (${expected:-<missing>})."
      echo "        Fix: cd ${repo} && .venv/bin/pip install -e ../${dep_dir}"
      FAILED=1
    fi
  done

  echo ""
done

if [ "${FAILED}" -eq 1 ]; then
  echo "One or more repos have a non-editable install shadowing source. Fix before trusting any script output from those repos."
  exit 1
else
  echo "All checked repos are editable and current."
  exit 0
fi