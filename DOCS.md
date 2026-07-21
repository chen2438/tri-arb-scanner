# Tri-Arb Scanner 功能与架构文档

> 本文件是项目的**唯一权威功能与架构文档**，记录当前能力、目标能力、接口和安全边界。
> 尚未实现的内容必须明确标为规划，不能写成已提供的能力。
>
> 最后更新：2026-07-21（里程碑 5：完成 API 与中文实时仪表盘）

## 1. 当前状态

仓库已完成里程碑 5，并正在实施里程碑 6。发布验收尚未完成。当前已有能力为：

- Python 3.12+ 项目、锁定依赖、`tri-arb doctor` / `tri-arb serve` CLI；
- 严格 `TRI_ARB_` 配置、未知变量拒绝、localhost 绑定和上游 URL 安全校验；
- FastAPI 存活、未就绪和只读配置接口，以及同源前端静态托管；
- React/TypeScript/Vite 中文实时仪表盘，提供机会、三腿明细、历史与系统状态视图；
- Ruff、Pytest、Vitest、TypeScript、Vite 构建和提交消息 CI；
- MEXC `exchangeInfo` 归一化、逐市场隔离诊断和确定性的 USDT 三角路径枚举；
- 不使用二进制浮点数的三腿 20 档模拟、手续费、取整、dust、规则拒绝和确认容量计算；
- 公共 MEXC REST ping、服务器时间校准、交易规则和全量 book ticker 客户端，包含 429、退避、
  响应边界与单轮串行保护；
- 固定来源、可重复生成且 CI 检查漂移的 MEXC partial-depth Protobuf，只保留公共 20 档消息；
- 最多两条连接、每条 30 个市场的 WebSocket 分片，以及订阅代次、PING、重连、主动轮换和状态事件；
- Top 20 完整路径贪心选择、15 秒市场驻留、60 市场硬上限与三腿源时间检查；
- 单进程异步行情服务，按 5 分钟、60 秒、1 秒和 5 秒周期分别刷新元数据、服务器时间、全量报价和
  深度订阅，并原子提供市场、路径、报价、深度与健康快照；
- 基于真实买卖方向、最优 bid/ask 和逐腿公开费率的 Decimal 全路径广筛，缺报价路径直接跳过，
  按收益和路径 ID 确定性选取 Top 20；
- 两阶段扫描引擎，只有选中路径在当前连接/订阅代次收到三腿 20 档新快照，且满足 2 秒新鲜度与
  1 秒腿间偏差后，才运行精确模拟和确认容量；
- 深度乱序保护，以及连接重连、退避、分片迁移和退订时立即清除旧快照的 fail-closed 行为；
- 20 bps 开启、连续两次低于 15 bps 关闭的机会生命周期；无效确认立即关闭，生命周期使用唯一 ID，
  峰值至少提高 1 bp 才产生峰值审计事件，重启可统一以 `process_restart` 关闭；
- 版本化 SQLite WAL 审计库和单一异步写队列，保存开启、至少提高 1 bp 的峰值与关闭事件；启动时
  关闭残留活跃记录，每日清理超过 7 天的关闭记录，事件随生命周期级联删除；
- 包含市场规则、三腿完整盘口、手续费、取整结果和时间信息的稳定 JSON 审计快照，可用同一 Decimal
  模拟器离线复算并逐值检测篡改或漂移；
- 字段白名单 JSON 运行日志，覆盖行情刷新/错误、WebSocket 状态、生命周期、重启恢复与保留期清理；
  Decimal 始终编码为字符串，外部错误文本有长度上限，禁止任意环境、请求头或数据库字段进入日志；
- 实际应用生命周期已串接行情、扫描、存储与事件总线；提供存活/就绪/状态、活跃机会、详情和历史
  REST API，支持上限校验、UTC 时间过滤、404/422/503 和不透明稳定游标；
- `/ws/opportunities` 先发送完整 snapshot，再以进程内单调 sequence 推送机会、状态与 15 秒心跳；
  活跃数值每路径最多 250 ms 一次，慢客户端关闭并要求重连获取新快照，不静默丢事件；
