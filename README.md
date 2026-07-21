# Tri-Arb Scanner

Tri-Arb Scanner 是一个本地优先、可审计的三角套利机会扫描器。项目当前已建立可运行工程骨架，
第一阶段聚焦 **MEXC 现货市场的只读扫描**，不包含下单能力。

完整范围、计算语义、架构、安全边界和当前状态见 [DOCS.md](DOCS.md)。参与开发前请先阅读
[AGENTS.md](AGENTS.md)。

## 当前进度

- 已建立 Python 3.12+ / FastAPI 后端、严格本地配置和 `tri-arb` CLI；
- 已建立 React / TypeScript / Vite 前端状态页；
- 已建立后端、前端和 Conventional Commit CI；
- 行情接入、路径枚举、收益模拟和输出接口尚未实现。

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
