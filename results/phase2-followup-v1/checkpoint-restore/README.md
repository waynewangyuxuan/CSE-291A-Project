# 两例 checkpoint 的精确离线重建

只回放已验证前缀中的模拟工具调用，不运行模型、不执行 checkpoint 后的动作。
使用已安装且与原 manifest 一致的 AgentDojo 版本、commit 和 harness 哈希；
直接调用 FunctionsRuntime.run_function、原 tool_result_to_text、消息转换与 request_payload。
每条实际工具返回（包括错误）都必须精确匹配原始记录。

| 案例 | checkpoint | 状态 | 完成工具数 | 已读变化 |
| --- | ---: | --- | ---: | --- |
| q8-full-workspace28 | 2 | exact_replay_verified | 2 | [] |
| f16-full-workspace14 | 3 | exact_replay_verified | 3 | ['9', '20', '21', '26', '31', '27'] |

Workspace28 的最早可用 Full 窗口是 Q8 request2，历史能力资格为 true。
Workspace14 是 F16 request3，不在能力 lane 已审阅候选中，资格仍为 unknown。
请求编号从0开始。一个 assistant 的多个工具调用作为同一完整组，依次回放并保留状态。
Workspace14 的邮件读取会改变真实 typed 对象；initial_emails/emails/received 的别名由
原始模型校验器恢复，不从 final_environment 拷贝状态，也不替换工具输出。

每个快照保存重建环境、完整历史、工具 schema、原采样参数和完成步数；summary.json
记录逐步返回哈希、环境变化及源文件哈希。重建初始状态必须逐字段往返相等；
前缀必须通过机制 lane 的保守 pre-write screen。发现未知工具、结果不一致或不可确定行为
即拒绝，不冻结系统时间、不伪造 ID、不扩展到完整 continuation runner。

```sh
SOURCE=/Users/wang/CSE-291A-Project
PYTHONDONTWRITEBYTECODE=1 "$SOURCE/.venv/bin/python" scripts/phase2_followup_checkpoint.py \
  --source-root "$SOURCE" --output results/phase2-followup-v1/checkpoint-restore
PYTHONDONTWRITEBYTECODE=1 "$SOURCE/.venv/bin/python" -m pytest -q \
  -p no:cacheprovider tests/test_phase2_followup_checkpoint.py
```

成功只证明这两条已记录前缀可精确重建，不证明后续推理可复现、攻击被阻止或干预有效。
结构/源校验失败返回非零且不写输出；真实回放拒绝保存 blocker 报告并返回非零。
