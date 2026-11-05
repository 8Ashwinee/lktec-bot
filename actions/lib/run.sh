#!/usr/bin/env bash
# Run one verb from a composite action. The verb name is the only argument; everything else
# comes from INPUT_* variables the action sets from its inputs, and the App identity from
# LKTEC_BOT_APP_ID / LKTEC_BOT_PRIVATE_KEY, which never appear on a command line.
#
# Exit codes of lktec-bot: 0 ran, 1 error, 2 usage, 3 denied by policy. A denial is a normal
# result (output `result=denied`) and does not fail the step unless `fail-on-denied` is true.
set -uo pipefail

verb="$1"
if ! command -v lktec-bot >/dev/null 2>&1; then
  echo "::error::lktec-bot is not installed; run the setup action (actions/setup) first"
  exit 2
fi

args=(--repository "${INPUT_REPOSITORY:?repository input is required}")
args+=(--module "${INPUT_MODULE:?module input is required}")
case "$verb" in
  ping) ;;
  comment)
    args+=(--target "${INPUT_TARGET:-pr}" --number "${INPUT_NUMBER:?number input is required}")
    if [ -n "${INPUT_BODY_FILE:-}" ]; then
      args+=(--body-file "$INPUT_BODY_FILE")
    else
      args+=(--body "${INPUT_BODY:-}")
    fi
    [ -n "${INPUT_MARKER:-}" ] && args+=(--marker "$INPUT_MARKER")
    ;;
  approve)
    args+=(--number "${INPUT_NUMBER:?number input is required}")
    if [ -n "${INPUT_BODY_FILE:-}" ]; then
      args+=(--body-file "$INPUT_BODY_FILE")
    elif [ -n "${INPUT_BODY:-}" ]; then
      args+=(--body "$INPUT_BODY")
    fi
    ;;
  enable-automerge)
    args+=(--number "${INPUT_NUMBER:?number input is required}" --method "${INPUT_METHOD:-squash}")
    ;;
  update-branch)
    args+=(--number "${INPUT_NUMBER:?number input is required}")
    ;;
  *)
    echo "::error::unknown verb '$verb'"
    exit 2
    ;;
esac

lktec-bot "$verb" "${args[@]}"
code=$?
if [ "$code" -eq 3 ] && [ "${INPUT_FAIL_ON_DENIED:-false}" != "true" ]; then
  echo "::notice::$verb was denied by policy; the step succeeds because fail-on-denied is not set"
  exit 0
fi
exit "$code"
