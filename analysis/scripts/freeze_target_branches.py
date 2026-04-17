"""Freeze the M2 target-branch set for the ablation experiment.

Supports --target re2 (original) and --target harfbuzz (new).

RE2 pipeline:
1. Load 3 per-test upstream coverage profiles and union them.
2. Build smoke corpus: random regex-shaped inputs + hand-crafted patterns.
3. Replay each smoke seed, collect per-seed coverage.
4. Filter to asymmetric gap branches (exactly one side covered upstream).
5. Mark as "smoke-reachable" iff ≥1 smoke seed takes the uncovered side.
6. Deterministically sample N=50, split 30 shown / 20 held-back.

Harfbuzz pipeline (hard-only definition):
1. Build baseline profile by replaying empty + minimal-sfnt inputs.
2. Generate ALL branch candidates from the baseline LLVM coverage export.
3. Filter to asymmetric gaps (one side covered by baseline).
4. Build smoke corpus: 50 random binary blobs + 25 minimal TTF skeletons.
5. For each candidate:
   - ttf_hits = count of TTF skeleton smoke seeds hitting uncovered side
   - rand_hits = count of random blob smoke seeds hitting uncovered side
   - "Hard" branch = ttf_hits >= 1 AND rand_hits == 0
     (requires font structure knowledge; random bytes can't reach it)
6. Deterministically sample N=50 hard branches, split 30/20.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    HB_FIXTURES_DIR,
    HB_M2_SMOKE_LOG_PATH,
    HB_M2_TARGETS_PATH,
    HB_UPSTREAM_UNION_PROFILE_PATH,
    M2_RNG_SEED,
    M2_SHOWN_COUNT,
    M2_SMOKE_LOG_PATH,
    M2_TARGET_COUNT,
    M2_TARGETS_PATH,
    RE2_V2_FIXTURES_DIR,
    RE2_V2_M2_SMOKE_LOG_PATH,
    RE2_V2_M2_TARGETS_PATH,
    RE2_V2_UPSTREAM_UNION_PROFILE_PATH,
    UPSTREAM_UNION_PROFILE_PATH,
)
from core.coverage_utils import parse_llvm_cov_json, union_coverage
from core.dataset_schema import CoverageGapsReport, CoverageProfile, FileCoverage
from core.logging_config import get_logger
from synthesis.scripts.generate_random_inputs import generate_random

logger = get_logger("utcf.ablation.freeze_targets")

# ─── RE2 paths ────────────────────────────────────────────────────────────────
RE2_TESTS_DIR = REPO_ROOT / "dataset/fixtures/re2_ab/re2/tests"
RE2_GAPS_PATH = REPO_ROOT / "dataset/fixtures/re2_ab/re2/coverage_gaps.json"
RE2_SEED_REPLAY = REPO_ROOT / "dataset/targets/src/re2/build/coverage/seed_replay"

# ─── Harfbuzz paths ───────────────────────────────────────────────────────────
HB_SEED_REPLAY = REPO_ROOT / "dataset/targets/src/harfbuzz/build/coverage/seed_replay"
HB_SRC_ROOT = REPO_ROOT / "dataset/targets/src/harfbuzz/upstream/src"

LLVM_PROFDATA = os.environ.get("LLVM_PROFDATA", "llvm-profdata-15")
LLVM_COV = os.environ.get("LLVM_COV", "llvm-cov-15")

# ─── RE2 hand-crafted smoke regexes ──────────────────────────────────────────
HAND_CRAFTED_REGEXES = [
    # Original 25 patterns
    "(?P<x>a+)",
    "(?P<longname>[abc]+)",
    "(a*)*",
    "((a+)+)+",
    "a{1000,}",
    "a{0,1000}",
    "a{5,10}",
    r"\p{Greek}+",
    r"\p{L}+",
    r"\p{N}{3}",
    "[^a-zA-Z0-9]",
    "[a-z&&[^aeiou]]",
    "[\\d\\D]+",
    "(?i)abc",
    "(?ms)^x.*y$",
    "(?:a|b|c)+",
    "a|b|c|d|e|f|g|h|i|j",
    r"\babc\b",
    r"\Bzzz\B",
    "^abc$",
    "(a)(b)(c)\\1\\2\\3",
    "(?=abc)",
    "(?!abc)",
    "(?<=abc)",
    ".{10,20}?",
    # Extended patterns targeting deep parser / simplify / walker branches
    "(?P<a>x)|(?P<b>y)",              # named alternation
    "(?P<n1>a)(?P<n2>b)(?P<n3>c)",   # multiple named groups
    "(?P<x>a+)?",                      # optional named group → quest simplify
    "(a+?)+",                          # non-greedy nested
    "(a{2,3}){4,5}",                   # nested counted repetition
    "a+?b+?c+?",                       # multiple non-greedy
    "(a|){50}",                        # alternation with empty
    "(?:(?:a|b)+c)+",                  # nested non-capturing
    r"\p{Lu}\p{Ll}+",                  # Unicode category sequence
    r"\p{Greek}\p{Cyrillic}",          # multiple Unicode scripts
    r"(?i)\p{L}+",                     # case-insensitive Unicode
    r"\p{Z}+",                         # Unicode separator
    r"\p{P}+",                         # Unicode punctuation
    "(?-i:ABC)",                       # inline flag removal
    "(?P<x>(?P<y>a+)b+)",             # nested named groups
    "(?P<first>[a-z])(?P<second>\\1)", # named backreference
    "a(?=b)b",                         # lookahead followed by match
    "(?<=a)b(?=c)",                    # surrounded by lookarounds
    "(?:a(?:b(?:c(?:d)?)?)?)?",       # deeply nested optional groups
    "(a)(b)|(c)(d)|(e)(f)",           # alternation with multiple captures
    "a{0}b{0,}c{1,1}",               # zero-count and exact-count
    "(?>a+)",                          # atomic group (invalid in RE2, exercises error path)
    "(?'name'abc)",                    # .NET-style named group (exercises parse error path)
    r"\k<name>",                       # named backreference without definition
    "(?P=undefined)",                  # undefined named back-reference
    "[[:alpha:]][[:digit:]]+",        # POSIX character classes
    "[[:space:][:punct:]]+",          # multiple POSIX classes
    r"\Q.*\E",                         # Perl quotemeta (invalid in RE2)
    r"(?x) a b c # comment",          # verbose mode (invalid in RE2, exercises error path)
]

# ─── Harfbuzz minimal TTF/OTF skeletons ──────────────────────────────────────
# Each entry is raw bytes that tell harfbuzz "this is a font with certain tables."
# They are deliberately incomplete/malformed to stress parser branches.
def _sfnt_header(version: int, num_tables: int) -> bytes:
    """12-byte sfnt header: version(4) numTables(2) searchRange(2) entrySelector(2) rangeShift(2)."""
    sr = 16 * (1 << (num_tables.bit_length() - 1)) if num_tables else 0
    es = max(0, num_tables.bit_length() - 1)
    rng = max(0, num_tables * 16 - sr)
    return struct.pack(">IHHHH", version, num_tables, sr, es, rng)

def _table_record(tag: str, offset: int, length: int) -> bytes:
    """16-byte table directory entry: tag(4) checksum(4) offset(4) length(4)."""
    return tag.encode("ascii")[:4].ljust(4, b"\x00") + struct.pack(">III", 0, offset, length)

def _build_ttf_skeletons() -> list[bytes]:
    """Build 60+ minimal TTF/OTF structures covering diverse harfbuzz parsing paths."""
    skeletons: list[bytes] = []
    rng = random.Random(1234)

    # ── Group 1: bare headers (6 variants) ──────────────────────────────────────
    # 1. Truly empty
    skeletons.append(b"")
    # 2-4. sfnt magic variants with 0 tables
    for magic in (0x00010000, 0x4F54544F, 0x74727565):  # TT, OTTO, 'true'
        skeletons.append(_sfnt_header(magic, 0))
    # 5. sfnt 'typ1' (Type 1 PostScript)
    skeletons.append(_sfnt_header(0x74797031, 0))
    # 6. sfnt with large numTables value (overflow stress)
    skeletons.append(_sfnt_header(0x00010000, 255))

    # ── Group 2: single required tables ─────────────────────────────────────────
    def _single_table(tag: str, body: bytes) -> bytes:
        hdr = _sfnt_header(0x00010000, 1) + _table_record(tag, 28, len(body))
        return hdr + body

    # 7. head table (54 bytes): version 1.0, magic, flags, unitsPerEm=2048, rest zeroed
    head = struct.pack(">HH", 1, 0)            # majorVersion, minorVersion
    head += struct.pack(">I", 0)               # fontRevision (Fixed)
    head += struct.pack(">I", 0xB1B0AFBA)      # checksumAdjustment
    head += struct.pack(">I", 0x5F0F3CF5)      # magicNumber
    head += struct.pack(">HH", 0x000B, 2048)   # flags, unitsPerEm
    head += b"\x00" * 16                        # created + modified (8 bytes each)
    head += struct.pack(">hhhh", 0, 0, 100, 100)   # xMin, yMin, xMax, yMax
    head += struct.pack(">HH", 0, 0)            # macStyle, lowestRecPPEM
    head += struct.pack(">hhh", 2, 0, 0)        # fontDirectionHint, indexToLocFormat, glyphDataFormat
    skeletons.append(_single_table("head", head))
    # 8. maxp version 0.5
    skeletons.append(_single_table("maxp", struct.pack(">HH", 0x0005, 10)))
    # 9. maxp version 1.0 (full table, 32 bytes)
    skeletons.append(_single_table("maxp", struct.pack(">HHHHHHHHHHHHHHH",
                                                        0x0001, 10, 10, 2, 0, 512, 96, 0, 0, 0, 0, 1, 1, 0, 0)))
    # 10. hhea
    skeletons.append(_single_table("hhea", struct.pack(">HHhhhHhhhhhhhH",
                                                        1, 0, 800, -200, 0, 1000, 0, 0, 0, 0, 0, 0, 0, 1)))
    # 11. post (format 2.0 header)
    skeletons.append(_single_table("post", struct.pack(">IIIIHH",
                                                        0x00020000, 0, 0, 0, 0, 0)))
    # 12. name (empty string storage)
    skeletons.append(_single_table("name", struct.pack(">HHH", 0, 0, 6)))

    # ── Group 3: GDEF variants ───────────────────────────────────────────────────
    # 13. GDEF v1.0 — all offsets zero
    skeletons.append(_single_table("GDEF", struct.pack(">HHHHHi", 1, 0, 0, 0, 0, 0)))
    # 14. GDEF v1.2 — with markGlyphSets pointer
    gdef12 = struct.pack(">HHHHHH", 1, 2, 0, 0, 0, 0) + struct.pack(">HH", 12, 1) + struct.pack(">H", 4) + struct.pack(">H", 0)
    skeletons.append(_single_table("GDEF", gdef12))
    # 15. GDEF v1.3 — with varStore pointer
    gdef13 = struct.pack(">HHHHHHHI", 1, 3, 0, 0, 0, 0, 0, 0)
    skeletons.append(_single_table("GDEF", gdef13))
    # 16. GDEF with non-zero GlyphClassDef offset (format 2 ClassDef)
    classdef = struct.pack(">HHH", 2, 1, 0)  # format=2, count=1, range
    classdef += struct.pack(">HHH", 0, 10, 1)  # startGlyph=0, endGlyph=10, class=1
    gdef_with_class = struct.pack(">HHHHHH", 1, 0, 12, 0, 0, 0) + classdef
    skeletons.append(_single_table("GDEF", gdef_with_class))

    # ── Group 4: GSUB with different script tags ─────────────────────────────────
    def _gsub_with_script(script_tag: bytes) -> bytes:
        # GSUB 1.0: scriptListOffset=10, featureListOffset=12, lookupListOffset=14
        gsub_hdr = struct.pack(">HH", 1, 0) + struct.pack(">HHH", 10, 12, 14)
        # ScriptList: 1 entry
        script_list = struct.pack(">H", 1) + script_tag[:4].ljust(4, b"\x00") + struct.pack(">H", 4)
        # DefaultLangSys: lookupOrderOffset=0, requiredFeatureIndex=0xFFFF, featureIndexCount=0
        default_langsys = struct.pack(">HHH", 0, 0xFFFF, 0)
        return gsub_hdr + script_list + default_langsys

    for tag in (b"DFLT", b"arab", b"deva", b"hebr", b"thai", b"hang", b"latn", b"grek",
                b"cyrl", b"kana", b"hani", b"syrc", b"tibt", b"geor", b"beng"):
        skeletons.append(_single_table("GSUB", _gsub_with_script(tag)))

    # ── Group 5: GSUB lookup types ───────────────────────────────────────────────
    # 31. GSUB lookup type 1 (single substitution) — format 1
    lookup1 = (struct.pack(">HHH", 1, 1, 6)  # lookupType=1, lookupFlag=0, subTableCount=1 ... actually this is the subtable
               + struct.pack(">HH", 1, 1)     # format=1, coverageOffset=4, deltaGlyphID
               + struct.pack(">H", 1))         # coverage: format 1
    skeletons.append(_single_table("GSUB", struct.pack(">HH", 1, 0) + struct.pack(">HHH", 10, 10, 10) + lookup1))

    # 32. GSUB lookup type 4 (ligature substitution) minimal
    lig_subst = struct.pack(">HHH", 1, 4, 6)  # format, coverageOffset, ligSetCount
    skeletons.append(_single_table("GSUB", struct.pack(">HH", 1, 0) + struct.pack(">HHH", 10, 10, 10) + lig_subst))

    # 33. GSUB lookup type 6 (chaining context) format 3
    chain = struct.pack(">HHHH", 3, 0, 0, 0)  # format=3, backtrackCount=0, inputCount=0, lookAheadCount=0
    skeletons.append(_single_table("GSUB", struct.pack(">HH", 1, 0) + struct.pack(">HHH", 10, 10, 10) + chain))

    # ── Group 6: GPOS ────────────────────────────────────────────────────────────
    # 34. GPOS 1.0 header only
    skeletons.append(_single_table("GPOS", struct.pack(">HHHHHH", 1, 0, 10, 10, 10, 10)))

    # 35. GPOS lookup type 1 (single adjustment) — format 1
    gpos_sub = struct.pack(">HHH", 1, 4, 4)  # format=1, coverageOffset=4, valueFormat=4
    gpos_sub += struct.pack(">H", 1)  # value: XAdvance=1
    skeletons.append(_single_table("GPOS", struct.pack(">HH", 1, 0) + struct.pack(">HHH", 10, 10, 10) + gpos_sub))

    # 36. GPOS lookup type 2 (pair adjustment) — format 1
    pair_sub = struct.pack(">HHHHHHi", 1, 4, 4, 4, 0, 0, 0)
    skeletons.append(_single_table("GPOS", struct.pack(">HH", 1, 0) + struct.pack(">HHH", 10, 10, 10) + pair_sub))

    # ── Group 7: cmap formats ────────────────────────────────────────────────────
    # 37. cmap format 4 (BMP Unicode)
    cmap4_segs = 2  # 1 real segment + terminator
    cmap4_seglen = cmap4_segs * 8 + 14
    cmap4 = struct.pack(">HHHHHH", 4, cmap4_seglen, 0, cmap4_segs * 2, 0, 0)
    cmap4 += struct.pack(">HH", 0x007E, 0xFFFF)  # endCode[]: last BMP, terminator
    cmap4 += struct.pack(">H", 0)                  # reservedPad
    cmap4 += struct.pack(">HH", 0x0020, 0xFFFF)   # startCode[]
    cmap4 += struct.pack(">HH", 0, 1)              # idDelta[]
    cmap4 += struct.pack(">HH", 0, 0)              # idRangeOffset[]
    cmap_enc = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 12)  # platform 3, enc 1
    skeletons.append(_single_table("cmap", struct.pack(">HH", 0, 1) + cmap_enc + cmap4))

    # 38. cmap format 12 (full Unicode)
    cmap12 = struct.pack(">HHI", 12, 0, 16)  # format=12, reserved=0, length=16
    cmap12 += struct.pack(">II", 0, 1)        # language=0, numGroups=1
    cmap12 += struct.pack(">III", 0x0041, 0x005A, 1)  # A-Z → glyph 1
    cmap_enc12 = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 10, 12)  # platform 3, enc 10
    skeletons.append(_single_table("cmap", struct.pack(">HH", 0, 1) + cmap_enc12 + cmap12))

    # 39. cmap format 13 (many-to-one range)
    cmap13 = struct.pack(">HHI", 13, 0, 16) + struct.pack(">II", 0, 1) + struct.pack(">III", 0, 0x10FFFF, 0)
    cmap_enc13 = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 10, 12)
    skeletons.append(_single_table("cmap", struct.pack(">HH", 0, 1) + cmap_enc13 + cmap13))

    # ── Group 8: multi-table combinations ────────────────────────────────────────
    # 40. head + maxp + hhea + hmtx (minimal OpenType font skeleton)
    tables_data = []
    tables_data.append(("head", head))
    tables_data.append(("maxp", struct.pack(">HH", 0x0005, 1)))
    tables_data.append(("hhea", struct.pack(">HHhhhHhhhhhhhH", 1, 0, 800, -200, 0, 1000, 0, 0, 0, 0, 0, 0, 0, 1)))
    tables_data.append(("hmtx", struct.pack(">Hh", 500, 0)))
    n = len(tables_data)
    hdr = _sfnt_header(0x00010000, n)
    offset = 12 + n * 16
    for tag, body in tables_data:
        hdr += _table_record(tag, offset, len(body))
        offset += len(body)
    body_bytes = b"".join(b for _, b in tables_data)
    skeletons.append(hdr + body_bytes)

    # 41. GSUB + GDEF together
    gsub_body = struct.pack(">HH", 1, 0) + struct.pack(">HHH", 10, 10, 10)
    gdef_body = struct.pack(">HHHHHH", 1, 0, 0, 0, 0, 0)
    n = 2; hdr = _sfnt_header(0x00010000, n)
    offset = 12 + n * 16
    hdr += _table_record("GDEF", offset, len(gdef_body)); offset += len(gdef_body)
    hdr += _table_record("GSUB", offset, len(gsub_body))
    skeletons.append(hdr + gdef_body + gsub_body)

    # 42. GSUB + GPOS together
    gpos_body = struct.pack(">HHHHHH", 1, 0, 10, 10, 10, 10)
    n = 2; hdr = _sfnt_header(0x00010000, n)
    offset = 12 + n * 16
    hdr += _table_record("GPOS", offset, len(gpos_body)); offset += len(gpos_body)
    hdr += _table_record("GSUB", offset, len(gsub_body))
    skeletons.append(hdr + gpos_body + gsub_body)

    # 43. head + cmap + GSUB (3-table font)
    cmap_body = struct.pack(">HH", 0, 0)
    n = 3; hdr = _sfnt_header(0x00010000, n)
    offset = 12 + n * 16
    hdr += _table_record("head", offset, len(head)); offset += len(head)
    hdr += _table_record("cmap", offset, len(cmap_body)); offset += len(cmap_body)
    hdr += _table_record("GSUB", offset, len(gsub_body))
    skeletons.append(hdr + head + cmap_body + gsub_body)

    # ── Group 9: loca + glyf (outline data) ─────────────────────────────────────
    # 44. Short loca + simple glyf
    loca_s = struct.pack(">HH", 0, 7)  # 1 glyph, short format
    glyf_data = struct.pack(">hhhhh", 1, 0, 0, 100, 100)  # 1 contour, bbox
    glyf_data += struct.pack(">H", 0)   # endPtsOfContours[0]
    skeletons.append(_single_table("loca", loca_s) + _single_table("glyf", glyf_data))

    # 45. Long loca (indexToLocFormat=1) + composite glyph
    loca_l = struct.pack(">II", 0, 14)  # long format
    skeletons.append(_single_table("loca", loca_l))

    # ── Group 10: variable fonts ─────────────────────────────────────────────────
    # 46. fvar table (font variations axis)
    axis_count = 1
    fvar = struct.pack(">HHHHHH", 1, 0, 16, axis_count, 20, 0)
    fvar += b"wght"  # axisTag
    fvar += struct.pack(">iii", 100 * 65536, 400 * 65536, 900 * 65536)  # min, default, max (Fixed)
    fvar += struct.pack(">HH", 0, 256)  # axisNameID, flags
    skeletons.append(_single_table("fvar", fvar))

    # 47. avar table (axis variations)
    avar = struct.pack(">HHI", 1, 0, 1)  # version, unused, axisCount
    avar += struct.pack(">H", 3)          # positionMapCount for first axis
    avar += struct.pack(">hh", -16384, -16384)  # from=-1 to=-1
    avar += struct.pack(">hh", 0, 0)            # from=0 to=0
    avar += struct.pack(">hh", 16384, 16384)    # from=1 to=1
    skeletons.append(_single_table("avar", avar))

    # 48. gvar table header
    gvar = struct.pack(">HHIHHII", 1, 0, 0, 0, 0, 0, 0)
    skeletons.append(_single_table("gvar", gvar))

    # ── Group 11: color fonts ─────────────────────────────────────────────────────
    # 49. COLR v0 table (color layers)
    colr = struct.pack(">HHHHI", 0, 0, 0, 0, 0)  # version, numBaseGlyphRecords, offsets
    skeletons.append(_single_table("COLR", colr))

    # 50. CPAL table (color palette)
    cpal = struct.pack(">HHHIH", 0, 1, 4, 0, 0)  # version, numPaletteEntries, numPalettes, numColorRecords, offsetFirstColorRecord
    cpal += struct.pack(">I", 0xFF0000FF)  # BGRA: red
    skeletons.append(_single_table("CPAL", cpal))

    # ── Group 12: advanced OT tables ─────────────────────────────────────────────
    # 51. MATH table header
    math_hdr = struct.pack(">HH", 1, 0)  # version 1.0
    math_hdr += struct.pack(">HHH", 0, 0, 0)  # MathConstants, MathGlyphInfo, MathVariants offsets
    skeletons.append(_single_table("MATH", math_hdr))

    # 52. BASE table (baseline coords)
    base = struct.pack(">HH", 1, 0)  # version 1.0
    base += struct.pack(">HH", 4, 0)  # horizAxisOffset, vertAxisOffset
    base += struct.pack(">H", 0)       # Axis: BaseTagList offset=0
    skeletons.append(_single_table("BASE", base))

    # 53. JSTF table (justification)
    jstf = struct.pack(">HH", 1, 0)  # version 1.0
    jstf += struct.pack(">H", 0)      # JstfScriptCount=0
    skeletons.append(_single_table("JSTF", jstf))

    # 54. kern table (legacy TrueType kerning)
    kern_pair = struct.pack(">HHh", 65, 66, -50)  # left=A, right=B, value=-50
    kern_sub = struct.pack(">HHHHHH", 0, 14, 0, 1, 8, 0)  # version, length, coverage, nPairs, searchRange, entrySelector
    kern_sub += struct.pack(">H", 0) + kern_pair  # rangeShift + pair
    kern = struct.pack(">HH", 0, 1) + kern_sub    # version=0, nTables=1
    skeletons.append(_single_table("kern", kern))

    # 55. 'morx' table (AAT extended morphology, triggers non-OT path)
    morx = struct.pack(">IHH", 0x00020000, 1, 0)  # version, nChains, unused
    skeletons.append(_single_table("morx", morx))

    # 56. 'feat' table (AAT features)
    feat = struct.pack(">HHI", 1, 0, 0)  # version 1.0, reserved, featureNameCount=0
    skeletons.append(_single_table("feat", feat))

    # ── Group 13: CFF structures ──────────────────────────────────────────────────
    # 57. CFF header + minimal name index
    cff_hdr = struct.pack(">BBBB", 1, 0, 4, 1)  # major, minor, hdrSize, offSize
    cff_name_idx = struct.pack(">H", 0)  # count=0
    skeletons.append(_sfnt_header(0x4F54544F, 1) + _table_record("CFF ", 28, len(cff_hdr) + len(cff_name_idx))
                     + cff_hdr + cff_name_idx)

    # 58. CFF2 header
    cff2_hdr = struct.pack(">BBHH", 2, 0, 5, 0)  # major, minor, hdrSize, topDictLength
    skeletons.append(_sfnt_header(0x4F54544F, 1) + _table_record("CFF2", 28, len(cff2_hdr)) + cff2_hdr)

    # ── Group 14: hinting / metrics overflow stress ───────────────────────────────
    # 59. cvt table (control values for TrueType hinting)
    cvt = struct.pack(">50h", *range(-25, 25))  # 50 control values
    skeletons.append(_single_table("cvt ", cvt))

    # 60. fpgm table (font program bytecode)
    # PUSHB[000] 0x01, SVTCA[y-axis] 0x01, EIF 0x59
    fpgm = bytes([0xB0, 0x01, 0x01, 0x59])
    skeletons.append(_single_table("fpgm", fpgm))

    # ── Group 15: random-magic fonts of various sizes ─────────────────────────────
    for sz in (16, 48, 128, 256, 512, 1024, 4096):
        payload = _sfnt_header(0x00010000, rng.randint(1, 8)) + bytes(
            rng.getrandbits(8) for _ in range(sz)
        )
        skeletons.append(payload)

    return skeletons


# ─── Path normalization (shared) ─────────────────────────────────────────────

def _normalize_path(p: str) -> str:
    """Return upstream-relative path: drop everything up to and including 'upstream/'."""
    marker = "upstream/"
    idx = p.find(marker)
    if idx >= 0:
        return p[idx + len(marker):]
    return p


def _normalize_profile(profile: CoverageProfile) -> CoverageProfile:
    """Return a copy of profile with file keys normalized to upstream-relative."""
    new_files = {}
    for filename, fc in profile.files.items():
        norm = _normalize_path(filename)
        new_branches = {}
        for key, br in fc.branches.items():
            _, _, line_str = key.rpartition(":")
            try:
                line_int = int(line_str)
            except ValueError:
                continue
            new_branches[f"{norm}:{line_int}"] = br
        new_files[norm] = FileCoverage(
            lines_covered=list(fc.lines_covered),
            lines_not_covered=list(fc.lines_not_covered),
            branches=new_branches,
            functions_covered=list(fc.functions_covered),
            functions_not_covered=list(fc.functions_not_covered),
        )
    return CoverageProfile(
        test_name=profile.test_name,
        upstream_file=profile.upstream_file,
        upstream_line=profile.upstream_line,
        framework=profile.framework,
        files=new_files,
        total_lines_covered=profile.total_lines_covered,
        total_lines_in_source=profile.total_lines_in_source,
        total_branches_covered=profile.total_branches_covered,
        total_branches_in_source=profile.total_branches_in_source,
    )


def _uncovered_side(union_profile: CoverageProfile, file: str, line: int) -> str | None:
    """Return 'true', 'false', or None if both/neither side is uncovered (asymmetric check)."""
    if file not in union_profile.files:
        return None
    br = union_profile.files[file].branches.get(f"{file}:{line}")
    if br is None:
        return None
    if br.true_taken and not br.false_taken:
        return "false"
    if br.false_taken and not br.true_taken:
        return "true"
    return None


def hits_uncovered_side(seed: CoverageProfile, baseline: CoverageProfile,
                        file: str, line: int) -> bool:
    """Did the seed take a (file, line) branch side the baseline did not?"""
    if file not in seed.files:
        return False
    seed_br = seed.files[file].branches.get(f"{file}:{line}")
    if seed_br is None:
        return False
    base_br = None
    if file in baseline.files:
        base_br = baseline.files[file].branches.get(f"{file}:{line}")
    base_true = base_br.true_taken if base_br else False
    base_false = base_br.false_taken if base_br else False
    return (seed_br.true_taken and not base_true) or (seed_br.false_taken and not base_false)


# ─── Generic seed replay ──────────────────────────────────────────────────────

def replay_seed(seed_path: Path, out_dir: Path, idx: int,
                binary: Path) -> CoverageProfile | None:
    """Run seed_replay on one seed, parse coverage, return the profile."""
    profraw = out_dir / f"seed_{idx}.profraw"
    profdata = out_dir / f"seed_{idx}.profdata"
    cov_json = out_dir / f"seed_{idx}.json"

    env = os.environ.copy()
    env["LLVM_PROFILE_FILE"] = str(profraw)
    try:
        subprocess.run(
            [str(binary), str(seed_path)],
            capture_output=True, timeout=15, check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        return None

    if not profraw.exists():
        return None

    subprocess.run(
        [LLVM_PROFDATA, "merge", "-sparse", str(profraw), "-o", str(profdata)],
        check=True, capture_output=True,
    )
    with open(cov_json, "w") as fh:
        subprocess.run(
            [LLVM_COV, "export", str(binary), f"-instr-profile={profdata}",
             "--skip-expansions"],
            check=True, stdout=fh, stderr=subprocess.PIPE,
        )

    profile = parse_llvm_cov_json(
        cov_json,
        test_name=f"smoke_{idx}",
        upstream_file="",
        upstream_line=1,
        framework="smoke",
    )
    return _normalize_profile(profile)


# ─── RE2: load upstream union from pre-computed per-test profiles ─────────────

def load_upstream_union() -> CoverageProfile:
    """Load and union the 3 per-test RE2 upstream coverage profiles."""
    profile_paths = sorted(RE2_TESTS_DIR.glob("test_*/coverage.json"))
    if not profile_paths:
        raise FileNotFoundError(f"no per-test profiles under {RE2_TESTS_DIR}")
    profiles = [
        _normalize_profile(CoverageProfile.model_validate_json(p.read_text()))
        for p in profile_paths
    ]
    union = union_coverage(profiles)
    logger.info("upstream union (RE2)", extra={
        "n_test_profiles": len(profiles),
        "branches_covered": union.total_branches_covered,
        "branches_total": union.total_branches_in_source,
    })
    return union


def build_smoke_corpus(work_dir: Path, n_random: int = 50) -> list[Path]:
    """RE2 smoke: random regex-shaped inputs + hand-crafted patterns."""
    work_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    random_inputs = generate_random(
        target="re2", count=n_random, max_len=64, seed=M2_RNG_SEED, input_format="regex",
    )
    for inp in random_inputs:
        out = work_dir / f"random_{inp.input_id}.bin"
        out.write_bytes(base64.b64decode(inp.content_b64))
        paths.append(out)

    for i, regex in enumerate(HAND_CRAFTED_REGEXES):
        body = regex.encode("utf-8", errors="replace")[:62]
        payload = b"\x00\x00" + body
        out = work_dir / f"handcrafted_{i:02d}.bin"
        out.write_bytes(payload)
        paths.append(out)

    logger.info("RE2 smoke corpus built", extra={"n_seeds": len(paths)})
    return paths


# ─── Harfbuzz: baseline + smoke + candidate enumeration ──────────────────────

def build_harfbuzz_baseline(work_dir: Path) -> CoverageProfile:
    """Run harfbuzz seed_replay with empty + minimal inputs; return union profile."""
    work_dir.mkdir(parents=True, exist_ok=True)

    # Baseline inputs: empty, minimal TrueType header, minimal CFF header, zero-filled
    baseline_inputs = [
        ("empty", b""),
        ("tt_header", _sfnt_header(0x00010000, 0)),
        ("cff_header", _sfnt_header(0x4F54544F, 0)),
        ("zeros_256", bytes(256)),
    ]
    profiles: list[CoverageProfile] = []
    for name, payload in baseline_inputs:
        seed_path = work_dir / f"{name}.bin"
        seed_path.write_bytes(payload)
        prof = replay_seed(seed_path, work_dir, len(profiles), HB_SEED_REPLAY)
        if prof is not None:
            profiles.append(prof)

    if not profiles:
        # If all fail, return empty profile — harfbuzz rejects them all
        return CoverageProfile(
            test_name="hb_baseline_empty",
            upstream_file="",
            upstream_line=1,
            framework="smoke",
        )

    baseline = union_coverage(profiles)
    logger.info("harfbuzz baseline built", extra={
        "n_profiles": len(profiles),
        "branches_covered": baseline.total_branches_covered,
    })
    return baseline


def _read_source_context(src_root: Path, file: str, line: int, context: int = 2) -> str:
    """Read ±context lines around `line` from `src_root/file`."""
    src_file = src_root / file
    if not src_file.exists():
        return ""
    lines = src_file.read_text(errors="replace").splitlines()
    start = max(0, line - context - 1)
    end = min(len(lines), line + context)
    return "\n".join(lines[start:end])


def enumerate_harfbuzz_candidates(
    baseline: CoverageProfile,
) -> list[tuple[str, int, str, str, str]]:
    """Return all asymmetric gap branches as (file, line, code_context, desc, uncov_side)."""
    candidates = []
    for filename, fc in baseline.files.items():
        # Only harfbuzz source files (not system headers)
        if not filename.startswith("src/"):
            continue
        for key, br in fc.branches.items():
            _, _, line_str = key.rpartition(":")
            try:
                line_int = int(line_str)
            except ValueError:
                continue
            uncov = _uncovered_side(baseline, filename, line_int)
            if uncov is None:
                continue
            ctx = _read_source_context(HB_SRC_ROOT, filename.removeprefix("src/"), line_int)
            desc = f"{filename}:{line_int} — take the {uncov.upper()} branch"
            candidates.append((filename, line_int, ctx, desc, uncov))
    logger.info("harfbuzz candidates enumerated", extra={"n_asymmetric": len(candidates)})
    return candidates


def build_harfbuzz_smoke(work_dir: Path, n_random: int = 100) -> tuple[list[Path], list[Path]]:
    """Build harfbuzz smoke corpus. Returns (random_paths, ttf_paths)."""
    work_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(M2_RNG_SEED)
    rand_paths: list[Path] = []
    for i in range(n_random):
        length = rng.randint(4, 512)
        payload = bytes(rng.getrandbits(8) for _ in range(length))
        p = work_dir / f"rand_{i:03d}.bin"
        p.write_bytes(payload)
        rand_paths.append(p)

    skeletons = _build_ttf_skeletons()
    ttf_paths: list[Path] = []
    for i, skel in enumerate(skeletons):
        p = work_dir / f"ttf_{i:02d}.bin"
        p.write_bytes(skel)
        ttf_paths.append(p)

    logger.info("harfbuzz smoke corpus built", extra={
        "n_random": len(rand_paths),
        "n_ttf": len(ttf_paths),
    })
    return rand_paths, ttf_paths


# ─── Main freeze orchestration ────────────────────────────────────────────────

def _run_smoke_replay(
    seed_paths: list[Path],
    replay_dir: Path,
    binary: Path,
    offset: int = 0,
) -> list[tuple[Path, CoverageProfile | None]]:
    results = []
    for i, sp in enumerate(seed_paths):
        prof = replay_seed(sp, replay_dir, offset + i, binary)
        results.append((sp, prof))
    n_ok = sum(1 for _, p in results if p is not None)
    logger.info("smoke replay batch", extra={"n": len(results), "n_ok": n_ok})
    return results


def freeze_targets_re2(*, dry_run: bool = False) -> dict:
    union_profile = load_upstream_union()
    gaps = CoverageGapsReport.model_validate_json(RE2_GAPS_PATH.read_text())

    all_candidates = gaps.gap_branches
    asymmetric = [
        (g.file, g.line, g.code_context, g.condition_description,
         _uncovered_side(union_profile, g.file, g.line))
        for g in all_candidates
        if _uncovered_side(union_profile, g.file, g.line) is not None
    ]
    logger.info("RE2 candidate filtering",
                extra={"n_all": len(all_candidates), "n_asymmetric": len(asymmetric)})

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        seed_paths = build_smoke_corpus(td_path / "seeds")
        replay_dir = td_path / "replay"
        replay_dir.mkdir()
        smoke_profiles = _run_smoke_replay(seed_paths, replay_dir, RE2_SEED_REPLAY)

        smoke_log: dict = {"candidates": [], "n_smoke_seeds": len(smoke_profiles)}
        reachable: list[tuple[str, int, str, str, str, list[str]]] = []
        for file, line, ctx, desc, uncov_side in asymmetric:
            hitters = []
            for seed_path, prof in smoke_profiles:
                if prof is None or file not in prof.files:
                    continue
                br = prof.files[file].branches.get(f"{file}:{line}")
                if br is None:
                    continue
                took = br.true_taken if uncov_side == "true" else br.false_taken
                if took:
                    hitters.append(seed_path.name)
            smoke_log["candidates"].append({
                "file": file, "line": line, "uncovered_side": uncov_side,
                "n_hitters": len(hitters), "hitter_names": hitters[:5],
            })
            if hitters:
                reachable.append((file, line, ctx, desc, uncov_side, hitters))

    logger.info("RE2 reachability", extra={
        "n_reachable": len(reachable), "n_asymmetric": len(asymmetric)
    })

    if len(reachable) < M2_TARGET_COUNT:
        raise RuntimeError(
            f"only {len(reachable)} smoke-reachable RE2 gaps; need >= {M2_TARGET_COUNT}"
        )

    rng = random.Random(M2_RNG_SEED)
    sampled = rng.sample(reachable, k=M2_TARGET_COUNT)
    sampled_sorted = sorted(sampled, key=lambda r: (r[0], r[1]))
    shown = sampled_sorted[:M2_SHOWN_COUNT]
    held_back = sampled_sorted[M2_SHOWN_COUNT:]

    target_record = {
        "rng_seed": M2_RNG_SEED, "target_count": M2_TARGET_COUNT,
        "shown_count": M2_SHOWN_COUNT,
        "n_all_candidates": len(all_candidates),
        "n_asymmetric_candidates": len(asymmetric),
        "n_smoke_reachable": len(reachable),
        "shown": [{"file": r[0], "line": r[1], "code_context": r[2],
                   "condition_description": r[3], "uncovered_side": r[4],
                   "smoke_hitters": r[5][:5]} for r in shown],
        "held_back": [{"file": r[0], "line": r[1], "code_context": r[2],
                       "condition_description": r[3], "uncovered_side": r[4],
                       "smoke_hitters": r[5][:5]} for r in held_back],
    }

    if dry_run:
        return {"would_write": str(M2_TARGETS_PATH), "summary": target_record}

    UPSTREAM_UNION_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPSTREAM_UNION_PROFILE_PATH.write_text(union_profile.model_dump_json(indent=2))
    M2_TARGETS_PATH.write_text(json.dumps(target_record, indent=2))
    M2_SMOKE_LOG_PATH.write_text(json.dumps(smoke_log, indent=2))
    return {
        "n_all_candidates": len(all_candidates),
        "n_asymmetric_candidates": len(asymmetric),
        "n_smoke_reachable": len(reachable),
        "n_shown": len(shown), "n_held_back": len(held_back),
        "first_shown": [{"file": s["file"], "line": s["line"],
                         "uncovered_side": s["uncovered_side"]}
                        for s in target_record["shown"][:5]],
    }


def build_re2_smoke_split(work_dir: Path, n_random: int = 100) -> tuple[list[Path], list[Path]]:
    """Build RE2 smoke as two separate corpora.

    Returns (rand_paths, structured_paths):
      rand_paths       — n_random truly-random regex-format strings (random char sequences)
      structured_paths — hand-crafted regexes covering specific RE2 features
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    rand_paths: list[Path] = []
    structured_paths: list[Path] = []

    random_inputs = generate_random(
        target="re2", count=n_random, max_len=64, seed=M2_RNG_SEED, input_format="regex",
    )
    for inp in random_inputs:
        out = work_dir / f"rand_{inp.input_id}.bin"
        out.write_bytes(base64.b64decode(inp.content_b64))
        rand_paths.append(out)

    for i, regex in enumerate(HAND_CRAFTED_REGEXES):
        body = regex.encode("utf-8", errors="replace")[:62]
        payload = b"\x00\x00" + body
        out = work_dir / f"structured_{i:02d}.bin"
        out.write_bytes(payload)
        structured_paths.append(out)

    logger.info("RE2 v2 smoke corpus built",
                extra={"n_random": len(rand_paths), "n_structured": len(structured_paths)})
    return rand_paths, structured_paths


