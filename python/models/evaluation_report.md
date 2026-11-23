# Model Evaluation Report — WEG PLC500 ML Pipeline

- **Model Version**: `1.0.0`
- **Trained At (UTC)**: `2026-11-23T14:18:25.357669Z`
- **Random Seed**: `42`
- **Feature Count**: `10` (`MODEL_FEATURE_COLUMNS`, zero train/serve skew)

## 1. Temporal Split & Leakage Audit

| Check | Result | Value |
|---|---|---|
| Forbidden Columns in `X` | PASS | `0` |
| `train -> val` Purge Gap | PASS | `67.5 s` (`>= 60.0 s`) |
| `val -> test` Purge Gap | PASS | `60.0 s` (`>= 60.0 s`) |
| Overlapping Raw Samples | PASS | `0` |
| Normal Train Windows (IsolationForest Fit) | PASS | `1701` (`severity == 0` only) |

## 2. Unsupervised Anomaly Detector (`StandardScaler + IsolationForest`)

| Split | ROC-AUC | PR-AUC | F1 | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| `val` | 0.8959 | 0.8291 | 0.7399 | 0.9182 | 0.6196 |
| `test` | 0.8993 | 0.8069 | 0.7299 | 0.8699 | 0.6287 |

## 3. Severity Classifier Comparison (`val` Selection -> `test` Evaluation)

- **Selected Winner**: `HistGradientBoostingClassifier`

| Model | Val Accuracy | Val Macro-F1 | Test Accuracy | Test Macro-F1 | Test Weighted-F1 | Test ROC-AUC (OvR) |
|---|---:|---:|---:|---:|---:|---:|
| `HistGradientBoostingClassifier` | 1.0000 | 1.0000 | 0.9973 | 0.9956 | 0.9973 | 0.9984 |
| `RandomForestClassifier` | 1.0000 | 1.0000 | 0.9986 | 0.9988 | 0.9986 | 1.0000 |

## 4. Winner Per-Class Test Metrics (`HistGradientBoostingClassifier`)

| Class | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| `NORMAL` | 0.9981 | 1.0000 | 0.9991 | 529 |
| `ATENCAO` | 1.0000 | 0.9818 | 0.9908 | 55 |
| `CRITICO` | 0.9924 | 0.9924 | 0.9924 | 132 |
| `EMERGENCIA` | 1.0000 | 1.0000 | 1.0000 | 15 |

## 5. Winner 4x4 Test Confusion Matrix (Rows=True, Cols=Pred)

| True \ Pred | NORMAL | ATENCAO | CRITICO | EMERGENCIA |
|---|---:|---:|---:|---:|
| `NORMAL` | 529 | 0 | 0 | 0 |
| `ATENCAO` | 0 | 54 | 1 | 0 |
| `CRITICO` | 1 | 0 | 131 | 0 |
| `EMERGENCIA` | 0 | 0 | 0 | 15 |

## 6. Top Permutation Feature Importances (Validation Split)

| Feature | Importance Mean | Std |
|---|---:|---:|
| `veto_emergencia` | 0.2698 | 0.0144 |
| `abs_zscore_presenca` | 0.2431 | 0.0089 |
| `duty_nivel` | 0.2163 | 0.0070 |
| `duty_sobretemp` | 0.0869 | 0.0046 |
| `veto_watchdog` | 0.0332 | 0.0031 |
| `taxa_presenca_min` | 0.0000 | 0.0000 |
| `zscore_presenca` | 0.0000 | 0.0000 |
| `chattering_index` | 0.0000 | 0.0000 |
| `veto_sobretemp` | 0.0000 | 0.0000 |
| `veto_nivel_sustentado` | 0.0000 | 0.0000 |
