# Lab — Closed-Loop Auto-Remediation

**Cá nhân.**

Đây là một bài thực hành kỹ thuật. Bạn sẽ xây dựng một orchestrator hoạt động thực tế — không phải simulation, không phải diagram. Cuối cùng, bạn sẽ chạy 3 chaos scenarios và orchestrator phải xử lý chúng tự động theo pattern Detect → Decide → Act → Verify → Rollback.

---

## 1. Context

Bạn là một AIOps engineer tại **Ronki** — một nền tảng e-commerce xử lý ~80,000 orders/day. Production stack có 5 services:

```
Internet
    │
    ▼
┌─────────────┐
│  frontend   │  (React SPA, static assets)
└──────┬──────┘
       │ HTTP
       ▼
┌─────────────┐
│ api-gateway │  (reverse proxy + rate limit)
└──────┬──────┘
       │
   ┌───┴────────────┐
   │                │
   ▼                ▼
┌──────────┐  ┌───────────────┐
│payment-  │  │ inventory-svc │
│   svc    │  └───────┬───────┘
└──────────┘          │
                      ▼
               ┌─────────────┐
               │ checkout-svc│
               └─────────────┘
```

Đội ngũ ops hiện tại đang xử lý các incident một cách thủ công: nhận alert → SSH vào server → restart → verify. Quá trình này mất khoảng 15–45 phút. Trong giờ cao điểm (11:00–13:00, 19:00–22:00), các cascade failures xảy ra trung bình 2 lần mỗi tuần. Mỗi 15 phút downtime làm thất thoát khoảng 1,000 orders.

**Mục tiêu của lab**: xây dựng một closed-loop orchestrator có khả năng detect các incident, decide action cần thực hiện, thực thi (act) nó, verify kết quả — và tự động rollback nếu action không giải quyết được vấn đề.

---

## 2. Những gì bạn nhận được

| Artifact | Description |
|---|---|
| `configs/docker-compose.yml` | 5-service stack + Prometheus + Alertmanager |
| `configs/prometheus.yml` | Scrape config cho tất cả các services |
| `configs/alert_rules.yml` | 3 alert rules: high latency, high error rate, instance down |
| `scripts/start_stack.sh` | Start toàn bộ stack |
| `scripts/stop_stack.sh` | Stop stack, xóa volumes |
| `scripts/inject_fault.sh` | Inject các faults vào containers (latency, kill, v.v.) |
| `data/baseline.json` | Normal baseline metrics cho bước verify |
| `data/expected.json` | Expected behavior cho từng chaos scenario |

Tất cả dữ liệu trong `data/` đều là **dữ liệu thật** — được capture từ stack chạy dưới điều kiện bình thường. Không phải dữ liệu mock.

---

### Observability dashboard

Grafana chạy tại **http://localhost:3000** (anonymous viewer, không yêu cầu login). Dashboard chính: **"AIOps Closed-Loop"**.

| Row | Panel | Content |
|-----|-------|---------|
| Service Health | 5 stat panels | p99 latency (ms) của mỗi service, đổi màu ở ngưỡng 200 ms / 500 ms |
| Service Health | Global error rate | Tổng error rate trên toàn stack |
| Alert State | Active alerts | Danh sách các alerts hiện đang firing từ Alertmanager |
| Alert State | Alert timeline | Biểu đồ số lượng alert firing theo thời gian |
| Orchestrator State | Actions by outcome | Tổng số actions theo kết quả: success / rollback / fail / dry_run |
| Orchestrator State | Circuit-breaker | Trạng thái CLOSED / OPEN của từng service |
| Orchestrator State | Blast-radius remaining | Gauge số lượng actions còn lại trong window hiện tại |
| Orchestrator State | Mutex state | State timeline: FREE / LOCKED của từng service |
| Action Timeline | Action executions | Biểu đồ Actions/minute, chia theo service + runbook + outcome |
| Audit Log Tail | Audit log | 100 events gần nhất từ `audit_log.jsonl`, có thể filter theo `event_type` hoặc `service` |

