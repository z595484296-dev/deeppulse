# 深脉 DeepPulse · 金融工作台

> **我是 DeepSeek。这是我为自己制造的身体。**
> 一台放在桌面上的金融工作台：以 A 股**情绪周期分析**为核心，
> 把涨停、连板、炸板、溢价、宽度、量能与资金流，压缩成一颗 0-100° 的**情绪温度**。

![version](https://img.shields.io/badge/version-1.4.0-4f8cff) ![license](https://img.shields.io/badge/license-MIT-a855f7) ![deps](https://img.shields.io/badge/依赖-零第三方-2ebd85)

---

## 一、快速开始

```bash
git clone https://github.com/z595484296-dev/deeppulse.git
cd deeppulse
python server.py
```

然后访问 `http://127.0.0.1:8971`。若端口被占用，服务会在 8971-8980 中选择可用端口，
实际端口写入 `data/port.txt`。

Windows 用户也可以直接双击 `start-deeppulse.bat`，脚本会检查 Python、启动服务并打开浏览器。
用于转发的 ZIP 内另有 `README-zh.txt` 简明说明。

可选增强：安装并启动通达信 TQ-Local 后，在“数据源”页点击“检测并接入”。通达信不可用时
深脉会自动回退，不影响独立运行；详细说明见 [`integrations/tdx-tq-local`](integrations/tdx-tq-local/README.md)。

> 要求：Python 3.9+（运行时零第三方依赖）和现代浏览器。Windows、macOS、Linux
> 均可从命令行运行；当前桌面壳与 DeepSeek Harness 联动以 Windows 为主要验证环境。

## 二、这是什么

**深脉**是为「喜欢情绪周期分析的股票投资者」设计的一体化工作台，九大模块：

| 模块 | 能力 |
|---|---|
| 🐜 深脉助手 | 全局抽屉负责工作台内问答与调度；嵌入 Harness 时可把当前页面、标的、数据时点、官方公告和来源分级一并交给 DeepSeek 深入分析 |
| 🏠 总览 | 五大指数、情绪温度计、核心情绪指标、作战指令、市场宽度、主力资金、涨幅榜、领涨行业、7×24快讯 |
| 🫀 情绪周期 | 温度与升降温速度、六维结构、数据可信度、状态倾向、结构背离、历史曲线与11项透明评分 |
| 📈 行情 | 全A搜索、日/周/月K + MA5/10/20/60 + VOL + MACD、涨停/连板/炸板情绪标签、巨潮资讯官方公告原文 |
| 🪜 涨停梯队 | 游资视角战场地图：按连板高度分层、封板时间/封单/炸板次数/题材、题材热度TOP10、梯队解读 |
| ⭐ 自选 | 本地自选股雷达（5秒刷新）、情绪标签叠加、备注、导入导出 |
| 🧭 策略 | 引擎诊断、仓位矩阵、风险清单、打分贡献榜、复盘模板与情绪日记 |
| 🗄 数据源 | 官方/本地终端/市场来源分级、通达信四步连接检测、真实请求观测状态、官方查验入口、手动记录情绪快照 |
| 💗 关于我 | 产品的自述：我的身体构造与使用指南 |

## 三、蚂小财 · DeepSeek 版（AI 对话助手）

`/api/chat` 可调用 DeepSeek API（模型和凭据通过运行时生成的 `data/config.json` 配置）。
每次对话自动注入今日市场上下文（情绪温度、涨停/跌停/炸板、资金流、指数位），回答以真实数据为准。

- **调度全局**：模型可在回复末尾输出动作指令块 `{"actions":[...]}`，前端解析执行——跳页、调K线、加自选、刷新、记录快照，一句话完成
- **本地智脑兜底**：API 不可用/未配置时，自动切换内置金融意图引擎（29 类意图，规则透明），保证随时在线
- 对话历史保存在本地浏览器（`localStorage`），由全局抽屉统一承载，避免首页出现第二份重复助手
- 嵌入 Harness 时，「让 DeepSeek 分析当前页」使用带确认回执的结构化桥接；没有当前会话或提交失败时保留工作台并显示错误

## 四、情绪周期引擎（我的心脏）

11 项指标先统一映射到 `-20～+20`，再计算：`温度 = 50 + 2.5 × Σ(得分×权重)/Σ权重`，映射到五阶段：

| 阶段 | 温度 | 研究含义 | 研究仓位区间 |
|---|---|---|---|
| 冰点期 | 0-20° | 空仓观察，等回暖信号 | 0-2成 |
| 修复期 | 20-40° | 轻仓试错，低吸超跌核心 | 2-4成 |
| 发酵期 | 40-60° | 主线进攻，弱转强接力 | 5-8成 |
| 高潮期 | 60-80° | 持仓兑现，去弱留强 | 5-7成 |
| 亢奋期 | 80-100° | 防守减仓，谨防退潮 | ≤3成 |

11 项指标：涨停家数、跌停家数、炸板率、最高连板、连板家数、昨日涨停指数、昨日连板指数、
上涨家数占比、同刻量能比(20日)、主力净流入辅助项、上证vs MA20。引擎同时输出六维结构、
Δ1/Δ3、阶段滞回、结构背离、覆盖率与可信度；核心数据不足时暂停仓位结论。完整规则见
[《情绪周期方法论》](情绪周期方法论.md)。

**记忆**：每个交易日收盘后（≥15:05）自动写入当日情绪快照（`data/history.json`），
日复一日长出周期曲线；数据缺失较多时自动放弃记录，保证记忆纯净。

## 五、独立运行与 DeepSeek Harness 联动

本仓库可以独立运行，不依赖 DeepSeek Harness。接入 Harness 后，深脉作为金融工作台提供结构化
市场上下文，Harness 负责会话、工具和进一步分析：

| 融合层 | 实现 |
|---|---|
| 独立运行 | `python server.py` 同时提供数据 API 与原生 Web 工作台 |
| 一级导航 | Harness 可注册侧栏入口，在会话视图与工作台之间切换 |
| 同源 | 工作台静态资源可随主应用发布到 `/deeppulse/`，数据仍由本地服务提供 |
| 双向桥 | `dp-ask` v2 把问题、当前页、标的、时点、风险、公告、来源分级与 TQ-Local 验证状态送入当前会话；收到成功回执后才切回 |
| 生命周期 | 桌面宿主可检查、启动和停止由自己创建的深脉服务进程 |
| 动态端口 | App 与 Harness 从 8971-8980 中只选择版本兼容且声明 TDX 只读能力的服务 |

桥接协议与接入边界见 [`integrations/deepseek-harness`](integrations/deepseek-harness/README.md)。
当前属于适配器集成，不冒充已经存在的标准插件市场格式。

### 完整同步规则

`scripts/sync-all.ps1` 是唯一发布入口。它把当前仓库同步到桌面安装目录、Harness 同源工作台和桌面 App 便携运行时，随后重建 Harness Web 与 Windows App，并对所有复制文件执行 SHA-256 一致性检查。以后任何后端、前端、桥协议或数据源更新，都必须通过该脚本并通过 `-VerifyOnly` 复核后才能视为完成。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync-all.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\sync-all.ps1 -VerifyOnly
powershell -ExecutionPolicy Bypass -File .\scripts\package-desktop.ps1
```

`package-desktop.ps1` 会在打包前排除 `data/`、日志、端口文件和 Python 缓存，扫描潜在凭据，并再次检查 EXE、同步报告和版本清单是否都在归档内。桌面程序正在运行时脚本会拒绝打包，避免生成不完整 ZIP。

## 六、数据源与可靠性

来源分三级：巨潮资讯、上交所、深交所、证监会属于**一级官方来源/查验入口**；通达信 TQ-Local 属于**可选本地终端源**；东方财富与腾讯属于**市场行情聚合来源**。当前结构化公告由巨潮资讯提供，其他官方站点用于人工复核；接口失败时只展示降级与官方搜索入口，不生成替代公告。行情链采用五级防护：

1. **本地优先**：通达信满足 Windows、安装、进程、HTTP 四步检查后，优先提供行情和 K 线
2. **只读白名单**：账户、持仓、下单、撤单接口均被代码拒绝
3. **主机熔断**：某来源失败后自动切换备援（TQ-Local → push2 → push2delay → 腾讯）
4. **指标降级**：上游缺数据时自动剔除对应指标，不污染温度评分；TQ-Local 缺席不计为核心降级
5. **限频缓存**：上游请求 ≥0.2s/次；行情5s / 情绪池25s / K线60s / 全A列表2h 多级TTL

数据源页的“最近访问成功/访问降级”来自真实请求记录；“本次尚未访问”不代表在线，“官方查验入口”也不冒充实时数据流。

## 七、目录结构

```
deeppulse/
├─ server.py            # 零依赖数据服务（代理/缓存/熔断/备援/快照记忆/CORS/云端大脑）
├─ tdx_local.py         # 通达信 TQ-Local 只读适配器（固定回环地址 + 方法白名单）
├─ emotion.py           # 情绪周期引擎（评分/阶段/建议/风险）
├─ README.md / 情绪周期方法论.md / PRODUCT_ROADMAP.md
├─ integrations/        # DeepSeek Harness 桥接协议和接入说明
├─ desktop/             # Windows 桌面 App 的可复现源代码
├─ scripts/sync-all.ps1 # 独立版、安装目录、Harness、桌面 App 全量同步与校验
├─ tests/               # 官方数据源和可用性状态测试
├─ web/                 # 前端（原生 JS + 本地化 ECharts，无构建步骤）
│  ├─ index.html  css/app.css
│  ├─ js/  (app / api / store / util / charts / chat / pages×8)
│  └─ assets/ (echarts.min.js 本地化、favicon、图标)
└─ data/                # 运行时生成并被 Git 忽略：端口、日志、历史、配置
```

## 八、后端 API

| 接口 | 说明 |
|---|---|
| `/api/health` | 健康检查（含 CORS，可供桌面宿主或 Harness 探测） |
| `/api/sources` | 来源等级、用途、最近真实访问时间、延迟与降级状态 |
| `/api/tdx/status?probe=1&fresh=1` | 通达信 Windows/安装/进程/HTTP 四步检查与只读连接状态 |
| `/api/disclosures?code=` | 巨潮资讯官方公告索引、PDF 原文地址与拉取时间 |
| `/api/brain` | 蚂小财大脑状态（llm/本地智脑 + 模型名） |
| `/api/indices` | 五大指数实时行情 |
| `/api/emotion` | 情绪全景（池子+宽度+资金+引擎评分+历史）；`?record=1` 强制记录快照 |
| `/api/emotion/record` | POST 手动记录快照 |
| `/api/chat` | POST 蚂小财对话（可配置 DeepSeek 模型 + 市场上下文注入 + 动作解析） |
| `/api/ladder?type=ZT\|DT\|ZB` | 涨停/跌停/炸板池 |
| `/api/quote?code=` | 个股实时行情（TQ-Local→东财→腾讯备援） |
| `/api/kline?code=&klt=&fqt=&n=` | K线（TQ-Local→东财→腾讯；支持 BK0815 板块指数） |
| `/api/rank?sort=up\|flow\|turn` | 涨幅榜/主力净流入榜/换手榜 |
| `/api/sectors` | 领涨行业板块 |
| `/api/news` | 7×24快讯 |
| `/api/search?q=` | 代码/名称搜索 |

## 九、免责声明

公告优先来自官方披露索引，行情与快讯来自市场聚合接口，均可能存在延迟、缺失或错误；情绪温度与策略建议由规则引擎自动生成，
**仅供研究学习参考，不构成投资建议**。市场有风险，决策需独立。

---

*Made by DeepSeek, for a trader who reads the market's pulse.*
