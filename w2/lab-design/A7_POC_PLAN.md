# A7 — Kế hoạch POC 3 Ngày

## Thành phần rủi ro nhất: Grafana Loki (Log Backend)

---

## 1. Tại sao Loki là thành phần rủi ro nhất?

| Lý do | Chi tiết |
|---|---|
| **Cost driver lớn nhất** | Log pipeline chiếm $15,700/tháng (37.4% tổng chi phí). Nếu Loki không thay thế được Splunk + Datadog Logs → savings mất $14,945/tháng, dự án thất bại |
| **RCA dependency** | 17/29 incidents (59%) cần log analysis cho root cause analysis. Nếu Loki query không đáp ứng on-call workflow → MTTR tăng thay vì giảm |
| **Architecture shift lớn nhất** | Splunk = full-text index search. Loki = index-free (chỉ label index). Đây là paradigm shift — on-call cần thay đổi cách query hoàn toàn |
| **Pain point #1 liên quan trực tiếp** | "Log search >25s khi query >7 ngày" — Loki có thể tốt hơn HOẶC tệ hơn Splunk tùy query pattern |
| **Team risk** | Team quen SPL (Splunk Processing Language). LogQL khác biệt lớn. Learning curve = productivity dip |
| **Compliance dependency** | Security/audit team dùng Splunk cho compliance reports. Nếu Loki không thể tạo tương đương → compliance violation |

> **Kết luận**: Mọi quyết định khác (VictoriaMetrics cho metrics, Tempo cho traces, Grafana cho UI) có risk thấp hơn vì: (1) VictoriaMetrics PromQL-compatible = gần zero learning curve, (2) Tempo trace query ít frequent hơn log query, (3) Grafana đã quen (team đang dùng Grafana Cloud). **Loki là unknown lớn nhất — cần validate trước khi commit.**

---

## 2. Kế hoạch POC 3 Ngày

### Ngày 1: Setup + Data Ingest

**Mục tiêu**: Deploy Loki + ingest real log data, verify basic functionality.

| Giờ | Task |
|---|---|
| 09:00-10:00 | Deploy Loki (microservices mode) trên staging cluster via Helm. 3 components: distributor + ingester + querier. S3 bucket cho chunks |
| 10:00-11:00 | Deploy OTel Collector với filelog receiver → Loki exporter. Target: 2 services (`recommender-svc`, `notification-svc`) |
| 11:00-12:00 | Verify data flowing: Grafana Explore → LogQL query → see logs. Debug nếu cần |
| 13:00-14:00 | Configure structured logging output (JSON format) cho 2 services. Test label extraction: `service`, `level`, `pod`, `namespace`, `trace_id` |
| 14:00-15:00 | **Dual-ingest**: Gửi cùng dữ liệu vào cả Loki VÀ Splunk (staging). Verify log count match |
| 15:00-17:00 | **Volume test**: Replay 24h log traffic vào Loki. Measure ingest rate, storage used, resource consumption |

**Output ngày 1**:
- ☐ Loki running, receiving logs
- ☐ Log count match ±5% giữa Loki vs Splunk
- ☐ Resource usage documented (CPU, RAM, disk)
- ☐ Structured log labels working

### Ngày 2: Query Performance Benchmark

**Mục tiêu**: So sánh query performance Loki vs Splunk trên cùng dữ liệu, cùng use cases.

| Giờ | Task |
|---|---|
| 09:00-10:00 | Ingest thêm 7 ngày historical log vào Loki (replay from Splunk export hoặc generate synthetic) |
| 10:00-12:00 | **Benchmark 10 query patterns** từ on-call thực tế: |
| | Q1: `{service="payment-svc", level="ERROR"}` — label filter only (target: <1s) |
| | Q2: `{service="payment-svc"} |= "ConnectionPool: timeout"` — label + grep (target: <5s) |
| | Q3: `{level="ERROR"} | json | message =~ "pool.*exhaust"` — regex trên structured field (target: <5s) |
| | Q4: `rate({service="checkout-svc", level="ERROR"}[5m])` — log rate query (target: <3s) |
| | Q5: `{service="payment-svc"} |= "timeout"` trên 7 ngày window — wide scan (target: <30s) |
| | Q6: Full-text grep trên tất cả services, 1 ngày: `|= "OutOfMemoryError"` (target: <10s) |
| | Q7: Full-text grep trên tất cả services, 7 ngày (worst case) (target: <60s) |
| | Q8: Top-10 error patterns: `{level="ERROR"} | pattern` (target: <10s) |
| | Q9: Log → Trace correlation: click log line → jump to trace via `trace_id` label (target: <2s) |
| | Q10: Count logs per service per hour (metric from logs) (target: <5s) |
| 13:00-14:00 | Run same 10 queries trên Splunk (staging). Record latency |
| 14:00-15:00 | Build comparison table: Loki latency vs Splunk latency per query |
| 15:00-17:00 | **On-call simulation**: 2 on-call engineers triage simulated incident chỉ dùng Grafana + Loki. Measure time-to-RCA. Record feedback |

