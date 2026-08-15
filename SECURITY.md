# Security Policy

## 敏感数据

深脉会在运行时生成 `data/config.json`、日志、历史快照和端口文件。`data/` 已被 Git
忽略，请勿强制提交。API 密钥只能放在本机运行时配置或环境变量中。

## 报告问题

请通过 GitHub Security Advisory 私下报告可能导致凭据泄露、任意代码执行、跨源消息伪造
或本地文件暴露的问题。普通缺陷可以使用 GitHub Issues。

