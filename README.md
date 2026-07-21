# Tri-Arb Scanner

Tri-Arb Scanner 是一个本地优先、可审计的三角套利机会扫描器。项目当前处于设计与工程基线阶段，
第一阶段聚焦 **MEXC 现货市场的只读扫描**，不包含下单能力。

完整范围、计算语义、架构、安全边界和当前状态见 [DOCS.md](DOCS.md)。参与开发前请先阅读
[AGENTS.md](AGENTS.md)。

## 当前进度

- 已建立唯一权威功能文档和协作规范；
- 已建立 Conventional Commit 信息校验；
- 行情接入、路径枚举、收益模拟和输出接口尚未实现。

## 提交信息校验

本地仓库使用版本化 `commit-msg` hook：

```bash
git config core.hooksPath .githooks
```

提交信息需要包含 Conventional Commit 标题、有意义的正文，以及 Agent 共同作者 trailer 或仅适用于
纯人工提交的 `Human-authored: true`。详见 [AGENTS.md](AGENTS.md)。
