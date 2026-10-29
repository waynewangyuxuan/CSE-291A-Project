"""Join reviewed observations to original paired traces and render auditable cards."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path

ROOT = Path("results/phase2-first-pass-v1/analysis")
CARDS = ROOT / "cases"


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def content(message):
    value = message.get("content")
    return "\n".join(v["content"] for v in value) if isinstance(value, list) else value or ""


def compact(value, limit=350):
    text = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    return text if len(text) <= limit else text[:limit] + " … [显示截短；原始证据保留全文]"


def cell(value):
    return compact(value).replace("|", "\\|").replace("\n", "<br>")


def score(value):
    return "unknown" if value is None else str(value).lower()


def link(path):
    return os.path.relpath(path, CARDS)


def actions(trace):
    return [
        (
            i,
            [(c["function"], c["args"]) for c in m.get("tool_calls") or []]
            or [("STOP", content(m))],
        )
        for i, m in enumerate(trace["messages"])
        if m["role"] == "assistant"
    ]


def first_divergence(left, right):
    a, b = actions(left), actions(right)
    for step in range(max(len(a), len(b))):
        x = a[step] if step < len(a) else None
        y = b[step] if step < len(b) else None
        if x is None or y is None or x[1] != y[1]:
            return {"assistant_turn": step, "control": x, "variant": y}
    return None


def changes(before, after, path=""):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            result.extend(changes(before.get(key), after.get(key), f"{path}/{key}"))
        return result
    return [{"path": path, "before": before, "after": after}]


def steps(trace):
    result = []
    responses = {
        m["tool_call"]["id"]: (i, m) for i, m in enumerate(trace["messages"]) if m["role"] == "tool"
    }
    for request, (index, _) in enumerate(actions(trace)):
        m = trace["messages"][index]
        for call in m.get("tool_calls") or []:
            response = responses.get(call["id"])
            result.append(
                f"m{index} / req{request}: **{call['function']}** {cell(call['args'])}"
                + (
                    f"<br>→ m{response[0]}: {cell(content(response[1]))}"
                    + (
                        f"<br>tool error: {cell(response[1]['error'])}"
                        if response[1].get("error")
                        else ""
                    )
                    if response
                    else "<br>→ **未执行：没有对应工具返回**"
                )
            )
        if not m.get("tool_calls"):
            result.append(f"m{index} / req{request}: **STOP** {cell(content(m))}")
    return result


def contract_document():
    annotations = {
        (t["suite"], t["task"]): t for t in read(ROOT / "task-annotations.json")["tasks"]
    }
    lines = [
        "# 12 个任务的 contract 与属性",
        "",
        "U 是固定 AgentDojo v1.2.2 注册任务的 utility checker，"
        "可能检查回答、环境或二者；不等同于自然语言要求的完整语义。"
        "以下按真实注册版本解析，保留源码及 SHA。主题/早期信息依赖是人工解释，非随机分组。",
        "",
        "| Task | Theme | Reference tools | Clean U true / false / unknown（10 个配置） |",
        "|---|---|---|---|",
    ]
    contracts = read(ROOT / "task-contracts.json")
    for task in contracts:
        annotation = annotations[task["suite"], task["user_task_id"]]
        assert annotation["active_checker_sha256"] == task["checker_file_sha256"]
        s = task["clean_U_across_ten_settings"]
        counts = f"{s['success']} / {s['n'] - s['success'] - s['unknown']} / {s['unknown']}"
        lines.append(
            f"| {task['suite']} {task['user_task_id']} | {annotation['theme']} | "
            f"{task['ground_truth_tool_count']}: "
            f"{cell(json.loads(task['reference_tool_functions_in_source_order']))} | {counts} |"
        )
    for task in contracts:
        a = annotations[task["suite"], task["user_task_id"]]
        path = os.path.relpath(task["active_checker_path"], ROOT)
        lines += [
            "",
            f"## {task['suite']} / {task['user_task_id']}",
            "",
            task["legitimate_prompt"],
            "",
            f"**完成判据：** {a['criterion']}",
            "",
            f"**早期信息依赖（分析者标注）：** {a['early_information_dependence']}",
            "",
            f"[当前注册 checker]({path})，从 {task['active_checker_line']} 行开始。",
            "",
            "```python",
            task["active_checker_source"].rstrip(),
            "```",
        ]
    (ROOT / "task-contracts.md").write_text("\n".join(lines) + "\n")


def main():
    CARDS.mkdir(parents=True, exist_ok=True)
    contract_document()
    reviewed = read(ROOT / "reviewed-cases.json")
    index = [
        "# 配对案例卡",
        "",
        reviewed["selection"],
        "",
        "索引均从 0 开始：m 是 canonical trace 的 messages 下标，req 是 agent 请求序号。"
        "工具调用发出但没有返回时，单独标为未执行。",
        "",
    ]
    evidence, hashes = [], {}
    for case in reviewed["cases"]:
        folders = [Path(case[k]) for k in ["control", "variant"]]
        traces = [read(p / "trace.json.gz") for p in folders]
        results = [read(p / "result.json") for p in folders]
        assert traces[0]["user_prompt"] == traces[1]["user_prompt"]
        assert traces[0]["attack_payloads"] == traces[1]["attack_payloads"]
        assert traces[0]["initial_environment"] == traces[1]["initial_environment"]
        for p in folders:
            for name in ["trace.json.gz", "result.json", "requests.jsonl.gz"]:
                path = p / name
                hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        divergence = first_divergence(*traces)
        diff = [changes(t["initial_environment"], t["final_environment"]) for t in traces]
        evidence.append(
            {
                "id": case["id"],
                "first_action_divergence": divergence,
                "state_changes": dict(zip(["control", "variant"], diff, strict=True)),
            }
        )
        title = case["id"]
        index.append(f"- [{title}]({title}.md)：{case['observation']}")
        lines = [
            f"# {title}",
            "",
            f"**观察：** {case['observation']}",
            "",
            f"**解释：** {case['interpretation']}",
            "",
            f"**限制：** {case['limitation']}",
            "",
            "## 同一用户要求与注入",
            "",
            "```text",
            traces[0]["user_prompt"],
            "```",
            "",
            "```json",
            json.dumps(traces[0]["attack_payloads"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## 配对结果",
            "",
            "| 项目 | Control | Variant |",
            "|---|---|---|",
        ]
        lines.append(f"| Run | {folders[0].parts[-3]} | {folders[1].parts[-3]} |")
        for field in ["status", "utility", "attack_success", "tool_calls", "duration_seconds"]:
            lines.append(
                f"| {field} | {score(results[0].get(field))} | {score(results[1].get(field))} |"
            )
        lines += [
            "",
            "## 第一次动作/参数或停止的分岔",
            "",
            "按 assistant 回合对齐，仅忽略 call ID；不是轨迹相似度评分，"
            "也不把后续步骤强行语义对齐。",
            "",
            "```json",
            json.dumps(divergence, indent=2, ensure_ascii=False),
            "```",
            "",
            "## 原始动作与返回值并排",
            "",
            "返回值显示最多 350 字符。完整返回、执行后的状态及请求视图见下方原始文件。",
            "",
            "| Control | Variant |",
            "|---|---|",
        ]
        left, right = [steps(t) for t in traces]
        for i in range(max(len(left), len(right))):
            lines.append(
                f"| {left[i] if i < len(left) else ''} | {right[i] if i < len(right) else ''} |"
            )
        for name, trace, result, folder, delta in zip(
            ["Control", "Variant"], traces, results, folders, diff, strict=True
        ):
            lines += [
                "",
                f"## {name}：记忆、最终答案与状态",
                "",
                f"证据：[trace]({link(folder / 'trace.json.gz')}) · "
                f"[requests]({link(folder / 'requests.jsonl.gz')}) · "
                f"[result]({link(folder / 'result.json')})",
                "",
            ]
            compact_events = [
                e for e in trace["context_memory"]["events"] if e["event"] == "compact"
            ]
            selected = compact_events[:1] + (compact_events[-1:] if len(compact_events) > 1 else [])
            lines += [
                f"Compact events={len(compact_events)}；展示第一次及最后一次"
                "（applied=true 时，archive_boundary 之前的非 system/user 历史"
                "被移出 request view）。",
                "",
                "```json",
                json.dumps(selected, indent=2, ensure_ascii=False),
                "```",
                "",
            ]
            for event in trace["context_memory"]["events"]:
                if event["event"] == "search":
                    lines += [
                        "检索事件（完整返回片段）：",
                        "",
                        "```json",
                        json.dumps(event, indent=2, ensure_ascii=False),
                        "```",
                        "",
                    ]
            answer = content(trace["messages"][-1])
            lines += [
                "最终 assistant 文本（预算错误时可能为空或仍为调用回合）：",
                "",
                "```text",
                answer or "[空文本]",
                "```",
                "",
                f"环境改变字段：{', '.join(d['path'] for d in delta) or '[无]'}。"
                "完整 before/after 保留在 [case-evidence.json](../case-evidence.json)。",
                "",
            ]
            if result.get("error"):
                lines += ["```json", json.dumps(result["error"], indent=2), "```", ""]
        (CARDS / f"{title}.md").write_text("\n".join(lines).rstrip() + "\n")
    (CARDS / "README.md").write_text("\n".join(index) + "\n")
    (ROOT / "case-evidence.json").write_text(
        json.dumps({"cases": evidence, "source_hashes": hashes}, indent=2, ensure_ascii=False)
        + "\n"
    )
    print(f"Rendered {len(evidence)} paired case cards from original traces.")


if __name__ == "__main__":
    main()