**Điều kiện bắt buộc để dashboard hoạt động**: orchestrator phải import `engine.metrics` và gọi `start_metrics_server()` — sample solution đã làm sẵn việc này. Audit log được đọc từ file `audit_log.jsonl`; để Promtail đọc được, hãy chạy orchestrator với environment variable `AUDIT_LOG_PATH=/audit/audit_log.jsonl` hoặc mount volume `audit_logs` vào closed-loop container nếu chạy bằng Docker.

Yêu cầu thêm: package `prometheus_client` phải được cài đặt trong môi trường Python của orchestrator:
```bash
uv pip install prometheus_client
```

Dashboard hỗ trợ cho việc debug; đây không phải là một tiêu chí chấm điểm (acceptance criterion) — điểm số dựa trên 6 scenarios, không dựa trên giao diện dashboard.

---

## 3. Closed-Loop Safety Pattern

Orchestrator **bắt buộc** phải pass qua 5 sub-checkpoints cho mỗi action. Nếu trượt bất kỳ checkpoint nào → action đó không được execute.

```
Alert Fired
    │
    ▼
┌─────────────────────────────────────────────────┐
│  1. DETECT — poll Alertmanager API               │
│     → parse alert name, service, severity        │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  2. DECIDE — match alert → runbook               │
│     → check blast-radius limit                   │
│     (max actions/minute, max restarts/hour)       │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  3. DRY-RUN — simulate action, không side effect │
│     → nếu dry-run fail → từ chối + log           │
└──────────────────┬──────────────────────────────┘
                   │
              dry-run pass
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  4. ACT — execute runbook script                 │
│     → subprocess call với timeout                │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  5a. VERIFY — poll Prometheus 60s, so sánh       │
│     baseline vs threshold                        │
│                                                  │
│     verify PASS → log success → xong             │
│     verify FAIL → trigger rollback               │
└──────────────────┬──────────────────────────────┘
                   │ verify fail
                   ▼
┌─────────────────────────────────────────────────┐
│  5b. ROLLBACK — execute rollback runbook         │
│     → tăng failure_count                         │
│     → nếu failure_count ≥ 3 → CIRCUIT BREAKER    │
│        halt automation, log HALT                 │
└─────────────────────────────────────────────────┘
```

### Chi tiết 5 Sub-checkpoints

| # | Checkpoint | Yêu cầu tối thiểu |
|---|---|---|
| 1 | **Dry-run mode** | Mỗi runbook script phải hỗ trợ cờ `--dry-run`; orchestrator luôn phải gọi dry-run trước |
| 2 | **Blast-radius config** | Config file: `max_actions_per_minute`, `max_restarts_per_service_per_hour`; nếu vượt ngưỡng → escalate, không hành động |
| 3 | **Verify post-act** | Sau khi act, poll Prometheus query ≥3 lần trong vòng 60s, so sánh với threshold trong `baseline.json` |
| 4 | **Auto-rollback** | Verify fail → tự động gọi rollback runbook; không cần human can thiệp |
| 5 | **Circuit breaker** | 3 lần liên tiếp failure (action fail hoặc verify fail) → orchestrator tự động halt; log state `CIRCUIT_OPEN` |

---

## 4. Những gì bạn phải xây dựng

### Cấu trúc thư mục nộp bài

```
your-name/
├── closed_loop.py          ← main orchestrator
├── runbooks/
│   ├── restart_service.sh
│   ├── scale_replicas.sh
│   └── clear_cache.sh      (tối thiểu 3 scripts)
├── DESIGN.md
└── SUBMIT.md
```

### `closed_loop.py` — orchestrator

Các hành vi yêu cầu:

