# README — vinh/

Pipeline MLOps hoàn chỉnh để train, serve, giám sát và tự động retrain mô hình phát hiện bất thường (IsolationForest) cho payment gateway metrics.

## Cách chạy từ đầu đến cuối

```bash
# 0. Cài dependencies
uv pip install 'mlflow==2.13.2' 'evidently==0.4.40' scikit-learn pandas numpy \
               fastapi uvicorn prometheus_client requests

# 1. Khởi động stack (MLflow, Postgres, Prometheus, Pushgateway, Grafana)
bash data-pack/scripts/start_stack.sh
# Đợi ~30s, sau đó verify:
# curl http://localhost:5000/health && curl http://localhost:3000/api/health

# 2. Generate datasets (seed=42, deterministic)
uv run python data-pack/data/generate_data.py

# 3. Train model v1 + register với alias 'production'
export MLFLOW_TRACKING_URI=http://localhost:5000
uv run python data-pack/vinh/pipeline.py --data data-pack/data/baseline.csv

# 4. Serve model (trong terminal riêng)
export MLFLOW_TRACKING_URI=http://localhost:5000
uv run python data-pack/vinh/serve.py
# Verify: curl http://localhost:8000/health/active-version

# 5. Drift detection (combined mode — data drift + concept drift)
uv run python data-pack/vinh/drift_detector.py \
    --reference       data-pack/data/baseline.csv \
    --current         data-pack/data/drifted.csv \
    --check-mode      combined \
    --labeled-current data-pack/data/drifted.csv \
    --model-uri       models:/anomaly-detector@production \
    --log-mlflow

# 6. Retrain + auto-approve + holdout validation + post-deploy monitor
uv run python data-pack/vinh/retrain.py \
    --reference        data-pack/data/baseline.csv \
    --current          data-pack/data/drifted.csv \
    --holdout          data-pack/data/holdout.csv \
    --post-deploy-eval data-pack/data/post_deploy_eval.csv \
    --auto-approve

# 7. Dừng stack
bash data-pack/scripts/stop_stack.sh
```

## Dashboards

- MLflow UI: http://localhost:5000
- Grafana: http://localhost:3000 → "AIOps MLOps Lifecycle"
- Audit log: `outputs/audit_log.jsonl`
- Drift reports: `outputs/drift_reports/*.html`
