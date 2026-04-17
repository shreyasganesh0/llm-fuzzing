#!/bin/bash
# Usage: ./build_instrumented.sh <target_config.yaml>
#
# Builds instrumented variants of the target:
#   1. coverage/  — -fprofile-instr-generate -fcoverage-mapping
#   2. sanitizer/ — -fsanitize=address,undefined
#   3. fuzzer/    — -fsanitize=fuzzer,address (libFuzzer binary)
#   4. afl/       — AFL++ instrumented binary (afl-clang-fast++)
#
# Each variant produces a static library, then links a fuzzer binary
# (for fuzzer/afl) or a seed_replay binary (for coverage).
set -euo pipefail
export PATH="${HOME}/.local/bin:${PATH}"

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
UPSTREAM_DIR="${REPO_ROOT}/dataset/targets/src/${NAME}/upstream"
BUILD_DIR="${REPO_ROOT}/dataset/targets/src/${NAME}/build"
HARNESS_DIR="${REPO_ROOT}/dataset/targets/src/${NAME}/harness"

if [ ! -d "${UPSTREAM_DIR}" ]; then
  echo "upstream missing: run fetch_target.sh first" >&2
  exit 1
fi

CC="${CC:-clang-15}"
CXX="${CXX:-clang++-15}"
command -v "${CC}" >/dev/null || { echo "${CC} not found (try apt install clang-15)"; exit 2; }

AFL_CC="${AFL_CC:-/home/shreyasganesh/tools/aflpp/afl-clang-fast}"
AFL_CXX="${AFL_CXX:-/home/shreyasganesh/tools/aflpp/afl-clang-fast++}"

# Probe for the newest installed libstdc++-dev and pass its paths explicitly.
GCC_HDR_VER=""
for v in 15 14 13 12 11; do
  if [ -d "/usr/include/c++/${v}" ] && [ -f "/usr/lib/gcc/x86_64-linux-gnu/${v}/libstdc++.so" ]; then
    GCC_HDR_VER="${v}"; break
  fi
done
STDLIB_FLAGS=""
if [ -n "${GCC_HDR_VER}" ]; then
  STDLIB_FLAGS="-I/usr/include/c++/${GCC_HDR_VER} -I/usr/include/x86_64-linux-gnu/c++/${GCC_HDR_VER} -B/usr/lib/gcc/x86_64-linux-gnu/${GCC_HDR_VER} -L/usr/lib/gcc/x86_64-linux-gnu/${GCC_HDR_VER}"
  echo "==> detected libstdc++ headers at /usr/include/c++/${GCC_HDR_VER}"
fi

mkdir -p "${BUILD_DIR}"

VARIANTS="coverage sanitizer fuzzer afl"

# --- RE2 build ---------------------------------------------------------------
build_re2() {
  local variant="$1" OUT="${BUILD_DIR}/${variant}"
  mkdir -p "${OUT}"

  local USE_CC="${CC}" USE_CXX="${CXX}" FLAGS=""
  case "${variant}" in
    coverage)   FLAGS="-O1 -g -fprofile-instr-generate -fcoverage-mapping";;
    sanitizer)  FLAGS="-O1 -g -fsanitize=address,undefined";;
    fuzzer)     FLAGS="-O1 -g -fsanitize=fuzzer,address";;
    afl)        FLAGS="-O1 -g -fsanitize=address"; USE_CC="${AFL_CC}"; USE_CXX="${AFL_CXX}";;
  esac
  FLAGS="${FLAGS} ${STDLIB_FLAGS}"

  echo "==> re2 ${variant} (CC=${USE_CC})"
  (cd "${UPSTREAM_DIR}" && make clean >/dev/null 2>&1 || true)
  (cd "${UPSTREAM_DIR}" && \
    CC="${USE_CC}" CXX="${USE_CXX}" \
    CXXFLAGS="${FLAGS}" CFLAGS="${FLAGS}" LDFLAGS="${FLAGS}" \
    make -j"$(nproc)" obj/libre2.a)
  cp -r "${UPSTREAM_DIR}/obj" "${OUT}/obj"

  case "${variant}" in
    fuzzer)
      echo "==> linking re2_fuzzer (libFuzzer)"
      ${USE_CXX} ${FLAGS} \
        -I"${UPSTREAM_DIR}" \
        "${HARNESS_DIR}/target.cc" \
        "${OUT}/obj/libre2.a" -lpthread \
        -o "${OUT}/re2_fuzzer"
      ;;
    afl)
      echo "==> linking re2_afl_fuzzer"
      ${USE_CXX} ${FLAGS} -fsanitize=fuzzer \
        -I"${UPSTREAM_DIR}" \
        "${HARNESS_DIR}/target.cc" \
        "${OUT}/obj/libre2.a" -lpthread \
        -o "${OUT}/re2_afl_fuzzer"
      ;;
    coverage)
      echo "==> linking re2 seed_replay"
      ${USE_CXX} ${FLAGS} \
        -I"${UPSTREAM_DIR}" \
        "${HARNESS_DIR}/target.cc" \
        "${HARNESS_DIR}/seed_replay_main.cc" \
        "${OUT}/obj/libre2.a" -lpthread \
        -o "${OUT}/seed_replay"
      ;;
  esac
}