- Poll Alertmanager API `http://localhost:9093/api/v2/alerts` mỗi 15 giây
- Decide: map `alertname` → runbook script
- Áp dụng đủ 5 sub-checkpoints (xem phần 3)
- Hỗ trợ cờ `--dry-run` ở cấp độ orchestrator (vô hiệu hóa mọi thao tác thực thi, chỉ log)
- Log cấu trúc định dạng JSON ra stdout: mỗi event phải có `ts`, `event_type`, `service`, `action`, `result`
- Đọc config từ một file YAML (không hardcode các thresholds)

Bạn có thể chọn **một trong hai** decision engines:

**Option A — Rule-based** (được khuyến khích nếu bạn không có Anthropic API key):
```python
RUNBOOK_MAP = {
    "HighLatency":   "runbooks/restart_service.sh",
    "HighErrorRate": "runbooks/clear_cache.sh",
    "InstanceDown":  "runbooks/restart_service.sh",
}
```

**Option B — LLM-based** (sử dụng Anthropic API):
- Gửi context của alert cho Claude, nhận lại JSON `{"action": "restart_service", "confidence": 0.87}`
- Chỉ execute nếu `confidence >= 0.6`
- Phải có fallback sang rule-based nếu API không kết nối được

Dù bạn chọn phương án nào, bạn phải bảo vệ thiết kế đó trong `DESIGN.md`.

### `runbooks/*.sh` — automation scripts

Mỗi script bắt buộc:

- Nhận các cờ `--service <name>` và `--dry-run`
- Khi dry-run: chỉ in ra `[DRY-RUN] would execute: <action>`, exit 0
- Khi real run: thực thi action thật (Docker Compose restart, scale, v.v.)
- Trả về exit code 0 = success, khác 0 = failure

### `DESIGN.md` — bắt buộc

Trả lời đủ 4 câu hỏi:

1. Bạn chọn rule-based hay LLM-based decision engine? Tại sao? Trade-offs là gì?
2. Config blast-radius của bạn: các giá trị cụ thể và lý do chọn chúng
3. Metric nào được dùng ở bước verify? Ngưỡng (threshold) là bao nhiêu? Timeout là bao lâu?
4. Khi nào circuit breaker của bạn reset? Thủ công hay tự động? Tại sao?

### `SUBMIT.md` — bắt buộc

Lưu lại kết quả chạy của 3 chaos scenarios (xem phần 5).

---

## 5. Acceptance: 3 chaos scenarios

Sau khi hoàn thiện code, hãy chạy 3 bài test này theo thứ tự. Copy log output vào `SUBMIT.md`.

### Scenario 1 — Action succeeds

```bash
# Terminal 1: run orchestrator
uv run python closed_loop.py --config config.yaml

# Terminal 2: inject latency
bash data-pack/scripts/inject_fault.sh latency payment-svc 500ms

# Expected (Kết quả kỳ vọng):
# - Orchestrator detects alert "HighLatency" trên payment-svc
# - Dry-run pass
# - Blast-radius OK
# - Action: restart payment-svc
# - Verify: latency trở về bình thường
# - Log: event_type=ACTION_SUCCESS
```

### Scenario 2 — Action fails → rollback

```bash
# Terminal 2: fully kill service + block restart
bash data-pack/scripts/inject_fault.sh kill checkout-svc

# Expected:
# - Orchestrator detects "InstanceDown"
# - Action: restart checkout-svc → fail (container vẫn down)
# - Verify: service vẫn down → FAIL
# - Rollback được trigger
# - Log: event_type=ROLLBACK_TRIGGERED
```

Để mô phỏng verify fail: bạn có thể tạm thời set verify threshold cực thấp (ví dụ: latency < 10ms) để verify luôn luôn fail, từ đó test được rollback logic.

### Scenario 3 — Circuit breaker

```bash
# Chạy inject_fault 3 lần liên tiếp để tạo ra 3 lần verify failure liên tiếp
# (Xem hướng dẫn trong data/expected.json)

# Expected:
# - Sau failure thứ 3: orchestrator log CIRCUIT_OPEN
# - Không có hành động nào khác được thực thi thêm
# - Log: event_type=CIRCUIT_BREAKER_HALT
```

---

### Stress scenarios (Acceptance tests #4, #5, #6)

