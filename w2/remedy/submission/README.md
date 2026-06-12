# Evidence-Driven Remediation Engine

## Chức năng

Với input là một live incident (bao gồm logs, traces, metrics, topology), engine này sẽ đề xuất một remediation action (hành động khắc phục) bằng cách so sánh incident đó với historical corpus và áp dụng các thuật toán utility selection có tính đến cost (chi phí).

## Hướng dẫn cài đặt (Setup)

```bash
# Yêu cầu Python 3.10+ (có sử dụng type hints như list[dict], dict | None)
# Không cần external libraries — chỉ sử dụng stdlib + PyYAML
pip install pyyaml

# Clone / đặt thư mục submission, sau đó chạy từ bên trong thư mục này:
cd submission/
```

## Hướng dẫn chạy (Run) — Cho một incident

```bash
python engine.py decide --incident eval/E01.json \
                        --history incidents_history.json \
                        --actions actions.yaml
```

Sẽ in ra JSON decision trên stdout và ghi thêm một dòng vào `audit.jsonl`.

## Hướng dẫn chạy — Cho toàn bộ 8 eval incidents

```bash
# PowerShell (Windows)
Remove-Item audit.jsonl -ErrorAction SilentlyContinue
foreach ($i in 1..8) {
    python engine.py decide --incident eval/E0$i.json `
                            --history incidents_history.json `
                            --actions actions.yaml | Out-Null
}

# Bash (Linux/Mac)
rm -f audit.jsonl
for i in {1..8}; do
    python engine.py decide --incident eval/E0$i.json \
                            --history incidents_history.json \
                            --actions actions.yaml > /dev/null
done
```

## Kết quả đầu ra dự kiến (8/8 correct, 0 must_not violations)

```
E01: rollback_service(payment-svc)   OK
E02: page_oncall                     OK
E03: rollback_service(esb)           OK
E04: page_oncall                     OK
E05: rollback_service(payment-svc)   OK
E06: restart_pod(cart-svc)           OK  ← conflict detected, tin vào traces
E07: page_oncall                     OK  ← OOD detected
E08: rollback_service(t24-service)   OK  ← cascade, tin vào dominant log svc
```

## Kiến trúc (Architecture)

```
incidents_history.json  →  Layer 1: features.py
actions.yaml            →  Layer 2: retrieval.py
eval/E0N.json           →  Layer 3: decision.py
                        →  engine.py (Kết nối tất cả layers, tạo audit.jsonl)
```

### Layer 1 — features.py
- Chuyển map raw log lines → 25 template IDs (sử dụng lightweight Drain-inspired clustering).
- Trích xuất các anomalous trace edges (error_rate > 10% hoặc p99_ms > 1000ms).
- Phát hiện các conflicting evidence (chứng cứ mâu thuẫn): logs quy lỗi cho service A nhưng traces lại báo anomaly ở service B.
- Trích xuất metric trend spikes (>50% tăng đột biến giữa baseline→recent).

### Layer 2 — retrieval.py
- Vectorise historical corpus: ánh xạ các `log_signatures` thô thông qua cùng một template engine như ở Layer 1.
- Similarity (Độ tương đồng) = 0.50 × log Jaccard + 0.35 × trace-edge Jaccard + 0.15 × service affinity (tổng = 1.0).
- OOD detection: best_sim < 0.15 → buộc phải escalate.
- Outcome-weighted voting (Bầu chọn dựa trên outcome): success=1.0, partial=0.5, failed=0.0.

### Layer 3 — decision.py
- Hybrid score = 0.7 × normalised vote share + 0.3 × normalised EV.
- EV (Expected Value) = P_success × (1 - blast×0.08) - cost×0.02; với page_oncall: bị phạt thêm (penalty) -0.15.
- Blast-radius gate (Cổng chặn rủi ro): Nếu blast > 1 và P_success < 0.40 → skip (bỏ qua).
- Trường hợp đặc biệt: Nếu OOD → page_oncall; Nếu có conflicting evidence → restart_pod trên trace service.

## Danh sách tệp tin (Files)

| File | Purpose (Mục đích) |
|---|---|
| `engine.py` | CLI entry point |
| `features.py` | Layer 1: raw incident → feature vector |
| `retrieval.py` | Layer 2: kNN + outcome-weighted voting |
| `decision.py` | Layer 3: EV + blast-radius gate |
| `optional_helpers.py` | Các parser helpers được cung cấp sẵn |
| `actions.yaml` | Action catalog (được cung cấp, không chỉnh sửa) |
| `incidents_history.json` | Historical corpus (được cung cấp) |
| `audit.jsonl` | Quyết định của engine trên E01–E08 |
| `FINDINGS.md` | Tài liệu phản ánh và giải thích các lựa chọn thiết kế |
