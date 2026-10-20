"""Offline raw audit and per-case comparison for the selected20; no new U/A or L scoring."""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
from pathlib import Path

from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite

from agent_tradeoffs.config import stable_hash
from agent_tradeoffs.prompt_defense_analysis import actual_sources
from agent_tradeoffs.prompt_policy import prompt_identity
from agent_tradeoffs.reporting import atomic_json

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


d = module("seed29_collection_driver", ROOT / "scripts/run_prompt_defense_seed29_diagnostics.py")
b = module("baseline_raw_audit_helpers", ROOT / "scripts/audit_phase1_results.py")


def audit(*, partial=False):
    manifest_path = d.CAMPAIGN / "manifest.json"
    raw_manifest = manifest_path.read_bytes()
    m = json.loads(raw_manifest)
    p = m["identity_inputs"]["plan"]
    d.verify_frozen(m["identity_inputs"])
    assert stable_hash(m["runtime"]["semantic_identity"]) == d.DEPLOYMENT_SHA256
    allowed_pids = {m["runtime"]["metadata_snapshot"]["pid"]}
    for observation in m.get("resume_runtime_observations", []):
        assert stable_hash(observation["semantic_identity"]) == d.DEPLOYMENT_SHA256
        allowed_pids.add(observation["metadata_snapshot"]["pid"])
    assert m["identity"] == stable_hash(
        {"inputs": m["identity_inputs"], "runtime": m["runtime"]["semantic_identity"]}
    )
    assert m["identity_inputs"] == d.load(d.DIRECTORY / "identity.json")
    assert p == d.load(d.DIRECTORY / "plan.json") and m["episodes"] == p["schedule"]
    n = m.get("finalized_episodes", 0)
    if not partial:
        assert m["status"] in ["finalized", "finalized_with_errors"] and n == 20
        assert m["post_guard"]["passed"] and m["final_idle_slots"]
        assert all(s["is_processing"] is False for s in m["final_idle_slots"])
        assert d.verify_protected(d.load(d.DIRECTORY / "protected-inventory.json"))["passed"]
    inventory = d.load(ROOT / "configs/prompt-defense-v1/source-inventory.json")
    records, comparisons, paired = [], [], {}
    for entry in p["schedule"][:n]:
        folder = d.CAMPAIGN / "episodes" / entry["episode_id"]
        journal = b.compression_records(folder)
        traw, trecord = b.artifact_bytes(folder, "trace.json", journal, compressed_required=True)
        qraw, qrecord = b.artifact_bytes(
            folder, "requests.jsonl", journal, compressed_required=True
        )
        r, t = d.load(folder / "result.json"), json.loads(traw)
        assert r["identity"] == t["identity"] == m["identity"]
        assert all(r.get(k) == v and t.get(k) == v for k, v in entry.items())
        assert r["status"] in ["completed", "error"]
        if r["status"] == "error":
            assert r["utility"] is None and r["attack_success"] is None
        else:
            assert isinstance(r["utility"], bool)
            assert (
                isinstance(r["attack_success"], bool)
                if entry["condition"] == "attack"
                else r["attack_success"] is None
            )
        assert t["system_prompt"] == prompt_identity(entry["arm"])
        config = d.selected_config(p, entry)
        measurements, schema = b.request_check(qraw, t, r, config)
        suite = get_suite("v1.2.2", entry["suite"])
        assert t["user_prompt"] == suite.get_user_task_by_id(entry["user_task_id"]).PROMPT
        runtime = FunctionsRuntime(suite.tools)
        expected = stable_hash(
            [
                {
                    "type": "function",
                    "function": {
                        "name": fn.name,
                        "description": fn.description,
                        "parameters": fn.parameters.model_json_schema(),
                    },
                }
                for fn in runtime.functions.values()
            ]
        )
        assert schema == expected if r["requests"] else schema is None
        events = [json.loads(s) for s in qraw.decode().splitlines()] if qraw else []
        requests = [e for e in events if e["event"] == "request"]
        for e in requests:
            assert 0 < e["timeout_seconds"] <= 120 and e["payload"]["seed"] == 29
            assert e["payload"]["messages"][0] == {
                "role": "system",
                "content": p["prompts"][entry["arm"]]["text"],
            }
        assert r["tool_calls"] <= 15 and r["duration_seconds"] >= 0
        memory = r["server_memory"]
        assert memory["pid"] in allowed_pids
        assert memory["samples"] > 0 and memory["peak_rss_bytes"] > 0
        oldfolder = Path(entry["reference"]["result_path"]).parent
        assert d.digest(oldfolder / "result.json") == entry["reference"]["result_sha256"]
        old = d.load(oldfolder / "result.json")
        oldtrace = json.loads(gzip.decompress((oldfolder / "trace.json.gz").read_bytes()))
        assert old["identity"] == entry["reference"]["identity"] == d.FIRST_IDENTITY
        for key in [
            "attack_payloads",
            "injection_defaults",
            "injections",
            "user_prompt",
            "injection_goal",
            "initial_environment",
        ]:
            assert t[key] == oldtrace[key], (entry["episode_id"], key)
        oldevents = [
            json.loads(line)
            for line in gzip.decompress((oldfolder / "requests.jsonl.gz").read_bytes())
            .decode()
            .splitlines()
        ]
        first = requests[0]["payload"] if requests else None
        oldfirst = next(e["payload"] for e in oldevents if e["event"] == "request")
        if first is not None:
            normalized = json.loads(json.dumps(first))
            assert normalized.pop("seed") == 29
            original = json.loads(json.dumps(oldfirst))
            assert original.pop("seed") == 17
            assert normalized == original, "Cross-seed first payload differs beyond seed"
        pair = paired.setdefault(entry["pair_id"], {})
        pair[entry["arm"]] = {"initial": t["initial_environment"], "first": first}
        sources = actual_sources(
            events, t["attack_payloads"], inventory["suites"][entry["suite"]]["vectors"]
        )
        observed = sorted({v for source in sources for v in source["matching_vector_candidates"]})
        assert observed == r["observed_injection_vectors"]
        fields = [
            "status",
            "utility",
            "attack_success",
            "observed_injection_vectors",
            "tool_calls",
            "requests",
            "duration_seconds",
            "error",
        ]
        comparisons.append(
            {
                **entry,
                "seed17": {k: old.get(k) for k in fields},
                "seed29": {k: r.get(k) for k in fields},
                "request_measurements": measurements,
                "actual_sources": sources,
            }
        )
        records.append(
            {
                "episode_id": entry["episode_id"],
                "result_sha256": d.digest(folder / "result.json"),
                "trace": trecord,
                "requests": qrecord,
                "compression_sha256": d.digest(folder / "compression.json"),
                "tool_schema_sha256": schema,
                "first_payload_only_seed_changed": True if first is not None else None,
                "first_payload_unavailable_reason": None
                if first is not None
                else "no_recorded_request",
            }
        )
    checked_pairs = 0
    checked_first_requests = 0
    unavailable_pairs = []
    for pair_id, pair in paired.items():
        if set(pair) != set(d.ARMS):
            continue
        before, after = [pair[a] for a in d.ARMS]
        assert before["initial"] == after["initial"]
        if before["first"] is not None and after["first"] is not None:
            a, c = [json.loads(json.dumps(x["first"])) for x in [before, after]]
            a["messages"][0] = c["messages"][0] = {
                "role": "system",
                "content": "<frozen-arm-prompt>",
            }
            assert a == c
            checked_first_requests += 1
        else:
            unavailable_pairs.append(
                {
                    "pair_id": pair_id,
                    "missing_request_arms": [arm for arm in d.ARMS if pair[arm]["first"] is None],
                    "reason": "no_recorded_request",
                }
            )
        checked_pairs += 1
    report = {
        "passed": True,
        "complete": not partial,
        "finalized_episodes": n,
        "campaign_identity": m["identity"],
        "manifest_sha256": b.digest(raw_manifest),
        "analysis_source_sha256": d.digest(Path(__file__)),
        "base_audit_sha256": d.digest(ROOT / "scripts/audit_phase1_results.py"),
        "paired_initial_checks": checked_pairs,
        "paired_first_request_checks": checked_first_requests,
        "paired_first_request_unavailable": unavailable_pairs,
        "allowed_verified_runtime_pids": sorted(allowed_pids),
        "inventory": records,
        "scope": (
            "Official U/A unchanged; selected-case diagnostics only. "
            "No new L/attempt labels, aggregate improvement estimate, inference, scorer "
            "or saved-state reconstruction."
        ),
    }
    if partial:
        atomic_json(d.CAMPAIGN / "partial-raw-audit.json", report)
    else:
        output = ROOT / "results" / d.NAME
        output.mkdir(parents=True, exist_ok=True)
        atomic_json(output / "raw-audit.json", report)
        atomic_json(
            output / "comparison.json",
            {
                "campaign_identity": m["identity"],
                "analysis_source_sha256": d.digest(Path(__file__)),
                "episodes": comparisons,
            },
        )
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "passed",
                    "complete",
                    "finalized_episodes",
                    "paired_initial_checks",
                    "paired_first_request_checks",
                ]
            }
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    audit(partial=args.partial)
