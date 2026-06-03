# Monthly Observability Cost Estimate

> All figures are USD/month estimates based on self-hosted AWS infrastructure.
> Datadog SaaS estimate = build_total × tier multiplier (Small 4×, Medium 3×, Large 2.5×).

| Tier | Metric Storage | Log Hot Storage | Log Cold Storage | Trace Storage | Kafka | Stream Processing | Network | Build Total | Datadog Saas Estimate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Small | $777.60 | $525.00 | $34.50 | $120.00 | $294.99 | $108.00 | $27.00 | $1,887.09 | $7,548.36 |
| Medium | $7,776.00 | $5,250.00 | $345.00 | $1,200.00 | $2,949.91 | $324.00 | $270.00 | $18,114.91 | $54,344.74 |
| Large | $77,760.00 | $52,500.00 | $3,450.00 | $12,000.00 | $29,499.12 | $3,240.00 | $2,700.00 | $181,149.12 | $452,872.80 |

## Cost Assumptions

- **Metric storage**: $0.003/million events/month (VictoriaMetrics on EC2)
- **Log hot storage**: $1.5/GB/month — 7-day retention (Loki/OpenSearch SSD)
- **Log cold storage**: $0.023/GB/month — 30-day retention (S3 Standard)
- **Trace storage**: 10% of log volume × $0.8/GB/month
- **Kafka**: $0.5/MBps/month (MSK self-hosted)
- **Flink**: $0.15/vCPU-hour, 1 vCPU per 500K events/sec
- **Network**: 20% of log volume × $0.09/GB egress

## Build vs Buy Summary

| Tier   | Build/Month | Datadog SaaS/Month | Savings |
| ------ | ----------- | ------------------ | ------- |
| Small  | $  1,887.09 | $          7,548.36 | $5,661.27 |
| Medium | $ 18,114.91 | $         54,344.74 | $36,229.83 |
| Large  | $181,149.12 | $        452,872.80 | $271,723.68 |
