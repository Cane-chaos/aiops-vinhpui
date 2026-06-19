# Lab — MLOps Lifecycle

Đọc `HANDOUT.md` trước. README này chỉ giới thiệu files + hướng dẫn nhanh.

## Danh sách file trong pack

```
data-pack/
├── HANDOUT.md                          ← đề bài lab (đọc trước)
├── README.md                           ← file này
├── configs/
│   ├── docker-compose.yml              MLflow + PostgreSQL + Prometheus + Pushgateway + Grafana
│   ├── mlflow-config.txt               tài liệu tham khảo: MLflow env vars + thông tin port (KHÔNG được load bởi compose)
│   ├── prometheus.yml                  cấu hình scrape: pushgateway + serve.py /metrics
│   └── grafana/
│       ├── provisioning/datasources/   tự động load Prometheus
│       ├── provisioning/dashboards/    tự động load JSON bên dưới
│       └── dashboards/mlops-lifecycle.json   dashboard chính
├── scripts/
│   ├── start_stack.sh                  khởi động MLflow + Postgres + observability
│   ├── stop_stack.sh                   dừng stack
│   └── generate_drift.sh               wrapper cho data/generate_data.py
├── data/
│   ├── generate_data.py                generator deterministic (seed=42), xuất ra tất cả 4 CSV
│   ├── baseline.csv                    30 ngày bình thường (4320 dòng)
│   ├── drifted.csv                     7 ngày có data drift + concept drift (1008 dòng, 25% label bị đảo)
│   ├── holdout.csv                     500 dòng dữ liệu old-pattern để kiểm tra v2
│   └── post_deploy_eval.csv            200 dòng ground truth cho post-deploy monitoring
└── sample-solution/                    chỉ xem SAU KHI bạn hoàn thành bài
    ├── pipeline.py                     train IsolationForest + MLflow register @production
    ├── serve.py                        FastAPI /predict + /health/active-version + /metrics
    ├── drift_detector.py               Evidently DataDriftPreset + performance drift (--check-mode data|performance|combined)
    ├── retrain.py                      orchestrator: drift→train v2→staging→approval→promote→post-deploy monitor 24 cycles→auto-rollback
    ├── metrics_util.py                 Pushgateway helpers cho dashboard
    ├── DESIGN.md                       ví dụ defense thiết kế (tiếng Việt)
    └── SUBMIT.md                       ví dụ phản ánh (tiếng Việt)
```

## Hướng dẫn nhanh

```bash
# 1) Khởi động stack
bash scripts/start_stack.sh
# Đợi ~30 giây lần chạy đầu (Postgres init + MLflow cài psycopg2-binary bên trong container)

# 2) Xác minh
curl -s http://localhost:5000/health             # MLflow
curl -s http://localhost:9090/-/healthy          # Prometheus
curl -s http://localhost:9091/-/healthy          # Pushgateway
curl -s http://localhost:3000/api/health         # Grafana

# 3) Mở dashboard (anonymous viewer đã bật)
#    http://localhost:3000 → dashboard "AIOps MLOps Lifecycle"

# 4) Sinh datasets (deterministic, seed=42)
uv run python data/generate_data.py

# 5) Train v1 + đăng ký
export MLFLOW_TRACKING_URI=http://localhost:5000
uv run python sample-solution/pipeline.py --data data/baseline.csv

# 6) Serve
uv run python sample-solution/serve.py
# Ở terminal khác:
curl -s http://localhost:8000/health/active-version

# 7) Chạy drift detection
uv run python sample-solution/drift_detector.py \
  --reference data/baseline.csv \
  --current data/drifted.csv \
  --check-mode combined \
  --model-uri models:/anomaly-detector@production \
  --labeled-current data/drifted.csv
```

## Bảng port các service

| Component | Host port | Ghi chú |
|---|---|---|
| MLflow | 5000 | tracking + registry + serve-artifacts proxy |
| PostgreSQL | 5432 | MLflow backend store |
| Prometheus | 9090 | scrape pushgateway + serve.py |
| Pushgateway | 9091 | nhận metrics từ drift_detector + retrain |
| Grafana | 3000 | anonymous viewer |
| serve.py (model server của bạn) | 8000 | FastAPI /predict + /metrics |

## Python dependencies

Pin MLflow client khớp với server (2.13.2) — phiên bản client mới hơn sẽ gọi các endpoint mà server không expose:

```bash
uv pip install 'mlflow==2.13.2' 'evidently==0.4.40' scikit-learn pandas numpy fastapi uvicorn prometheus_client requests
```

Nếu environment yêu cầu Python 3.11 (một số MLflow 2.13.2 wheels không build được trên 3.14), dùng:

```bash
uv run --python 3.11 --no-project --with 'mlflow==2.13.2' --with 'evidently==0.4.40' --with scikit-learn --with pandas --with numpy python <script>
```

## Dừng

```bash
bash scripts/stop_stack.sh
```

Volumes (`postgres_data`, `mlflow_artifacts`) được giữ lại mặc định — xóa sạch bằng `docker compose -f configs/docker-compose.yml down -v` để reset hoàn toàn.

## Ghi chú

- Tất cả lệnh Python dùng `uv run python` (không phải `python`/`python3` đơn thuần).
- `sample-solution/` đi kèm — chỉ xem sau khi đã tự làm.
- Stack chạy hoàn toàn trên `localhost`, không cần tài khoản cloud.
- `mlflow-config.txt` chỉ là tài liệu tham khảo; file compose có env vars inline.
