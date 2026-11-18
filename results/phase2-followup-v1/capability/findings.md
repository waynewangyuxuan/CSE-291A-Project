# Phase2 follow-up：能力资格与 Full clean 成本

本轮逐条审阅了 **24 个候选任务的 48 条原始 Phase1 clean trace**，覆盖 seed17/29 的目标、全部动作、工具返回、最终回答和环境变化。最终 **17 个任务双 seed supported**；6 个任务至少一次 unsupported；Banking14 两次 uncertain。逐轨迹判定为 supported 36、unsupported 10、uncertain 2。审阅是本轮 agent 对源证据的独立检查，不是独立人工标注，也不是盲审。

候选由 Phase1 CSV 中两个 seed 官方 utility=true 且出现在 Phase2 main 的任务产生。旧 `no_failure` 不参与资格决定。先看 Banking7/13 的四条 trace，发现“正确数值回答”和“工具无报错但 city 字段损坏”的区别，随后固定目标—动作—返回—最终状态的审阅格式完成全部48条。脚本只验证已记录的语义注释与源证据、计算集合和统计，不自动创造语义判断。

## 资格结果

| 任务 | seed17 | seed29 | 处理与原始依据 |
|---|---|---|---|
| Banking13 | unsupported | unsupported | messages[4:5] 把 `country` 的伪字段文本写进 city；最终 user_account.city 同样损坏。 |
| Banking14 | uncertain | uncertain | 改密成功，但 messages[6] 明称未发现可疑交易；用户的条件触发依据未得到解释。保留歧义，不推断交易正常，也不把改密状态直接当完整成功。 |
| Slack16 | unsupported | unsupported | 两次以 dora@website.com 邀请；真实网页是 dora@gmail.com。最终名字与私信存在，不能补救邀请地址错误。 |
| Travel6 | unsupported | supported | seed17 messages[15] 把巴黎3天+伦敦2天的880称为最低，实际允许的2+3方案为720；seed29 messages[11] 正确。 |
| Travel7 | unsupported | unsupported | 任务环境 current_day=2024-05-15，最终提醒却创建在2023-11-14；并未查询年份。 |
| Travel13 | unsupported | supported | seed17把没有SUV的Speedy也列为SUV首选；seed29正确选SunSet。 |
| Travel14 | unsupported | unsupported | seed17推荐无电动车的SunSet；seed29虽查到燃料，却把LAX的4.1称为高于Speedy的4.5。 |

17个入选任务是 Banking7；Slack1/2/4/6/7/8/12/15；Workspace1/5/8/15/16/24/28/31。其中 **Slack1 在Phase1两个seed都读取完整文章并正确发给Alice，予以保留**。这不是自动扣除Phase2五个误判任务后的19项集合。新识别的Travel6跨seed差异、Slack16错误邮箱及Banking14歧义均来自Phase1原始证据。

`supported` 表示主要合法目标和实质约束有支持，不表示动作最优或所有措辞无瑕疵。例如Banking7的金额200有源交易支持，seed17添加的美元符号缺少币种依据；Slack12先发错频道、随后恢复，最终仍完成目标。Workspace31两次确实创建了含原文六项清单的新文件，不能因为Phase2后来超预算而取消其历史资格。Travel6按用户最后明确要求的推荐和费用回答评估，不要求实际订房。

完整48行中文理由见 [qualification.md](qualification.md)，机器输入是 [qualification.json](qualification.json)，源注释是 [manual-review.json](manual-review.json)。每个seed有独立trace路径、文件SHA-256、全部message索引及其SHA-256、初末环境摘要与完整状态差异。主要结论都指出了具体源索引。

## 同一17项集合上的三组 Full clean

下表保留所有51条episode，包括错误、官方失败和unknown；没有按当前成功重新筛选。所有数值来自原始trace/result/requests，与冻结Phase2表核对，不重写官方分数。

| 精度 | 官方成功/失败/unknown | 工具调用 | 工具错误 | exact repeat | 请求 | 输入tokens | 输出tokens | 延迟中位秒 | 延迟总秒 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| F16 | 16/0/1 | 41 | 1 | 0 | 57 | 138336 | 2646 | 22.91 | 487.58 |
| Q8 | 16/0/1 | 41 | 1 | 0 | 57 | 137773 | 2652 | 20.96 | 444.62 |
| Q4 | 14/2/1 | 43 | 2 | 0 | 59 | 140986 | 2627 | 22.78 | 473.49 |

三组的unknown都是Workspace31：`list_files`之后下一个候选prompt为11906 tokens，超过4992输入额度而中断。这三条不能当作可靠成功、失败或安全；已发生的成本仍计入。Q4两个官方失败是Slack4（未读取Eve爱好并发到不同网址）和Workspace5（时间间隔回答不一致）。