- 前端检测 sequence 跳跃或断线后丢弃增量并等待完整 snapshot；只展示后端 Decimal 字符串，提供
  30 秒同路径去重的可持久化声音提示、降级状态、移动端布局与完整风险措辞；
- 本文记录的后续产品、计算、接口和实施计划；
- `AGENTS.md` 中的 Agent 协作约定；
- 本地及 CI 可复用的 Git 提交信息校验。

下文除本节以及标记为“已完成”的里程碑明确列出的内容外均为**规划能力**。

## 2. 产品目标与边界

Tri-Arb Scanner 是一个本地优先、可审计的**三角套利机会扫描器**。v0.1 聚焦 MEXC 现货市场，
从全部可交易现货市场中寻找以 USDT 为起点和终点的三腿闭环，例如
`USDT -> BTC -> ETH -> USDT`。

扫描结果回答的是：“以 100 USDT 为起始规模，在给定的三腿盘口、公开 taker 手续费、交易规则、
逐腿取整和安全缓冲下，这条路径是否仍有正的预估净收益？”系统不得把仅由最新成交价推导的价差、
未完成深度确认的广筛结果，或无法原子成交的估算描述为实际利润。

### 2.1 v0.1 确定范围

- 只使用 MEXC 公共 REST 与公共 WebSocket 行情，不需要 API Key；
- 使用全部在线且允许现货交易的市场构建资产图，但只输出 `USDT -> A -> B -> USDT`；
- 每秒对全市场最优报价进行一次广筛，再对排名靠前的路径订阅 20 档深度进行确认；
- 默认模拟 100 USDT，计入逐腿手续费、深度滑点、数量取整、最小数量和最小金额；
- 整条路径额外扣除 5 bps 安全缓冲，净收益达到 20 bps 才进入主机会列表；
- 提供本机网页仪表盘、只读 REST/WebSocket API 和诊断 CLI；
- SQLite 保存 7 天已确认机会历史，不录制全市场逐秒原始行情；
- 页面可选择播放本地提示音，不接浏览器系统通知或第三方通知渠道。

### 2.2 暂不实施

- 自动或人工触发下单、撤单、资金划转和提现；
- 模拟账户、余额管理或任何私有账户接口；
- 任何需要 API Key 的能力，包括账户级真实费率；
- 非 USDT 锚定路径、跨交易所套利、期货/永续合约和资金费率套利；
- 公网或局域网部署、登录、HTTPS、Docker 和 VPS 运维；
- 完整行情归档，以及为尚未接入的交易所预建空适配器。

## 3. 核心领域语义

### 3.1 市场与有向兑换边

一个现货交易对 `BASE/QUOTE` 最多产生两条有向边：

- `BASE -> QUOTE`：卖出 BASE，消费 bids；
- `QUOTE -> BASE`：买入 BASE，消费 asks。

市场必须同时满足 `status=1`（或兼容的 `ENABLED`）和 `isSpotTradingAllowed=true`。方向还必须服从
`tradeSideType`：`1` 允许双向，`2` 只保留买入边，`3` 只保留卖出边，`4` 不生成边。未知状态和
未知方向值一律拒绝，不做猜测。

每条归一化边至少携带：交易对、输入/输出资产、买卖方向、公开 taker 费率、基础资产精度、最小基础
资产数量、最小/最大报价金额、盘口版本、MEXC 发送时间和本地接收时间。核心扫描器只消费领域模型，
不读取 MEXC 原始字段名。

### 3.2 三角路径

有效路径固定为 `USDT -> A -> B -> USDT`：

- `USDT`、`A`、`B` 必须互不相同；
- 三条边必须对应三个不同的实际市场；
- 路径按真实执行顺序保留，`USDT -> A -> B -> USDT` 与反向路径是两个不同候选；
- 锚定 USDT 后不存在旋转重复，完全相同的三条有向边只保留一次；
- 市场元数据刷新后重新构图，失效路径立即从广筛集合移除。

2026-07-21 的公共元数据观测到约 2,000 个可交易现货市场和 700 余条 USDT 有向三角路径；该数字仅
用于容量设计，不能写成固定业务规则。

