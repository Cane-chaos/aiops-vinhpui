# A5 — Kế hoạch Migration 8 Tuần

## Tổng quan Timeline

```
Week 1: Baseline + Inventory + Cost Attribution
Week 2: Deploy OTel Collector + OSS backends ở Staging
Week 3: Dual-write cho 2 low-risk services (recommender-svc, notification-svc)
Week 4: Build Grafana dashboards, alert parity audit, SLO dashboards
Week 5: Migrate log hot path + retention tiering cho 4 medium-risk services
Week 6: Expand tới critical services (payment-svc, checkout-svc, edge-lb) + incident replay test
Week 7: Cost optimization, sampling tuning, cardinality control, archive query validation
Week 8: Cutover production, vendor contract reduction, final rollback window
```

> **Nguyên tắc xuyên suốt**: Không bao giờ có thời điểm nào hệ thống production không được giám sát (zero monitoring blackout). Dual-write đảm bảo cả hệ thống cũ và mới đều nhận dữ liệu song song.

---

## Week 1: Baseline + Inventory + Cost Attribution

| Item | Detail |
|---|---|
| **Goal** | Thiết lập baseline đo lường hiện tại, inventory toàn bộ alerts/dashboards, attribution chi phí |
| **Scope** | Tất cả 10 services, tất cả 3 vendors |
| **Actions** | 1. Export toàn bộ Datadog monitors via API → JSON (đếm: ~100+ monitors) |
| | 2. Export toàn bộ Splunk saved searches + alerts → CSV |
| | 3. Export Grafana Cloud dashboards → JSON |
| | 4. Đo MTTD/MTTR baseline hiện tại cho 10 incidents gần nhất |
| | 5. Tạo cost attribution table: chi phí per-service (top 5 services by log volume, metric cardinality) |
| | 6. Document hiện tại: log volume/day per service, trace span count, custom metric series count |
| | 7. Xác nhận Splunk contract timeline: ngày hết hạn, 90-day notice deadline |
| | 8. Setup Git repo cho observability-as-code (alert rules, dashboard JSON, OTel config) |
| **Validation** | ☐ Có danh sách đầy đủ monitors/alerts (count matches Datadog/Splunk UI) |
| | ☐ Cost attribution table hoàn chỉnh |
| | ☐ Baseline MTTD/MTTR documented |
| | ☐ Splunk notice deadline calendared (120 ngày trước, tránh lặp pain point #10) |
| **Go/No-go** | ✅ Go nếu: inventory export hoàn chỉnh, cost attribution rõ ràng |
| | ❌ No-go nếu: không export được Datadog monitors (API issue) → fix trước khi tiếp |
| **Rollback** | N/A (tuần 1 chỉ đọc/export, không thay đổi gì) |
| **Owner** | Platform SRE Lead |
| **Risk** | Low — chỉ đọc data, không thay đổi production |

---

## Week 2: Deploy OTel Collector + OSS Backends ở Staging

| Item | Detail |
|---|---|
| **Goal** | Dựng toàn bộ target stack trong staging environment, validate connectivity |
| **Scope** | Staging cluster only. Không ảnh hưởng production |
| **Actions** | 1. Deploy VictoriaMetrics cluster (3 nodes) trong staging via Helm |
| | 2. Deploy Grafana Loki (microservices mode, 3 nodes) + S3 bucket cho chunks |
| | 3. Deploy Grafana Tempo (2 nodes) + S3 bucket cho traces |
| | 4. Deploy Grafana (2 instances HA) + PostgreSQL cho metadata |
| | 5. Deploy OTel Collector DaemonSet + Gateway (2 replicas) trong staging |
| | 6. Configure OTel Collector pipeline: `receivers → processors → exporters` cho metrics/logs/traces |
| | 7. Configure tail-based sampling tại Gateway: 100% error traces, 10% normal |
| | 8. Deploy Blackbox Exporter với 10 synthetic checks (canary) |
| | 9. Deploy Alertmanager (2 replicas) + configure grouping rules |
| | 10. Decommission staging Prometheus (replace bằng VictoriaMetrics) → save $300/tháng |
| **Validation** | ☐ VictoriaMetrics nhận metrics từ OTel Collector (verify qua vmui) |
| | ☐ Loki nhận logs (verify qua Grafana Explore → LogQL query) |
| | ☐ Tempo nhận traces (verify qua Grafana Explore → TraceQL query) |
| | ☐ Exemplars linking works: metric → trace → log trong Grafana |
| | ☐ Alertmanager firing test alert → nhận trong Grafana |
| | ☐ All components healthy (no CrashLoopBackOff, no OOM) |
| **Go/No-go** | ✅ Go nếu: tất cả 3 pipelines (metrics/logs/traces) end-to-end working trong staging |
| | ❌ No-go nếu: bất kỳ pipeline nào fail → debug trong staging trước khi tiến production |
| **Rollback** | Xóa staging deployment. Không ảnh hưởng production |
| **Owner** | Platform SRE Team (2 engineers) |
| **Risk** | Low — staging only. Risk chính: Helm chart configuration issues |

---

## Week 3: Dual-write cho 2 Low-risk Services

| Item | Detail |
|---|---|
| **Goal** | Bắt đầu dual-write từ production, validate data quality trên OSS stack |
| **Scope** | `recommender-svc` (criticality: low) và `notification-svc` (criticality: low) |
| **Actions** | 1. Deploy OTel Collector DaemonSet trên production nodes chạy 2 services target |
| | 2. Configure OTel Collector dual-export: gửi tới **cả** Datadog/Splunk (existing) VÀ VictoriaMetrics/Loki/Tempo (new) |
| | 3. Giữ Datadog Agent + Splunk Forwarder chạy song song (chưa xóa) |
| | 4. So sánh data: metric values giống nhau giữa Datadog vs VictoriaMetrics? Log count match giữa Splunk vs Loki? |
| | 5. Build Grafana dashboard cho 2 services target (clone từ Datadog dashboards) |
| | 6. Run synthetic incident: inject latency spike vào recommender-svc → verify alert fire trên cả Datadog VÀ Grafana |
| **Validation** | ☐ Metric giá trị match ±5% giữa Datadog và VictoriaMetrics cho cùng time range |
| | ☐ Log event count match ±2% giữa Splunk và Loki |
| | ☐ Trace coverage: Tempo có ≥ traces Datadog (vì sampling rate tăng 1%→10%) |
| | ☐ Synthetic incident detected bởi cả 2 hệ thống |
| | ☐ OTel Collector resource usage <200MB RAM, <0.5 CPU per node |
| | ☐ No production impact (latency, error rate unchanged) |
| **Go/No-go** | ✅ Go nếu: data match ±5%, zero production impact, synthetic incident detected |
| | ❌ No-go nếu: data mismatch >10%, hoặc OTel Collector gây latency spike → rollback OTel, giữ Datadog Agent |
| **Rollback** | Tắt OTel Collector DaemonSet trên production nodes. Datadog Agent + Splunk Forwarder vẫn chạy bình thường. Rollback time: <5 phút (kubectl delete daemonset) |
| **Owner** | Platform SRE + Service owners (ml-platform, growth teams) |
| **Risk** | Medium — production change nhưng trên low-criticality services. Dual-write = double egress bandwidth |

---

## Week 4: Build Grafana Dashboards + Alert Parity Audit

| Item | Detail |
|---|---|
| **Goal** | Xây dựng dashboards và alert rules trong Grafana đảm bảo feature parity với Datadog + Splunk |
| **Scope** | Toàn bộ 10 services (dashboards), top 20 critical alerts |
| **Actions** | 1. Tạo Grafana dashboards cho tất cả 10 services: RED metrics (Rate, Error, Duration), resource usage (CPU, Memory, Network) |
| | 2. Tạo SLO dashboard: availability SLO per service (target 99.9% cho critical, 99.5% cho medium, 99% cho low) |
| | 3. Convert top 20 Datadog monitors → Grafana alert rules (PromQL + LogQL) |
| | 4. Convert top 10 Splunk alerts → Grafana alert rules (LogQL) |
| | 5. Configure Alertmanager routing: critical → PagerDuty, warning → Slack, info → Grafana only |
| | 6. Configure Alertmanager grouping: `group_by: [service, alertname]`, `group_wait: 30s`, `group_interval: 5m` |
| | 7. Configure inhibition rules: `edge-lb` firing → suppress downstream `auth-svc`, `checkout-svc` alerts |
| | 8. Side-by-side test: trigger known incident pattern → compare Datadog alert vs Grafana alert timing |
| | 9. Tạo on-call runbook cho Grafana workflow (thay thế `tools/oncall-runbook.sh`) |
| **Validation** | ☐ Dashboard parity: mỗi Datadog dashboard có Grafana equivalent |
| | ☐ Alert parity: top 20 alerts fire trong ±30s so với Datadog monitors |
| | ☐ Alertmanager grouping: test cascade scenario → verify 47 alerts → 4 groups |
| | ☐ PagerDuty integration: Alertmanager → PagerDuty webhook → page received |
| | ☐ On-call engineer review: "Tôi có thể triage incident từ Grafana alone?" |
| **Go/No-go** | ✅ Go nếu: ≥90% alert parity confirmed, on-call approves Grafana workflow |
| | ❌ No-go nếu: >3 critical alerts missing, hoặc on-call rejects Grafana workflow → thêm 1 tuần dashboard tuning |
| **Rollback** | Grafana alerts disable → Datadog monitors re-enable. Rollback time: <15 phút |
| **Owner** | Platform SRE + On-call rotation leads (all teams) |
| **Risk** | Medium — alert parity sai = missed alert hoặc alert fatigue. Cần careful audit |

---

## Week 5: Migrate Log Hot Path + Retention Tiering (4 Medium-risk Services)

| Item | Detail |
|---|---|
| **Goal** | Chuyển log ingest primary path sang Loki cho medium-risk services. Kích hoạt tiered retention |
| **Scope** | `cart-svc`, `catalog-svc`, `inventory-svc`, `search-svc` (medium criticality) |
| **Actions** | 1. Deploy OTel Collector DaemonSet trên nodes chạy 4 services target |
| | 2. Configure Loki retention: hot=7d (EBS), warm=30d (S3 Standard), cold=90d (S3-IA) |
| | 3. Verify Loki compactor lifecycle policy hoạt động đúng |
| | 4. **Chuyển primary log source**: On-call bắt đầu dùng Grafana Explore (Loki) thay vì Splunk cho 4 services này |
| | 5. Giữ Splunk dual-write thêm 2 tuần (safety net) |
| | 6. Test cold log query: retrieve log từ S3-IA → verify latency <60s |
| | 7. Test Grafana log → trace correlation: click log line → jump to trace |
| | 8. Gửi notice non-renewal cho Splunk (nếu chưa gửi — 90-day window) |
| **Validation** | ☐ Loki hot query latency <5s cho 7-day structured query |
| | ☐ Loki warm query latency <30s cho 30-day label-filtered query |
| | ☐ Log volume match giữa Loki vs Splunk cho 4 services |
| | ☐ Tiered retention working: chunks di chuyển EBS → S3 Standard → S3-IA theo schedule |
| | ☐ On-call triage 1 real/simulated incident thành công chỉ dùng Grafana |
| | ☐ Splunk non-renewal notice sent (if applicable) |
| **Go/No-go** | ✅ Go nếu: Loki query latency acceptable, on-call triage thành công |
| | ❌ No-go nếu: Loki query >30s cho structured queries trên hot data, HOẶC on-call không thể triage từ Grafana alone → pause, tune Loki, hoặc consider ClickHouse alternative |
| **Rollback** | Switch on-call back to Splunk. Disable Loki log exporter cho 4 services. Rollback time: <10 phút (OTel config change) |
| **Owner** | Platform SRE + checkout, catalog, inventory, search team leads |
| **Risk** | **HIGH** — Đây là tuần rủi ro nhất. Log là RCA source chính. Nếu Loki không đáp ứng on-call workflow → MTTR tăng |

---

## Week 6: Expand tới Critical Services + Incident Replay Test

| Item | Detail |
|---|---|
| **Goal** | Migrate critical services. Chạy incident replay để validate end-to-end |
| **Scope** | `payment-svc` (critical), `checkout-svc` (high), `edge-lb` (high), `auth-svc` (high) |
| **Actions** | 1. Deploy OTel Collector cho 4 critical services |
| | 2. Dual-write tới cả Datadog VÀ OSS stack (safety net cho critical path) |
| | 3. **Incident replay test**: Dùng 3 incidents lịch sử (INC-2025-11-08 connection pool, INC-2025-12-12 config push, INC-2026-01-04 thread starvation) → inject synthetic signals → on-call triage hoàn toàn trên Grafana |
| | 4. Measure MTTD/MTTR trên Grafana stack so với baseline |
| | 5. Validate payment-svc SLO dashboard: error budget tracking, burn rate alerts |
| | 6. Validate trace coverage: payment-svc traces phải 100% cho errors, ≥10% overall |
| | 7. Validate alert routing: payment-svc critical alert → PagerDuty → payments-oncall |
| **Validation** | ☐ Incident replay: on-call RCA success trong ≤30 phút (target MTTR giảm 30%) |
| | ☐ payment-svc SLO dashboard accurate (compare Datadog vs Grafana SLO values) |
| | ☐ Trace coverage ≥10× hiện tại (1% → 10%+) cho critical services |
| | ☐ Zero data loss: metric/log/trace count match giữa old và new stack |
| | ☐ Alerts đồng bộ: Grafana alert fire within ±30s so với Datadog |
| **Go/No-go** | ✅ Go nếu: incident replay MTTR ≤30 phút, zero data loss, alert parity confirmed |
| | ❌ No-go nếu: on-call MTTR > baseline (42 phút) trên Grafana → tuần 6 kéo dài, thêm training + dashboard tuning |
| **Rollback** | Tắt OTel Collector dual-write cho critical services. Re-enable Datadog-only mode. Rollback time: <5 phút |
| **Owner** | Platform SRE Lead + payments, checkout, identity, platform team leads |
| **Risk** | **HIGH** — Critical services. Incident miss = revenue impact. Dual-write là safety net bắt buộc |

---

## Week 7: Cost Optimization + Sampling Tuning + Archive Validation

| Item | Detail |
|---|---|
| **Goal** | Fine-tune chi phí, sampling rates, cardinality control. Validate archive và restore |
| **Scope** | Toàn bộ stack |
| **Actions** | 1. **Cardinality audit**: Identify top 20 high-cardinality metric series → add relabel_config để drop hoặc aggregate |
| | 2. **Log sampling**: Configure OTel Collector drop debug/verbose logs cho low-criticality services (recommender-svc, notification-svc) → giảm ~30% log volume |
| | 3. **Trace sampling tuning**: Adjust tail-based sampling thresholds dựa trên 4 tuần data: optimize error-capture rate vs storage cost |
| | 4. **Archive validation**: Restore log từ S3-IA (cold) → verify content integrity và query ability |
| | 5. **Compliance test**: Run audit report trên Loki data → verify compliance team có thể tạo required reports |
| | 6. **Cost measurement**: So sánh actual spend vs A3 cost model → adjust assumptions |
| | 7. **Performance tuning**: Loki query caching, VictoriaMetrics deduplication tuning, Grafana query optimization |
| | 8. **Reserved Instance evaluation**: Nếu compute stable → mua RI 1-year cho ~40% savings |
| **Validation** | ☐ Cardinality giảm ≥20% sau relabeling |
| | ☐ Log volume giảm ≥25% sau filtering (từ 52 GB/day → ≤39 GB/day) |
| | ☐ Cold archive restore successful (data integrity verified) |
| | ☐ Actual monthly cost ≤ $8,000 (within 20% of A3 model) |
| | ☐ Compliance team signs off trên Loki-based audit reports |
| **Go/No-go** | ✅ Go nếu: actual cost ≤ A3 model ±20%, compliance approved, archive restore works |
| | ❌ No-go nếu: cost > $12,000 (cardinality explosion), hoặc compliance rejects Loki reports → address trước cutover |
| **Rollback** | Revert sampling/filtering configs. Cost-only changes, không ảnh hưởng functionality |
| **Owner** | Platform SRE + Finance + Compliance/Security team |
| **Risk** | Medium — Aggressive filtering có thể drop useful logs. Conservative approach: filter chỉ debug level trước |

---

## Week 8: Production Cutover + Vendor Contract Reduction + Final Validation

| Item | Detail |
|---|---|
| **Goal** | Cutover hoàn toàn sang OSS stack. Disable Datadog Agent. Initiate Splunk non-renewal |
| **Scope** | Toàn bộ production |
| **Actions** | 1. **Disable Datadog Agent**: Remove Datadog Agent DaemonSet từ production cluster. OTel Collector trở thành sole collection agent |
| | 2. **Cancel Datadog subscription**: Monthly billing → effective immediately (hoặc end of billing cycle) |
| | 3. **Splunk**: Chuyển sang read-only mode (không ingest mới). Giữ read access cho archive cũ cho đến hết contract |
| | 4. **PagerDuty**: Reduce active users 65 → 30. Remove viewer accounts |
| | 5. **Grafana Cloud**: Cancel subscription (self-hosted Grafana active) |
| | 6. **Final rollback window**: Giữ Datadog account (không xóa data) trong 30 ngày sau cutover. Nếu issue → re-enable agent |
| | 7. **Documentation**: Update on-call playbook, runbook, escalation procedures → Grafana-only workflow |
| | 8. **Celebration + retrospective**: Đo MTTR thực tế tuần đầu post-cutover. Document lessons learned |
| **Validation** | ☐ Datadog Agent removed, zero data flowing to Datadog |
| | ☐ PagerDuty user count = 30 |
| | ☐ All 10 services observable via Grafana |
| | ☐ On-call successfully triages ≥1 real incident post-cutover without Datadog |
| | ☐ Monthly bill ≤ $8,000 (first full month) |
| | ☐ MTTR for post-cutover incidents ≤ 30 phút |
| **Go/No-go** | ✅ Cutover nếu: tuần 6-7 tất cả validation passed |
| | ❌ Delay cutover nếu: bất kỳ Week 6-7 gate fail → fix trước, cutover tuần 9-10 |
| **Rollback** | Re-deploy Datadog Agent (Helm chart in Git). Re-enable Datadog subscription (monthly billing). Rollback time: <30 phút |
| **Owner** | Platform SRE Lead + VP Engineering (sign-off) |
| **Risk** | **HIGH** — Production cutover. 30-day rollback window là safety net. First real incident post-cutover là moment of truth |

---

## Tóm tắt Rollback Plan mỗi Tuần

| Week | Thay đổi | Rollback nếu fail | Thời gian rollback |
|---|---|---|---|
| 1 | Read-only export | N/A | N/A |
| 2 | Deploy staging | Delete staging | <5 phút |
| 3 | Dual-write 2 low-risk services | Disable OTel DaemonSet | <5 phút |
| 4 | Grafana dashboards + alerts | Disable Grafana alerts, re-enable Datadog | <15 phút |
| 5 | Log primary → Loki (4 medium services) | Switch back to Splunk | <10 phút |
| 6 | Critical services dual-write | Disable OTel cho critical services | <5 phút |
| 7 | Cost optimization (sampling, filtering) | Revert configs | <5 phút |
| 8 | Full cutover | Re-deploy Datadog Agent | <30 phút |

---

## Tóm tắt Risk Level mỗi Tuần

```
Week 1: ░░░░░ LOW        (read-only)
Week 2: ░░░░░ LOW        (staging only)
Week 3: ██░░░ MEDIUM     (production, low-risk services)
Week 4: ██░░░ MEDIUM     (alert parity audit)
Week 5: ████░ HIGH       (log path migration — rủi ro nhất)
Week 6: ████░ HIGH       (critical services)
Week 7: ██░░░ MEDIUM     (optimization only)
Week 8: ████░ HIGH       (full cutover)
```
