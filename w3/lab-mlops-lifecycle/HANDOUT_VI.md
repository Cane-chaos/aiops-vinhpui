# Lab — MLOps Lifecycle: Anomaly Detection Model từ Train đến Retrain

**Cá nhân.**

Bài lab này là công việc kỹ thuật (engineering). Bạn xây dựng một pipeline MLOps hoàn chỉnh — train model, đăng ký model, serve model, giám sát drift, trigger retrain, và swap phiên bản mới vào. Không có đáp án duy nhất đúng. Sản phẩm cần nộp là những gì một MLOps engineer bàn giao cho đội on-call để duy trì model trong production.

---

## 1. Kịch bản

Bạn vừa gia nhập đội Platform tại một công ty fintech. Họ đã deploy một model anomaly detection lên production 2 tháng trước — model phát hiện bất thường trong latency và error rate của payment gateway. Tại thời điểm deploy, model đạt precision 91% và recall 88% trên validation set. Tháng trước, đội on-call báo cáo rằng model đang bỏ sót nhiều incident thật và sinh ra nhiều false positive hơn trước.

Nguyên nhân gốc đã được xác định: **model decay**. Phân phối dữ liệu production đã lệch khỏi phân phối training — traffic tăng 35% sau một campaign, baseline latency tăng do thêm tích hợp bên thứ 3, và pattern error rate thay đổi sau khi rollout payment processor mới. Model v1 không còn phản ánh thực tế hiện tại.

CTO yêu cầu 2 thứ, đều bắt buộc:

1. **Xây dựng drift monitoring** — phát hiện khi phân phối dữ liệu production lệch khỏi phân phối training.
2. **Xây dựng retrain pipeline** — khi phát hiện drift, tự động hoặc bán tự động train model mới, đăng ký model, và swap vào production theo cơ chế blue-green rollout.

Cả hai, đồng thời. Không downtime. Không mất observability. Không rollback thủ công phức tạp.

> 3 thứ team sẽ reject:
> - Retrain pipeline không có approval gate — "tự động hoàn toàn không kiểm soát" không phải MLOps, đó là chaos.
> - Drift threshold hardcode không có justification. Threshold 0.05 hay 0.30 đều có thể đúng tùy context — bạn phải bảo vệ lựa chọn của mình.
> - Versioning chỉ có "latest" — không có rollback path là không chấp nhận được trong production.

---

## 2. Sơ đồ lifecycle

```
baseline.csv          drifted.csv
     │                     │
     ▼                     ▼
┌─────────────┐     ┌──────────────────┐
│  pipeline.py │     │ drift_detector.py │
│  Train v1   │     │ Evidently batch   │
│  IsoForest  │     │ drift score       │
└──────┬──────┘     └────────┬─────────┘
       │                     │ score > threshold?
       ▼                     ▼
┌─────────────┐     ┌──────────────────┐
│   MLflow    │◄────│   retrain.py     │
│  Registry   │     │ Orchestrator:    │
│  v1 "prod"  │     │ train → register │
└──────┬──────┘     │ → staging → swap │
       │             └──────────────────┘
       ▼
┌─────────────┐
│   serve.py  │  GET /predict
│   FastAPI   │  GET /health/active-version
│  port 8000  │
└─────────────┘
```

Luồng hoàn chỉnh:

1. `pipeline.py` — train model trên `baseline.csv`, log metrics vào MLflow, đăng ký artifact trong MLflow Registry với alias `production`
2. `serve.py` — FastAPI load model từ registry alias `production`, expose `/predict` và `/health/active-version`
3. `drift_detector.py` — nhận một batch dữ liệu production, so sánh với phân phối reference (baseline), trả về drift score và flag
4. `retrain.py` — orchestrator: poll drift_detector → nếu phát hiện drift → train model mới trên sliding window data → đăng ký v2 với alias `staging` → đợi approval signal → promote `staging` → `production` → serve.py reload
5. Blue-green swap: `/health/active-version` cho phép xác minh version trước khi cutover toàn bộ

---

## 3. Stack

| Component | Vai trò | Port |
|---|---|---|
| MLflow Tracking Server | Experiment log, artifact store, model registry | 5000 |
| PostgreSQL | MLflow backend store | 5432 |
| FastAPI (serve.py) | Model serving, blue-green endpoint | 8000 |
| Evidently (drift_detector.py) | Tính toán data drift (không cần container riêng) | — |
| Prometheus | Metrics scraping + time-series store | 9090 |
| Pushgateway | Batch job metrics ingestion (drift_detector, retrain) | 9091 |
| Grafana | Observability dashboard | 3000 |

Stack chạy qua Docker Compose (`configs/docker-compose.yml`). FastAPI và Evidently chạy trực tiếp trên host (`uv run python`).

