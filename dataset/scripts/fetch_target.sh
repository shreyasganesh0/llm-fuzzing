#!/bin/bash
# Usage: ./fetch_target.sh <target_config.yaml>
#
# Clones the UPSTREAM project repository at the commit pinned in
# pinned_versions.yaml. Also fetches the FuzzBench harness (from
# fuzzer-test-suite or oss-fuzz, depending on harness_source).
#
# Fails fast if pinned_versions.yaml has <FILL> placeholders for this target.
set -euo pipefail

TARGET_YAML="${1:?usage: $0 <target_config.yaml>}"

if [ ! -f "$TARGET_YAML" ]; then
  echo "target config not found: $TARGET_YAML" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

# Resolve the pinned config via python (we rely on pinned_loader.load_target_yaml).
read -r -d '' RESOLVE_PY <<'PY' || true
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from dataset.scripts.pinned_loader import load_target_yaml
cfg = load_target_yaml(sys.argv[1], require_resolved=True)
print(json.dumps(cfg))
PY

RESOLVED="$(python3 -c "$RESOLVE_PY" "$TARGET_YAML")"

NAME="$(echo "$RESOLVED" | python3 -c "import json,sys;print(json.load(sys.stdin)['name'])")"
REPO_URL="$(echo "$RESOLVED" | python3 -c "import json,sys;print(json.load(sys.stdin)['upstream']['repo'])")"
COMMIT="$(echo "$RESOLVED" | python3 -c "import json,sys;print(json.load(sys.stdin)['upstream']['commit'])")"
HARNESS_SOURCE="$(echo "$RESOLVED" | python3 -c "import json,sys;print(json.load(sys.stdin)['fuzzbench']['harness_source'])")"
HARNESS_FILE="$(echo "$RESOLVED" | python3 -c "import json,sys;print(json.load(sys.stdin)['fuzzbench']['harness_file'])")"
DICTIONARY="$(echo "$RESOLVED" | python3 -c "import json,sys;d=json.load(sys.stdin)['fuzzbench'].get('dictionary');print(d if d else '')")"

DEST_DIR="dataset/targets/src/${NAME}"
UPSTREAM_DIR="${DEST_DIR}/upstream"
HARNESS_DIR="${DEST_DIR}/harness"

mkdir -p "${DEST_DIR}"

if [ ! -d "${UPSTREAM_DIR}/.git" ]; then
  echo "==> clone ${REPO_URL} -> ${UPSTREAM_DIR}"
  git clone --filter=blob:none "${REPO_URL}" "${UPSTREAM_DIR}"
fi

echo "==> checkout ${COMMIT}"
git -C "${UPSTREAM_DIR}" fetch --depth 1 origin "${COMMIT}" || true
git -C "${UPSTREAM_DIR}" checkout --detach "${COMMIT}"

ACTUAL_COMMIT="$(git -C "${UPSTREAM_DIR}" rev-parse HEAD)"
if [ "${ACTUAL_COMMIT}" != "${COMMIT}" ]; then
  echo "commit mismatch: expected ${COMMIT}, got ${ACTUAL_COMMIT}" >&2
  exit 2
fi

# Fetch the FuzzBench harness (from fuzzer-test-suite or oss-fuzz).
FTS_COMMIT="$(python3 -c "import yaml;print(yaml.safe_load(open('pinned_versions.yaml'))['fuzzer_test_suite']['commit'])")"
FTS_URL="$(python3 -c "import yaml;print(yaml.safe_load(open('pinned_versions.yaml'))['fuzzer_test_suite']['repo'])")"
FUZZBENCH_COMMIT="$(python3 -c "import yaml;print(yaml.safe_load(open('pinned_versions.yaml'))['fuzzbench']['commit'])")"
FUZZBENCH_URL="$(python3 -c "import yaml;print(yaml.safe_load(open('pinned_versions.yaml'))['fuzzbench']['repo'])")"

mkdir -p "${HARNESS_DIR}"

case "${HARNESS_SOURCE}" in
  fuzzer-test-suite)
    if [ ! -f "${HARNESS_DIR}/${HARNESS_FILE##*/}" ]; then
      RAW_URL="https://raw.githubusercontent.com/google/fuzzer-test-suite/${FTS_COMMIT}/${HARNESS_FILE}"
      echo "==> fetch harness ${RAW_URL}"
      curl -fsSL "${RAW_URL}" -o "${HARNESS_DIR}/$(basename "${HARNESS_FILE}")"
    fi
    ;;
  oss-fuzz)
    # oss-fuzz harness paths look like projects/<name>/<file>.c
    RAW_URL="https://raw.githubusercontent.com/google/oss-fuzz/master/${HARNESS_FILE}"
    echo "==> fetch harness ${RAW_URL}"
    curl -fsSL "${RAW_URL}" -o "${HARNESS_DIR}/$(basename "${HARNESS_FILE}")"
    ;;
  *)
    echo "unknown harness_source: ${HARNESS_SOURCE}" >&2
    exit 3
    ;;
esac

if [ -n "${DICTIONARY}" ]; then
  case "${HARNESS_SOURCE}" in
    fuzzer-test-suite)
      DICT_URL="https://raw.githubusercontent.com/google/fuzzer-test-suite/${FTS_COMMIT}/${DICTIONARY}"
      ;;
    oss-fuzz)
      DICT_URL="https://raw.githubusercontent.com/google/oss-fuzz/master/${DICTIONARY}"
      ;;
  esac
  echo "==> fetch dictionary ${DICT_URL}"
  curl -fsSL "${DICT_URL}" -o "${HARNESS_DIR}/$(basename "${DICTIONARY}")" || echo "dictionary fetch failed (continuing)"
fi

PROV_PATH="${DEST_DIR}/provenance.json"
python3 - "${NAME}" "${REPO_URL}" "${ACTUAL_COMMIT}" "${HARNESS_SOURCE}" "${HARNESS_FILE}" "${PROV_PATH}" <<'PY'
import json, subprocess, sys, datetime
name, repo, commit, harness_src, harness_file, out = sys.argv[1:]
log = subprocess.run(
    ["git", "-C", f"dataset/targets/src/{name}/upstream", "log", "-1", "--format=%H%n%an%n%ae%n%ad%n%s"],
    capture_output=True, text=True, check=True,
).stdout.strip()
with open(out, "w") as f:
    json.dump({
        "target": name,
        "upstream_repo": repo,
        "upstream_commit": commit,
        "harness_source": harness_src,
        "harness_file": harness_file,
        "clone_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_log_head": log,
    }, f, indent=2)
print("wrote", out)
PY

echo "==> fetched ${NAME} @ ${ACTUAL_COMMIT}"
