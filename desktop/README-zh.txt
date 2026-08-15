深脉 DeepPulse · DeepSeek Harness 桌面 App
==========================================

1. 双击 DeepSeekHarnessDesktop.exe。
2. App 会先连接本机 3080 端口；若端口未启动，会在相邻/上级目录查找 DeepSeek Harness，或读取 DSH_DESKTOP_HOME 后自动启动。
3. App 从 8971~8980 中发现兼容的深脉服务，并优先使用同目录 DeepPulse 文件夹中的后端。
4. 转发给其他电脑前，请确认对方已安装 Python 3.9+ 和 DeepSeek Harness；也可以让对方先独立启动 Harness 的 3080 端口。
5. 使用通达信 TQ-Local 时，请先启动并登录官方通达信金融终端。
6. 深脉只调用行情、K线和市场统计等只读接口，不包含委托、撤单或账户操作。

版本：1.3.1
完整同步：独立版、Harness 内嵌工作台、桌面 App 使用同一份前端和兼容后端版本。

仅供研究与学习参考，不构成投资建议。
