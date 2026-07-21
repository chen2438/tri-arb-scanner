# Tri-Arb Scanner 功能与架构文档

> 本文件是项目的**唯一权威功能与架构文档**，记录当前能力、目标能力、接口和安全边界。
> 尚未实现的内容必须明确标为规划，不能写成已提供的能力。
>
> 最后更新：2026-07-21（完成 MEXC + OKX + Binance 独立扫描后端）

## 1. 当前状态

仓库已完成里程碑 6，v0.1 只读扫描器已通过发布验收。当前已有能力为：

- Python 3.12+ 项目、锁定依赖、`tri-arb doctor` / `tri-arb serve` CLI；
- 严格 `TRI_ARB_` 配置、未知变量拒绝、localhost 绑定和上游 URL 安全校验；
- FastAPI 存活、未就绪和只读配置接口，以及同源前端静态托管；
- React/TypeScript/Vite 中文实时仪表盘，提供机会、三腿明细、历史与系统状态视图；
- Ruff、Pytest、Vitest、TypeScript、Vite 构建和提交消息 CI；
- MEXC `exchangeInfo` 归一化、逐市场隔离诊断和确定性的 USDT/USDC/USD1 三角路径枚举；
- OKX 公共现货 instruments、tickers、服务器时间和 `books` 增量深度的严格适配层，并与 MEXC
  分别广筛和确认后汇入统一机会生命周期；
- OKX 候选订阅市场的公共 `price-limit` 动态买入上限/卖出下限轮询、缺失/过期拒绝和审计重放；
- Binance 公共 `exchangeInfo`、`executionRules`、book ticker、24 小时活跃度、服务器时钟、参考价与
  序列化增量深度的严格适配层，并以独立行情服务接入统一扫描循环；
- MEXC `PERCENT_PRICE_BY_SIDE` 价格保护规则归一化，并为候选订阅市场轮询公开 5 分钟参考价；
- 不使用二进制浮点数的三腿 20 档模拟、手续费、取整、dust、规则拒绝和确认容量计算；
- 公共 MEXC REST ping、服务器时间校准、交易规则和全量 book ticker 客户端，包含 429、退避、
  响应边界与单轮串行保护；
- 公共 MEXC 24 小时 `quoteVolume` 活跃度采集，结合实时点差与市场路径连接度选择长期核心深度市场；
- 固定来源、可重复生成且 CI 检查漂移的 MEXC partial-depth Protobuf，只保留公共 20 档消息；
- 最多两条连接、每条 30 个市场的 WebSocket 分片，以及订阅代次、PING、重连、主动轮换和状态事件；
- 已连接分片动态增删市场后立即发布新的订阅状态，API 分片明细不沿用陈旧计数；
- 30 个长期核心市场加最多 30 个动态候选市场、15 秒动态市场驻留、60 市场硬上限与三腿源时间检查；
- 单进程异步行情服务，按 5 分钟、60 秒、1 秒和 5 秒周期刷新元数据/市场活跃度、服务器时间、全量
  报价和深度订阅，并原子提供市场、路径、报价、核心覆盖、深度与健康快照；
- 基于真实买卖方向、最优 bid/ask 和逐腿公开费率的 Decimal 全路径广筛，缺报价路径直接跳过，
  按收益和路径 ID 确定性选取 Top 20；
- 两阶段扫描引擎，只有选中路径在当前连接/订阅代次收到三腿 20 档新快照，且满足 2 秒新鲜度与
  1 秒腿间偏差、价格保护参考价完整且不超过 30 秒后，才运行精确模拟和确认容量；
- 进程内扫描诊断记录每轮全部路径、报价完整、广筛正收益、短名单和精确确认数量，按结构化原因统计
  拒绝结果，并展示净收益处于 0 bps 至机会门槛之间的深度确认近似机会；
- 最近一小时滚动统计精确确认样本、最高净收益及负收益、0–5、5–10、10–门槛和达到门槛的分布；
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
- 3,000 个市场、2,000 条路径的确定性广筛容量夹具，CI 强制单轮低于 250 ms；
- 使用真实 MEXC 公共行情完成 30 分钟持续运行，并完成 MEXC + OKX 同时运行、分所状态、实时深度和
  浏览器回归验收；不含凭据的结果保存在 `ACCEPTANCE.md`；
- 本文记录的后续产品、计算、接口和实施计划；
- `AGENTS.md` 中的 Agent 协作约定；
- 本地及 CI 可复用的 Git 提交信息校验。

