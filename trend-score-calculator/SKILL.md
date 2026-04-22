---
name: trend-score-calculator
description: >
  计算 A 股 ETF 标的的 Trend Score（趋势值）。
  当用户要求计算某个 ETF 的趋势值、trend score、趋势评分，或分析某个标的的趋势状态时触发使用。
  支持给定标的代码后计算过去 N 个交易日的趋势值，默认过去 5 个交易日。
  默认参数：MA 短周期=5，中周期=10，长周期=20，ATR 周期=20，其他权重与项目默认一致。
  数据源优先级：iFinD (同花顺) > efinance (东方财富) > akshare > 本地 parquet。
---

# Trend Score Calculator

## 用途

给定一个 A 股 ETF 标的代码，计算该标的过去 N 个交易日的 Trend Score 并输出表格。

## 触发场景

- "计算 510330 的趋势值"
- "看一下 512500 最近 5 天的 trend score"
- "算一下这个 ETF 的趋势评分"
- "分析一下 563300 的趋势状态"

## 使用方法

### 1. 执行计算脚本

```bash
python .agents/skills/trend-score-calculator/scripts/calculate_trend_score.py <symbol> [days]
```

参数：
- `symbol`: 标的代码，如 `510330.SS`、`512500.SS`
- `days`: 计算过去多少个交易日，默认为 5

示例：
```bash
python .agents/skills/trend-score-calculator/scripts/calculate_trend_score.py 510330.SS 5
```

### 2. iFinD 账号配置（可选）

如需优先使用同花顺 iFinD 数据，设置环境变量：

```bash
$env:IFIND_USERNAME = "你的账号"
$env:IFIND_PASSWORD = "你的密码"
```

或在脚本中直接传入（修改 `fetch_data_ifind` 调用处的参数）。

### 3. 解析输出

脚本输出格式（制表符分隔）：
```
SYMBOL=510330.SS
DAYS=5
SOURCE=local
ROWS=2723
<date>\t<close>\t<trend_score>\t<price_direction>\t<confidence>\t<atr>\t<ma10>
```

将结果整理为 Markdown 表格呈现给用户。

## 计算公式

Trend Score = Price Direction × Confidence

### Price Direction
- Bias_n = (Close - MA_n) / ATR
- Slope_n = (EMA_n(今日) - EMA_n(昨日)) / (ATR × n)
- norm_bias = tanh(bias_mix / 2) × 100
- norm_slope = tanh(slope_mix) × 100
- Price Direction = 0.5 × norm_bias + 0.5 × norm_slope

### Confidence
- Volume Factor: vol_ratio / 3（上限为 1）
- ER（效率比率）: 10 日
- Confidence = volume_factor^0.3 × er^0.7

## 默认参数

| 参数 | 默认值 |
|------|--------|
| n_short (MA短周期) | 5 |
| n_mid (MA中周期) | 10 |
| n_long (MA长周期) | 20 |
| atr_period | 20 |
| w_bias_short/mid/long | 0.4 / 0.4 / 0.2 |
| w_slope_short/mid/long | 0.4 / 0.4 / 0.2 |
| w_bias_norm / w_slope_norm | 0.5 / 0.5 |
| vol_ma_period | 20 |
| er_period | 10 |
| w_vol / w_er | 0.3 / 0.7 |

## 数据来源（按优先级）

1. **iFinD (同花顺)** — 需账号登录，数据质量最佳
2. **efinance (东方财富)** — 免费，无需账号
3. **akshare** — 免费，无需账号
4. **本地 parquet** — 本项目 `data/market/etf/{symbol}.parquet`

自动降级：若高优先级源获取失败，自动尝试下一级。