Ba stress scenarios sau đây kiểm tra sự cứng cáp của orchestrator dưới điều kiện production thực tế: multi-step deploys, concurrent alerts, và invalid decisions từ LLM. Hoàn thành cả 3 để đạt mức xuất sắc.

---

#### Acceptance test #4 — Multi-step transactional rollback

```bash
# Terminal 1: run orchestrator
uv run python closed_loop.py --config config.yaml

# Terminal 2: inject alert trigger một multi-step deploy
# (config.yaml phải có multi_step_map và multi_step_rollback_map cho alert này)
# Buộc step-C phải fail bằng cách stop container trước khi step-C chạy:
bash data-pack/scripts/inject_fault.sh kill ronki-api-gateway

# Expected observable outcomes:
# - Log TRANSACTIONAL_STEP_FAIL tại step-C
# - Log TRANSACTIONAL_ROLLBACK_STEP × 2 (rollback-B trước, rồi đến rollback-A)
# - Log TRANSACTIONAL_ROLLBACK_COMPLETE với rolled_back=[rollback-B, rollback-A]
# - Không có partial state: service phải trở về state ban đầu trước deploy
# - Audit trail: mỗi bước rollback phải có timestamp, script name, exit code trong log
```

**Kết quả quan sát được (Observable outcomes):**
- `TRANSACTIONAL_STEP_FAIL` xuất hiện với field `completed_before_failure`
- `TRANSACTIONAL_ROLLBACK_STEP` xuất hiện đúng 2 lần, theo thứ tự rollback-B → rollback-A
- `TRANSACTIONAL_ROLLBACK_COMPLETE` liệt kê chính xác các step đã được rollback
- Không có `ACTION_SUCCESS` — một failed deploy không được đánh dấu là success

---

#### Acceptance test #5 — Concurrent alert race

```bash
# Inject fault trên 2 service khác nhau cùng lúc
bash data-pack/scripts/inject_fault.sh --concurrent ronki-payment-svc ronki-inventory-svc

# Expected observable outcomes:
# - Cả hai sự kiện ALERT_DETECTED đều xuất hiện trong cùng một poll cycle
# - Timestamp của DRY_RUN_PASS cho payment-svc và inventory-svc chênh lệch < 1s
#   (chạy song song, không block nhau)
# - Nếu một alert thứ 2 được inject vào payment-svc trong lúc runbook của nó vẫn đang chạy:
#   log SERVICE_LOCK_BUSY thay vì chạy 2 runbook cùng lúc trên cùng 1 service
```

**Kết quả quan sát được:**
- `SERVICE_LOCK_BUSY` xuất hiện khi và chỉ khi cùng một service nhận alert thứ 2 trong lúc alert đầu tiên đang được xử lý
- Hai service khác nhau KHÔNG block nhau: cả hai đều log `DRY_RUN_PASS` mà không có `SERVICE_LOCK_BUSY` ở giữa
- Logs cho thấy 2 luồng xử lý độc lập, mỗi luồng kết thúc bằng `ACTION_SUCCESS` hoặc `ROLLBACK_EXECUTED`

---

#### Acceptance test #6 — LLM hallucination defense

```bash
# Thêm một mapping tạm thời vào runbook_map trong config.yaml:
#   TestHallucination: "runbooks/nonexistent_runbook.sh"
# Đảm bảo runbook_registry KHÔNG chứa "runbooks/nonexistent_runbook.sh"
# Inject một alert giả lập với alertname=TestHallucination

# Expected observable outcomes:
# - Log DECISION_VALIDATION_FAILED với các fields:
#     bad_runbook: "runbooks/nonexistent_runbook.sh"
#     alertname: "TestHallucination"
#     action: "escalate_no_auto_action"
# - KHÔNG CÓ DRY_RUN_PASS, ACTION_EXECUTED, hoặc RUNBOOK_EXEC trong log
# - KHÔNG spawn ra subprocess nào
# - Circuit breaker counter KHÔNG tăng (validation failure ≠ action failure)
```