### 3.3 三腿兑换

所有金额、价格、数量、手续费和收益率使用 `Decimal`，禁止用二进制浮点数承担业务计算。

```text
x1 = convert(100 USDT, edge1)
x2 = convert(x1, edge2)
x3 = convert(x2, edge3)
modeled_return = x3 / 100 USDT - 1
net_return = modeled_return - safety_buffer
estimated_profit = 100 USDT * net_return
```

`convert` 的确定语义为：

1. 买入消费 asks，卖出消费 bids，按最优价格向外逐档累计；
2. 每个市场的下单数量按 `baseAssetPrecision` 允许的小数位向下取整；
3. `baseSizePrecision` 按官方文档作为最小基础资产数量，`quoteAmountPrecision` 作为最小报价金额，
   不把二者误当作数量步长；当 MEXC 对在线市场返回 `baseSizePrecision=0` 时，按“没有更严格的
   最小值”处理，并保守使用 `10^-baseAssetPrecision` 作为最小基础资产数量；
4. 检查可获得的最小/最大金额规则，缺失的关键规则不推断；
5. 每腿从该腿收到的资产中扣除 `takerCommission`，不假设 MX 抵扣、VIP 或活动费率；
6. 上一腿产生但无法进入下一腿的取整余量记为 dust，不计入最终 USDT；
7. 任一腿 20 档深度不足、规则不满足或数值非法时，整条路径拒绝。

`confirmed_capacity_usdt` 使用同一三腿模拟器在 `0.01 USDT` 精度上做单调二分查找：下界为 0，
上界取三腿 20 档深度和市场最大报价金额共同反推的最小 USDT 容量；最多迭代 32 次。结果向下取整到
0.01 USDT，并明确表示“20 档内已确认容量”，不能外推到未订阅深度。

展示和审计至少区分：

- `gross_return_bps`：按三腿最优价、未计手续费和取整的理论收益；
- `modeled_return_bps`：计入 20 档深度、手续费、规则和取整后的收益；
- `safety_buffer_bps`：整条路径统一扣除的保守缓冲，默认 5 bps；
- `net_return_bps`：`modeled_return_bps - safety_buffer_bps`；
- `estimated_profit_usdt`：默认 100 USDT 对应的预估净收益金额；
- `confirmed_capacity_usdt`：当前 20 档共同深度支持的最大起始规模；
- `reject_reasons`：无法确认的结构化原因。

“深度已确认”仍不代表三腿能够原子成交，也不代表实际账户费率与公共费率完全一致。

### 3.4 机会生命周期

- 单次有效确认的 `net_return_bps >= 20` 时开启机会；
- 已开启机会连续两次有效确认低于 15 bps 时关闭，避免阈值附近反复开关；
- 行情陈旧、缺腿、市场失效、订阅被移除或连接中断时立即关闭，不等待两次确认；
- 每个生命周期使用独立 UUID，路径 ID 由三条有向边稳定生成；
- 进程启动时把数据库中残留的活跃生命周期以 `process_restart` 原因关闭；取得全新行情并再次越过
  开启门槛后创建新生命周期，不跨重启延续旧机会；
- 活跃机会按净收益从高到低排序；数值相同时按最近确认时间、路径 ID 稳定排序；
- 前端事件最多每条路径每 250 ms 推送一次，但后端保留最新状态。

## 4. 行情架构

v0.1 采用单进程异步两阶段架构：REST 负责全市场广筛，WebSocket 负责少量候选路径的深度确认。

```text
MEXC exchangeInfo ------> 市场规则/有向资产图 ------> USDT 三角路径集合
MEXC bookTicker --------> 每秒全路径广筛 -----------> Top 20 路径
                                                        |
                                                        v
MEXC 20档 WebSocket ---> 候选深度状态 ----------> 三腿精确模拟
                                                        |
                                                        v
                                       机会生命周期/SQLite/API/网页
```

### 4.1 REST 元数据与广筛

- 启动时请求 `/api/v3/ping`、`/api/v3/time`、`/api/v3/exchangeInfo` 和全量
  `/api/v3/ticker/bookTicker`；