Khởi động: `bash scripts/start_stack.sh`
Dừng: `bash scripts/stop_stack.sh`
Sinh dữ liệu drift: `bash scripts/generate_drift.sh`

### Observability dashboard

Grafana: http://localhost:3000 — anonymous Viewer access, không cần login.
Dashboard: **AIOps MLOps Lifecycle** (tự động provision khi stack khởi động).

Các panel và ý nghĩa:

| Panel | Mô tả |
|---|---|
| Drift Score Timeline | Chuỗi thời gian drift score (0–1) với đường ngưỡng đỏ — khi score vượt đường đỏ, retrain được trigger |
| Drift Status | Panel trạng thái: "No drift" (xanh) / "DRIFT" (đỏ) — snapshot trạng thái hiện tại |
| Precision & Recall per Model Version | Biểu đồ đa đường so sánh precision và recall giữa v1 (baseline) và v2+ (retrained) |
| F1 Score per Model Version | F1 theo thời gian, dễ phát hiện regression sau retrain |
| Active Model Version — Alias State | Bảng alias → version: production / staging / archived |
| Retrain Count | Tổng số retrain event được trigger |
| Auto-Rollback Count | Tổng số auto-rollback event (đỏ nếu > 0) |
| Production / Staging Version Number | Số version hiện tại của alias production và staging |
| Serve Request Rate | Throughput của endpoint /predict (req/s) |
| Predict Latency (p99 / p50) | Latency percentile 99 và 50 của /predict |
| Serve Active Version | Model version đang được serve.py load |
| Lifecycle Event Rate | Biểu đồ thanh tần suất retrain_triggered và auto_rollback theo cửa sổ 5 phút |

Dashboard là công cụ debug và quan sát — không phải acceptance criterion. Pipeline phải thỏa mãn tất cả 6 acceptance criteria trước; dashboard giúp bạn hiểu chuyện gì đang xảy ra bên trong.

**Prerequisite bổ sung:** `prometheus_client` phải được cài trong Python environment chạy `serve.py`, `drift_detector.py`, và `retrain.py`:
```
uv pip install prometheus_client
```
Nếu pushgateway không chạy khi bạn gọi `drift_detector.py` hoặc `retrain.py`, các lệnh metrics sẽ in warning và bị bỏ qua — pipeline sẽ không crash.

---

## 4. Dữ liệu

### `data/baseline.csv` — 30 ngày hoạt động bình thường
- 4320 dòng (một dòng mỗi 10 phút)
- Cột: `timestamp`, `latency_p99` (ms), `error_rate` (%), `rps` (requests/sec)
- Phân phối: latency ~ N(120, 15), error_rate ~ N(0.8, 0.3), rps ~ N(450, 80)

### `data/drifted.csv` — 7 ngày sau campaign + thay đổi tích hợp
- 1008 dòng (một dòng mỗi 10 phút)
- Cùng schema nhưng phân phối đã dịch chuyển: latency mean +30% (~156ms), error_rate gấp đôi (~1.6%), rps tăng 40% (~630)
- Đây là dữ liệu bạn đưa vào drift_detector để trigger retrain

Sinh lại dữ liệu: `uv run python data/generate_data.py`

---

## 5. Yêu cầu bài nộp

### P1. `pipeline.py`

Train IsolationForest trên `baseline.csv`, log experiment vào MLflow:

- Log parameters: `contamination`, `n_estimators`, `random_state`
- Log metrics: `train_anomaly_rate`, `feature_count`
- Log artifact: model serialize bằng `mlflow.sklearn.log_model`
- Đăng ký model trong MLflow Registry dưới tên `anomaly-detector`, gán alias `production`

### P2. `serve.py`

Ứng dụng FastAPI:

- Startup: load model từ MLflow Registry alias `models:/anomaly-detector@production`
- `POST /predict` — nhận JSON `{features: [...]}`, trả `{prediction: int, score: float, version: str}`
- `GET /health/active-version` — trả version đang được serve
- `POST /reload` — reload model từ registry (dùng sau khi swap)

### P3. `drift_detector.py`

Wrapper cho Evidently DataDriftPreset:

- `detect_drift(reference_df, current_df, threshold)` → `DriftResult(score, is_drift, report_path)`
- Lưu Evidently HTML report vào `outputs/drift_reports/`
- Log drift score vào MLflow dưới dạng metric (để visualize trend theo thời gian)

### P4. `retrain.py`

Script orchestrator:

- Load `drifted.csv` dùng rolling window (mặc định: 7 ngày gần nhất)
- Gọi `drift_detector.py` để so sánh với baseline
- Nếu phát hiện drift: train model mới qua `pipeline.py` trên data window mới, đăng ký v2 với alias `staging`
- In approval prompt: "Drift detected. Model v2 registered as staging. Promote to production? [y/N]"
- Nếu được chấp thuận: promote `staging` → `production`, gọi `POST /reload` trên serve.py
- Log toàn bộ decision trail vào MLflow run (parameters + metrics + tags)

