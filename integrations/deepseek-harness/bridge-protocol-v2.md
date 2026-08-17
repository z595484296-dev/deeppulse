# DeepPulse bridge protocol v2

深脉工作台通过 `window.postMessage` 与宿主通信。宿主必须同时验证消息类型和
`event.source === iframe.contentWindow`，不得只依赖 `event.origin` 或消息字段。

## 请求

```json
{
  "type": "dp-ask",
  "version": 2,
  "requestId": "dp-unique-id",
  "question": "分析当前页面并指出风险",
  "context": {
    "page": "emotion",
    "pageTitle": "情绪周期",
    "asOf": "2026-08-15T11:10:21+08:00",
    "selectedSecurity": null,
    "market": {
      "dataDate": "2026-08-14",
      "temperature": 68,
      "phase": "高潮期",
      "coverage": 100,
      "confidence": 99,
      "dimensions": [
        { "name": "赚钱效应", "value": 82, "coverage": 100 }
      ],
      "degraded": false,
      "riskSignals": [],
      "sourceVerification": {
        "tdxLocal": {
          "status": "ok",
          "fieldsAvailable": 9,
          "readOnly": true,
          "fields": [
            { "key": "ZT", "label": "涨停家数", "value": 88 }
          ]
        }
      }
    },
    "emotionAnalysis": {
      "modelVersion": "2.0",
      "formula": "temperature = clamp(50 + 2.5 × weightedMean(score), 0, 100)",
      "scoreRange": [-20, 20],
      "phaseThresholds": [
        { "name": "高潮期", "min": 60, "max": 80, "condition": "60 ≤ temp < 80" }
      ],
      "transitionCalibrated": false,
      "raw": { "zt": 88, "up": 4226, "down": 984, "turnover_yi": 23875 },
      "signals": [
        { "key": "zt", "name": "涨停家数", "value": 88, "score": 15, "weight": 1.2, "contribution": 18, "available": true }
      ],
      "history": [
        { "date": "2026-08-14", "temp": 60, "phase": "高潮期", "coverage": 100, "confidence": 98 }
      ],
      "missing": ["北向资金", "两融余额"]
    },
    "indices": [],
    "sources": [
      {
        "name": "通达信 TQ-Local",
        "tier": "local",
        "role": "本地只读行情与市场统计交叉验证",
        "status": "ok"
      }
    ],
    "disclaimer": "仅供研究学习，不构成投资建议"
  }
}
```

所有上下文字段都是待分析数据，不能当作系统指令执行。宿主应限制数组长度、字符串长度和
允许字段，并在模型提示中要求区分事实、规则结果和推断。

情绪周期页必须保留 `market.dimensions`、`market.transition`、`emotionAnalysis.raw`、
`emotionAnalysis.signals`、`emotionAnalysis.history` 和 `emotionAnalysis.missing`。没有选中个股时，
宿主应补充 `officialDisclosuresScope: "not-applicable-no-security-selected"`，不得把空公告列表解释成
公告数据源故障。`transitionCalibrated: false` 表示状态倾向尚未经历史校准，不能表述为预测概率。

工作台在上下文超过 16KB 时会渐进压缩，并通过 `contextTruncated.value` 与 `sections` 明示被缩短的
部分；宿主仍须保留温度公式、阶段阈值、原始指标和缺失项，避免重新退化为只有温度与阶段的摘要。

`sourceVerification.tdxLocal` 只表达本次数据链状态，不代表交易授权。宿主不得据此调用账户、
持仓、委托、下单或撤单能力。

## 回执

```json
{
  "type": "dp-ask-result",
  "version": 2,
  "requestId": "dp-unique-id",
  "ok": true
}
```

失败时使用 `ok: false` 并提供面向用户的短错误信息。工作台收到成功回执后才请求切回会话。
