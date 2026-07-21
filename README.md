# Tri-Arb Scanner

Tri-Arb Scanner 是一个本地优先、可审计的三角套利机会扫描器，当前支持
**MEXC、OKX、Binance 与 Bybit 现货市场的独立只读扫描**，不包含下单能力，也不拼接跨交易所的三条腿。

完整范围、计算语义、架构、安全边界和当前状态见 [DOCS.md](DOCS.md)。参与开发前请先阅读
[AGENTS.md](AGENTS.md)。

## 当前进度

- 已完成 MEXC、OKX、Binance 与 Bybit 公共 REST/实时深度，四所分别维护 Top 20 候选；
- 已完成 Decimal 三角路径广筛、深度确认、容量、机会生命周期和 SQLite 审计重放；
- 已完成四交易所统一确认、只读 REST/WebSocket 机会接口及应用运行时；
- 已完成带交易所筛选和分所状态的 React / TypeScript / Vite 中文仪表盘；
- 已通过自动化、性能、诊断和 30 分钟真实 MEXC 公共行情发布验收，证据见
  [ACCEPTANCE.md](ACCEPTANCE.md)；
- 后端、前端和 Conventional Commit 检查均由 CI 执行。

## 本地开发

需要 Python 3.12+、Node.js 22.12+ 和 pnpm 11.13.1。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps

pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
tri-arb doctor
tri-arb serve
```

发布 wheel 会通过 Hatchling 构建钩子按锁文件安装前端依赖、执行生产构建，并把页面资源放入 Python
包；因此从源码构建 wheel 同样要求 Node.js 和 pnpm：

```bash
python -m pip wheel . --no-deps --wheel-dir wheelhouse
python scripts/check_wheel_contents.py wheelhouse/*.whl
```

浏览器访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。开发前端时可另行执行
`pnpm --dir frontend dev`，Vite 只监听 127.0.0.1 并代理本地后端 API。

配置模板见 `.env.example`。当前版本只接受回环监听地址；四家交易所正式 endpoint 必须使用
HTTPS/WSS。OKX、Binance 与 Bybit 默认启用，可分别用 `TRI_ARB_OKX_ENABLED=false`、
`TRI_ARB_BINANCE_ENABLED=false`、`TRI_ARB_BYBIT_ENABLED=false` 关闭。

## 提交信息校验

本地仓库使用版本化 `commit-msg` hook：

```bash
git config core.hooksPath .githooks
```

提交信息需要包含 Conventional Commit 标题、有意义的正文，以及 Agent 共同作者 trailer 或仅适用于
纯人工提交的 `Human-authored: true`。详见 [AGENTS.md](AGENTS.md)。
