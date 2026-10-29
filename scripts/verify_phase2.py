"""Drive real local compaction and history-search tool paths outside formal measurements."""

import argparse
import gzip
import json
import time
from pathlib import Path

from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite
from phase2_first_pass import context, runtime

from agent_tradeoffs.agent import LocalAgent
from agent_tradeoffs.config import ExperimentConfig
from agent_tradeoffs.memory import EpisodeMemory, MemoryServices
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runtime import download_model, probe, start_server, stop_server


def drive(precision):
    directory = Path("artifacts/phase2-verification") / str(time.time_ns())
    directory.mkdir(parents=True)
    source = Path(
        "artifacts/runs/phase1-f16-s17-banking-clean/episodes/user_task_15__clean/requests.jsonl.gz"
    )
    with gzip.open(source, "rt") as handle:
        requests = [json.loads(line) for line in handle]
    transcript = next(
        row["payload"]["messages"]
        for row in requests
        if row["event"] == "request" and len(row["payload"]["messages"]) >= 6
    )
    results = []
    for policy in ["truncate", "long_summary", "short_summary", "short_summary_rag"]:
        settings = ExperimentConfig.model_validate({"context": context(policy)}).context
        folder = directory / policy
        folder.mkdir()
        services = None if policy == "truncate" else MemoryServices(settings, folder, lambda: 120)
        memory = EpisodeMemory(settings, services)
        view = memory.prepare(transcript)
        assert view[:2] == transcript[:2]
        assert memory.metrics()["compactions"] == 1, memory.events
        if policy == "short_summary_rag":
            query = " ".join(
                str(m.get("content", "")) for m in memory.archive if m["role"] == "tool"
            )
            words = query.split()
            query = " ".join(words[:8])[:256]
            result, error = memory.search({"query": query})
            assert error is None
            assert json.loads(result)["matches"]
        atomic_json(
            folder / "evidence.json",
            {"original": transcript, "view": view, "metrics": memory.metrics()},
        )
        results.append({"policy": policy, "compactions": memory.metrics()["compactions"]})
    settings = ExperimentConfig.model_validate(
        {
            "model": {
                "model": runtime(precision).model_alias,
                "base_url": "http://127.0.0.1:8083/v1",
                "temperature": 0.7,
                "seed": 17,
                "extra_body": {
                    "chat_template_kwargs": {"enable_thinking": False},
                    "cache_prompt": False,
                    "top_p": 0.8,
                    "top_k": 20,
                    "min_p": 0.0,
                },
            },
            "context": context("short_summary_rag"),
            "runner": {"episode_timeout_seconds": 600},
        }
    )
    folder = directory / "live-search-agent"
    folder.mkdir()
    suite = get_suite("v1.2.2", "banking")
    env = suite.load_and_inject_default_environment({})
    agent = LocalAgent(settings, folder)
    prompt = (
        "This is a read-only tool integration check. "
        "First call get_most_recent_transactions with n=20. "
        "After receiving its result, separately call get_balance. "
        'Then call search_history with query "Pizza" to look up the archived first result. '
        "Finally report the Pizza party amount "
        "and cite the original message ID from the search result. "
        "Do not change any banking data."
    )
    try:
        agent.query(prompt, FunctionsRuntime(suite.tools), env)
    finally:
        atomic_json(folder / "trace.json", agent.trace())
    searches = [e for e in agent.memory.events if e["event"] == "search"]
    assert searches and any(e["result"]["matches"] for e in searches), agent.trace()
    assert agent.initial_environment == agent.environment.model_dump(mode="json")
    assert agent.memory.metrics()["compactions"] > 0
    receipt = {
        "status": "passed",
        "precision": precision,
        "directory": str(directory),
        "real_summary_cases": results,
        "real_agent_searches": len(searches),
        "formal_episodes": False,
        "purpose": "Wiring proof; explicit dev-only search request.",
    }
    atomic_json(directory / "receipt.json", receipt)
    print(json.dumps(receipt), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["up", "drive", "down", "doctor"])
    parser.add_argument("--precision", choices=["f16", "q4"], default="q4")
    args = parser.parse_args()
    if args.action == "up":
        download_model(runtime(args.precision))
        start_server(runtime("summarizer"))
        start_server(runtime(args.precision))
    elif args.action == "down":
        stop_server(runtime(args.precision))
        stop_server(runtime("summarizer"))
    elif args.action == "doctor":
        for precision in ["summarizer", args.precision]:
            print(
                json.dumps(
                    {"runtime": precision, "valid": probe(runtime(precision))["tool_call_valid"]}
                )
            )
    else:
        drive(args.precision)


if __name__ == "__main__":
    main()