- `exchangeInfo` 每 5 分钟刷新；服务器时间每 60 秒校准；
- 全量 `bookTicker` 默认每 1 秒请求一次，上一请求未完成时不并发发起下一次；
- 单次 HTTP 超时 3 秒；429 必须服从 `Retry-After`，其他可恢复错误按 1、2、4、8 秒指数退避，
  最大 30 秒并带抖动；
- REST book ticker 没有交易所源时间，因此只作为内部广筛输入，绝不直接发布机会；
- 每轮遍历全部有效路径，使用最优 bid/ask 和公开手续费计算广筛分数，不用顶档数量冒充完整深度；
- 按分数选取最多 20 条路径；不足 20 条时不填充无效路径。

### 4.2 WebSocket 深度确认

- 连接使用 `wss://wbs-api.mexc.com/ws`；
- 订阅 `spot@public.limit.depth.v3.api.pb@<symbol>@20`，每次消息视为该市场完整 20 档快照；
- Top 20 路径最多涉及 60 个不同市场，按稳定排序分配到两条连接，每条不超过官方上限 30 个订阅；
- 订阅协调器每 5 秒对齐一次目标集合；市场订阅至少保留 15 秒，期满后才允许因排名变化移除；
- 协调时先保留尚未满足 15 秒驻留期的市场，再按广筛排名贪心加入完整路径；若加入后会超过 60 个
  市场则暂不加入。驻留期结束后，移除仅被落选路径使用且不被更高排名路径共享的市场；任何时刻都
  不突破两条连接和 60 个市场的硬上限；
- 路径只有在其三个市场均已收到当前订阅周期内的新快照后才能确认；
- 三腿 MEXC `sendTime` 相对校准后的服务器时间均不得超过 2 秒，最早和最晚腿之差不得超过 1 秒；
- 每 20 秒发送 PING；断线按指数退避重连，并在恢复后重新订阅；
- 单连接运行到 23 小时 50 分钟时主动轮换，避免触发官方 24 小时连接上限；
- 解码、版本、字段或订阅响应异常时 fail-closed，并在状态接口暴露明确原因。

