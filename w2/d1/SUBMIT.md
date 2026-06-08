# SUBMIT — W2D1: Alert Correlation

**Tên:** *(điền tên)*
**Ngày:** 2026-06-12

---

## 7.3 — Design Decisions

### 1. Tại sao chọn `gap_sec = 120`?

Chọn `gap_sec = 120` (2 phút) vì toàn bộ 20 alert trong dataset xảy ra trong khoảng ~6.5 phút (09:42:01 → 09:48:30). Gap lớn nhất giữa 2 alert liên tiếp chỉ là **49 giây** (a-0015 → a-0016 và a-0017 → a-0018). Với gap_sec = 120, tất cả alert thuộc cùng 1 session — phản ánh đúng thực tế rằng đây là **một incident duy nhất** (payment-svc pool exhaustion) lan ra nhiều service. Nếu dùng gap nhỏ hơn (ví dụ 30s), ta có thể cắt sai 1 incident thành nhiều session riêng biệt, gây confusion khi on-call engineer phải switch context giữa nhiều cluster thực ra là cùng root cause.

### 2. Tại sao chọn `max_hop = 2`?

Chọn `max_hop = 2` vì cascade failure thường lan qua 1-2 hop trên service graph: service gốc bị lỗi → service gọi nó bị ảnh hưởng → load balancer phía trước cũng bị kéo theo. Ví dụ: `payment-svc` → `checkout-svc` (1 hop) → `edge-lb` (2 hop). Với max_hop = 2, correlator gom được cả 3 service chính của cascade. Trade-off: max_hop = 2 trên graph có nhiều service trung gian (như `catalog-svc`, dù không alert) sẽ kết nối transitively cả `recommender-svc` và `search-svc` vào cluster chính qua đường `edge-lb → catalog-svc → recommender-svc`. Điều này chấp nhận được vì false positive (gom thêm alert không liên quan) ít nguy hiểm hơn false negative (miss alert liên quan) trong incident response.

### 3. Alert ID nào bị "miss"?

Không có alert nào bị miss hoàn toàn — tất cả 20 alert đều thuộc 1 cluster. Tuy nhiên, **a-0013** (`recommender-svc|cpu_utilization|warn`) là alert **unrelated** — nó do batch retrain gây ra (label ghi rõ `"note": "unrelated — concurrent batch retrain"`), không phải do payment-svc cascade. Correlator vẫn gom nó vào cluster chính vì `recommender-svc` cách `edge-lb` chỉ 2 hop qua `catalog-svc` trên undirected graph. Đây là **false positive** — limitation của topology grouping khi chỉ dùng graph distance mà không xét causality direction hay metric semantic.

Tương tự, **a-0016** (`search-svc|catalog_db_query_time_ms|warn`) cũng là noise (label: `"independent slow query"`), nhưng `search-svc` cách `edge-lb` chỉ 1 hop nên không thể tách ra bằng topology alone.

### 4. Nếu có 10,000 alert, code chậm ở đâu?

Bottleneck chính là **topology_group()**: với N service unique có alert, ta chạy `O(N²)` cặp shortest-path computation. Mỗi lần gọi `nx.shortest_path_length()` trên undirected graph là `O(V + E)` (BFS). Tổng = `O(N² × (V + E))`. Với N = hàng trăm service, V ~ 1000 node, E ~ 5000 edge → rất chậm.

Cách cải thiện:
- **Pre-compute all-pairs shortest path** bằng `nx.all_pairs_shortest_path_length(G, cutoff=max_hop)` — chạy 1 lần O(V × (V+E)), cache kết quả.
- Dùng **BFS radius** thay vì all-pairs: với mỗi service có alert, BFS ra max_hop, rồi merge bằng Union-Find.
- **Batch processing** bằng time-window trước → giảm N per batch.

---

## 8. EOD Checkpoint

### Câu 1: Vì sao fingerprint không include timestamp hay value?

