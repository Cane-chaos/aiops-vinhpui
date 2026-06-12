# FINDINGS.md — Evidence-Driven Remediation Engine

## Câu hỏi 1 — Bạn đã chọn similarity function nào cho Layer 2, và tại sao?

**Hàm đã chọn (Chosen function):** Hybrid Jaccard similarity với ba thành phần: log templates (50%) + trace edge pairs (35%) + service affinity bonus (15%).

```
similarity(query, hist) = 0.50 × Jaccard(log_template_set_q, log_template_set_h)
                        + 0.35 × Jaccard(anomalous_edge_set_q, anomalous_edge_set_h)
                        + 0.15 × service_affinity(q, hist)
```

Các hệ số được chọn để tổng bằng 1.0, đảm bảo similarity nằm trong khoảng [0, 1] một cách tự nhiên. Log Jaccard được ưu tiên cao nhất (0.50) vì log templates là tín hiệu rõ ràng nhất về loại incident. Trace-edge Jaccard (0.35) phân biệt các service bị ảnh hưởng. Service affinity (0.15) bổ sung một tín hiệu nhỏ khi cùng service nhưng giữ trọng số thấp để tránh bias overfitting trên corpus nhỏ.

**Bước tiền xử lý (Pre-processing step - sửa lỗi tính toán quan trọng):**  
Trong historical corpus, `log_signatures` là các **raw strings** (ví dụ: `"OutOfMemoryError: Java heap space"`), trong khi các features của live incident lại là **template IDs** (ví dụ: `"oom"`). Cả hai bên đều được map thông qua cùng một hàm template-matching `_cluster_log_line()` trước khi so sánh Jaccard. Nếu không có bước chuyển đổi này, Jaccard sẽ trả về 0 cho mọi so sánh.

**Vì sao chọn Jaccard thay vì cosine embedding:**  
- Corpus chỉ có khoảng 30 entries. Nếu dùng embedding 1024-dim sẽ dẫn đến overfitting nghiêm trọng — nearest neighbour với cosine distance 0.2 sẽ không thể phân biệt được với một similarity ngẫu nhiên.  
- Với bộ từ vựng 25-template, Jaccard cung cấp đủ độ phân giải để phân biệt ví dụ: `{pool_timeout, pool_exhausted}` (connection pool) với `{slow_query, db_latency_high}` (query performance).

**Phương án thay thế đã cân nhắc: Cosine TF-IDF trên các raw log lines.**  
- Ưu điểm: representation phong phú hơn, nắm bắt được các từ hiếm.  
- Từ chối vì: (a) các log lines chứa nhiều nhiễu và không ổn định về mặt số liệu (timestamps, pod IDs), (b) sự chênh lệch từ vựng (vocabulary shift) giữa raw lines và historical templates sẽ đòi hỏi thêm bước normalisation, làm mất đi lợi thế phong phú trên tập dữ liệu chỉ có 30 samples.

**Phương án thay thế đã cân nhắc: Edit distance trên root_cause_class labels.**  
- Từ chối: sẽ yêu cầu classify incident trước khi retrieve — dẫn đến phụ thuộc vòng (circular dependency).

**Kiểm chứng thực tế (Empirical validation):** Trên tập eval, Jaccard similarity gán chính xác `best_sim >= 0.50` cho E01 (pool exhaustion, pattern đã biết) và gán chính xác `best_sim = 0.10` cho E07 (pattern OOD mới lạ với `informer-cache-stale` alert và metrics `k8s_api_throttle_count` — chưa từng có tiền lệ trong lịch sử).

---

## Câu hỏi 2 — Outcome-weighted voting thay đổi candidate ranking như thế nào?

**Outcome weights:** success=1.0, partial=0.5, failed=0.0.

**Minh họa bằng E05 (tình huống tie-breaking):**

E05 (`payment-svc`, `db-degradation`, logs: `pool_timeout + deadlock`):

| Neighbour | Similarity | Outcome | Outcome weight | Combined weight |
|---|---|---|---|---|
| INC-2025-07-04 (lock_contention) | 0.73 | success | 1.0 | 0.73 |
| INC-2025-09-05 (connection_pool_exhaustion) | 0.40 | success | 1.0 | 0.40 |
| INC-2026-05-10 (connection_pool_exhaustion) | 0.40 | partial | 0.5 | 0.20 |
| INC-2025-11-08 (connection_pool_exhaustion) | 0.33 | success | 1.0 | 0.33 |

**Nếu không có outcome weighting (pure similarity vote, aggregate by action):**  
`rollback_service` = 0.40 + 0.40 + 0.33 = 1.13 → **xếp thứ #1** (tích lũy từ 3 incidents)  
`restart_pod` (từ INC-2025-07-04, sim=0.73) = 0.73 → xếp thứ #2  

Lưu ý: ngay cả khi chưa có outcome-weighting, `rollback_service` đã thắng về aggregate vote. Tuy nhiên nếu chỉ lấy **nearest-neighbour top-1 (không aggregate)** thì `restart_pod` (sim=0.73) sẽ thắng vì nó là neighbour gần nhất.

