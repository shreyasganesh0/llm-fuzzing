# UTCF top-level orchestration
# See docs/plan_v3.md §Makefile Dependency Graph for the full map.

PYTHON ?= python3
VENV   ?= .venv
PY     := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip

TARGETS_TIER1 ?= re2 harfbuzz libxml2
TARGETS_TIER2 ?= sqlite3 openssl libjpeg_turbo lcms proj
TARGETS_TIER3 ?= libpng freetype2 zlib
TARGETS_ALL   ?= $(TARGETS_TIER1) $(TARGETS_TIER2)
TARGETS_LOO   ?= $(TARGETS_TIER1) $(TARGETS_TIER2)

MODELS            ?= gpt-4o-2024-08-06
SYNTHESIS_MODELS  ?= gpt-4o-2024-08-06 claude-sonnet-4-6
EXP2_MODELS       ?= gpt-4o-2024-08-06 claude-sonnet-4-6

PHASE3_CONFIGS    ?= empty unittest_seeds fuzzbench_seeds llm_seeds random_seeds combined_seeds
EXP2_CONFIGS      ?= source_only_llm_seeds source_only_combined

.PHONY: help venv install pin-versions \
        dataset contamination predict context-ablation sensitivity \
        synthesize random-baseline transfer tier3 \
        fuzz dedup failure-analysis stats \
        finetune finetune-data \
        source-only-predict source-only-synthesize source-only-fuzz compare-experiments \
        figures audit all \
        sanity-exp1-b sanity-exp2-b sanity sanity-fixture \
        test lint clean

help:
	@echo "UTCF — Unit Test-Conditioned LLM-Guided Fuzzing"
	@echo ""
	@echo "Setup:"
	@echo "  make venv              create Python virtualenv at $(VENV)"
	@echo "  make install           install requirements.txt inside venv"
	@echo "  make pin-versions      validate pinned_versions.yaml"
	@echo ""
	@echo "Phase 1 — Dataset:"
	@echo "  make dataset           fetch, build, extract, coverage for \$$TARGETS_ALL"
	@echo "  make contamination     run contamination probes for dataset × models"
	@echo "  make audit             provenance + contamination audit"
	@echo ""
	@echo "Phase 2 — Prediction:"
	@echo "  make predict           coverage prediction across models × few-shot"
	@echo "  make context-ablation  RQ3: 3 context sizes × Tier 1 × GPT-4o 5-shot"
	@echo "  make sensitivity       RQ prompt-wording: 3 variants × Tier 1 × GPT-4o 5-shot"
	@echo ""
	@echo "Phase 3 — Synthesis + Campaigns:"
	@echo "  make synthesize        T=0.7 3-sample LLM input synthesis → seed corpora"
	@echo "  make random-baseline   matched-count random baseline seeds"
	@echo "  make fuzz              20-trial × 23h libFuzzer campaigns per config"
	@echo "  make dedup             stack-hash + coverage crash dedup per campaign"
	@echo "  make failure-analysis  TV6 corpus-pollution / seed-survival"
	@echo "  make stats             pairwise Mann-Whitney + Â₁₂ + Friedman-Nemenyi"
	@echo ""
	@echo "Phase Transfer — LOO:"
	@echo "  make transfer          LOO cross-target prediction + synthesis (Tier 1+2)"
	@echo "  make tier3             held-out evaluation on Tier 3 (libpng / freetype / zlib)"
	@echo ""
	@echo "Phase 4 — Fine-tuning (skeleton; GPU-free up to training step):"
	@echo "  make finetune-data     build Alpaca JSONL from Phase 1 dataset"
	@echo "  make finetune          LoRA training driver (dry-run unless GPU present)"
	@echo ""
	@echo "Experiment 2 — Source-only:"
	@echo "  make source-only-predict     source-only hard-branch prediction"
	@echo "  make source-only-synthesize  source-only input synthesis"
	@echo "  make source-only-fuzz        source-only libFuzzer campaigns"
	@echo "  make compare-experiments     Exp 1 vs Exp 2 Mann-Whitney + Â₁₂"
	@echo ""
	@echo "Reporting:"
	@echo "  make figures           coverage curves + threat tables + Config A-I table"
	@echo "  make all               audit → predict → synthesize → fuzz → stats → figures"
	@echo ""
	@echo "Sanity (mini exp1_b / exp2_b against LiteLLM proxy, ~\$$1 budget):"
	@echo "  make sanity-fixture    build RE2 mini fixture only (no LLM calls)"
	@echo "  make sanity-exp1-b     exp1_b: test-conditioned prediction + synthesis"
	@echo "  make sanity-exp2-b     exp2_b: source-only prediction + synthesis"
	@echo "  make sanity            both (default model from sanity/config.py)"
	@echo ""
	@echo "QA:"
	@echo "  make test              full pytest suite (no network / LLM / LLVM)"
	@echo "  make lint              ruff check ."

