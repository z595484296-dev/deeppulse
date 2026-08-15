# DeepSeek Harness integration

深脉可以独立运行，也可以由 DeepSeek Harness 作为一级工作台承载。当前集成是一个明确的
宿主适配器协议，不宣称已经存在标准插件市场或无需宿主改动的安装格式。

## 宿主职责

1. 将 `web/` 发布为同源静态路径，例如 `/deeppulse/`。
2. 检查 `http://127.0.0.1:8971/api/health`，需要时从项目目录启动
   `python server.py`。
3. 在侧栏注册深脉入口，并在会话视图与工作台 iframe 之间切换。
4. 只接受来自该 iframe `contentWindow` 的桥接消息。
5. 验证 `dp-ask` 的字段、长度和类型，只把白名单上下文送入模型。
6. 会话成功接收请求后返回 `dp-ask-result`；失败时保持工作台打开。
7. 只停止由宿主自己创建的深脉服务进程。

## 静态资源同步

宿主可以在构建时将本仓库 `web/` 的内容复制到自己的公共目录。不要复制 `data/`，因为其中
可能包含本机配置、日志和用户数据。

## 协议

完整消息格式见 [bridge-protocol-v2.md](bridge-protocol-v2.md)。

若未来 Harness 提供正式插件清单和生命周期 API，本适配器应迁移为独立安装包；在此之前，
升级宿主适配器仍可能需要重新构建 Harness。

