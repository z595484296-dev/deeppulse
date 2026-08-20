# DeepPulse bridge protocol v3

v3 在 v2 的“交给 DeepSeek”单向分析请求之外，增加一条会把完整回答送回工作台的生成链路。
`dp-ask` / `dp-ask-result` v2 保持兼容，来源校验、16KB 上下文限制和字段白名单仍以
[v2 协议](bridge-protocol-v2.md)为准。

## 生成请求

```json
{
  "type": "dp-generate",
  "version": 3,
  "requestId": "dp-unique-id",
  "question": "生成 2026-08-20 的情绪周期复盘，直接输出正文",
  "context": {
    "page": "strategy",
    "intent": "strategy-calendar-review-fill",
    "asOf": "2026-08-20T15:00:00+08:00",
    "market": {},
    "emotionAnalysis": {},
    "sources": []
  }
}
```

宿主复用 v2 的白名单规范化与可信来源提示。它先记录当前会话序号，再提交提示词，随后等待该
提示词自己的 `user` 节点落地，并只收集它之后同一轮的 `assistant` 文本。生成提示要求模型用
`<deeppulse_fill>` 与 `</deeppulse_fill>` 包住最终正文：闭合标签一旦出现在已完成的 assistant
消息中，宿主即可提取标签内正文并回填，无需等待该轮后续的深度分析；若模型没有返回标签，则
继续等待对应 `turn/end` 作为兼容兜底。这样既不会误取上一轮内容，也不会因无关的后续分析拖延
已经完整的编辑框正文。

## 生成结果

```json
{
  "type": "dp-generate-result",
  "version": 3,
  "requestId": "dp-unique-id",
  "ok": true,
  "reply": "## 今日复盘\n..."
}
```

失败时使用 `ok: false` 和不超过 500 字的 `error`。宿主与工作台都必须按 `requestId` 关联，忽略
未知或已经超时的结果；正文上限为 16KB。工作台只把 `reply` 放入对应编辑框，不自动调用保存。

## 失败和降级

- 没有当前 Harness 会话时，应提示用户先打开会话。
- 模型错误、中止或 180 秒内未完成时，返回明确失败原因。
- 深脉独立运行时继续使用自己的 `/api/chat`；嵌入 Harness 但生成失败时也可以尝试这一备用链。
- 自动保存、下单或把数据源状态解释为交易授权均不属于本协议。