### P5. `DESIGN.md`

Bạn phải trả lời **4 sub-checkpoint** sau, mỗi câu ít nhất 3-4 câu văn và có số liệu cụ thể:

1. **Drift threshold** — Bạn chọn giá trị nào (ví dụ 0.15)? Tại sao? Đã test trên drifted.csv chưa? Điều gì xảy ra nếu threshold quá thấp?
2. **Drift type** — Đây là data drift, concept drift, hay performance drift? `drift_detector.py` của bạn detect loại nào? Tại sao loại này phù hợp với bài toán anomaly payment?
3. **Retrain trigger configuration** — Manual hay automatic? Nếu manual: ai approve? Timeout approve là bao lâu? Nếu dùng cadence (ví dụ retrain hàng tuần bất kể drift), hãy bảo vệ lý do.
4. **Versioning + rollback** — Dùng aliases hay version numbers? Rollback trông như thế nào khi v2 underperform? Ai có quyền trigger rollback?

### P6. `SUBMIT.md`

Phản ánh ngắn — 5 câu hỏi, mỗi câu ít nhất 3-4 câu văn, tham chiếu code và số liệu:

1. Drift threshold bạn chọn là bao nhiêu và tại sao? Đã validate threshold đó trên dữ liệu thực chưa?
2. Điều gì xảy ra nếu model v2 sau retrain lại tệ hơn v1 trong production? Pipeline xử lý trường hợp này thế nào?
3. Sự khác biệt giữa data drift và concept drift là gì? Evidently detect loại nào trong lab này?
4. Tại sao blue-green swap quan trọng hơn so với việc thay thế file model trực tiếp?
5. Nếu phải tự động hóa approval gate (không cần con người), bạn sẽ dùng metric nào và threshold nào?

---

## 6. Stress scenarios — acceptance phases 4-6

Ba kịch bản sau kiểm tra khả năng chịu tải (resilience) của pipeline trong các điều kiện thực tế phức tạp hơn. Mỗi kịch bản cần một thay đổi nhỏ và có tiêu chí test cụ thể.

### Stress 1 — Bẫy phân loại sai drift type

**Bối cảnh:** `drifted.csv` chứa cả data drift (feature distribution dịch chuyển) và concept drift (25% label bị đảo — cùng input features nhưng mối quan hệ với `anomaly_label` đã thay đổi). Chỉ dùng `DataDriftPreset` sẽ phát hiện data drift nhưng **hoàn toàn bỏ sót** concept drift vì feature values trông bình thường.

**Acceptance criterion 4:** Chạy `drift_detector.py --check-mode combined --labeled-current data/drifted.csv --model-uri models:/anomaly-detector@production`. Output phải in cả `Drift score` (data) và `Perf precision` (performance). Chạy với `--check-mode data` sẽ không hiện precision drop — đây là bằng chứng hai cơ chế phát hiện loại drift khác nhau. `DESIGN.md` phải giải thích tại sao combined mode là cần thiết với ít nhất 1 ví dụ số cụ thể.

### Stress 2 — Lựa chọn dữ liệu retrain

**Bối cảnh:** Nếu `retrain.py` train v2 chỉ trên drift window (7 ngày), v2 sẽ overfit phân phối mới và perform tệ hơn v1 trên `data/holdout.csv` (500 dòng từ pattern cũ). Chiến lược sliding window (baseline + drift window) giữ được performance trên cả hai chế độ.

**Acceptance criterion 5:** Chạy `retrain.py --reference data/baseline.csv --current data/drifted.csv --holdout data/holdout.csv`. Output phải in dòng `Holdout validation — v2 precision: X.XXXX  recall: X.XXXX`. Giá trị precision phải ≥ v1 precision đo trên cùng holdout. `DESIGN.md` phải so sánh chiến lược sliding window với ít nhất 1 chiến lược thay thế.

### Stress 3 — Auto-rollback khi post-deploy degradation

**Bối cảnh:** Sau khi v2 được promote lên `@production`, pipeline tiếp tục giám sát v2 trên `data/post_deploy_eval.csv` (200 dòng, có label rõ ràng). Nếu precision v2 giảm dưới 0.65 trong 24 polling cycles, v2 bị hạ xuống `@archived` và v1 tự động được khôi phục lên `@production`.

**Acceptance criterion 6:** Chạy pipeline end-to-end với `--post-deploy-eval data/post_deploy_eval.csv`. Sau promotion, terminal phải in các dòng `post_deploy_monitor Cycle XX/24`. Nếu rollback xảy ra, dòng cuối phải là `Rollback complete. v1 restored to @production. v2 → @archived`. File `outputs/audit_log.jsonl` phải chứa event `auto_rollback_v2_to_v1` với các fields `demoted_version`, `restored_version`, `trigger_precision`, `cycle`.