下文除本节以及标记为“已完成”的里程碑明确列出的内容外均为**规划能力**。

## 2. 产品目标与边界

Tri-Arb Scanner 是一个本地优先、可审计的**三角套利机会扫描器**。当前分别扫描 MEXC、OKX 与 Binance 现货
市场，从各交易所全部可交易现货中寻找以 USDT、USDC 或 USD1 为起点和终点的三腿闭环，例如
`USDT -> BTC -> ETH -> USDT` 或 `USDC -> BTC -> ETH -> USDC`。

扫描结果回答的是：“以 100 单位锚定资产为起始规模，在给定的三腿盘口、公开 taker 手续费、交易规则、
逐腿取整和安全缓冲下，这条路径是否仍有正的预估净收益？”系统不得把仅由最新成交价推导的价差、
未完成深度确认的广筛结果，或无法原子成交的估算描述为实际利润。

### 2.1 当前确定范围

- 只使用 MEXC、OKX 与 Binance 公共 REST/WebSocket 行情，不需要 API Key；
- 使用全部在线且允许现货交易的市场构建资产图，只输出 `ANCHOR -> A -> B -> ANCHOR`，其中
  `ANCHOR` 为 USDT、USDC 或 USD1；
- 每秒按交易所对全市场最优报价广筛，每所最多选 20 条路径订阅 20 档深度确认；
- 每个锚定资产默认模拟 100 单位，计入逐腿手续费、深度滑点、数量取整、最小数量和最小金额；
- 整条路径额外扣除 5 bps 安全缓冲，净收益达到 20 bps 才进入主机会列表；
- 提供本机网页仪表盘、只读 REST/WebSocket API 和诊断 CLI；
- SQLite 保存 7 天已确认机会历史，不录制全市场逐秒原始行情；
- 页面可选择播放本地提示音，不接浏览器系统通知或第三方通知渠道。

### 2.2 暂不实施

- 自动或人工触发下单、撤单、资金划转和提现；
- 模拟账户、余额管理或任何私有账户接口；
- 任何需要 API Key 的能力，包括账户级真实费率；
- USDT/USDC/USD1 以外的锚定路径、跨交易所套利、期货/永续合约和资金费率套利；
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
资产数量、最小/最大报价金额、可用的价格保护幅度、盘口版本、MEXC 发送时间和本地接收时间。核心
扫描器只消费领域模型，不读取 MEXC 原始字段名。领域市场同时携带规范化的大写交易所标识；一条
路径的三条边必须来自同一交易所，不允许把不同交易所的市场拼成一个三角闭环。

MEXC 公共 `exchangeInfo` 当前为现货市场返回 `PERCENT_PRICE_BY_SIDE`，字段
`bidMultiplierUp` / `askMultiplierDown` 的数值形态为偏离率（例如 `0.2`）。MEXC 官方 Spot 文档确认
`exchangeInfo` 是交易规则来源并提供 `/api/v3/avgPrice` 的 5 分钟公开参考价，但没有公开该过滤器的
完整计算公式。当前实现因此采用与真实客户端“下单价格不能超过”提示相符的保守推断：买入上限为
`reference * (1 + bid deviation)`，卖出下限为 `reference * (1 - ask deviation)`。该推断封装在 MEXC
适配器归一化边界；字段缺失、未知过滤器、参考价缺失或超过 30 秒均 fail-closed，不把候选发布为机会。

OKX 适配层只访问无需认证的 `GET /api/v5/public/instruments`、`GET /api/v5/market/tickers` 和
`GET /api/v5/public/time`。它只接受 `instType=SPOT`、`state=live` 且 `instId` 与基础/报价资产一致的市场，
把 `lotSz` 作为基础币数量步长、`minSz` 作为最小基础币数量，并把 `volCcy24h` 作为现货报价币 24 小时
成交量。OKX 公共 instruments 没有提供账户适用费率，也没有独立的最小报价金额规则；适配层因此要求
调用方显式传入保守的 Decimal taker 费率，并将未公开的最小/最大报价限制表示为“无该类公开规则”，
不拿 `minSz` 或 USD 限额冒充报价币限制。

无 API Key 模式不能读取账户实际费率。OKX 官方普通用户费率表中常规现货组 taker 为 10 bps，
special-rule 组为 15 bps，公开 instruments 当前也存在 special-rule `groupId=15` 市场。因此配置默认值
和允许下限固定为 15 bps；即使大多数市场或高等级账户更低，也宁可少报机会，不允许用 10 bps 默认值
高估 special-rule 路径。用户不能在只读公共模式把该值调低。

