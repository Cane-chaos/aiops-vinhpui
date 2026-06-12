# Lab W2 — Observability + AIOps Stack Redesign — Submission

## Tổng quan

Đây là bài nộp cho Lab "Observability + AIOps Stack Redesign". Hệ thống GeekShop (10 microservices, 4 backing stores) hiện tốn **$42,000/tháng** cho observability qua 3 SaaS vendor (Datadog, Splunk, PagerDuty). Thiết kế đề xuất chuyển sang **Grafana LGTM stack (self-hosted) + OpenTelemetry Collector**, giảm chi phí xuống **$6,841/tháng (-83.7%)** đồng thời giảm MTTR ước tính **~36%** nhờ consolidate UI và tăng trace coverage.

## Cấu trúc Submission

```
lab-w2-observability-stack-redesign-20260611/
├── data-pack/                          (Input data — không chỉnh sửa)
│   ├── services.json
│   ├── current-stack.md
│   ├── incidents_history.json
│   ├── pain_points.md
│   ├── current-architecture.png
│   └── README.md
├── A1_TARGET_ARCHITECTURE.md           (Sơ đồ Mermaid + giải thích kiến trúc đích)
├── A2_COMPONENT_DECISION_TABLE.md      (Bảng chọn component 11 hạng mục)
├── A3_COST_MODEL.md                    (Mô hình chi phí: $42K → $6.8K, sensitivity x2)
├── A4_ADR_001.md                       (ADR: Chuyển log từ Splunk → Loki + S3)
├── A4_ADR_002.md                       (ADR: Chuẩn hóa OTel + Grafana single-pane)
├── A5_MIGRATION_PLAN_8_WEEKS.md        (Kế hoạch 8 tuần, rollback + go/no-go mỗi tuần)
├── A6_RISK_REGISTER.md                 (8 rủi ro + mitigation + contingency)
├── A7_POC_PLAN.md                      (POC 3 ngày cho Loki log backend)
├── FINDINGS.md                         (5 câu reflection + self-review scorecard 94/100)
└── README.md                           (File này)
```

## Đọc theo thứ tự nào?

1. **Bắt đầu** với `A1_TARGET_ARCHITECTURE.md` — xem sơ đồ tổng quan.
2. **Chi tiết quyết định** trong `A2_COMPONENT_DECISION_TABLE.md` và 2 file ADR.
3. **Kiểm tra số liệu** trong `A3_COST_MODEL.md`.
4. **Kế hoạch thực thi** trong `A5_MIGRATION_PLAN_8_WEEKS.md`.
5. **Rủi ro + POC** trong `A6_RISK_REGISTER.md` và `A7_POC_PLAN.md`.
6. **Tổng kết** trong `FINDINGS.md` (bao gồm self-review scorecard).

## Key Numbers

| Metric | Giá trị |
|---|---|
| Current cost | $42,000/tháng |
| Target cost | $6,841/tháng |
| Savings | $35,159/tháng = **$421,908/năm** |
| Reduction | **83.7%** |
| MTTR reduction (est.) | ~36% (42 phút → 27 phút) |
| Self-review score | 94/100 (Excellent tier) |
