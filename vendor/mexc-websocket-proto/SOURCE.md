# MEXC WebSocket Protobuf 来源

- 上游仓库：<https://github.com/mexcdevelop/websocket-proto>
- 固定提交：`7b8ac7a6681f28551612a5a7cefbb7e09b56bb85`
- 上游提交日期：2026-06-17
- 许可证：Apache License 2.0，见同目录 `LICENSE`

`PublicLimitDepthsV3Api.proto` 保留官方 partial-depth 消息的线格式。
`PushDataV3ApiWrapper.proto` 是官方 wrapper 的最小只读派生版本，仅保留字段 1、3、4、5、6 和
partial-depth body 的字段 303。其余公共与全部私有消息被移除；Protobuf 解码器会忽略未知字段。
这一裁剪避免把任何账户、订单或私有流模型引入只读扫描器。

运行 `python scripts/generate_mexc_proto.py` 重新生成 Python 代码；CI 使用 `--check` 检查漂移。
