# W1-D1 Assignment — Metric Anomaly Detection

## 1. Dataset

Dataset used: `realKnownCause/machine_temperature_system_failure.csv` from NAB.

This dataset contains a univariate time series with two columns: `timestamp` and `value`. The metric represents machine temperature over time. The sampling interval is 5 minutes, so:

* 1 hour = 12 data points
* 1 day = 288 data points
* 1 week = 2016 data points

Ground truth labels were loaded from NAB `combined_windows.json`. Each anomaly is represented as an anomaly window, and all points inside the window were labeled as anomaly.

## 2. Phase 1 — EDA Summary

The histogram and density plot show that the data is not Gaussian. The skewness value is `-1.8`, which means the distribution is heavily left-skewed. Most values are concentrated in the higher temperature range, while some lower values create a long left tail.

The rolling mean and rolling standard deviation are not constant over time. This shows that the time series is not fully stationary. A global mean and global standard deviation would not represent the whole dataset well.

The ACF plot does not show a clear peak at lag `288` or `2016`. Since the data interval is 5 minutes, lag `288` would represent a daily period and lag `2016` would represent a weekly period. Because there is no clear repeated peak at these lags, the dataset does not have strong daily or weekly seasonality.

Conclusion: the data is a univariate time series, heavily left-skewed, not fully stationary, and without strong seasonality.

## 3. Detector Choice

### Detector 1 — IQR

IQR was selected as the statistical detector because the data is heavily skewed. Rolling Z-score or global 3σ is not suitable because those methods depend on mean and standard deviation, which can be distorted by skewed distributions and outliers.

STL was not selected because the ACF plot did not show a clear seasonal period.

### Detector 2 — Isolation Forest

Isolation Forest was selected as the ML detector. Before training, I created a feature table with time-series context features, including:

* current value
* rolling mean
* rolling standard deviation
* rolling median
* rate of change
* lag features
* hour of day
* day of week
* rolling z-score

These features help Isolation Forest detect anomaly based on local context instead of only raw values.

## 4. Detector Comparison

| Detector                      |   Precision |   Recall |       F1 |   False Alarms |   Detected Anomalies |   TP |   FP |   FN |    TN |
|:------------------------------|------------:|---------:|---------:|---------------:|---------------------:|-----:|-----:|-----:|------:|
| Detector 1 - IQR              |    0.58007  | 0.587743 | 0.583881 |            965 |                 2298 | 1333 |  965 |  935 | 19462 |
| Detector 2 - Isolation Forest |    0.662801 | 0.327601 | 0.438477 |            378 |                 1121 |  743 |  378 | 1525 | 19785 |

IQR detected more anomalies and achieved higher recall and F1-score. Isolation Forest achieved higher precision, meaning that when it predicted anomaly, it was more likely to be correct. However, its recall was much lower, meaning that it missed more true anomaly points.

## 5. Tuning Logs

### IQR Tuning

|   k |   Precision |   Recall |       F1 |   False Alarms |   Detected Anomalies |
|----:|------------:|---------:|---------:|---------------:|---------------------:|
| 1   |    0.483284 | 0.643739 | 0.552089 |           1561 |                 3021 |
| 1.5 |    0.58007  | 0.587743 | 0.583881 |            965 |                 2298 |
| 2   |    0.657126 | 0.485891 | 0.558682 |            575 |                 1677 |

Observation:

Lower `k` makes IQR more sensitive and detects more anomalies, but it may increase false alarms. Higher `k` makes the detector more conservative and reduces false alarms, but it may miss true anomalies.

### Isolation Forest Tuning

|   contamination |   precision |    recall |       f1 |   detected_anomalies |
|----------------:|------------:|----------:|---------:|---------------------:|
|            0.01 |    0.995556 | 0.0987654 | 0.179703 |                  225 |
|            0.02 |    0.939866 | 0.186067  | 0.310637 |                  449 |
|            0.05 |    0.662801 | 0.327601  | 0.438477 |                 1121 |

Observation:

Lower contamination makes Isolation Forest more conservative and detects fewer anomalies. Higher contamination makes it more sensitive and detects more anomalies, but it can also increase false alarms.

## 6. Reflection

The dataset is heavily left-skewed with skewness = `-1.8`, so it is not Gaussian. The rolling mean and rolling standard deviation change over time, so the data is not fully stationary. The ACF plot does not show a clear peak at daily or weekly periods, so the dataset does not have strong seasonality.

Because of this EDA result, I selected IQR as the statistical detector. IQR is robust to skewed data and outliers because it uses percentiles instead of mean and standard deviation.

I also implemented Isolation Forest as the ML detector. Isolation Forest used engineered time-series features, including rolling statistics, lag features, and rate of change. This gives the model more context than raw value alone.

Based on the result, IQR performed better overall because it achieved a higher F1-score and higher recall. Isolation Forest had higher precision but much lower recall, meaning it missed many true anomalies.

In AIOps, recall is usually very important because missing a real anomaly can delay incident detection and increase system impact. Therefore, for this dataset, I would choose IQR or rolling IQR as the main production detector. Isolation Forest can be used as a secondary detector or as a filter to reduce false alarms, but it needs more tuning and better feature engineering to improve recall.

## 7. Submitted Files

* `assignment.ipynb`
* `SUBMIT.md`
* `anomaly_detection_comparison.png`
* `isolation_forest_machine_temperature.joblib`
