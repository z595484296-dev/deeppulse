# DeepSeek Harness integration

深脉可以独立运行，也可以由 DeepSeek Harness 作为一级工作台承载。当前集成是一个明确的
宿主适配器协议，不宣称已经存在标准插件市场或无需宿主改动的安装格式。

## 宿主职责

1. 将 `web/` 发布为同源静态路径，例如 `/deeppulse/`。
2. 从 8971-8980 探测 `/api/health`，只连接版本满足 `deeppulse.manifest.json` 且声明
   `capabilities.tdx_read_only=true` 的服务；需要时从项目目录启动 `python server.py`。
3. 在侧栏注册深脉入口，并在会话视图与工作台 iframe 之间切换。
4. 只接受来自该 iframe `contentWindow` 的桥接消息。
5. 验证 `dp-ask` / `dp-generate` 的字段、长度和类型，只把白名单上下文送入模型；允许读取
   `sourceVerification.tdxLocal`，但不要把来源状态解释为交易授权。
6. 会话成功接收请求后返回 `dp-ask-result`；失败时保持工作台打开。
7. `dp-generate` 必须等待与本次请求对应的完整会话轮次结束，再通过 `dp-generate-result`
   回传正文；不得把上一轮回答或流式半成品回填，回填后也不得替用户自动保存。
8. 只停止由宿主自己创建的深脉服务进程。

## 静态资源同步

必须使用仓库根目录的 `scripts/sync-all.ps1` 同步：它会复制 `web/`、宿主适配器和测试，重建
Harness Web，并以 SHA-256 校验独立版与 `/deeppulse/` 的一致性。不要复制 `data/`，因为其中
可能包含本机配置、日志和用户数据。`-VerifyOnly` 用于发布前只读复核。

## 协议

完整消息格式见 [bridge-protocol-v3.md](bridge-protocol-v3.md)；旧的只发送分析请求仍兼容
[bridge-protocol-v2.md](bridge-protocol-v2.md)。

通达信的安装检查、只读白名单与降级链见 [`../tdx-tq-local/README.md`](../tdx-tq-local/README.md)。

宿主适配器的源文件保存在 `app/`，由同步脚本写入 Harness；任何宿主改动都必须重新构建 Harness。
