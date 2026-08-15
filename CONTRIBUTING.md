# Contributing

感谢你改进深脉 DeepPulse。

## 开发检查

项目运行时只使用 Python 标准库。提交前请运行：

```bash
python -m py_compile server.py emotion.py
python -m unittest discover -s tests -v
```

前端是原生 JavaScript，可使用 Node.js 做语法检查：

```bash
find web -type f -name '*.js' -print0 | xargs -0 -n1 node --check
```

请不要提交 `data/`、API 密钥、日志、个人自选列表或本机绝对路径。新增数据源时，应说明
来源等级、用途、失败行为和数据时间，不得把聚合内容标成官方披露。

