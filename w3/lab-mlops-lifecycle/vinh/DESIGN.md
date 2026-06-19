# DESIGN.md — MLOps Lifecycle: Anomaly Detection Pipeline

## Tổng quan

Pipeline phát hiện drift trong metrics payment gateway (latency_p99, error_rate, rps), trigger retrain model IsolationForest, và swap phiên bản mới qua MLflow Registry alias không cần downtime. Stack: MLflow + PostgreSQL + FastAPI + Evidently + Prometheus + Grafana.

---

## Sub-checkpoint 1: Drift Threshold

**Giá trị đã chọn: 0.15** (15% features bị drift theo Evidently DataDriftPreset).

**Cách chọn:** Chạy drift_detector trên chính baseline.csv, chia 70/30 (3 tháng đầu làm reference, 1 tháng cuối làm current). Kết quả drift score = 0.04 — đây là "noise floor" của hệ thống khi không có drift thực sự (intraday variation, seasonal fluctuation). Chọn threshold = 0.15, tức 3.75× noise floor — đủ xa để không bị false positive từ biến động bình thường, nhưng đủ thấp để bắt drift sớm. Với drifted.csv, drift score đo được là 0.67 (cả 3 features latency_p99, error_rate, rps đều drift), vượt threshold rõ ràng.

**Rủi ro nếu threshold quá thấp (ví dụ 0.05):** false positive — retrain bị trigger sau mỗi sáng/tối khi traffic tự nhiên thay đổi. Tốn compute, gây alert fatigue, và team mất niềm tin vào hệ thống monitoring.

**Rủi ro nếu threshold quá cao (ví dụ 0.50):** false negative — bỏ sót drift thực, model tiếp tục serve với distribution không còn phù hợp, precision/recall giảm âm thầm trong nhiều tuần trước khi bị phát hiện qua complaint.

---

## Sub-checkpoint 2: Loại Drift

**Loại được detect: Data drift** — P(X) thay đổi, tức phân phối input features (latency_p99, error_rate, rps) đã dịch chuyển so với training data.

**Evidently DataDriftPreset hoạt động như thế nào:** Chạy statistical test trên từng feature riêng lẻ. Với numerical features, mặc định dùng Wasserstein distance. Khi `share_of_drifted_columns` vượt threshold → flag `is_drift = True`. Ví dụ: latency_p99 tăng từ mean 120ms → 156ms (+30%), error_rate tăng gấp đôi từ 0.8% → 1.6%, rps tăng 40% từ 450 → 630 — cả 3 features đều drift.

**Tại sao data drift phù hợp với bài toán này:** Payment gateway anomaly detection cần biết khi nào "bình thường mới" (new normal) khác với "bình thường cũ". Sau campaign, latency baseline tăng lên 156ms — model v1 train với baseline 120ms sẽ coi 156ms là anomaly dù thực ra là normal operation. Detect data drift cho phép retrain model với distribution mới trước khi precision giảm đáng kể.

**Concept drift (P(Y|X) thay đổi) không được detect** bởi DataDriftPreset vì không có production ground-truth labels. Để bổ sung, pipeline dùng `--check-mode combined` với `check_performance_drift()` đánh giá model trên labeled holdout — xem Sub-checkpoint 5.

---

## Sub-checkpoint 3: Retrain Trigger Configuration

**Trigger type: Manual approval gate** — semi-automatic.

**Cadence:** Không có schedule cố định. Drift check được gọi khi có batch production data mới (tích hợp vào daily batch job). Promotion từ staging → production **luôn yêu cầu human approval** qua prompt `[y/N]`.

**Lý do chọn manual:** Model anomaly detection trong payment system ảnh hưởng trực tiếp đến on-call SLA. Một model tệ hơn được promote tự động có thể gây false negatives trên real incident (miss alert), hoặc false positive storm làm on-call team không còn tin vào alert. Approval gate đảm bảo ML engineer review anomaly_rate của v2 vs v1 trước khi cutover — bước này chỉ mất 5 phút nhưng ngăn được thảm họa production.

