#!/usr/bin/env bash
# Install lktec-bot from the repository that holds this action.
#
# Every dependency, the build backend included, comes from requirements.txt with hashes, so
# nothing unpinned is fetched at run time: pip verifies the hashes, and the package itself is
# built without isolation from the already installed backend.
set -euo pipefail

root="$(cd "$ACTION_PATH/../.." && pwd)"
pip_install=(python -m pip install --quiet --disable-pip-version-check --no-deps)

"${pip_install[@]}" --require-hashes -r "$root/requirements.txt"

if [ ! -d "$root/.git" ]; then
  # Used as an action from another repository: GitHub downloads the action without git
  # metadata, so hatch-vcs cannot derive the version. Derive a PEP 440 version from the ref
  # the caller pinned (a tag like v1.2.3, or a commit SHA), never from anything else.
  ref="${ACTION_REF:-}"
  if [[ "${ref#v}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    version="${ref#v}"
  else
    local_segment="$(printf '%s' "${ref:-unknown}" | tr -c 'A-Za-z0-9.' '.' | cut -c1-40)"
    version="0.0.0+${local_segment}"
  fi
  export SETUPTOOLS_SCM_PRETEND_VERSION="$version"
fi

"${pip_install[@]}" --no-build-isolation "$root"
echo "installed lktec-bot $(lktec-bot version)"
