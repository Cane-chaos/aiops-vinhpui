# Detection Approach — DESIGN.md

## Approach tôi dùng
Rule-based Threshold Detection kết hợp với Log Pattern Matching.

## Tại sao chọn approach này
Trong bối cảnh streaming data có dạng chuỗi thời gian (time-series) liên tục với khoảng giá trị cơ sở (baseline) đã biết tương đối ổn định (có nhiễu nhỏ và chu kỳ ngày đêm), việc sử dụng các ngưỡng cố định (static thresholds) là cách tiếp cận đơn giản, ít tốn kém tài nguyên tính toán nhất mà vẫn đảm bảo độ chính xác cao và TTD (Time To Detect) thấp. Đồng thời, kết hợp với các log ở mức `ERROR` hay `WARN` đặc trưng của mỗi loại lỗi sẽ tránh được các cảnh báo giả (false positives).

## Cách hoạt động
Pipeline liên tục trích xuất các metric `memory_usage_bytes`, `http_requests_per_sec`, `upstream_timeout_rate` từ payload nhận được và đối chiếu với các ngưỡng tối đa được định nghĩa sẵn. Ngoài ra, pipeline quét qua mảng `logs` trong payload để tìm các keyword đặc trưng:
- Báo động **memory_leak** khi `memory_usage_bytes > 1GB` hoặc thấy log chứa `OutOfMemoryWarning` / `GC pause exceeded`.
- Báo động **traffic_spike** khi `http_requests_per_sec > 250` hoặc thấy log `Queue depth high` / `Request rejected`.
- Báo động **dependency_timeout** khi `upstream_timeout_rate > 2.0%` hoặc thấy log `Circuit breaker OPEN`.

## Parameters tôi chọn
- **Memory Leak**: Threshold `1,000,000,000` (1GB). Lý do: Baseline RAM là khoảng 800MB ± 20MB. Mức 1GB vượt đủ xa mức dao động bình thường này, giúp phát hiện sớm leak mà không bị nhiễu.
- **Traffic Spike**: Threshold `250` req/s. Lý do: RPS cơ sở cao nhất vào giờ cao điểm là khoảng `120 * 1.4 = 168` req/s, cộng thêm nhiễu ±10 req/s. Việc chọn ngưỡng `250` đủ xa đỉnh sinh lý để chắc chắn đó là một đợt spike (vì lỗi bơm lưu lượng lên gấp nhiều lần).
- **Dependency Timeout**: Threshold `2.0%`. Lý do: Baseline timeout rate cao nhất là khoảng `0.1 + 0.1 = 0.2%`. Việc tăng lên `2.0%` (hoặc cao hơn do lỗi bơm lên rất nhanh) là dấu hiệu rõ ràng của timeout.

## Cải thiện nếu có thêm thời gian
- Áp dụng các thuật toán **Moving Average** (SMA, EMA) để mượt mà hóa biểu đồ và sử dụng **Z-Score** để tìm các điểm bất thường tự động dựa trên độ lệch chuẩn thay vì ngưỡng cứng, giúp thích ứng tốt hơn nếu baseline có thay đổi lớn theo mùa vụ.
- Thiết lập cơ chế chống spam (Debouncing / Throttling) để chỉ fire alert một lần duy nhất cho một sự cố kéo dài, tránh file alert bị phình to bởi các cảnh báo trùng lặp.
