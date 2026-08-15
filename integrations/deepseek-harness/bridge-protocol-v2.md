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
    "page": "market",
    "pageTitle": "行情",
    "asOf": "2026-08-15T11:10:21+08:00",
    "selectedSecurity": {
      "code": "600519",
      "name": "贵州茅台",
      "price": 1341.99,
      "pct": -0.98,
      "officialDisclosures": []
    },
    "market": {
      "dataDate": "2026-08-14",
      "temperature": 42,
      "phase": "修复期",
      "degraded": false,
      "riskSignals": [],
      "sourceVerification": {
        "tdxLocal": {
          "status": "ok",
          "fieldsAvailable": 9,
          "readOnly": true
        }
      }
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