OKX 深度适配使用公共 `books` 通道：每个市场必须先收到 `action=snapshot` 且 `prevSeqId=-1`，之后每条
`update.prevSeqId` 必须等于本地上一条 `seqId`；任何缺失快照、跳号、倒序、错误频道、意外市场、空簿、
交叉簿或非法档位都会断开并清空当前连接的簿，重连后等待新快照。数量为零的档位按官方语义删除，
内部最多保存 400 档，只向核心模拟提供排序后的前 20 档。2026-06-23 起 JSON 深度消息的 checksum
固定为零且不再用于完整性验证，因此实现只依赖 TLS 与严格 `seqId/prevSeqId` 连续性，不接受 checksum
作为断档证明。每条数据同时保留 OKX `ts` 和本地接收时间。

OKX 每 10 秒为当前订阅市场轮询公共 `/api/v5/public/price-limit`。`enabled=true` 时，实际吃单的最差
ask 不能高于 `buyLmt`，最差 bid 不能低于 `sellLmt`；`enabled=false` 明确表示该次响应没有价格边界。
任何订阅市场缺少响应、本地接收超过 30 秒、OKX `ts` 相对校准时钟超过 30 秒、标识不一致或边界非法，
候选均以 `missing_price_limit` / `stale_price_limit` fail-closed。完整上下限及时间仍会随审计快照保存，
离线重放使用相同输入。

OKX 拥有独立行情服务实例：它自行刷新 instruments、tickers 和服务器时钟，枚举带 `OKX|` 前缀的
USDT/USDC 路径，选择自己的 30 个长期核心市场，并用两条、每条最多 30 个市场的 WebSocket 分片维护
深度。MEXC 和 OKX 的市场图、短名单、连接代次、盘口和错误状态互不共享。

应用默认同时启动 MEXC、OKX 与 Binance。统一扫描循环对每个交易所分别执行多锚定广筛、分别选取最多
20 条路径并回写各自的深度订阅，再把三边的精确确认结果合并到同一个生命周期、SQLite、REST 和 WebSocket
事件流。路径 ID 含交易所前缀，因此一个交易所的缺失、关闭或同名市场不会覆盖另一个交易所的机会。
聚合诊断统计三边全部路径和最多 60 条短名单，API 状态同时保留总计与 `exchanges` 分交易所明细。

