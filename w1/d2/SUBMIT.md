# W1-D2 Submit — Log Mining + Parsing + Anomaly from Log

## 1. Dataset

- **Dataset:** `HDFS_2k.log` từ [Loghub HDFS](https://github.com/logpai/loghub/tree/master/HDFS)
- **Lý do chọn:** Dataset nhỏ (2000 dòng), dễ chạy, nhiều pattern lặp lại phù hợp để thử Drain3.
- **Lưu ý:** HDFS_2k sample **không có** `anomaly_label.csv`, nên precision/recall không tính đáng tin cậy. Chỉ báo cáo anomaly candidate không giám sát (3-sigma spikes).

---

## 2. Phase 1 — Drain3 Parsing

- **Tổng số dòng:** 2000
- **Số template unique** (sim_th=0.5): **48**
- **File top-10 templates:** `results/top_templates.csv`

### Top-5 Templates (thực tế từ kết quả)

| Rank | template_id | Count | % | Template (rút gọn) |
|------|------------|-------|---|---------------------|
| 1 | 1 | 286 | 14.3% | `PacketResponder <*> for block <*> terminating` |
| 2 | 2 | 286 | 14.3% | `NameSystem.addStoredBlock: blockMap updated: <*> is added to <*> size <*>` |
| 3 | 4 | 268 | 13.4% | `DataXceiver: Receiving block <*> src: <*> dest: <*>` |
| 4 | 3 | 260 | 13.0% | `PacketResponder: Received block <*> of size <*> from <*>` |
| 5 | 7 | 260 | 13.0% | `FSDataset: Deleting block <*> file <*>` |

Template 1 và 2 chiếm gần **28.6%** tổng log — phản ánh hai hoạt động chính của HDFS: kết thúc write pipeline và cập nhật block map.

---

## 3. Drain3 Tuning

| sim_th | num_templates | Nhận xét |
|--------|--------------|----------|
| 0.3 | 41 | Gộp aggressive — nhiều pattern khác nhau bị merge thành 1 |
| **0.5** | **48** | **Cân bằng — được chọn** |
| 0.7 | 916 | Tách quá chi tiết — mỗi ngày (081109 vs 081110) thành template riêng |

**Chọn:** `best_sim_th = 0.5`

**Lý do:** 0.3 gộp quá nhiều có thể mất thông tin phân biệt; 0.7 tạo 916 template (quá nhiều, phần lớn do tiền tố ngày khác nhau), gây khó phân tích. 0.5 cho ra 48 template hợp lý, phản ánh đúng các hành vi chính của HDFS.

---

## 4. Phase 2 — Template Count Time Series & Anomaly Detection

- **Window:** 5 phút
- **Detector:** 3-sigma (z-score > 3.0)

### Ảnh kết quả

- `screenshots/template_count_timeseries.png`
- `screenshots/anomaly_highlighted.png`

### Spike được phát hiện (top entries)

Có **nhiều spike** 3-sigma được phát hiện. Spike cao nhất:

| timestamp | template_id | count | mean | std | z_score | Template (rút gọn) |
|-----------|------------|-------|------|-----|---------|---------------------|
| 2008-11-10 21:00 | 7 | 18 | 0.862 | 4.607 | **5.15** | FSDataset: Deleting block |
| 2008-11-10 22:05 | 11 | 19 | 0.734 | 5.004 | **3.65** | NameSystem.delete: added to invalidSet |
| 2008-11-11 07:00 | 6 | 1 | 0.066 | 0.248 | **3.77** | DataBlockScanner: Verification succeeded |

File đầy đủ: `results/template_spikes_3sigma.csv`

---

## 5. Precision/Recall

> **Không có `anomaly_label.csv`.**
>
> "Because HDFS_2k.log does not include `anomaly_label.csv`, I could not compute reliable precision/recall. I reported unsupervised anomaly candidates (3-sigma spikes) instead."

File ghi note: `results/log_anomaly_metrics.csv`

---

## 6. Phase 3 — TF-IDF Similarity + New Template

- **File similarity matrix:** `results/template_similarity_matrix.csv`
- **File similar pairs:** `results/similar_template_pairs.csv`

### Top similar pairs

| template_id_1 | template_id_2 | similarity | Nhận xét |
|--------------|--------------|-----------|----------|
| 2 | 2 (081109 variant) | 0.9749 | Cùng pattern, khác prefix ngày |
| 9 | 9 (081109 variant) | 0.9717 | WARN DataXceiver variants |
| 6 | 6 (081109 variant) | 0.9713 | DataBlockScanner variants |

> **Nhận xét:** Các cặp giống nhau nhất đều là cùng template nhưng Drain3 chia ra do prefix ngày (081109 vs 081110) với sim_th=0.5. Điều này minh họa tại sao sim_th=0.7 tạo ra quá nhiều template.

### Inject log lạ

```
999999 235959 999999 ERROR strange-service Alien quantum failure happened in module XZ-999 with impossible_code=CHAOS123
```

| Field | Kết quả |
|-------|---------|
| Cluster ID | Template mới (khác với 48 template trước) |
| Template | `<*> <*> <*> ERROR strange-service Alien quantum failure...` |
| Change type | `cluster_created` |
| New template detected | **True** |

---

## 7. Phase 4 — Mini Log Analyzer

**Script:** `log_analyzer.py`

**Command:**
```bash
python log_analyzer.py data/HDFS_2k.log
```

**Output tóm tắt:**

```
[1] Total log lines: 2000
[2] Unique templates: 21

[3] Top-5 Templates:
  1  ID=1   286  14.3%  PacketResponder <*> for block <*> terminating
  2  ID=2   286  14.3%  NameSystem.addStoredBlock: blockMap updated...
  3  ID=4   268  13.4%  Receiving block <*> src: <*> dest: <*>
  4  ID=3   260  13.0%  Received block <*> of size <*> from <*>
  5  ID=7   260  13.0%  Deleting block <*> file <*>

[4] Spike (z > 3.0) trong 1 giờ cuối:
  Template ID=16  Recent=9  Hist Mean=1.25  Z-Score=6.19

[5] New templates trong 1 giờ cuối: None
```

Output đầy đủ: `results/log_analyzer_output.txt`

**Lưu ý:** Chỉ test với HDFS_2k vì chưa có dataset thứ 2 (BGL, Spark).

---

## 8. Reflection

Drain3 parse khá tốt với HDFS_2k vì log có nhiều cấu trúc lặp lại, chỉ khác các parameter như block id, số byte, IP hoặc thời gian. Các template xuất hiện nhiều nhất như `PacketResponder terminating` và `addStoredBlock` giúp hiểu hành vi chính của HDFS: ghi và nhân bản block.

Khi chuyển template thành time series theo cửa sổ 5 phút, log text được biến thành dữ liệu định lượng. Từ đó có thể áp dụng anomaly detection giống như metric — ví dụ 3-sigma rule phát hiện spike của `FSDataset: Deleting block` vào lúc 21:00 ngày 10/11 với z-score = 5.15, cho thấy đợt xóa block bất thường.

**Metric vs Log:**
- Metric cho biết *cái gì* đang sai (latency tăng, error rate cao, CPU cao).
- Log cho biết *vì sao* sai (block operation lỗi, connection timeout, exception).

Kết hợp metric và log giúp thu hẹp thời gian điều tra và tìm root cause nhanh hơn: khi metric anomaly xảy ra tại thời điểm T, lọc log trong khoảng ±5 phút, parse template, tìm spike, drill down parameter để xác định root cause.