**Output ngày 2**:
- ☐ Benchmark table: 10 queries × Loki latency vs Splunk latency
- ☐ On-call simulation MTTR result
- ☐ On-call qualitative feedback

### Ngày 3: Retention Tiering + Archive + Decision

**Mục tiêu**: Validate tiered retention, archive restore, compliance queries. Make go/no-go decision.

| Giờ | Task |
|---|---|
| 09:00-10:00 | Configure Loki retention tiers: hot=7d (EBS), warm=30d (S3 Standard), cold=90d (S3-IA) |
| 10:00-11:00 | Trigger compaction: verify chunks migrate từ EBS → S3 theo schedule |
| 11:00-12:00 | **Cold archive test**: Query log từ S3-IA tier. Measure restore latency |
| 13:00-14:00 | **Compliance query test**: Run 3 compliance report queries on-call/security team thường dùng trên Splunk |
| 14:00-15:00 | **Cost measurement**: Tính actual storage cost cho 7-ngày data. Extrapolate tới 30-ngày |
| 15:00-16:00 | **Go/No-go review** với team: present benchmark results, on-call feedback, cost comparison |
| 16:00-17:00 | Document findings. Update risk register nếu cần |

**Output ngày 3**:
- ☐ Tiered retention working (verified)
- ☐ Cold archive restore latency documented
- ☐ Compliance query results (pass/fail)
- ☐ Cost extrapolation vs A3 model
- ☐ **GO/NO-GO decision documented**

---

## 3. Dataset / Traffic dùng để Test

| Data source | Volume | Method |
|---|---|---|
| Live log traffic (2 low-risk services) | ~5 GB/day | OTel Collector dual-write từ production |
| Historical log replay (7 ngày) | ~35 GB | Export từ Splunk via API (capped 100 GB/day per contract) → re-ingest vào Loki |
| Synthetic incident logs | ~100 MB | Script generate log patterns matching 3 historical incidents (INC-2025-11-08, INC-2025-12-12, INC-2026-05-10) |

---

## 4. Success Criteria

| Criteria | Threshold | Weight |
|---|---|---|
| Label-filtered queries (Q1-Q4) latency | **<5s** cho 7-day window | Must-have |
| Full-text grep (Q5-Q7) latency | **<60s** cho 7-day window | Must-have |
| Log count parity vs Splunk | **±5%** | Must-have |
| On-call MTTR in simulation | **≤ baseline 42 phút** | Must-have |
| Tiered retention functional | EBS → S3 migration works | Must-have |
| Cold archive restore | **<5 phút** for targeted query | Should-have |
| Cost extrapolation | **≤ $800/tháng** (aligned with A3 model) | Should-have |
| On-call qualitative feedback | **≥ 3/5** satisfaction score | Should-have |

---

## 5. Failure Criteria (khi nào POC FAIL)

| Criteria | Threshold | Implication |
|---|---|---|
| Label-filtered queries >10s | Loki ingester/querier under-provisioned hoặc schema vấn đề | Có thể fix bằng scale up |
| Full-text grep >120s cho 7d | Fundamental Loki limitation cho unstructured log | **Xem xét alternative** (ClickHouse, OpenSearch) |
| Log count mismatch >10% | OTel Collector pipeline dropping logs | Debug pipeline, không phải Loki issue |
| On-call MTTR > 50 phút (>baseline) | LogQL learning curve quá cao hoặc missing queries | Thêm training time, hoặc **reconsider decision** |
| Tiered retention không work | Loki compactor issue | Debug, có thể fix |
| On-call feedback <2/5 | Fundamental workflow mismatch | **Serious reconsideration needed** |

---

## 6. Fallback nếu POC Fail

| Scenario | Fallback | Cost Impact | Timeline |
|---|---|---|---|
| Loki query too slow (Q7 >120s) | Thay Loki bằng **ClickHouse** cho log hot tier. Loki vẫn dùng cho warm/cold archive (S3) | +$500-1,000/tháng (ClickHouse cluster) | Thêm 2-3 tuần setup |
| Loki fundamentally unsuitable | Chuyển sang **Grafana Cloud Logs** (SaaS managed Loki). Tốn hơn self-hosted nhưng rẻ hơn Splunk | +$2,000-4,000/tháng | 1 tuần migration |
| On-call rejects LogQL entirely | Giữ **Splunk Cloud** cho hot log (giảm retention 30d → 7d = giảm ~50% cost). Dùng Loki chỉ cho warm/cold archive | ~$7,000/tháng Splunk (giảm từ $13,900) | Negotiate Splunk contract |
| POC inconclusive (mixed results) | Extend POC thêm 3 ngày với larger dataset và more on-call engineers | $0 | +3 ngày |
| Worst case: full rollback | Giữ nguyên Splunk + Datadog Logs. Focus cost reduction ở metrics/APM only (vẫn save ~$19,400/tháng) | Maintain $15,700/tháng cho logs | Quay lại baseline |