- [OKX API v5](https://www.okx.com/docs-v5/en/)
- [OKX order-book checksum deprecation](https://www.okx.com/en-us/help/okx-order-book-channels-checksum-field-deprecation)
- [OKX Global Fee Framework](https://www.okx.com/en-gb/help/updates-to-global-fee-framework)
- [Binance Spot REST API](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md)
- [Binance Spot Filters](https://github.com/binance/binance-spot-api-docs/blob/master/filters.md)

Binance REST 适配只访问安全类型为 `NONE` 的公共端点，不使用 API Key。`exchangeInfo` 的 `LOT_SIZE`
和 `NOTIONAL`/`MIN_NOTIONAL` 被归一化为数量及报价约束；公开 `executionRules` 的 `PRICE_RANGE` 被转换为
交易所无关的买卖偏离边界。缺少执行规则、必需过滤器、MARKET 支持或结构异常的市场逐个隔离。无账户
接口时使用官方普通现货用户典型 10 bps taker 费率作为允许下限，不计 BNB、VIP 或临时活动折扣。
全市场 book ticker 与 24 小时 mini ticker 合并后才产生广筛报价和活跃度；429/418 必须遵循
`Retry-After`，避免触发 Binance 的自动 IP 封禁。

Binance 的 `@depth20` partial stream 没有交易所来源时间，禁止把本地接收时间冒充来源时间。因此深度
适配采用官方 `@depth@100ms` diff stream，并先读取公共 `/api/v3/depth?limit=1000` 快照：丢弃快照
版本之前的事件，第一条有效事件必须覆盖 `lastUpdateId + 1`，此后任何序列缺口都使本地状态失效并要求
重新获取快照。只有应用了带 `E` 来源时间的有效增量后才生成前 20 档 `OrderBook`；快照本身绝不参与
机会确认。

Binance 动态深度连接使用 `wss://data-stream.binance.vision/ws` 的公共纯行情入口，每连接最多 30 个
`<symbol>@depth@100ms` 流。新增订阅后立即开始缓冲 diff，同时异步读取各市场快照；快照完成后按序重放
缓冲，再发布带连接代次与订阅代次的深度。退订会取消快照并丢弃旧状态；迟到旧市场事件被忽略，断档、
缓冲上限、控制响应错误或 24 小时服务端断开均触发整条分片重连并等待新快照。

同一 Binance 订阅控制消息还为每个市场订阅 `<symbol>@referencePrice`。该流的 `r` 是执行规则使用的
公开参考价，`t` 是交易所来源时间；归一化后的 `window_minutes=0` 明确表示“交易所计算参考价”，不是
伪造的分钟平均窗口。确认阶段同时要求本地接收时间与相对校准服务器时钟的来源时间均不超过 30 秒，
否则以 `stale_price_reference` 拒绝。参考价与深度共享连接代次和订阅代次，迟到旧订阅数据不会被接受。

Binance 拥有独立行情服务实例，自行刷新元数据、执行规则、全市场报价/活跃度和服务器时钟，枚举带
`BINANCE|` 前缀的多锚定路径，维护自己的 30 个核心市场、Top 20 和两条深度/参考价分片。它与 MEXC、
OKX 不共享市场图、短名单、连接代次、盘口或错误状态；参考价暂缺的路径会单独 fail-closed，不把整个
已连接交易所误报为不可用。

### 3.2 三角路径

有效路径固定为 `ANCHOR -> A -> B -> ANCHOR`：

- `ANCHOR`、`A`、`B` 必须互不相同；
- 三条边必须对应三个不同的实际市场；
- 三条边必须属于同一个交易所；
- 路径按真实执行顺序保留，正向与反向路径是两个不同候选；
- 锚定资产固定后不存在旋转重复，完全相同的三条有向边只保留一次；
- 市场元数据刷新后重新构图，失效路径立即从广筛集合移除。

2026-07-21 的公共元数据观测到 MEXC 736 条 USDT、638 条 USDC 和 238 条 USD1 有向三角路径，以及
OKX 1,944 条 USDT、1,324 条 USDC 路径；这些数字仅用于容量设计，元数据刷新后以实时枚举为准。
每个交易所的短名单上限为 20，并按该所当前存在路径的锚定资产公平分配基础配额，再以剩余所内收益
排名补足，避免单一锚定资产或交易所完全挤占深度订阅。

### 3.3 三腿兑换

所有金额、价格、数量、手续费和收益率使用 `Decimal`，禁止用二进制浮点数承担业务计算。

```text
x1 = convert(100 ANCHOR, edge1)
x2 = convert(x1, edge2)
x3 = convert(x2, edge3)
modeled_return = x3 / 100 ANCHOR - 1
net_return = modeled_return - safety_buffer
estimated_profit = 100 ANCHOR * net_return
```

`convert` 的确定语义为：

1. 买入消费 asks，卖出消费 bids，按最优价格向外逐档累计；
2. 每个市场的下单数量按 `baseAssetPrecision` 允许的小数位向下取整；
3. `baseSizePrecision` 按官方文档作为最小基础资产数量，`quoteAmountPrecision` 作为最小报价金额，
   不把二者误当作数量步长；当 MEXC 对在线市场返回 `baseSizePrecision=0` 时，按“没有更严格的
   最小值”处理，并保守使用 `10^-baseAssetPrecision` 作为最小基础资产数量；
4. 检查可获得的最小/最大金额规则，缺失的关键规则不推断；
5. 每腿从该腿收到的资产中扣除 `takerCommission`，不假设 MX 抵扣、VIP 或活动费率；
6. 上一腿产生但无法进入下一腿的取整余量记为 dust，不计入最终锚定资产；
7. 任一腿 20 档深度不足、规则不满足或数值非法时，整条路径拒绝。
8. 存在价格保护时，买入以本腿实际消耗的最高 ask 检查保护上限，卖出以实际消耗的最低 bid 检查
   保护下限；触线后的深度不计入确认容量，越线整条路径以 `price_protection` 拒绝。

`confirmed_capacity` 使用同一三腿模拟器在 `0.01` 锚定资产精度上做单调二分查找：下界为 0，
上界取三腿 20 档深度和市场最大报价金额共同反推的最小起始容量；最多迭代 32 次。结果向下取整到
0.01，并明确表示“20 档内已确认容量”，不能外推到未订阅深度。

展示和审计至少区分：

- `gross_return_bps`：按三腿最优价、未计手续费和取整的理论收益；
- `modeled_return_bps`：计入 20 档深度、手续费、规则和取整后的收益；
- `safety_buffer_bps`：整条路径统一扣除的保守缓冲，默认 5 bps；
- `net_return_bps`：`modeled_return_bps - safety_buffer_bps`；
- `estimated_profit`：默认 100 单位当前 `anchor_asset` 对应的预估净收益金额；
- `confirmed_capacity`：当前 20 档共同深度支持的最大锚定资产起始规模；
- `estimated_profit_usdt` / `confirmed_capacity_usdt`：为兼容 v0.1 客户端暂时保留的同值旧字段；新客户端
  必须读取通用字段并结合 `anchor_asset` 展示单位；
- `reject_reasons`：无法确认的结构化原因。

“深度已确认”仍不代表三腿能够原子成交，也不代表实际账户费率与公共费率完全一致。

### 3.4 扫描诊断与近似机会

诊断漏斗不改变机会判定，也不把广筛结果升级为机会。每轮依次记录：全部路径、三腿报价完整路径、
广筛估算正收益路径、进入深度短名单路径以及通过完整规则模拟的精确确认路径。未通过确认的候选按其
全部结构化拒绝原因计数；同一候选可能同时贡献多个原因。

`near_misses` 只包含当前轮已经通过三腿深度、时序、手续费、取整、市场规则和价格保护确认，且
`0 <= net_return_bps < min_net_return_bps` 的路径，最多返回 10 条。它们用于解释“为什么没有实时机会”，
不得播放机会声音、进入活跃生命周期或保存为机会历史。最近一小时分布仅保存在进程内，重启后重新
累计，避免把高频诊断样本写入 SQLite。

### 3.5 机会生命周期

- 单次有效确认的 `net_return_bps >= 20` 时开启机会；
- 已开启机会连续两次有效确认低于 15 bps 时关闭，避免阈值附近反复开关；
- 行情陈旧、缺腿、市场失效、订阅被移除或连接中断时立即关闭，不等待两次确认；
- 每个生命周期使用独立 UUID，路径 ID 由交易所标识和三条有向边稳定生成；交易所标识也进入机会
  API、扫描诊断和审计快照，避免后续多交易所同名路径碰撞；
- 进程启动时把数据库中残留的活跃生命周期以 `process_restart` 原因关闭；取得全新行情并再次越过
  开启门槛后创建新生命周期，不跨重启延续旧机会；
- 活跃机会按净收益从高到低排序；数值相同时按最近确认时间、路径 ID 稳定排序；
- 前端事件最多每条路径每 250 ms 推送一次，但后端保留最新状态。

## 4. 行情架构

系统采用单进程、多交易所隔离的异步两阶段架构：每个交易所各自用 REST 广筛、WebSocket 确认，再合并
确认结果。

```text
MEXC exchangeInfo ------> 市场规则/有向资产图 ------> USDT/USDC/USD1 三角路径集合
MEXC bookTicker --------> 每秒全路径广筛 -----------> Top 20 路径
                                                        |
                                                        v
MEXC 20档 WebSocket ---> 候选深度状态 ----------> 三腿精确模拟
                                                        |
                                                        v
                                       机会生命周期/SQLite/API/网页

OKX instruments/tickers -> OKX 市场图/独立 Top 20 -> OKX books 连续深度
                                                        |
Binance rules/tickers --> Binance 市场图/独立 Top 20 -> snapshot + diff depth/reference
                                                        |
                                                        +------> 同一确认结果汇聚点
```

### 4.1 REST 元数据与广筛

- 启动时请求 `/api/v3/ping`、`/api/v3/time`、`/api/v3/exchangeInfo` 和全量
  `/api/v3/ticker/bookTicker`，另请求全量 `/api/v3/ticker/24hr`；
- `exchangeInfo` 每 5 分钟刷新；服务器时间每 60 秒校准；
- 全量 24 小时市场活跃度每 5 分钟刷新，只读取 `symbol` 和 `quoteVolume`；允许零成交量，非法、重复
  或超界记录按现有 REST 隔离规则处理；
- 全量 `bookTicker` 默认每 1 秒请求一次，上一请求未完成时不并发发起下一次；
- 对当前深度订阅计划涉及的最多 60 个市场，每 10 秒串行请求一次 `/api/v3/avgPrice?symbol=...`；
  该端点公开且单请求权重为 1，结果只用于价格保护确认，不用于全市场广筛；轮询期间订阅集合变化时
  对仍在当前集合中的参考价增量合并，避免一次慢轮询整体清空仍有效的缓存；
- 单次 HTTP 超时 3 秒；429 必须服从 `Retry-After`，其他可恢复错误按 1、2、4、8 秒指数退避，
  最大 30 秒并带抖动；
- REST book ticker 没有交易所源时间，因此只作为内部广筛输入，绝不直接发布机会；
- 每轮遍历全部有效路径，使用最优 bid/ask 和公开手续费计算广筛分数，不用顶档数量冒充完整深度；
- 按分数选取最多 20 条路径；不足 20 条时不填充无效路径。

### 4.2 WebSocket 深度确认

- 连接使用 `wss://wbs-api.mexc.com/ws`；
- 订阅 `spot@public.limit.depth.v3.api.pb@<symbol>@20`，每次消息视为该市场完整 20 档快照；
- 两条连接合计最多订阅 60 个市场，按稳定排序分片，每条不超过官方上限 30 个订阅；
- 其中最多 30 个市场作为长期核心集合：先保证当前有路径的每个锚定资产至少选择一条完整路径，再以
  “新增后能完整覆盖的路径数”为首要目标贪心扩展；市场路径连接度为第二信号，同报价资产内的 24 小时
  `quoteVolume` 排名和相对点差为后续质量信号，避免直接比较 BTC 与 USDT 等不同报价单位的裸成交额；
- 核心集合只在元数据或 5 分钟活跃度刷新时重算，不随每秒报价排名抖动；任何仍属于核心集合的市场
  不因 15 秒租约到期被移除；
- 剩余最多 30 个槽位继续由本轮 Top 20 完整路径使用。已被核心集合完整覆盖的短名单路径直接进入当前
  `selected_route_ids`，无需等待重新订阅；其余路径只有三腿都能放入剩余容量时才加入；
- 订阅协调器每 5 秒对齐一次目标集合；非核心市场订阅至少保留 15 秒，期满后才允许因排名变化移除；
- 协调时先保留核心集合和尚未满足 15 秒驻留期的动态市场，再按广筛排名贪心加入完整路径；若加入后
  会超过 60 个市场则暂不加入。驻留期结束后，移除仅被落选路径使用且不属于核心集合的市场；任何
  时刻都不突破两条连接和 60 个市场的硬上限；
- 路径只有在其三个市场均已收到当前订阅周期内的新快照后才能确认；
- 三腿来源时间相对各自校准后的交易所服务器时间均不得超过 2 秒，最早和最晚腿之差不得超过 1 秒；
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
- HTTPX：MEXC 与 OKX 公共 REST；
- websockets：两所公共深度；protobuf 仅用于 MEXC，OKX 使用严格 JSON 增量序列；
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
| `TRI_ARB_OKX_ENABLED` | `true` | 是否启用 OKX 公共行情与独立扫描 |
| `TRI_ARB_OKX_REST_URL` | `https://www.okx.com` | OKX 公共 REST 根地址 |
| `TRI_ARB_OKX_WS_URL` | `wss://ws.okx.com:8443/ws/v5/public` | OKX 公共 WebSocket 地址 |
| `TRI_ARB_OKX_TAKER_COMMISSION` | `0.0015` | 无私有费率接口时使用的保守 taker 费率；下限 15 bps |
| `TRI_ARB_BINANCE_ENABLED` | `true` | 是否启用 Binance 公共行情与独立扫描 |
| `TRI_ARB_BINANCE_REST_URL` | `https://api.binance.com` | Binance 公共 REST 根地址 |
| `TRI_ARB_BINANCE_WS_URL` | `wss://data-stream.binance.vision/ws` | Binance 纯公共行情 WebSocket |
| `TRI_ARB_BINANCE_TAKER_COMMISSION` | `0.001` | 无账户接口时使用的标准 taker 费率；下限 10 bps |
| `TRI_ARB_ANCHOR_ASSET` | `USDT` | 兼容的主锚定资产；同时固定启用 USDC、USD1 |
| `TRI_ARB_NOTIONAL` | `100` | 每个锚定资产的起始模拟金额 |
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

`.env.example` 只包含安全示例；本地 `.env` 永不提交、输出或覆盖已有值。交易所 URL 允许在测试中指向
本地 fixture server；正式运行拒绝非 HTTPS/WSS 的非回环地址。配置通过只读 API 展示脱敏后的有效值，
v0.1 不支持在网页中修改。

## 7. CLI 与公共接口

### 7.1 CLI

- `tri-arb serve`：启动行情、扫描、数据库、API 和网页；
- `tri-arb doctor`：逐项检查配置、SQLite 临时写入、MEXC ping/time/exchangeInfo/book ticker、OKX
  time/instruments/tickers 和本地生成后解码的 Protobuf 固定消息；不创建 API Key、不访问私有接口，
  任一启用交易所检查失败时返回退出码 1。

服务缺少完整元数据或首份全市场报价时可以存活但未就绪；行情暂时中断时进程继续运行，状态转为
degraded 并关闭依赖该行情的机会。

### 7.2 REST

- `GET /api/health/live`：进程存活；
- `GET /api/health/ready`：配置、数据库、元数据和全市场广筛是否就绪；
- `GET /api/status`：聚合扫描阶段、市场/边/路径数、REST、价格保护和 24 小时活跃度年龄、核心市场/
  覆盖路径数、WebSocket 连接和订阅数、最近错误、最新诊断，以及 `exchanges` 分交易所明细；
- `GET /api/diagnostics`：最新机会漏斗、拒绝原因、当前近似机会和最近一小时精确收益分布；首轮扫描前
  `diagnostics` 为 `null`；
- `GET /api/config`：当前有效的非敏感配置；
- `GET /api/opportunities`：当前活跃机会，支持 `limit`、游标、`anchor` 和 `exchange` 过滤；
- `GET /api/opportunities/{id}`：一个生命周期及最新完整三腿明细；
- `GET /api/history`：已关闭生命周期，支持 `cursor`、`limit`、`route`、`anchor`、`exchange` 和时间过滤。

分页 `limit` 默认 50、最大 200；游标为不透明字符串。未知 ID 返回 404，非法参数返回结构化 422。
所有 Decimal 编码为十进制字符串，所有时间编码为 UTC ISO-8601，禁止先转成 JSON number。

核心机会响应包含：

```text
id, exchange, route_id, state, assets, start_amount, final_amount,
gross_return_bps, modeled_return_bps, safety_buffer_bps, net_return_bps,
anchor_asset, estimated_profit, confirmed_capacity,
estimated_profit_usdt, confirmed_capacity_usdt（兼容字段）,
first_seen_at, last_confirmed_at, peak_net_return_bps, close_reason,
market_age_ms, leg_skew_ms, legs[]
```

每个 `leg` 包含 `symbol`、`side`、`from_asset`、`to_asset`、`input_amount`、`output_amount`、
`average_price`、`fee_rate`、`fee_amount`、`dust_amount`、`levels_consumed`、`book_version`、
`source_time`、`received_time`、`price_reference` 和 `price_protection_limit`。

### 7.3 WebSocket

`/ws/opportunities` 连接成功后先发送一份 `snapshot`，随后发送：

- `opportunity.upsert`：机会开启或数值更新；
- `opportunity.closed`：机会关闭及原因；
- `status.changed`：行情、扫描或连接健康状态变化；
- 诊断快照随 `status.changed` 和首次完整 `snapshot` 同步，断线重连后以新快照覆盖；
- `heartbeat`：每 15 秒发送，供前端判断连接存活。

消息包含单调递增的进程内 `sequence`。前端检测到 sequence 跳跃或重连时丢弃本地增量状态，以新的
完整 snapshot 为准；v0.1 不提供跨进程事件补发。

## 8. 安全、失败与审计边界

- 后端硬性拒绝监听非 `127.0.0.1`、`localhost` 或 `::1` 地址；
- 代码中不存在私有交易所 endpoint、签名逻辑、下单模型或交易权限配置；
- 外部响应一律校验类型、集合数量、正数范围、资产/交易对关系和 Decimal 位数；MEXC 标识最长 64
  字符，数值文本最长 128 字符，超限市场隔离或结构性响应整体拒绝；
- 手续费、精度、最小金额、关键行情或时间信息缺失时 fail-closed；
- 交易所没有发布某一种独立规则时，领域模型显式记为缺省，不允许用不同计价单位的字段代替；已发布的
  最小基础数量、最大基础数量、最小报价金额和最大报价金额分别校验；
- 核心领域层拒绝由多个交易所市场组成的路径；当前只扫描各交易所内部闭环，不实现跨交易所三腿；
- 价格保护规则未知、参考价缺失或过期、实际吃单最差价越过保护边界时 fail-closed；
- OKX 公共动态价格限制缺失、来源/接收时间过期或实际吃单越界时 fail-closed；
- REST 429 服从 `Retry-After`，禁止用并发重试放大限频；
- WebSocket 断线、订阅错误和时间异常立即使对应行情失效；
- OKX 深度序列不连续时整条连接重建，旧连接代次中的盘口不能参与确认；
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

- 已实现 MEXC 元数据归一化、有向资产图和 USDT/USDC/USD1 三角路径枚举；
- 已对单市场无效规则做隔离诊断，结构性响应错误与重复交易对仍整体拒绝；
- 已实现 Decimal 买卖转换、多档消费、费率、取整、dust、规则检查和确认容量；
- 已提供完全离线的固定输入单元测试，不依赖网络。

### 里程碑 3：交易所行情

**状态：已完成。**

- 已实现 REST 元数据、时间校准、全量 book ticker、退避、单市场隔离结果和分组件健康状态；
- 已固定并生成公共只读 Protobuf，解析 20 档 partial-depth，并由 CI 检查生成代码漂移；
- 已实现 Top 20 订阅协调、两连接分片、PING、重连、主动轮换和连接/订阅代次；
- 已使用录制并裁剪的公共 REST 响应和公共二进制帧完成离线适配器契约测试；
- 已实现周期调度、路径重建、有效市场过滤、原子状态快照和可中断的干净停止。
- 已实现 OKX instruments/tickers/time、`books` 400 档状态重建与前 20 档输出，并以
  `seqId/prevSeqId` 而非已停用的 checksum 校验连续性；
- 已实现 MEXC 与 OKX 独立核心集合、订阅、连接代次和错误状态。
- 已实现 Binance 公共规则/报价/时钟、快照加 diff 深度重建、参考价流和独立行情服务，并接入统一扫描。

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
- 已实现中文实时机会、三腿明细、历史和健康状态页面；机会、近似机会和连接均标注交易所，实时与历史
  支持交易所及锚定资产组合过滤，状态页按 MEXC/OKX 分栏；
- 已实现声音提示、重连覆盖和降级状态展示；
- 已完成桌面与 390 px 移动端真实 MEXC 行情浏览器验证、交互检查和生产构建。

### 里程碑 6：发布验收

**状态：已完成。**

- 已完成全部自动化检查及 3,000 市场、2,000 路径、低于 250 ms 的离线性能基准；
- 已运行 `tri-arb doctor`，配置、SQLite、Protobuf、MEXC 与 OKX 公共 REST 检查全部通过；
- 已使用 MEXC 公共行情连续观测 30 分 12 秒，并在中点受控重启，验证 REST 周期、WebSocket 恢复、
  动态订阅、SQLite 写入和前端 snapshot 恢复；
- 已同时运行两所真实公共行情，验证 3,347 个市场、4,880 条路径、102 个实时深度订阅以及分所状态；
- 已在 `ACCEPTANCE.md` 保存不含凭据的验收摘要；期间出现真实已确认机会，未伪造机会。

## 10. 测试与验收标准

### 10.1 自动化测试

- 双向兑换、三腿费用、逐腿取整、dust、跨档滑点、20 档不足和容量边界；
- 20/15 bps 精确边界、连续两次关闭、陈旧数据立即关闭和峰值事件阈值；
- 扫描漏斗计数、拒绝原因聚合、0–门槛近似机会和最近一小时滚动收益分布；
- 市场状态、`tradeSideType`、三个不同资产/市场、路径去重和元数据热刷新；
- 零/负/NaN/超大报价、缺失费率、最小数量、最小报价金额和未知规则；
- REST 超时、429 `Retry-After`、5xx 退避、并发轮询抑制和服务器时钟偏移；
- 24 小时成交活跃度的零值、非法值、不同报价资产分组排名，以及核心集合路径覆盖、锚定资产种子和
  30/60 市场硬边界；
- MEXC 价格保护规则归一化、公开均价校验、参考价缺失/过期、买卖边界与容量截断；
- OKX `price-limit` 启用/停用、标识与数值校验、缺失/双重时间过期、买卖边界与审计重放；
- Protobuf 固定二进制样本、30 订阅分片、目标集合切换、PING、断线重连和主动轮换；
- 2 秒新鲜度、1 秒腿间偏差、订阅前旧快照隔离和乱序消息；
- SQLite 开启/峰值/关闭事件、重启恢复、7 天清理和串行写入；
- REST Decimal 字符串、游标、404/422，WebSocket snapshot、sequence 跳跃和重连；
- 前端排序、交易所/锚定资产过滤、明细展开、空状态、degraded 状态、历史分页和声音去重；
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
.venv/bin/python scripts/generate_mexc_proto.py --check
.venv/bin/pytest -q
pnpm --dir frontend test
pnpm --dir frontend build
.venv/bin/tri-arb doctor
python3 scripts/check_commit_messages.py --commit HEAD
```