def freeze_targets_re2_v2(*, dry_run: bool = False, target_count: int | None = None) -> dict:
    """RE2 freeze with the same hard-branch filter as harfbuzz.

    Hard branch = hit by ≥1 structured regex seed AND not hit by any random-regex seed.
    This ensures the random seed baseline will score near 0 on M2, making the metric
    meaningful (same design as harfbuzz_ab).
    """
    union_profile = load_upstream_union()
    gaps = CoverageGapsReport.model_validate_json(RE2_GAPS_PATH.read_text())

    all_candidates = gaps.gap_branches
    asymmetric = [
        (g.file, g.line, g.code_context, g.condition_description,
         _uncovered_side(union_profile, g.file, g.line))
        for g in all_candidates
        if _uncovered_side(union_profile, g.file, g.line) is not None
    ]
    logger.info("RE2 v2 candidate filtering",
                extra={"n_all": len(all_candidates), "n_asymmetric": len(asymmetric)})

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        rand_paths, structured_paths = build_re2_smoke_split(td_path / "seeds", n_random=100)
        replay_dir = td_path / "replay"
        replay_dir.mkdir()

        rand_profiles = _run_smoke_replay(rand_paths, replay_dir, RE2_SEED_REPLAY, offset=0)
        structured_profiles = _run_smoke_replay(
            structured_paths, replay_dir, RE2_SEED_REPLAY, offset=len(rand_paths)
        )

        smoke_log: dict = {
            "n_rand_smoke": len(rand_profiles),
            "n_structured_smoke": len(structured_profiles),
            "candidates": [],
        }
        hard: list[tuple[str, int, str, str, str, list[str]]] = []

        for file, line, ctx, desc, uncov_side in asymmetric:
            rand_hitters, struct_hitters = [], []
            for seed_path, prof in rand_profiles:
                if prof is None or file not in prof.files:
                    continue
                br = prof.files[file].branches.get(f"{file}:{line}")
                if br is not None:
                    took = br.true_taken if uncov_side == "true" else br.false_taken
                    if took:
                        rand_hitters.append(seed_path.name)
            for seed_path, prof in structured_profiles:
                if prof is None or file not in prof.files:
                    continue
                br = prof.files[file].branches.get(f"{file}:{line}")
                if br is not None:
                    took = br.true_taken if uncov_side == "true" else br.false_taken
                    if took:
                        struct_hitters.append(seed_path.name)

            is_hard = len(struct_hitters) >= 1 and len(rand_hitters) == 0
            smoke_log["candidates"].append({
                "file": file, "line": line, "uncovered_side": uncov_side,
                "rand_hits": len(rand_hitters), "struct_hits": len(struct_hitters),
                "is_hard": is_hard,
            })
            if is_hard:
                hard.append((file, line, ctx, desc, uncov_side, struct_hitters))

    logger.info("RE2 v2 hard-branch classification",
                extra={"n_asymmetric": len(asymmetric), "n_hard": len(hard)})

    # Use all hard branches (RE2 has fewer than harfbuzz by design; that's a finding)
    n_use = target_count if target_count is not None else min(len(hard), M2_TARGET_COUNT)
    if len(hard) < 6:
        raise RuntimeError(
            f"only {len(hard)} hard RE2 branches found (need >= 6 for a meaningful split). "
            "Consider adding more structured regex seeds to HAND_CRAFTED_REGEXES."
        )
    n_use = min(n_use, len(hard))
    n_shown = max(1, n_use * 2 // 3)   # ~2/3 shown, ~1/3 held-back
    n_held = n_use - n_shown

    rng = random.Random(M2_RNG_SEED)
    sampled = rng.sample(hard, k=n_use)
    sampled_sorted = sorted(sampled, key=lambda r: (r[0], r[1]))
    shown = sampled_sorted[:n_shown]
    held_back = sampled_sorted[n_shown:]

    target_record = {
        "rng_seed": M2_RNG_SEED, "target_count": n_use,
        "shown_count": n_shown,
        "n_all_candidates": len(all_candidates),
        "n_asymmetric_candidates": len(asymmetric),
        "n_hard_candidates": len(hard),
        "shown": [{"file": r[0], "line": r[1], "code_context": r[2],
                   "condition_description": r[3], "uncovered_side": r[4],
                   "smoke_hitters": r[5][:5]} for r in shown],
        "held_back": [{"file": r[0], "line": r[1], "code_context": r[2],
                       "condition_description": r[3], "uncovered_side": r[4],
                       "smoke_hitters": r[5][:5]} for r in held_back],
    }

    if dry_run:
        return {"would_write": str(RE2_V2_M2_TARGETS_PATH), "summary": target_record}

    RE2_V2_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    RE2_V2_UPSTREAM_UNION_PROFILE_PATH.write_text(union_profile.model_dump_json(indent=2))
    RE2_V2_M2_TARGETS_PATH.write_text(json.dumps(target_record, indent=2))
    RE2_V2_M2_SMOKE_LOG_PATH.write_text(json.dumps(smoke_log, indent=2))
    return {
        "n_all_candidates": len(all_candidates),
        "n_asymmetric_candidates": len(asymmetric),
        "n_hard_candidates": len(hard),
        "n_shown": len(shown), "n_held_back": len(held_back),
        "first_shown": [{"file": s["file"], "line": s["line"],
                         "uncovered_side": s["uncovered_side"]}
                        for s in target_record["shown"][:5]],
    }


# Keep original name for backward compat
def freeze_targets(*, dry_run: bool = False) -> dict:
    return freeze_targets_re2(dry_run=dry_run)


def freeze_targets_harfbuzz(*, dry_run: bool = False) -> dict:
    HB_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)

        # Step 1: baseline profile
        baseline = build_harfbuzz_baseline(td_path / "baseline")

        # Step 2: enumerate all asymmetric gap branches from the baseline
        candidates = enumerate_harfbuzz_candidates(baseline)
        if not candidates:
            raise RuntimeError(
                "No asymmetric gap branches found in harfbuzz baseline coverage. "
                "Check that the coverage binary produces non-empty profiles."
            )

        # Step 3: smoke corpus — split random vs TTF
        rand_paths, ttf_paths = build_harfbuzz_smoke(td_path / "smoke_seeds", n_random=100)
        replay_dir = td_path / "replay"
        replay_dir.mkdir()

        rand_profiles = _run_smoke_replay(rand_paths, replay_dir, HB_SEED_REPLAY, offset=0)
        ttf_profiles = _run_smoke_replay(ttf_paths, replay_dir, HB_SEED_REPLAY, offset=len(rand_paths))

        # Step 4: classify each candidate as "hard" (ttf_hits ≥ 1 AND rand_hits == 0)
        smoke_log: dict = {
            "n_candidates": len(candidates),
            "n_rand_smoke": len(rand_profiles),
            "n_ttf_smoke": len(ttf_profiles),
            "candidates": [],
        }
        hard: list[tuple[str, int, str, str, str, list[str]]] = []

        for file, line, ctx, desc, uncov_side in candidates:
            rand_hitters = []
            for sp, prof in rand_profiles:
                if prof is None or file not in prof.files:
                    continue
                br = prof.files[file].branches.get(f"{file}:{line}")
                if br is None:
                    continue
                took = br.true_taken if uncov_side == "true" else br.false_taken
                if took:
                    rand_hitters.append(sp.name)

            ttf_hitters = []
            for sp, prof in ttf_profiles:
                if prof is None or file not in prof.files:
                    continue
                br = prof.files[file].branches.get(f"{file}:{line}")
                if br is None:
                    continue
                took = br.true_taken if uncov_side == "true" else br.false_taken
                if took:
                    ttf_hitters.append(sp.name)

            is_hard = len(ttf_hitters) >= 1 and len(rand_hitters) == 0
            smoke_log["candidates"].append({
                "file": file, "line": line, "uncovered_side": uncov_side,
                "rand_hits": len(rand_hitters), "ttf_hits": len(ttf_hitters),
                "is_hard": is_hard,
            })
            if is_hard:
                hard.append((file, line, ctx, desc, uncov_side, ttf_hitters))

        logger.info("harfbuzz hard-branch classification", extra={
            "n_candidates": len(candidates),
            "n_hard": len(hard),
        })

        # Fallback: if fewer than M2_TARGET_COUNT hard branches, include
        # any branch that TTF seeds can hit (even if random also hits some)
        if len(hard) < M2_TARGET_COUNT:
            logger.warning(
                "fewer hard branches than target count; falling back to all TTF-reachable",
                extra={"n_hard": len(hard), "target": M2_TARGET_COUNT},
            )
            ttf_reachable: list[tuple[str, int, str, str, str, list[str]]] = []
            for entry in smoke_log["candidates"]:
                if entry["ttf_hits"] >= 1:
                    # find the original candidate tuple
                    for cand in candidates:
                        if cand[0] == entry["file"] and cand[1] == entry["line"]:
                            ttf_hitters_names = [
                                sp.name for sp, prof in ttf_profiles
                                if prof and entry["file"] in prof.files
                                and prof.files[entry["file"]].branches.get(
                                    f"{entry['file']}:{entry['line']}"
                                ) is not None
                            ]
                            ttf_reachable.append((*cand, ttf_hitters_names))
                            break
            hard = ttf_reachable
            logger.info("fallback set size", extra={"n_ttf_reachable": len(hard)})

    if len(hard) < M2_TARGET_COUNT:
        raise RuntimeError(
            f"only {len(hard)} harfbuzz hard branches found; need >= {M2_TARGET_COUNT}. "
            "Consider increasing the smoke corpus or lowering M2_TARGET_COUNT."
        )

    rng = random.Random(M2_RNG_SEED)
    sampled = rng.sample(hard, k=M2_TARGET_COUNT)
    sampled_sorted = sorted(sampled, key=lambda r: (r[0], r[1]))
    shown = sampled_sorted[:M2_SHOWN_COUNT]
    held_back = sampled_sorted[M2_SHOWN_COUNT:]

    target_record = {
        "rng_seed": M2_RNG_SEED, "target_count": M2_TARGET_COUNT,
        "shown_count": M2_SHOWN_COUNT,
        "n_all_candidates": len(candidates),
        "n_hard_candidates": len(hard),
        "shown": [{"file": r[0], "line": r[1], "code_context": r[2],
                   "condition_description": r[3], "uncovered_side": r[4],
                   "smoke_hitters": r[5][:5]} for r in shown],
        "held_back": [{"file": r[0], "line": r[1], "code_context": r[2],
                       "condition_description": r[3], "uncovered_side": r[4],
                       "smoke_hitters": r[5][:5]} for r in held_back],
    }

    if dry_run:
        return {"would_write": str(HB_M2_TARGETS_PATH), "summary": target_record}

    HB_UPSTREAM_UNION_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    HB_UPSTREAM_UNION_PROFILE_PATH.write_text(baseline.model_dump_json(indent=2))
    HB_M2_TARGETS_PATH.write_text(json.dumps(target_record, indent=2))
    HB_M2_SMOKE_LOG_PATH.write_text(json.dumps(smoke_log, indent=2))
    logger.info("harfbuzz artifacts written", extra={
        "targets": str(HB_M2_TARGETS_PATH),
        "n_shown": len(shown), "n_held_back": len(held_back),
    })
    return {
        "n_all_candidates": len(candidates),
        "n_hard": len(hard),
        "n_shown": len(shown), "n_held_back": len(held_back),
        "first_shown": [{"file": s["file"], "line": s["line"],
                         "uncovered_side": s["uncovered_side"]}
                        for s in target_record["shown"][:5]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["re2", "re2_v2", "harfbuzz"], default="re2",
                        help="which target to freeze (default: re2). "
                             "re2_v2 applies the harfbuzz-style rand_hits==0 hard-branch filter.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the would-be sample without writing files")
    args = parser.parse_args()

    if args.target == "harfbuzz":
        summary = freeze_targets_harfbuzz(dry_run=args.dry_run)
    elif args.target == "re2_v2":
        summary = freeze_targets_re2_v2(dry_run=args.dry_run)
    else:
        summary = freeze_targets_re2(dry_run=args.dry_run)

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