**Approval timeout:** Không implement trong lab. Trong production, recommend 24h timeout — nếu không có approval trong 24h, staging version bị archive tự động và drift check reset. Tránh trạng thái "staging model treo mãi không ai review".

**Nếu tự động hoàn toàn:** Dùng anomaly_rate delta giữa v2 và v1 trên cùng validation window. Auto-promote nếu `|v2_anomaly_rate - v1_anomaly_rate| < 0.05` và `0.01 < v2_anomaly_rate < 0.10`. Ngưỡng 5% delta là conservative cho payment domain — sai lệch 5% trên 1000 req/phút = 50 missed anomalies/phút.

---

## Sub-checkpoint 4: Versioning và Rollback

**Chiến lược versioning: MLflow Registry aliases**, không phụ thuộc vào version numbers trong code.

- `production` alias → version đang serve (serve.py load `models:/anomaly-detector@production`)
- `staging` alias → version candidate sau retrain
- `archived` alias → version bị rollback
- Version numbers (1, 2, 3…) là immutable audit trail — không bao giờ xóa

**Tại sao alias tốt hơn hardcode version number:** `mlflow.pyfunc.load_model("models:/anomaly-detector@production")` không thay đổi khi swap alias. Nếu hardcode version number trong serve.py, phải redeploy container mỗi lần retrain — tốn thời gian, rủi ro downtime.

**Rollback path (< 30 giây, không cần redeploy):**
1. Phát hiện v2 underperform (precision drop, alert storm)
2. `MlflowClient.set_registered_model_alias("anomaly-detector", "archived", v2_version)` — hạ v2 xuống
3. `MlflowClient.set_registered_model_alias("anomaly-detector", "production", v1_version)` — khôi phục v1
4. `POST /reload` trên serve.py — load lại v1 trong runtime
5. Toàn bộ < 5 giây, zero downtime, zero container restart

**Ai có quyền rollback:** ML engineer on-call (có MLflow admin access). Trong production, rollback nên được wrap thành Runbook command với audit log và mandatory 2-person rule cho payment system.

**Retention policy:** Giữ tất cả registered versions vô thời hạn. Model IsolationForest < 1MB, storage không phải vấn đề. Cần cho audit và khả năng rollback về bất kỳ version nào.

---

## Kiến trúc component

```
baseline.csv (reference)
     │
     ├──► pipeline.py ──► MLflow Run ──► Registry v1 @production
     │
drifted.csv (current window)
     │
     ├──► drift_detector.py
     │         │ score=0.67 > threshold=0.15 → is_drift=True
     │         ▼
     └──► retrain.py
               │
               ├── Sliding window (baseline+drift) → train v2
               ├── Holdout validation → v2 precision ≥ v1
               ├── MLflow Run → Registry v2 @staging
               ├── [HUMAN APPROVAL y/N]
               ├── set alias: staging→production, v1→archived
               ├── POST /reload → serve.py nạp v2
               └── post_deploy_monitor (24 cycles)
                         │ precision < 0.65?
                         └── auto-rollback: v2→archived, v1→production
```

---

## Sub-checkpoint 5: Tại sao cần combined mode (Stress 1)

Chỉ dùng `DataDriftPreset` là **chưa đủ**. Data drift phát hiện khi P(X) thay đổi — phân phối input features dịch chuyển. Nhưng trong payment gateway, có thể xảy ra **concept drift**: P(Y|X) thay đổi trong khi P(X) ổn định.

**Ví dụ cụ thể với số liệu:** Sau khi payment processor mới rollout, drifted.csv có 25% labels bị flip — cùng input features nhưng anomaly_label ngược lại. Evidently DataDriftPreset **sẽ không phát hiện** điều này vì feature distribution (latency_p99, error_rate, rps) không thay đổi đáng kể trong scenario này. Kết quả khi chỉ chạy `--check-mode data`: drift_score = 0.04 → `is_drift = False` → retrain không được trigger. Nhưng model v1 đạt precision = 0.91 trên baseline mà chỉ đạt precision = 0.62 trên holdout với flipped labels — degradation 32% bị bỏ sót hoàn toàn.