---

## 7. Rubric (30 điểm)

| Tiêu chí | Điểm | Mô tả |
|---|---|---|
| Train + Register (pipeline.py) | 5 | Model train thành công, MLflow log đầy đủ params/metrics/artifact, đăng ký với alias production |
| Serve quality (serve.py) | 5 | /predict hoạt động, load đúng version, /health/active-version trả version string, /reload reload thành công |
| Drift detection (drift_detector.py) | 5 | Evidently DataDriftPreset chạy, score được tính, flag được raise khi vượt threshold, HTML report được lưu |
| Retrain pipeline (retrain.py) | 5 | Orchestrator chạy end-to-end: detect → train v2 → register staging → prompt approval → promote → reload |
| Defense trong DESIGN.md | 5 | Cả 4 sub-checkpoint gốc được trả lời với số liệu cụ thể, lập luận nhất quán với code |
| Lifecycle Robustness | 5 | **1**: pipeline chạy nhưng không xử lý stress case nào; **2**: xử lý 1/3 stress cases với acceptance criterion đạt; **3**: xử lý 2/3 stress cases; **4**: xử lý cả 3, DESIGN.md trả lời cả 3 sub-checkpoint mới (4/5/6); **5**: cả 3 stress cases pass, DESIGN.md có số liệu thực từ lần chạy, audit log hợp lệ |

Tier: ≥27 xuất sắc, ≥22 đạt, ≥15 cần chỉnh sửa.

---

## 8. Nộp bài

Một thư mục duy nhất chứa:

```
tên-của-bạn/
├── pipeline.py
├── serve.py
├── drift_detector.py
├── retrain.py
├── DESIGN.md
├── SUBMIT.md
└── README.md    (1 đoạn: cách chạy pipeline từ đầu đến cuối)
```

---

## 9. Ngoài phạm vi

- Bạn **không cần** deploy lên cloud. Toàn bộ lab chạy local với Docker Compose + localhost.
- Bạn **không cần** viết bộ test đầy đủ — validate bằng cách chạy pipeline end-to-end và quan sát output.
- Bạn **không cần** GPU hoặc model lớn. IsolationForest trên 4320 dòng chạy trong < 1 giây.
- Bạn **không cần** implement authentication cho FastAPI endpoint.

---

## 10. Tham khảo

### Khái niệm

- **MLflow Tracking**: `mlflow.start_run()`, `mlflow.log_param()`, `mlflow.log_metric()`, `mlflow.sklearn.log_model()` — MLflow docs: [mlflow.org/docs](https://mlflow.org/docs/latest/)
- **MLflow Registry**: Model aliases (`production`, `staging`), `MlflowClient.set_registered_model_alias()`, `mlflow.pyfunc.load_model("models:/name@alias")` — xem MLflow Model Registry guide
- **Evidently DataDriftPreset**: `from evidently.report import Report`, `from evidently.metric_preset import DataDriftPreset` — [docs.evidentlyai.com](https://docs.evidentlyai.com)
- **FastAPI lifespan**: pattern `@asynccontextmanager` để load model một lần khi startup — [fastapi.tiangolo.com/advanced/events](https://fastapi.tiangolo.com/advanced/events/)

### Lý thuyết Drift

- **Data drift** — phân phối input features thay đổi: P(X) thay đổi, P(Y|X) không đổi. Phát hiện qua statistical tests trên feature values.
- **Concept drift** — mối quan hệ input-output thay đổi: P(Y|X) thay đổi. Phát hiện bằng cách so sánh model performance theo thời gian.
- **Performance drift** — proxy cho concept drift khi không có ground truth: anomaly rate, phân phối prediction confidence.
- Jensen-Shannon divergence và Wasserstein distance: 2 metric phổ biến Evidently dùng để đo khoảng cách phân phối.

### Gợi ý thiết kế

- **Threshold không phải con số thần kỳ.** Chạy drift_detector trên baseline data (split 70/30) trước để lấy baseline drift score, và dùng đó làm upper bound cho "no drift". Threshold = baseline score × 1.5 là heuristic hợp lý.
- **Approval gate không cần phức tạp.** Một prompt `[y/N]` trong terminal là đủ cho lab này. Điều quan trọng là gate *tồn tại* trong code — không phải promotion tự động vô điều kiện.
- **MLflow aliases tốt hơn version numbers** cho production routing vì bạn có thể swap alias mà không cần thay đổi code trong serve.py. `models:/anomaly-detector@production` luôn trỏ đến version đúng.
- **Blue-green = 2 endpoints, không phải 2 servers.** Trong lab này, `/predict` serve production model, `/predict-shadow` (tùy chọn) serve staging model. Swap = đổi alias trong registry + reload.

---
