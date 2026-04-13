# Phase 2 — LLM Coverage Prediction

Given a held-out upstream unit test + source context, ask the LLM to predict
which functions/branches the test will cover. Metrics: function- and branch-
level P/R/F1, coverage MAE, Spearman rank correlation.

## Pipeline

```bash
python prediction/scripts/run_prediction.py \
  --target re2 --model gpt-4o-2024-08-06 --few-shot 5

python prediction/scripts/evaluate_prediction.py --target re2
python prediction/scripts/prompt_sensitivity.py --target re2 \
  --model gpt-4o-2024-08-06 --few-shot 5
```

## Determinism

- temperature = 0.0, top_p = 1.0 for prediction
- held-out split seed = 42 (plan §2.2)
- few-shot selection: stratified by coverage decile, seed 42

## Outputs

- `prediction/results/raw/<target>/<model>/shot<N>/<test_id>.json`
- `prediction/results/log.jsonl` (one line per API call)
- `prediction/results/metrics.json`
- `prediction/results/prompt_sensitivity_report.json`