# --- Harfbuzz build -----------------------------------------------------------
# Harfbuzz 1.3.2 uses autotools. We configure once (vanilla flags) then for
# each variant we clean objects and rebuild with the appropriate compiler/flags.
_harfbuzz_configured=0
_harfbuzz_configure() {
  if [ "${_harfbuzz_configured}" = "1" ]; then return; fi
  if [ ! -f "${UPSTREAM_DIR}/configure" ]; then
    echo "==> running autogen.sh"
    (cd "${UPSTREAM_DIR}" && NOCONFIGURE=1 ./autogen.sh)
  fi
  (cd "${UPSTREAM_DIR}" && make distclean >/dev/null 2>&1 || true)
  (cd "${UPSTREAM_DIR}" && \
    CC="${CC}" CXX="${CXX}" \
    CXXFLAGS="-O1 -g ${STDLIB_FLAGS}" CFLAGS="-O1 -g ${STDLIB_FLAGS}" \
    ./configure \
      --without-glib --without-cairo --without-freetype \
      --without-fontconfig --without-icu \
      --enable-static --disable-shared \
      --quiet)
  _harfbuzz_configured=1
}

build_harfbuzz() {
  local variant="$1" OUT="${BUILD_DIR}/${variant}"
  mkdir -p "${OUT}"

  _harfbuzz_configure

  local USE_CC="${CC}" USE_CXX="${CXX}" FLAGS=""
  case "${variant}" in
    coverage)   FLAGS="-O1 -g -fprofile-instr-generate -fcoverage-mapping";;
    sanitizer)  FLAGS="-O1 -g -fsanitize=address,undefined";;
    fuzzer)     FLAGS="-O1 -g -fsanitize=fuzzer,address";;
    afl)        FLAGS="-O1 -g -fsanitize=address"; USE_CC="${AFL_CC}"; USE_CXX="${AFL_CXX}";;
  esac
  FLAGS="${FLAGS} ${STDLIB_FLAGS}"

  echo "==> harfbuzz ${variant} (CC=${USE_CC})"

  # Clean object files, rebuild with new flags. Makefile stays from configure.
  (cd "${UPSTREAM_DIR}/src/hb-ucdn" && make clean >/dev/null 2>&1 || true)
  (cd "${UPSTREAM_DIR}/src" && rm -f .libs/libharfbuzz.a && \
    find . -maxdepth 1 -name '*.lo' -o -name '*.o' | xargs rm -f 2>/dev/null || true)

  (cd "${UPSTREAM_DIR}" && \
    make -j"$(nproc)" -C src/hb-ucdn \
      CC="${USE_CC}" CXX="${USE_CXX}" CXXFLAGS="${FLAGS}" CFLAGS="${FLAGS}")
  (cd "${UPSTREAM_DIR}" && \
    make -j"$(nproc)" -C src libharfbuzz.la \
      CC="${USE_CC}" CXX="${USE_CXX}" CXXFLAGS="${FLAGS}" CFLAGS="${FLAGS}")

  cp "${UPSTREAM_DIR}/src/.libs/libharfbuzz.a" "${OUT}/libharfbuzz.a"

  local HARNESS="${HARNESS_DIR}/hb-fuzzer.cc"
  local INC="-I${UPSTREAM_DIR}/src"

  case "${variant}" in
    fuzzer)
      echo "==> linking harfbuzz_fuzzer (libFuzzer)"
      ${USE_CXX} ${FLAGS} ${INC} \
        "${HARNESS}" "${OUT}/libharfbuzz.a" \
        -lpthread \
        -o "${OUT}/harfbuzz_fuzzer"
      ;;
    afl)
      echo "==> linking harfbuzz_afl_fuzzer"
      ${USE_CXX} ${FLAGS} -fsanitize=fuzzer ${INC} \
        "${HARNESS}" "${OUT}/libharfbuzz.a" \
        -lpthread \
        -o "${OUT}/harfbuzz_afl_fuzzer"
      ;;
    coverage)
      echo "==> linking harfbuzz seed_replay"
      ${USE_CXX} ${FLAGS} ${INC} \
        "${HARNESS}" "${HARNESS_DIR}/seed_replay_main.cc" \
        "${OUT}/libharfbuzz.a" \
        -lpthread \
        -o "${OUT}/seed_replay"
      ;;
  esac
}

# --- Dispatch -----------------------------------------------------------------
case "${NAME}" in
  re2)
    for variant in ${VARIANTS}; do
      build_re2 "${variant}"
    done
    ;;
  harfbuzz)
    for variant in ${VARIANTS}; do
      build_harfbuzz "${variant}"
    done
    ;;
  *)
    echo "build_instrumented: ${NAME} not yet wired (stub)" >&2
    exit 3
    ;;
esac

echo "==> ${NAME} built into ${BUILD_DIR}"
