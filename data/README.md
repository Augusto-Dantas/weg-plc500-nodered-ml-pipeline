# WEG PLC500 Synthetic Telemetry Dataset

Reproducible 18-hour @ 2 Hz (`129,600` raw samples, `seed = 42`) industrial bench dataset for anomaly detection, severity classification, and safety-rule validation on the **WEG PLC500** supervisory pipeline.

## Files

- `raw/raw_telemetry_2hz.csv.gz` — Full 18h @ 2 Hz raw telemetry (`129,600` rows, gzip-compressed CSV, no Git LFS).
- `raw/raw_sample_500.csv` — First `500` rows (`0.0 s .. 249.5 s`) for quick inspection.
- `processed/window_features_60s.csv` — `3,688` sliding windows (`W = 60 s`, `stride = 15 s`, `K = 120` samples) with strict temporal `split` column.
- `processed/scenario_summary.json` — Split statistics, purge gap proof, scenario counts, and `UINT16` wrap-around audit.
- `data_dictionary.md` — Column-by-column schema and Modbus register mapping.

## Strict Temporal Split & Purge Gaps (Zero Leakage)

```text
[ train: 0..42120 s ] --(60 s purge)-- [ val: 42180..51900 s ] --(60 s purge)-- [ test: 51960..64800 s ]
```

| Split | Windows | Ratio | Raw Index Range | Purge Gap to Next Split |
|---|---:|---:|---|---|
| `train` | 2,404 | 65.18% | `0..84224` | `67.5 s` |
| `val` | 553 | 14.99% | `84360..103799` | `60.0 s` |
| `test` | 731 | 19.82% | `103920..129589` | — |

## Regeneration

```bash
/home/augusto_dantas/sla/.venv/bin/python -m plc500_ml.data_generator
```