venv:
	@if [ ! -d "$(VENV)" ]; then $(PYTHON) -m venv $(VENV); fi

install: venv
	$(PIP) install -r requirements.txt

pin-versions:
	$(PY) -c "import yaml,sys; d=yaml.safe_load(open('pinned_versions.yaml')); \
	          t=d['targets']; \
	          unresolved=[k for k,v in t.items() if '<FILL>' in str(v)]; \
	          print('Targets with <FILL> placeholders:', unresolved); \
	          print('Resolved targets:', [k for k in t if k not in unresolved])"

# --- Phase 1 ---
dataset: pin-versions
	@for tgt in $(TARGETS_ALL); do \
	  echo "==> dataset: $$tgt"; \
	  ./dataset/scripts/fetch_target.sh dataset/targets/$$tgt.yaml || exit 1; \
	  ./dataset/scripts/build_instrumented.sh dataset/targets/$$tgt.yaml || exit 1; \
	  $(PY) dataset/scripts/build_dataset.py --target $$tgt || exit 1; \
	done

contamination: dataset
	@for tgt in $(TARGETS_ALL); do \
	  for m in $(MODELS); do \
	    echo "==> contamination: $$tgt × $$m"; \
	    $(PY) dataset/scripts/contamination_probe.py --target $$tgt --model $$m || exit 1; \
	  done; \
	done

# --- Phase 2 ---
predict: dataset contamination
	@for tgt in $(TARGETS_ALL); do \
	  for m in $(MODELS); do \
	    for k in 0 1 3 5 10; do \
	      echo "==> predict: $$tgt × $$m × $$k-shot"; \
	      $(PY) prediction/scripts/run_prediction.py --target $$tgt --model $$m --few-shot $$k || exit 1; \
	    done; \
	  done; \
	done
	@for tgt in $(TARGETS_ALL); do \
	  $(PY) prediction/scripts/evaluate_prediction.py --target $$tgt || exit 1; \
	done

context-ablation: dataset
	@for tgt in $(TARGETS_TIER1); do \
	  for ctx in function_only file multi_file; do \
	    echo "==> context-ablation: $$tgt × $$ctx"; \
	    $(PY) prediction/scripts/run_prediction.py --target $$tgt --model gpt-4o-2024-08-06 --few-shot 5 --context-size $$ctx || exit 1; \
	  done; \
	done

sensitivity: predict
	@for tgt in $(TARGETS_TIER1); do \
	  echo "==> sensitivity: $$tgt"; \
	  $(PY) prediction/scripts/prompt_sensitivity.py --target $$tgt --model gpt-4o-2024-08-06 --few-shot 5 || exit 1; \
	done

audit: dataset contamination
	$(PY) -c "print('Provenance + contamination audit — see dataset/scripts/build_dataset.py and contamination_probe.py reports')"