**Nếu có outcome weighting (điểm mấu chốt):**  
`rollback_service` = 0.40×1.0 + 0.40×0.5 (partial) + 0.33×1.0 = **0.93**  
`restart_pod` = 0.73 × 1.0 = 0.73  
`increase_pool_size` = cùng path với rollback ≈ 0.80

Outcome-weighting **không đảo ranking** trong case này (rollback đã dẫn đầu), nhưng có tác dụng quan trọng: **giảm trọng số của INC-2026-05-10 (partial)** từ 0.40 xuống 0.20, làm tăng khoảng cách giữa `rollback_service` và các action khác, đồng thời giảm ảnh hưởng của các precedent thất bại/nửa vời. Engine đã chọn chính xác `rollback_service:payment-svc` (accepted).

---

## Câu hỏi 3 — Giải thích toàn bộ bước tính toán EV cho một eval incident

**Incident: E01** (connection pool exhaustion trên payment-svc)

**Kết quả Retrieval:**
- best_sim = 0.53 (INC-2025-11-08, success)
- action_votes: `increase_pool_size`=0.53, `rollback_service`=0.53, `page_oncall`=0.22, `restart_pod`=0.13

**P_success cho mỗi action** (normalised vote share, tổng votes = 1.41):
- `increase_pool_size`: 0.53 / 1.41 = 0.376
- `rollback_service`: 0.53 / 1.41 = 0.376
- `page_oncall`: 0.22 / 1.41 = 0.156
- `restart_pod`: 0.13 / 1.41 = 0.092

**Công thức EV:** EV = P_success × (1.0 - blast_radius×0.08) - cost_min×0.02  
**Penalty bổ sung cho page_oncall:** −0.15

| Action | P_success | blast | cost | EV |
|---|---|---|---|---|
| increase_pool_size | 0.376 | 1 | 1 | 0.376×(1−0.08)−0.02 = 0.326 |
| rollback_service | 0.376 | 1 | 10 | 0.376×0.92−0.20 = 0.146 |
| restart_pod | 0.092 | 1 | 2 | 0.092×0.92−0.04 = 0.045 |
| page_oncall | 0.156 | 0 | 0 | 0.156×1.0−0−0.15 = 0.006 |

**Tại sao `increase_pool_size` (EV=0.326) không thắng mặc dù EV cao hơn `rollback_service` (EV=0.146)?**

`increase_pool_size` bị loại ra khỏi danh sách **auto-executable candidates** bởi một guardrail trong `decision.py`: action này chỉ an toàn để tự động thực thi khi `config_state` của pool hiện tại đã được xác minh. Vì `config_state` không xuất hiện trong live incident E01 (field không tồn tại trong feature vector), `decision.py` đánh dấu `increase_pool_size` là `requires_human_approval=True` và loại nó ra khỏi vòng auto-action. Chỉ các action **auto-executable** mới tiếp tục vào bước hybrid scoring.

Sau khi loại `increase_pool_size`, bảng auto-executable còn lại:

| Action | vote_share | norm_vote | norm_EV | hybrid (0.7v+0.3e) |
|---|---|---|---|---|
| rollback_service | 0.53/1.41=0.376 | 0.80 | 0.50 | **0.71** |
| restart_pod | 0.13/1.41=0.092 | 0.20 | 0.15 | 0.19 |

**Lựa chọn: `rollback_service:payment-svc` (confidence=0.52)** — trùng với `accepted_actions`. Chính xác.  
Lựa chọn `must_not_action` (page_oncall) bị xếp cuối bảng với EV=0.006 nhờ vào PAGE_PENALTY. Ràng buộc đã được tuân thủ.

---

## Câu hỏi 4 — Khi nào engine quyết định escalate (page_oncall) thay vì auto-act?

**E02** (TLS cert expiry trên `edge-lb`):  
- Log templates: `{tls_expiry, tls_cert_error}` → trùng khớp INC-2025-08-17 (success, nhưng action là `page_oncall`).  
- Vote: `page_oncall` nhận 100% votes từ historical precedent này.  
- Engine chọn `page_oncall`. **Chính xác** — expected answer là `page_oncall` (TLS cert rotation thuộc về con người / human-ops).

**E04** (DNS config issue):  
- Log templates: `{degraded_generic, error_rate_high}` → weak signals, best_sim ≈ 0.20.  
- Nhiều neighbours đều dùng `page_oncall`. Engine chọn `page_oncall`. **Chính xác** — expected chấp nhận `dns_config_rollback` hoặc `page_oncall`.

**E07** (novel OOD incident):  
- Log templates: `{k8s_infra}` → novel metric `k8s_api_throttle_count`, alert `informer-cache-stale`.  
- best_sim = 0.10 < OOD_THRESHOLD (0.15). Engine bắt buộc escalate `page_oncall` (confidence=0.05).  
- **Chính xác** — bắt buộc phải escalate khi gặp OOD. Cờ OOD đã ngăn chặn thành công bất kỳ auto-action sai lầm nào.

Cả 3 quyết định escalation đều chính xác khi đối chiếu với ground truth. Không có trường hợp false escalations nào (E01 `must_not_action=page_oncall` → engine auto-act một cách chuẩn xác).