Fingerprint = `service|metric|severity`. Nếu include **timestamp**, mỗi lần alert fire sẽ tạo fingerprint mới → dedup sẽ **không bao giờ match** được → alert storm vẫn tràn ngập. Ví dụ: `payment-svc|latency_p99_ms|crit` fire 3 lần (a-0003, a-0008, a-0015) — nếu có timestamp, cả 3 sẽ là unique, mất luôn dedup layer.

Nếu include **value**, alert dao động quanh threshold (value = 0.99 lần này, 1.00 lần sau) sẽ tạo fingerprint khác → miss dedup. Ví dụ: a-0002 (`db_pool = 0.99`) và a-0011 (`db_pool = 1.00`) sẽ không match, dù cùng nói "pool gần cạn".

### Câu 2: Sự khác biệt giữa "duplicate" và "correlated" alert?

- **Duplicate**: Cùng fingerprint — cùng service, cùng metric, cùng severity. Chỉ khác timestamp/value. Ví dụ: a-0003 và a-0008 và a-0015 đều là `payment-svc|latency_p99_ms|crit` — duplicate vì cùng 1 alert fire lặp lại.

- **Correlated**: Khác fingerprint nhưng liên quan nhân quả. Ví dụ: a-0002 (`payment-svc|db_connection_pool_used_ratio|crit`) và a-0006 (`checkout-svc|downstream_payment_error_rate|crit`) — 2 alert khác service, khác metric, nhưng correlated vì payment-svc pool exhaustion → checkout-svc thấy downstream error tăng.

### Câu 3: `gap_sec = 30` vs `gap_sec = 600`

- **`gap_sec = 30`**: Cắt thành 3+ session — tách các alert cách nhau >30s thành incident riêng. Risk: **over-split** — 1 incident bị xé thành nhiều cluster nhỏ, on-call phải tự ghép lại mentally.

- **`gap_sec = 600`**: Gom gần như tất cả alert trong 10 phút vào 1 session. Risk: **under-split** — 2 incident thật sự riêng biệt xảy ra trong 10 phút bị gom chung, gây confusion về root cause.

### Câu 4: Recommender-svc có bị gom vào cluster chính không?

**Có.** Correlator gom `recommender-svc` (a-0013) vào cluster chính vì:
1. **Time-window**: a-0013 xảy ra lúc 09:45:10, nằm trong session chính (gap < 120s).
2. **Topology**: `recommender-svc` cách `edge-lb` chỉ **2 hop** trên undirected graph (edge-lb → catalog-svc → recommender-svc), nên Union-Find merge chúng.

Đây là **false positive** — `recommender-svc` alert do batch retrain (ghi rõ trong label), hoàn toàn independent khỏi payment-svc cascade. Correlator không phân biệt được vì nó **chỉ dùng structural proximity (graph distance)**, không xét **causal direction** hay **metric semantics**. Muốn fix, cần thêm layer: ví dụ semantic similarity (kiểm tra metric name có liên quan không: `cpu_utilization` vs `latency_p99_ms` → không liên quan → tách ra).

### Câu 5: Limitation lớn nhất của topology grouping

**Limitation**: Topology grouping chỉ dùng **graph distance** (structural proximity) mà **bỏ qua chiều của dependency** (causality direction) và **thời gian lan truyền** (propagation timing). Kết quả: bất kỳ 2 service nào cách ≤ max_hop đều bị gom, dù alert hoàn toàn unrelated (ví dụ recommender-svc batch retrain bị gom với payment-svc cascade).

**Đề xuất khắc phục**: Kết hợp **causal scoring** — khi 2 service cùng alert, kiểm tra chiều dependency: nếu A gọi B và A alert *sau* B → khả năng cascade cao (causal score cao). Nếu A alert *trước* B nhưng A không gọi B → likely coincidence (causal score thấp). Chỉ merge khi causal score vượt threshold. Approach này kết hợp topology + temporal ordering + dependency direction, giảm đáng kể false positive.

---

## Summary

| Metric | Value |
|---|---|
| Input alerts | 20 |
| Output clusters | 1 |
| Reduction ratio | 0.95 |
| gap_sec | 120 |
| max_hop | 2 |