# --- Phase 3: gap-filling synthesis + libFuzzer campaigns ---
synthesize: predict
	@for tgt in $(TARGETS_ALL); do \
	  for m in $(SYNTHESIS_MODELS); do \
	    echo "==> synthesize: $$tgt × $$m"; \
	    $(PY) synthesis/scripts/generate_inputs.py --target $$tgt --model $$m || exit 1; \
	  done; \
	done

random-baseline: synthesize
	@for tgt in $(TARGETS_ALL); do \
	  echo "==> random-baseline: $$tgt"; \
	  count=$$(find synthesis/results/seeds/$$tgt -maxdepth 3 -type f 2>/dev/null | wc -l); \
	  [ $$count -gt 0 ] || count=100; \
	  $(PY) synthesis/scripts/generate_random_inputs.py --target $$tgt --count $$count || exit 1; \
	done

# 20-trial × 23h campaigns. Requires instrumented binaries at
# dataset/data/<target>/binaries/<target>_fuzzer. Skip with DRY_RUN=1.
DRY_RUN ?= 0
FUZZ_FLAGS := $(if $(filter 1,$(DRY_RUN)),--dry-run,)
fuzz: synthesize random-baseline
	@for tgt in $(TARGETS_ALL); do \
	  bin=dataset/data/$$tgt/binaries/$${tgt}_fuzzer; \
	  for cfg in $(PHASE3_CONFIGS); do \
	    echo "==> fuzz: $$tgt × $$cfg"; \
	    $(PY) synthesis/scripts/run_fuzzing.py \
	      --config synthesis/campaign_configs/$$cfg.yaml \
	      --target $$tgt --binary $$bin $(FUZZ_FLAGS) || exit 1; \
	  done; \
	done

dedup: fuzz
	@for tgt in $(TARGETS_ALL); do \
	  for cfg in $(PHASE3_CONFIGS); do \
	    work=synthesis/results/campaigns/$$tgt/$$cfg; \
	    [ -d "$$work" ] || continue; \
	    echo "==> dedup: $$tgt × $$cfg"; \
	    $(PY) synthesis/scripts/dedup_crashes.py --target $$tgt --config-name $$cfg --campaign-work-dir $$work || exit 1; \
	  done; \
	done

failure-analysis: fuzz
	@for tgt in $(TARGETS_ALL); do \
	  for cfg in llm_seeds combined_seeds; do \
	    echo "==> failure-analysis: $$tgt × $$cfg"; \
	    $(PY) synthesis/scripts/failure_analysis.py --target $$tgt --config-name $$cfg || exit 1; \
	  done; \
	done

stats: fuzz
	@for tgt in $(TARGETS_ALL); do \
	  echo "==> stats: $$tgt"; \
	  $(PY) synthesis/scripts/compare_baselines.py --target $$tgt || exit 1; \
	done

# --- Phase Transfer: leave-one-out + Tier 3 held-out ---
TRANSFER_FLAGS := $(if $(filter 1,$(DRY_RUN)),--dry-run,)
transfer: predict
	@for tgt in $(TARGETS_LOO); do \
	  for m in $(SYNTHESIS_MODELS); do \
	    echo "==> transfer predict: $$tgt × $$m"; \
	    $(PY) transfer/scripts/run_transfer_prediction.py --held-out-target $$tgt --model $$m $(TRANSFER_FLAGS) || exit 1; \
	    echo "==> transfer synthesize: $$tgt × $$m"; \
	    $(PY) transfer/scripts/run_transfer_synthesis.py --held-out-target $$tgt --model $$m $(TRANSFER_FLAGS) || exit 1; \
	  done; \
	done
	$(PY) transfer/scripts/evaluate_transfer.py

tier3:
	@for tgt in $(TARGETS_TIER3); do \
	  for m in $(SYNTHESIS_MODELS); do \
	    echo "==> tier3: $$tgt × $$m"; \
	    $(PY) transfer/scripts/run_tier3_evaluation.py --target $$tgt --model $$m || exit 1; \
	  done; \
	done

