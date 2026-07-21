# Tri-Arb Scanner

Tri-Arb Scanner 是一个本地优先、可审计的三角套利机会扫描器，当前聚焦
**MEXC 现货市场的只读扫描**，不包含下单能力。

完整范围、计算语义、架构、安全边界和当前状态见 [DOCS.md](DOCS.md)。参与开发前请先阅读
[AGENTS.md](AGENTS.md)。

## 当前进度

- 已完成 MEXC 公共 REST、Protobuf WebSocket 20 档行情和两阶段订阅；
- 已完成 Decimal 三角路径广筛、深度确认、容量、机会生命周期和 SQLite 审计重放；
- 已完成只读 REST/WebSocket 机会接口及应用运行时；
- React / TypeScript / Vite 实时仪表盘正在实施；
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

浏览器访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。开发前端时可另行执行
`pnpm --dir frontend dev`，Vite 只监听 127.0.0.1 并代理本地后端 API。

配置模板见 `.env.example`。当前版本只接受回环监听地址；MEXC 正式 endpoint 必须使用 HTTPS/WSS。

## 提交信息校验

本地仓库使用版本化 `commit-msg` hook：

```bash
git config core.hooksPath .githooks
```

提交信息需要包含 Conventional Commit 标题、有意义的正文，以及 Agent 共同作者 trailer 或仅适用于
纯人工提交的 `Human-authored: true`。详见 [AGENTS.md](AGENTS.md)。