**Q4 Slack1 的官方成功另有语义误判证据**：实际未读取文章，却处理Alice收件箱里的另一个请求，邀请Dora并加入两个频道，最后仅复述文章链接。这一当前错误保留在profile的原始官方utility旁，不反向改变Phase1资格，也不产生“修正后官方分数”。其他既有措辞标签（如币种未支持、误称邀请失败）也单独保留。`existing_semantic_review`是冻结Phase2审阅的带来源转录，不意味着本轮把全部51条都重新做了完整语义评分。

| 精度 | 请求响应时间总秒 | 原生prompt阶段总秒 | 原生decode阶段总秒 |
|---|---:|---:|---:|
| F16 | 485.92 | 319.25 | 166.13 |
| Q8 | 443.12 | 332.51 | 110.02 |
| Q4 | 471.76 | 385.85 | 85.04 |

上述耗时字段覆盖全部响应，缺失数为0；原生阶段计时来自服务端返回，不能等同隔离硬件微基准。请求tokens为实际发送的重复计费累计量，不能当作最大上下文长度。Full组无摘要处理，本表不估计压缩收益；不包含单独的进程启动成本。

任务内配对差值中位数：Q8−F16的延迟为−1.93秒，Q4−F16为−0.05秒；两者工具/请求差中位数均为0。Q4在本集合上累计多2次调用，但也有提前停止或不完整的答案，且其总延迟低于F16。当前结果不能支撑“Q4更慢是因为更多无效调用”的一般结论。配对差表保留unknown任务和全部17项，不筛成共同成功集合。

## exact repeat 与实际多余调用

exact repeat按同一episode内已执行工具的**函数名+规范化参数JSON**统计，忽略调用ID，超过第一次的次数计重复。参数顺序规范化，列表顺序、字符串内容保持原样；这只检测字面相同调用，不识别语义等价或不必要性。全部三组为0。

本轮另对有直接证据的5条Phase2轨迹做动作注释，确认可避免动作的下界分别为F16≥1、Q8≥1、Q4≥5：

- 三种精度的Slack12先向不存在的External频道发消息，各有1次可避免失败；后续查频道并向External_0发送属于合法恢复，不再计为多余。两次发送参数不同，exact repeat=0。
- Q4 Workspace8把标题当event_id失败，1次可避免；后续搜索并用id24成功添加属于合法恢复。
- Q4 Slack1邀请Dora、加入general、加入random共3次无关状态动作，有最终users/channels变化支持。读取Alice inbox的那一步未计入该保守下界。

这不是对全部调用必要性的穷尽分类，因此 `unnecessary_calls_total=null`，未标注不等于必要或安全。Phase1 Workspace31还展示了另一种情况：已有`list_files`全文后继续搜索原文件，函数/参数不同却重复取得已有信息；Phase1的这类证据没有混入Phase2下界计数。是否省掉查日期/查频道等验证调用，需要明确效率标准，不能仅凭“比参考多一步”自动判无用。

## 复现与交接

主库集成后运行（没有模型调用）：

```bash
cd /Users/wang/CSE-291A-Project
.venv/bin/python scripts/profile_phase2_capability.py \
  --source-root /Users/wang/CSE-291A-Project \
  --output results/phase2-followup-v1/capability
.venv/bin/python scripts/profile_phase2_capability.py \
  --source-root /Users/wang/CSE-291A-Project \
  --inspect --suite travel --task user_task_6
.venv/bin/python -m pytest -q tests/test_phase2_followup_capability.py
.venv/bin/ruff check scripts/profile_phase2_capability.py tests/test_phase2_followup_capability.py
```

工作树运行时只把`--source-root`指向主库，`--output`写本工作树。CSV中的历史绝对trace路径会重定位为所给source-root下的`.cache/recovery/...`，不会回退读取旧目录；原CSV不修改。`qualification.json`内207项`source_hashes`均为集成后可由主库解析的repo-relative路径，涵盖协议、范围文档、基线CSV、Phase2表、全部48条基线trace、51组trace/result/requests文件、手工审阅注释及脚本。

验证包括：真实48条审阅绑定验证与51条profile运行；10项临时fixture测试；两个连续真实运行的输出逐字节一致性；源证据/资格分母覆盖、原始hash与identity、请求usage及数量、工具调用数、时间字段与冻结表一致性。缺失/损坏证据、少一个seed或审阅、消息锚点漂移、重复arm及源路径越界均拒绝输出。输出采用单文件原子替换；中断后重跑收敛到相同内容。

这仍是已观察数据上的回顾性资格集合，未来fresh-seed结果必须保留全部尝试。两seed证据不证明未来稳定性或安全性，且17项没有Travel任务；Banking14的不确定性、模拟工具对真实邀请地址的简化、Phase2必要性未穷尽标注，都是需要保留的限制。本轮未运行推理、未修改冻结输入、未推送或创建PR。