# --- Phase 4: Fine-tuning (skeleton; requires GPU for actual training) ---
FINETUNE_FLAGS := $(if $(filter 1,$(DRY_RUN)),--dry-run,)
finetune-data: dataset
	$(PY) finetuning/scripts/prepare_finetune_data.py --targets $(TARGETS_ALL)

finetune: finetune-data
	$(PY) finetuning/scripts/finetune.py \
	  --config finetuning/configs/lora_8b.yaml \
	  --data-dir finetuning/results/finetune_data \
	  --out-dir finetuning/results/adapters/llama-3.1-8b-lora $(FINETUNE_FLAGS)

# --- Experiment 2: Source-only ablation ---
EXP2_FLAGS := $(if $(filter 1,$(DRY_RUN)),--dry-run,)
source-only-predict: dataset
	@for tgt in $(TARGETS_ALL); do \
	  for m in $(EXP2_MODELS); do \
	    echo "==> source-only-predict: $$tgt × $$m"; \
	    $(PY) synthesis/scripts/run_source_prediction.py --target $$tgt --model $$m $(EXP2_FLAGS) || exit 1; \
	  done; \
	done

source-only-synthesize: source-only-predict
	@for tgt in $(TARGETS_ALL); do \
	  for m in $(EXP2_MODELS); do \
	    echo "==> source-only-synthesize: $$tgt × $$m"; \
	    $(PY) synthesis/scripts/generate_source_inputs.py --target $$tgt --model $$m $(EXP2_FLAGS) || exit 1; \
	  done; \
	done

source-only-fuzz: source-only-synthesize
	@for tgt in $(TARGETS_ALL); do \
	  bin=dataset/data/$$tgt/binaries/$${tgt}_fuzzer; \
	  for cfg in $(EXP2_CONFIGS); do \
	    work=synthesis/results/campaigns/$$tgt/$$cfg; \
	    echo "==> source-only-fuzz: $$tgt × $$cfg"; \
	    $(PY) synthesis/scripts/run_source_fuzzing.py \
	      --config $$cfg --target $$tgt --binary $$bin --work-dir $$work $(EXP2_FLAGS) || exit 1; \
	  done; \
	done

compare-experiments: source-only-fuzz fuzz
	$(PY) synthesis/scripts/compare_experiments.py --targets $(TARGETS_ALL)

# --- Reporting ---
figures: stats compare-experiments transfer finetune
	@for tgt in $(TARGETS_ALL); do \
	  $(PY) analysis/scripts/plot_coverage.py --target $$tgt || exit 1; \
	done
	$(PY) analysis/scripts/threat_analysis.py
	$(PY) finetuning/scripts/compare_all.py

all: audit predict synthesize fuzz dedup failure-analysis stats transfer tier3 \
     source-only-fuzz compare-experiments finetune figures
	@echo "==> pipeline complete"

# --- Sanity (mini exp1_b / exp2_b) ---
# Uses LiteLLM proxy; set UTCF_LITELLM_URL + secrets/llm_key before running.
SANITY_FLAGS ?=
sanity-fixture:
	$(PY) -m sanity.build_fixture

sanity-exp1-b:
	$(PY) -m sanity.run_sanity --experiment exp1_b $(SANITY_FLAGS)

sanity-exp2-b:
	$(PY) -m sanity.run_sanity --experiment exp2_b $(SANITY_FLAGS)

sanity:
	$(PY) -m sanity.run_sanity --experiment both $(SANITY_FLAGS)

# --- QA ---
test:
	$(PY) -m pytest dataset/tests/ prediction/tests/ \
	  synthesis/tests/ transfer/tests/ finetuning/tests/ \
	  core/tests/ -q

lint:
	$(VENV)/bin/ruff check .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .cache .llm_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
