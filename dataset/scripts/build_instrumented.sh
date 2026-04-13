#!/bin/bash
# Usage: ./build_instrumented.sh <target_config.yaml>
#
# Builds three variants of the target:
#   1. coverage/  — -fprofile-instr-generate -fcoverage-mapping (Phase 1)
#   2. sanitizer/ — -fsanitize=address,undefined (crash detection)
#   3. fuzzer/    — -fsanitize=fuzzer (Phase 3 libFuzzer)
#
# For RE2 specifically, also builds the Google Test binary under the coverage
# profile so Phase 1 can run each TEST(...) individually via --gtest_filter.
set -euo pipefail

TARGET_YAML="${1:?usage: $0 <target_config.yaml>}"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

read -r -d '' RESOLVE_PY <<'PY' || true
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from dataset.scripts.pinned_loader import load_target_yaml
cfg = load_target_yaml(sys.argv[1], require_resolved=True)
print(json.dumps(cfg))
PY

RESOLVED="$(python3 -c "$RESOLVE_PY" "$TARGET_YAML")"
NAME="$(echo "$RESOLVED" | python3 -c "import json,sys;print(json.load(sys.stdin)['name'])")"
UPSTREAM_DIR="dataset/targets/src/${NAME}/upstream"
BUILD_DIR="dataset/targets/src/${NAME}/build"

if [ ! -d "${UPSTREAM_DIR}" ]; then
  echo "upstream missing: run fetch_target.sh first" >&2
  exit 1
fi

CC="${CC:-clang-15}"
CXX="${CXX:-clang++-15}"
command -v "${CC}" >/dev/null || { echo "${CC} not found (try apt install clang-15)"; exit 2; }

# Some distros ship clang-15 that defaults to a libstdc++ dir that isn't
# installed (e.g. Ubuntu 24.04 has g++-13 + g++-14 stubs but only
# libstdc++-13-dev). Probe the newest installed libstdc++-dev and pass
# its include + library paths explicitly so the build doesn't fail with
# "'string' file not found" / "cannot find -lstdc++".
GCC_HDR_VER=""
for v in 15 14 13 12 11; do
  if [ -d "/usr/include/c++/${v}" ] && [ -f "/usr/lib/gcc/x86_64-linux-gnu/${v}/libstdc++.so" ]; then
    GCC_HDR_VER="${v}"; break
  fi
done
STDLIB_FLAGS=""
if [ -n "${GCC_HDR_VER}" ]; then
  STDLIB_FLAGS="-I/usr/include/c++/${GCC_HDR_VER} -I/usr/include/x86_64-linux-gnu/c++/${GCC_HDR_VER} -B/usr/lib/gcc/x86_64-linux-gnu/${GCC_HDR_VER} -L/usr/lib/gcc/x86_64-linux-gnu/${GCC_HDR_VER}"
  echo "==> detected libstdc++ headers at /usr/include/c++/${GCC_HDR_VER}; adding ${STDLIB_FLAGS}"
fi

mkdir -p "${BUILD_DIR}"

# --- Target-specific builders ---------------------------------------------
case "${NAME}" in
  re2)
    for variant in coverage sanitizer fuzzer; do
      OUT="${BUILD_DIR}/${variant}"
      mkdir -p "${OUT}"
      case "${variant}" in
        coverage) FLAGS="-O1 -g -fprofile-instr-generate -fcoverage-mapping";;
        sanitizer) FLAGS="-O1 -g -fsanitize=address,undefined";;
        fuzzer) FLAGS="-O1 -g -fsanitize=fuzzer,address";;
      esac
      FLAGS="${FLAGS} ${STDLIB_FLAGS}"

      echo "==> re2 ${variant} (CXXFLAGS=\"${FLAGS}\")"
      # RE2 ships a Makefile-based build; pass flags via CXXFLAGS.
      (cd "${UPSTREAM_DIR}" && \
        make clean >/dev/null 2>&1 || true)
      (cd "${UPSTREAM_DIR}" && \
        CC="${CC}" CXX="${CXX}" \
        CXXFLAGS="${FLAGS}" \
        CFLAGS="${FLAGS}" \
        LDFLAGS="${FLAGS}" \
        make -j"$(nproc)" obj/libre2.a obj/test/regexp_test)
      cp -r "${UPSTREAM_DIR}/obj" "${OUT}/obj"
    done
    ;;
  *)
    echo "build_instrumented: ${NAME} not yet wired (stub)" >&2
    echo "Add a case here once the target is enabled." >&2
    exit 3
    ;;
esac

echo "==> ${NAME} built into ${BUILD_DIR}"