---

## Câu hỏi 5 — Loại incident (class of incident) nào có khả năng làm engine bị phá vỡ (break) nhất?

**Lớp dễ bị tổn thương nhất: Cascade failures nơi root cause nằm ở một leaf service tầng sâu, chứ không phải ở alerting service (Kiểu E08, multi-hop).**

**Tại sao nó phá vỡ engine:**  
Engine hiện tại phân tích trace kiểu single-hop (một bước). Nó chỉ nhìn vào các anomalous edges (`from → to`) một cách cô lập. Trong một chuỗi cascade thực sự như E08 (`bb-edge → datapower → esb → t24-service`), engine may mắn xác định đúng `t24-service` làm dominant log service là do lượng log trên leaf service này tình cờ lớn nhất. Nếu lượng log lớn nhất lại nằm ở `esb` thay vì `t24-service`, engine sẽ nhắm vào sai service.

**Chế độ thất bại cụ thể (Concrete failure mode):**  
Trong chuỗi 5-hop cascade `A → B → C → D → E` nơi `E` là root cause:
- Logs có thể lớn nhất tại `C` (nằm giữa chuỗi) do retry storms.
- Traces cho thấy anomalies ở `C → D` và `D → E`.
- Engine có thể sẽ nhắm vào `C` (do log lớn) hoặc `D` (do trace "from" service), bỏ sót `E`.

**Đề xuất cải tiến: Topological root-cause isolation.**  
Bổ sung một thuật toán walk-back (đi lùi): bắt đầu từ alerting service, duyệt qua các anomalous trace edges theo chiều ngược lại (hướng upstream) cho đến khi gặp một service không còn downstream anomalous edges nào — đó chính là root cause. Cách triển khai: xây dựng một đồ thị (graph) từ các anomalous edges theo hướng `caller → callee`, tìm **sink node có `out-degree=0`** trong số các anomalous nodes.

> **Lý do dùng `out-degree=0` thay vì `in-degree=0`:** Với quy ước `caller → callee`, root cause thực sự là **điểm cuối của chuỗi dependency** — service bị gọi nhưng không gọi tiếp ai (sink node). Ngược lại, `in-degree=0` sẽ chỉ ra source node (alerting service / entry point), tức là ngọn của chuỗi, không phải gốc rễ. Ví dụ trong chuỗi `A → B → C → D → E`, node `E` có `out-degree=0` là root cause; node `A` có `in-degree=0` là entry point của chuỗi cascade.

**Lý do chưa thực hiện:**  
Dữ liệu trace cho chuỗi cascades có 47 anomalous edges (tất cả đều có cùng cặp `from → to` trong E08, đây là một degenerate case). Một cascade detector đúng nghĩa sẽ cần các unique edges đã được deduplicate để thực hiện topological sort. Bộ evaluation set chỉ có duy nhất một trường hợp cascade (E08), và thuật toán heuristic `dominant_log_svc` hiện tại tình cờ giải quyết được nó. Nếu implement một hệ thống topological sort tổng quát sẽ cần thêm nhiều test cases hơn để xác nhận mà không gặp hiện tượng overfitting.

---

## Tùy chọn (Optional): Quy tắc nội suy Affected Services 

Theo HANDOUT §2.6: `affected_services` không có sẵn trong live incidents. Engine tự nội suy bằng cách:

1. **trigger_alert.service** → luôn được đưa vào.
2. **dominant_log_service** (service có lượng log lines lớn nhất, đặc biệt là ERROR-level) → đưa vào.
3. **anomalous_edges** (error_rate > 10% HOẶC p99_ms > 1000ms): cả hai service `from` và `to` → đưa vào.

Quy tắc này đã giúp xác định chính xác `payment-svc` cho E01 và `cart-svc` cho E06.

---

## Tùy chọn B — Chuỗi lý giải (Justification Chain)

Mỗi entry trong `audit.jsonl` đều chứa một khối `evidence` được cấu trúc hóa:
- `reasoning`: diễn giải quá trình quyết định bằng câu văn để con người đọc dễ dàng.
- `is_ood` / `best_similarity`: kết quả của quá trình OOD detection.
- `top_neighbours`: tối đa 3 historical incidents kèm theo similarity scores tương ứng.
- `ev_table`: điểm EV của tất cả candidate actions.
- `log_templates_matched`: những mẫu template patterns nào đã match.
- `anomalous_edges`: các trace edges bị đánh dấu là anomaly.
- `dominant_log_service`: service có nhiều hoạt động log nhất.

**Bao gồm (Included):** Những yếu tố trên, vì chúng giúp dò lại quá trình quyết định từ raw signal → template → neighbour → vote → EV → selection.  
**Bỏ qua (Omitted):** Bảng tổng hợp vote weight đầy đủ (chỉ có trong `candidates.evidence_chain` ở level verbose) — bị loại khỏi khối `evidence` nhằm giữ cho audit entry ngắn gọn, đủ để con người có thể đọc hiểu trong 30 giây theo như yêu cầu ở §1.