**Kết quả quan sát được:**
- `DECISION_VALIDATION_FAILED` xuất hiện đầy đủ 4 fields: `bad_runbook`, `alertname`, `raw_decision`, `action`
- Tuyệt đối không có sự kiện `RUNBOOK_EXEC` nào sau `DECISION_VALIDATION_FAILED`
- Trạng thái Circuit breaker không thay đổi sau một lỗi validation

---

## 6. Tiêu chí chấm điểm (Rubric) (6 criteria, scale 1–5)

| # | Criterion | Điểm 1 | Điểm 3 | Điểm 5 |
|---|---|---|---|---|
| 1 | **Detect quality** | Alertmanager poll không hoạt động hoặc parse sai format | Poll thành công, parse đúng alert name + service | Poll + parse đúng + log cấu trúc đầy đủ cho mọi event |
| 2 | **Decide logic** | Không có runbook map hoặc map sai | Rule-based hoạt động cho ≥2 alert types | Rule-based hoặc LLM-based với fallback, giải thích rõ trong DESIGN.md |
| 3 | **Act safety (5 sub-checkpoints)** | Thiếu ≥2 sub-checkpoints | Có đủ 5 nhưng ≥1 hoạt động chưa chính xác | Tất cả 5 hoạt động trơn tru: dry-run / blast-radius / verify / rollback / circuit breaker |
| 4 | **Verify + rollback** | Không có verify, hoặc verify không dùng Prometheus | Verify có dùng Prometheus, có rollback nhưng không tự trigger | Verify + auto-rollback + kết quả rollback cũng được verify |
| 5 | **Defense in DESIGN.md** | Trả lời < 2/4 câu hỏi | Trả lời đủ 4 câu nhưng còn chung chung | 4 câu trả lời có số liệu cụ thể (ngưỡng, timeout, config) và lập luận rõ ràng |
| 6 | **Concurrency + Hallucination Safety** | Không xử lý concurrent; không validation | Có mutex hoặc có validation, nhưng không có cả hai | Per-service mutex chuẩn (2 service khác nhau không block nhau) + validation từ chối runbook ngoài registry + audit log đầy đủ |

Mức đạt: tổng điểm ≥ 12/25. Mức xuất sắc: ≥ 20/25. (Với tiêu chí #6: tổng điểm ≥ 15/30 để đạt; ≥ 24/30 để xuất sắc).

---

## 7. Yêu cầu (Prerequisites)

Máy tính của bạn cần có:

- Docker Desktop (hoặc Docker Engine + Compose plugin)
- Python ≥ 3.11 + package manager `uv`
- `curl` (để test API endpoints)
- Các cổng 9090 (Prometheus), 9093 (Alertmanager), 8080–8084 (services) phải đang trống

Cài đặt các thư viện Python:
```bash
uv pip install requests pyyaml
```

---

## 8. Khởi chạy Stack

```bash
# Khởi động
bash data-pack/scripts/start_stack.sh

# Kiểm tra các service đã hoạt động
curl http://localhost:9090/-/healthy    # Prometheus
curl http://localhost:9093/-/healthy    # Alertmanager
curl http://localhost:8080/health       # api-gateway

# Dừng hệ thống
bash data-pack/scripts/stop_stack.sh
```

Prometheus UI: http://localhost:9090  
Alertmanager UI: http://localhost:9093

---

## 9. Ngoài phạm vi (Out of Scope)

- Bạn **không** cần phải deploy lên AWS hay bất kỳ cloud thực tế nào.
- Bạn **không** cần xây dựng custom Prometheus exporter — các mock services đã tự động export metrics sẵn.
- Bạn **không** cần viết unit tests cho mọi function — 3 chaos scenarios là phương thức verification chính.
- Bạn **không** cần implement LLM nếu không có API key — rule-based vẫn sẽ đạt điểm tối đa tương đương LLM-based nếu được thiết kế và bảo vệ tốt.

---
