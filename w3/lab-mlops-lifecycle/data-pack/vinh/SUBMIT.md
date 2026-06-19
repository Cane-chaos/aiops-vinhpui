# SUBMIT.md — Reflection: MLOps Lifecycle Lab

## Câu 1: Drift threshold bạn chọn là bao nhiêu và tại sao? Đã validate chưa?

Threshold là **0.15** (15% features drifted theo Evidently). Cách chọn: chạy drift_detector trên baseline.csv với split 70/30 — phần 30% cuối làm "current" dù thực ra không có drift — noise floor đo được 0.04. Threshold 0.15 = 3.75× noise floor, đủ xa để tránh false positive từ intraday traffic variation (sáng/tối khác nhau ~8%), nhưng đủ thấp để bắt drift sớm khi chỉ 1-2 features bắt đầu dịch chuyển. Validation với drifted.csv: drift score = 0.67 (3/3 features drifted: latency +30%, error_rate ×2, rps +40%) — vượt threshold rõ ràng 4.5×. Nếu chọn 0.05, drift check sẽ fire mỗi ngày do seasonal fluctuation. Nếu chọn 0.50, sẽ bỏ sót drift giai đoạn đầu khi chỉ 1 feature bắt đầu lệch — có thể mất nhiều tuần trước khi bị phát hiện.

---

## Câu 2: Điều gì xảy ra nếu model v2 sau retrain lại tệ hơn v1?

Pipeline có 2 lớp bảo vệ. **Lớp 1 — manual approval gate:** ML engineer xem anomaly_rate của v2 (in ra khi retrain xong) và so sánh với v1 trước khi promote. Nếu v2 anomaly_rate bất thường (quá cao = flag quá nhiều normal, quá thấp = bỏ sót anomaly), engineer từ chối, v2 ở lại alias `staging` không ảnh hưởng production. **Lớp 2 — auto-rollback:** Nếu v2 đã được promote mà precision trên post_deploy_eval.csv giảm dưới 0.65 trong 24 polling cycles, hệ thống tự động `set_registered_model_alias("production", v1_version)` + `POST /reload`. Rollback hoàn tất < 5 giây, không cần redeploy container, v2 bị đánh dấu `@archived`. Cải tiến trong production: implement shadow mode — serve.py gọi song song v1 và v2 trong 24h trước khi cutover, so sánh anomaly_rate delta trên real traffic.

---

## Câu 3: Sự khác biệt giữa data drift và concept drift? Evidently detect loại nào?

**Data drift**: phân phối input thay đổi — P(X) thay đổi, nhưng mối quan hệ X→Y giữ nguyên. Ví dụ trong lab: latency baseline tăng từ 120ms lên 156ms sau khi thêm 3rd-party integration. Model vẫn đúng về logic nhưng threshold anomaly không còn phù hợp — cùng feature values 156ms trước đây là anomaly, nay là normal. **Concept drift**: mối quan hệ input-output thay đổi — P(Y|X) thay đổi. Ví dụ: sau khi payment processor mới rollout, cùng latency 200ms trước đây là incident thực, nay là bình thường vì processor xử lý nhanh hơn — model hoàn toàn sai dù feature distribution không đổi nhiều. **Evidently DataDriftPreset detect data drift** — chạy Wasserstein distance trên từng numerical feature. Concept drift không được detect trực tiếp vì cần production labels. Trong pipeline này, concept drift được bổ sung bằng `check_performance_drift()` so sánh precision/recall model hiện tại trên labeled holdout (`--check-mode combined`).

---

## Câu 4: Tại sao blue-green swap quan trọng hơn replace file trực tiếp?

Replace file trực tiếp (overwrite model artifact trên disk) tạo ít nhất 3 vấn đề nghiêm trọng. (1) **Race condition**: serve.py đang xử lý in-flight request với model cũ, đồng thời file bị ghi đè → corrupted read → crash hoặc wrong prediction không có cách phát hiện. (2) **Không có rollback**: version cũ đã bị xóa hoặc overwrite — nếu v2 tệ hơn, không có cách quay lại v1 nhanh chóng. (3) **Không audit trail**: không biết thời điểm chính xác swap, ai swap, version nào đang chạy. Blue-green qua MLflow alias giải quyết cả 3: alias swap là atomic operation trong registry — serve.py chỉ load model mới khi nhận `POST /reload`, tất cả in-flight request trước đó hoàn thành với v1. Nếu v2 có vấn đề, swap alias về v1 + reload = rollback ngay lập tức < 5 giây. Audit trail đầy đủ trong audit_log.jsonl và MLflow run history.

---

## Câu 5: Nếu automate approval gate, dùng metric gì và threshold nào?

Dùng **anomaly_rate delta** giữa v2 và v1 trên cùng một validation holdout window (20% cuối của current window, không dùng cho training). Điều kiện auto-promote khi TẤT CẢ 3 điều kiện thỏa:

- `abs(v2_anomaly_rate - v1_anomaly_rate) < 0.05` — v2 không thay đổi behavior quá nhiều
- `v2_anomaly_rate < 0.10` — không bị degenerate (flag toàn bộ data là anomaly)
- `v2_anomaly_rate > 0.01` — không quá conservative (không phát hiện gì)

Ngưỡng 5% delta là conservative cho payment domain: sai lệch 5% trên 1000 req/phút = 50 missed anomalies/phút, mỗi anomaly có thể là incident thực. Ngoài điều kiện trên, cần thêm: holdout precision v2 ≥ holdout precision v1 (đã implement trong `--holdout` flag). Nếu cả điều kiện thỏa, auto-promote sau 1h shadow mode. Nếu không, đẩy alert cho ML engineer review trong 4h — không nên để hệ thống tự promote hoàn toàn không có human-in-the-loop cho payment domain.
