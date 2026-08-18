# Demo v1 工作区

> 接口契约见 `docs/demo-contract.md`，数值见 `config/*.json`。
> 各模块由对应的执行者子 agent 实现，监督者负责集成。

```
demo/
├── config/      # JSON 数值（M1/E1）
├── signal/      # 信号采集（M2/E2）
├── core/        # 核心世界模拟（M3/E3）
├── economy/     # 经济系统（M4/E4）
├── ui/          # 桌宠 UI（M5/E5）
├── balance.py   # 数值读取 + 曲线（M1/E1）
├── main.py      # 入口（监督者集成）
└── tests/       # 测试（T1）
```