`--check-mode combined` chạy song song 2 cơ chế: (1) Evidently DataDriftPreset trên feature distribution, và (2) `check_performance_drift()` đánh giá precision/recall của model hiện tại trên labeled data. Nếu một trong hai flag (`is_drift = True` hoặc `perf_is_degraded = True`), retrain được trigger. Threshold performance mặc định: precision ≥ 0.70.

---

## Sub-checkpoint 6: Data selection — sliding window vs alternatives (Stress 2)

Khi retrain chỉ trên drift window (7 ngày gần nhất), model v2 overfit vào phân phối mới: nó học rằng latency 156ms là "bình thường" nhưng quên rằng hệ thống vẫn phải xử lý batch jobs chạy theo pattern cũ (latency 120ms). Thực nghiệm: train chỉ trên drift window → v2 precision trên `holdout.csv` (old pattern) giảm ~18% so với v1.

**Sliding window strategy** (baseline + drift window concat) cho kết quả tốt hơn vì model thấy cả 2 regime. Với baseline.csv (4320 rows) + drifted.csv (1008 rows) = 5328 rows training, IsolationForest không bị dominated bởi distribution mới. Acceptance criterion: v2 precision và recall trên holdout.csv phải ≥ v1 precision/recall trên cùng tập.

**So sánh với các alternatives:**

| Strategy | Ưu điểm | Nhược điểm |
|---|---|---|
| Pure drift window (7 ngày) | Đơn giản, phản ánh nhanh distribution mới | Overfit mới, quên cũ, precision holdout giảm ~18% |
| **Sliding window (baseline + drift)** ← chọn | Generalise cả 2 regime, an toàn | Cần concat, training lâu hơn chút |
| Weighted sampling (oversample baseline) | Tốt khi drift window rất nhỏ | Phức tạp hơn, cần tune weight |
| Full historical concat (tất cả data) | An toàn nhất | Tốn compute, data cũ có thể mislead |

Sliding window là trade-off tốt nhất: đơn giản, safe, generalise đủ tốt cho lab này.

---

## Sub-checkpoint 7: Auto-rollback — threshold và policy (Stress 3)

Sau khi v2 được promote lên `@production`, `post_deploy_monitor` chạy 24 polling cycles đánh giá precision trên `post_deploy_eval.csv` (200 rows có nhãn rõ ràng: 60% clear-normal, 40% clear-anomaly).

**Ngưỡng auto-rollback: precision < 0.65.** Tại sao 0.65?
- Baseline v1 đạt precision = 0.91 trên validation set ban đầu
- Threshold 0.65 nằm ở điểm "model rõ ràng đang sai lệch nghiêm trọng" — thấp hơn baseline 29%
- Tính toán: với 80 anomaly rows (40% trong 200 rows), nếu model miss 30 → precision ≈ 0.62; nếu model hoạt động bình thường → precision ≥ 0.85. Ngưỡng 0.65 không trigger false rollback do sampling noise
- Thấp hơn performance threshold 0.70 (drift detection) vì post-deploy precision được đánh giá trên clear-labeled data — ít noise hơn

**Rollback flow (< 5 giây):**
1. `client.set_registered_model_alias(MODEL_NAME, "archived", v2_version)`
2. `client.set_registered_model_alias(MODEL_NAME, "production", v1_version)`
3. `POST /reload` → serve.py nạp lại v1
4. Ghi event `auto_rollback_v2_to_v1` vào `outputs/audit_log.jsonl` với fields: `demoted_version`, `restored_version`, `trigger_precision`, `cycle`

---

## Trade-offs đã chấp nhận

| Quyết định | Được | Mất |
|---|---|---|
| Manual approval gate | An toàn, human oversight, phù hợp payment domain | Latency retrain loop (hours, không phải minutes) |
| Sliding window training | Generalise tốt, không overfit distribution mới | Training data lớn hơn, lâu hơn (vẫn < 1s với IsoForest) |
| IsolationForest | Train < 1s, explainable, no GPU, phù hợp unsupervised | Không capture temporal patterns, mỗi row độc lập |
| combined drift mode | Bắt cả data drift và concept drift | Cần labeled data để check performance |
| Local artifact store | Không cần S3 setup, chạy hoàn toàn offline | Không scale multi-node |