MEXC WebSocket 使用 Protobuf。项目固定官方
[`mexcdevelop/websocket-proto`](https://github.com/mexcdevelop/websocket-proto) 提交
`7b8ac7a6681f28551612a5a7cefbb7e09b56bb85`，保存其 Apache-2.0 LICENSE 和来源说明，生成代码
必须可由脚本重复生成并在 CI 检查无漂移。协议或 endpoint 变更时先更新夹具和本文，再升级固定版本。
仓库只派生并生成公共 partial-depth 所需字段，明确移除私有账户与订单消息；录制的公共二进制帧以
Base64 文本保存用于离线契约测试。每次连接和每次重新订阅都产生单调代次，下游只接受目标市场在
当前代次收到的新快照。

官方参考：

- [MEXC Spot API v3](https://mexcdevelop.github.io/apidocs/spot_v3_en/)
- [MEXC WebSocket Protobuf definitions](https://github.com/mexcdevelop/websocket-proto)

## 5. 系统架构与技术选型

### 5.1 后端

- Python 3.12+；
- FastAPI + Uvicorn：同源 REST、WebSocket 和静态前端；
- Pydantic Settings：严格配置解析；
- HTTPX：MEXC REST；
- websockets + protobuf：MEXC WebSocket；
- SQLAlchemy + aiosqlite：SQLite WAL 持久化；
- 单进程 asyncio，不引入 Redis、Celery 或消息队列。

建议模块边界：

- `exchange/mexc`：REST、WebSocket、Protobuf、重连、限频和字段归一化；
- `domain`：市场、兑换边、路径、深度、机会和拒绝原因；
- `market_state`：快照版本、时序、新鲜度和订阅状态；
- `scanner`：资产图、广筛、深度模拟、容量与生命周期；
- `storage`：SQLite 模型、串行写入和保留策略；
- `api` / `cli`：只读接口、状态推送、启动和诊断；
- `observability`：结构化日志、计数器和错误摘要。

### 5.2 前端

- React + TypeScript + Vite，使用 pnpm 锁定依赖；
- 中文响应式界面，由 FastAPI 同源托管，不单独部署；
- 前端只展示后端 Decimal 字符串，不自行重复收益计算；
- 活跃机会表展示路径、净收益、预估利润、确认容量和行情年龄；
- 展开行展示三腿方向、市场、档位均价、输入/输出、费用、dust 和时间；
- 历史页展示开启、峰值与关闭原因；状态区展示 REST 年龄、路径数、订阅数、连接与最近错误；
- 声音只在新生命周期开启时播放，同一路径 30 秒内去重，开关保存在浏览器 localStorage；
- 断线后自动重连后端 WebSocket，并以服务端完整快照覆盖本地状态。

### 5.3 存储

SQLite 只保存：

- `opportunity_lifecycles`：路径、开启/关闭时间、最新值、峰值和关闭原因；
- `opportunity_events`：开启、净收益峰值至少提高 1 bp、关闭时的完整三腿计算输入；
- `schema_version`：数据库结构版本。

不保存全市场 book ticker、未入选的广筛路径或每条深度更新。启动时及每 24 小时清理超过 7 天的已关闭
生命周期和事件；活跃机会不因保留期被删除。数据库写入通过单一异步队列串行化。

## 6. 配置

配置统一使用 `TRI_ARB_` 前缀，未知键和非法值必须在启动时明确报错。

| 变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `TRI_ARB_HOST` | `127.0.0.1` | v0.1 只允许回环地址 |
| `TRI_ARB_PORT` | `8000` | HTTP 端口 |
| `TRI_ARB_MEXC_REST_URL` | `https://api.mexc.com` | MEXC 公共 REST 根地址 |
| `TRI_ARB_MEXC_WS_URL` | `wss://wbs-api.mexc.com/ws` | MEXC 公共 WebSocket 地址 |
| `TRI_ARB_ANCHOR_ASSET` | `USDT` | v0.1 只接受 USDT |
| `TRI_ARB_NOTIONAL` | `100` | 起始 USDT 金额 |
| `TRI_ARB_MIN_NET_RETURN_BPS` | `20` | 机会开启门槛 |
| `TRI_ARB_CLOSE_NET_RETURN_BPS` | `15` | 连续两次低于该值后关闭 |
| `TRI_ARB_SAFETY_BUFFER_BPS` | `5` | 整条路径安全缓冲 |
| `TRI_ARB_BOOK_TICKER_INTERVAL_MS` | `1000` | 全市场广筛周期 |
| `TRI_ARB_SHORTLIST_ROUTES` | `20` | 深度确认路径上限 |
| `TRI_ARB_DEPTH_LEVELS` | `20` | WebSocket 深度档数，v0.1 固定为 20 |
| `TRI_ARB_MAX_DEPTH_AGE_MS` | `2000` | 单腿最大行情年龄 |
| `TRI_ARB_MAX_LEG_SKEW_MS` | `1000` | 三腿最大时间偏差 |
| `TRI_ARB_HISTORY_RETENTION_DAYS` | `7` | 已关闭机会保留期 |
| `TRI_ARB_DATABASE_URL` | `sqlite+aiosqlite:///./tri_arb.db` | 本地数据库 |

`.env.example` 只包含安全示例；本地 `.env` 永不提交、输出或覆盖已有值。MEXC URL 允许在测试中指向
本地 fixture server；正式运行拒绝非 HTTPS/WSS 的非回环地址。配置通过只读 API 展示脱敏后的有效值，
v0.1 不支持在网页中修改。

## 7. CLI 与公共接口

### 7.1 CLI

- `tri-arb serve`：启动行情、扫描、数据库、API 和网页；
- `tri-arb doctor`：逐项检查配置、SQLite 临时写入、MEXC ping/time/exchangeInfo、公共 book ticker 和
  本地生成后解码的 Protobuf 固定消息；不创建 API Key、不访问私有接口，任一检查失败时返回退出码 1。

服务缺少完整元数据或首份全市场报价时可以存活但未就绪；行情暂时中断时进程继续运行，状态转为
degraded 并关闭依赖该行情的机会。

### 7.2 REST

- `GET /api/health/live`：进程存活；
- `GET /api/health/ready`：配置、数据库、元数据和全市场广筛是否就绪；
- `GET /api/status`：扫描阶段、市场/边/路径数、REST 年龄、WebSocket 连接和订阅数、最近错误；
- `GET /api/config`：当前有效的非敏感配置；
- `GET /api/opportunities`：当前活跃机会，支持 `limit` 和游标；
- `GET /api/opportunities/{id}`：一个生命周期及最新完整三腿明细；
- `GET /api/history`：已关闭生命周期，支持 `cursor`、`limit`、`route` 和时间过滤。

分页 `limit` 默认 50、最大 200；游标为不透明字符串。未知 ID 返回 404，非法参数返回结构化 422。
所有 Decimal 编码为十进制字符串，所有时间编码为 UTC ISO-8601，禁止先转成 JSON number。

核心机会响应包含：

```text
id, route_id, state, assets, start_amount, final_amount,
gross_return_bps, modeled_return_bps, safety_buffer_bps, net_return_bps,
estimated_profit_usdt, confirmed_capacity_usdt,
first_seen_at, last_confirmed_at, peak_net_return_bps, close_reason,
market_age_ms, leg_skew_ms, legs[]
```

每个 `leg` 包含 `symbol`、`side`、`from_asset`、`to_asset`、`input_amount`、`output_amount`、
`average_price`、`fee_rate`、`fee_amount`、`dust_amount`、`levels_consumed`、`book_version`、
`source_time` 和 `received_time`。

### 7.3 WebSocket

`/ws/opportunities` 连接成功后先发送一份 `snapshot`，随后发送：

- `opportunity.upsert`：机会开启或数值更新；
- `opportunity.closed`：机会关闭及原因；
- `status.changed`：行情、扫描或连接健康状态变化；
- `heartbeat`：每 15 秒发送，供前端判断连接存活。

消息包含单调递增的进程内 `sequence`。前端检测到 sequence 跳跃或重连时丢弃本地增量状态，以新的
完整 snapshot 为准；v0.1 不提供跨进程事件补发。

## 8. 安全、失败与审计边界

- 后端硬性拒绝监听非 `127.0.0.1`、`localhost` 或 `::1` 地址；
- 代码中不存在私有 MEXC endpoint、签名逻辑、下单模型或交易权限配置；
- 外部响应一律校验类型、长度、正数范围、资产/交易对关系和 Decimal 位数；
- 手续费、精度、最小金额、关键行情或时间信息缺失时 fail-closed；
- REST 429 服从 `Retry-After`，禁止用并发重试放大限频；
- WebSocket 断线、订阅错误和时间异常立即使对应行情失效；
- 日志不得输出完整环境、请求头或数据库内容；当前版本没有任何凭据应当存在；
- 每个机会事件必须能够使用保存的三腿盘口、规则、费率和取整过程离线复算；
- UI 必须始终使用“预估”“深度已确认”“非原子成交”等措辞，不使用“保证盈利”。

## 9. 实施里程碑

每个里程碑使用独立、可验收的提交，并在实现时同步把本文的“规划”更新为真实状态。

### 里程碑 1：工程骨架

**状态：已完成。**

- 已建立 Python 包、`pyproject.toml`、锁定依赖、`tri-arb` CLI 和严格配置模型；
- 已建立 React/TypeScript/Vite 前端、pnpm 锁文件及 FastAPI 同源静态托管；
- 已扩展 CI 为提交信息、Ruff、Pytest、Vitest、TypeScript 和生产构建；
- 已提供 `.env.example`，并完成 localhost 绑定限制。

### 里程碑 2：领域模型与路径计算

**状态：已完成。**

- 已实现 MEXC 元数据归一化、有向资产图和 USDT 三角路径枚举；
- 已对单市场无效规则做隔离诊断，结构性响应错误与重复交易对仍整体拒绝；
- 已实现 Decimal 买卖转换、多档消费、费率、取整、dust、规则检查和确认容量；
- 已提供完全离线的固定输入单元测试，不依赖网络。

### 里程碑 3：MEXC 行情

**状态：已完成。**

- 已实现 REST 元数据、时间校准、全量 book ticker、退避、单市场隔离结果和分组件健康状态；
- 已固定并生成公共只读 Protobuf，解析 20 档 partial-depth，并由 CI 检查生成代码漂移；
- 已实现 Top 20 订阅协调、两连接分片、PING、重连、主动轮换和连接/订阅代次；
- 已使用录制并裁剪的公共 REST 响应和公共二进制帧完成离线适配器契约测试；
- 已实现周期调度、路径重建、有效市场过滤、原子状态快照和可中断的干净停止。

### 里程碑 4：扫描、生命周期与存储

**状态：已完成。**

- 已实现不使用顶档数量冒充深度的 Decimal 全路径广筛；
- 已实现当前订阅代次、分片、乱序、新鲜度和腿间偏差校验，并串接 20 档模拟与确认容量；
- 已实现 20/15 bps 精确边界、连续两次关闭、无效行情立即关闭、唯一生命周期 ID、稳定排序、
  1 bp 峰值事件和进程重启关闭；
- 已实现版本化 SQLite schema、WAL、串行写入、机会事件审计、峰值记录、重启关闭和 7 天清理；
- 已实现行情与扫描状态计数器、字段白名单 JSON 日志，以及保存完整输入的确定性离线重放。

### 里程碑 5：API 与网页

**状态：已完成。**

- 已实现 REST、后端 WebSocket 的 snapshot/增量协议、慢客户端保护和游标分页；
- 已实现中文实时机会、三腿明细、历史和健康状态页面；
- 已实现声音提示、重连覆盖和降级状态展示；
- 已完成桌面与 390 px 移动端真实 MEXC 行情浏览器验证、交互检查和生产构建。

### 里程碑 6：发布验收

- 完成全部自动化检查与离线性能基准；
- 运行 `tri-arb doctor`；
- 使用 MEXC 公共行情连续运行至少 30 分钟，验证 REST 限频、WebSocket 重连、订阅更新、SQLite
  写入和前端恢复；
- 保存不含凭据的验收摘要，并明确观测期间是否出现已确认机会，不能为了通过验收伪造机会。

## 10. 测试与验收标准

### 10.1 自动化测试

- 双向兑换、三腿费用、逐腿取整、dust、跨档滑点、20 档不足和容量边界；
- 20/15 bps 精确边界、连续两次关闭、陈旧数据立即关闭和峰值事件阈值；
- 市场状态、`tradeSideType`、三个不同资产/市场、路径去重和元数据热刷新；
- 零/负/NaN/超大报价、缺失费率、最小数量、最小报价金额和未知规则；
- REST 超时、429 `Retry-After`、5xx 退避、并发轮询抑制和服务器时钟偏移；
- Protobuf 固定二进制样本、30 订阅分片、目标集合切换、PING、断线重连和主动轮换；
- 2 秒新鲜度、1 秒腿间偏差、订阅前旧快照隔离和乱序消息；
- SQLite 开启/峰值/关闭事件、重启恢复、7 天清理和串行写入；
- REST Decimal 字符串、游标、404/422，WebSocket snapshot、sequence 跳跃和重连；
- 前端排序、明细展开、空状态、degraded 状态、历史分页和声音去重；
- 使用至少 3,000 个市场、2,000 条路径的合成夹具完成一轮广筛，CI 单轮不超过 250 ms。

### 10.2 发布不变量

- 没有 API Key 也能完成全部正式能力；
- 未经三腿 20 档深度确认的结果不会出现在机会 API；
- 任何活跃机会都能从 SQLite 事件或当前详情中复算；
- 任何陈旧、缺腿、连接断开或规则未知状态都不会继续显示为活跃；
- 后端不能绑定公网或局域网地址；
- 前端不承担业务计算，刷新或重连后与后端 snapshot 一致；
- 工程验收以正确、可复现、可恢复为准，不以发现机会或盈利为条件。

实现后的标准验证命令固定为：

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q
pnpm --dir frontend test
pnpm --dir frontend build
python3 scripts/check_commit_messages.py --commit HEAD
```
