#!/usr/bin/env bash
# audit_editable_installs.sh
#
# Checks every repo with a local .venv for the class of bug that hit
# safelabs-eval twice: a stale, non-editable installed package silently
# shadowing the actual source tree, so scripts import old code without
# any error or warning.
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

  # pip show -f gives the installed Location and whether it's an
  # editable install (editable installs show a .pth/direct_url pointing
  # back at the source checkout; non-editable installs point into
  # site-packages with a copied/built snapshot).
  show_output=$("${repo}/.venv/bin/pip" show "${pkg}" 2>&1)

  if echo "${show_output}" | grep -qi "not found"; then
    echo "  SKIP — ${pkg} not installed in this venv"
    echo ""
    continue
  fi

  location=$(echo "${show_output}" | grep -E "^Location:" | sed 's/^Location: //')
  editable_loc=$(echo "${show_output}" | grep -E "^Editable project location:" | sed 's/^Editable project location: //')

  echo "  Location:                  ${location}"
  if [ -n "${editable_loc}" ]; then
    echo "  Editable project location: ${editable_loc}"
    echo "  STATUS: OK — editable install, points at source tree"
  else
    echo "  Editable project location: (none — NOT an editable install)"
    echo "  STATUS: *** PROBLEM *** — this is a stale/built snapshot, not"
    echo "          your live source tree. Code changes will NOT take"
    echo "          effect until this is fixed. Run:"
    echo "            cd ${repo} && .venv/bin/pip uninstall -y ${pkg} && .venv/bin/pip install -e . --break-system-packages"
    FAILED=1
  fi
  echo ""
done

if [ "${FAILED}" -eq 1 ]; then
  echo "One or more repos have a non-editable install shadowing source. Fix before trusting any script output from those repos."
  exit 1
else
  echo "All checked repos are editable and current."
  exit 0
fi