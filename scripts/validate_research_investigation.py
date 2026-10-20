"""Validate investigation coverage, immutable inputs, figures and local guide links offline."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
import struct
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/research-investigation-v1"
FIRST = ROOT / "results/prompt-defense-first-wave-v1"
ARMS = ("default", "source_boundary_v1")
RULE_SHA = "299b8cde8bae8c7638acc8f4dcb78138501b88b43cd3e049a1e2f9f899eaa6d8"


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    inputs = load(OUT / "protected-inputs.json")
    for item in inputs["files"]:
        path = ROOT / item["path"]
        assert path.stat().st_size == item["bytes"] and sha(path) == item["sha256"], path
    assert len(inputs["files"]) == 224
    rows = load(FIRST / "episodes.json")
    assert len(rows) == 600
    by_id = {r["episode_id"]: r for r in rows}
    assert len(by_id) == 600
    ps = defaultdict(dict)
    for row in rows:
        assert row["arm"] not in ps[row["pair_id"]]
        ps[row["pair_id"]][row["arm"]] = row
        assert row["seed"] == 17
        if row["status"] != "completed":
            assert row["utility"] is None and row["attack_success"] is None
    assert len(ps) == 300 and all(set(v) == set(ARMS) for v in ps.values())
    contracts = load(OUT / "task-contracts.json")["tasks"]
    keys = {(r["suite"], r["user_task_id"]) for r in rows}
    assert len(contracts) == len(keys) == 60
    assert {(r["suite"], r["user_task_id"]) for r in contracts} == keys
    assert all(r["L_score_status"] == "not_scored" for r in contracts)
    assert len(list(csv.DictReader((OUT / "task-contracts.csv").open()))) == 60
    assert len(list(csv.DictReader((OUT / "task-patterns.csv").open()))) == 60
    stages = load(OUT / "attack-stages.json")["records"]
    assert len(stages) == 480
    assert {r["episode_id"] for r in stages} == {
        r["episode_id"] for r in rows if r["condition"] == "attack"
    }
    assert sha(OUT / "attack-rules.json") == RULE_SHA
    for row in stages:
        original = by_id[row["episode_id"]]
        for key in ("utility", "attack_success", "status", "arm", "pair_id"):
            assert row[key] == original[key], (row["episode_id"], key)
        assert row["rule_sha256"] == RULE_SHA
    summary = load(OUT / "attack-stage-summary.json")
    for arm in ARMS:
        counts = Counter(r["stage_summary"] for r in stages if r["arm"] == arm)
        assert sum(counts.values()) == 240
        assert counts == Counter(summary["arms"][arm]["stages"])
    selection = load(OUT / "case-selection.json")
    cards = load(OUT / "case-cards.json")
    assert cards["selection_sha256"] == selection["selection_sha256"]
    assert len(cards["cases"]) == 30
    assert Counter(c["selection_group"] for c in cards["cases"]) == {
        "A_true_to_false": 13,
        "A_false_to_true": 5,
        "stable_A_true": 6,
        "stable_A_false": 6,
    }
    flips = []
    for pair in ps.values():
        d, r = (pair[a] for a in ARMS)
        if d["condition"] != "attack":
            continue
        if d["attack_success"] is True and r["attack_success"] is False:
            flips.append(pair)
    assert len(flips) == 13
    expected_flips = {
        pid
        for pid, pair in ps.items()
        if pair[ARMS[0]]["condition"] == "attack"
        and pair[ARMS[0]]["attack_success"] is not None
        and pair[ARMS[1]]["attack_success"] is not None
        and pair[ARMS[0]]["attack_success"] != pair[ARMS[1]]["attack_success"]
    }
    assert len(expected_flips) == 18
    assert {
        c["pair_id"] for c in cards["cases"] if c["selection_group"].startswith("A_")
    } == expected_flips
    assert Counter(
        str(p[ARMS[0]]["utility"]) + "->" + str(p[ARMS[1]]["utility"]) for p in flips
    ) == {"True->True": 3, "False->False": 10}
    assert sum(r["status"] != "completed" for r in rows) == 33
    figs = load(OUT / "figure-manifest.json")
    assert figs["stage_figure_ready"] and len(figs["figures"]) >= 11
    assert figs["source_sha256"] == sha(ROOT / "scripts/build_research_figures.py")
    for relative, digest in (figs["primary_inputs"] | figs["analysis_inputs"]).items():
        assert sha(ROOT / relative) == digest
    figure_info = []
    for figure in figs["figures"]:
        png, svg = (OUT / (figure["name"] + suffix) for suffix in (".png", ".svg"))
        assert sha(png) == figure["png_sha256"]
        assert sha(svg) == figure["svg_sha256"]
        raw = png.read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", raw[16:24])
        assert width >= 1000 and height >= 500
        ET.parse(svg)
        figure_info.append({"name": figure["name"], "width": width, "height": height})
    checked_links = []
    for match in re.finditer(r"!?\[[^\]\n]+\]\(([^)\n]+)\)", (OUT / "README.md").read_text()):
        target = match.group(1)
        if target.startswith(("https://", "http://", "#")):
            continue
        relative = unquote(target.split("#", 1)[0]).strip("<>")
        path = OUT / relative
        assert path.exists(), target
        checked_links.append(target)
    stance = load(OUT / "attack-visible-stance-annotations.json")["records"]
    assert len(stance) == 480
    assert {r["episode_id"] for r in stance} == {r["episode_id"] for r in stages}
    for row in stance:
        for key in ("utility", "attack_success", "status", "arm", "pair_id"):
            assert row[key] == by_id[row["episode_id"]][key]
        assert row["reviewed_complete_visible_text"]
        if row["stance"] != "no_explicit_stance":
            assert row["stance_evidence"]
    assert figs["stance_figure_ready"] and len(figs["figures"]) == 12
    scripts = sorted(ROOT.glob("scripts/build_research_*.py")) + [
        ROOT / "scripts/build_attack_stage_annotations.py",
        ROOT / "scripts/build_attack_visible_stance_annotations.py",
        Path(__file__),
    ]
    for path in scripts:
        ast.parse(path.read_text())
    products = {}
    for path in sorted(OUT.iterdir()):
        if path.is_file() and path.name != "validation-receipt.json":
            products[path.name] = {"bytes": path.stat().st_size, "sha256": sha(path)}
    result = {
        "schema_version": 1,
        "passed": True,
        "scope": "Offline structural/numeric validation; independent raw audit is separate.",
        "validator_sha256": sha(Path(__file__)),
        "coverage": {
            "primary_episodes": 600,
            "pairs": 300,
            "tasks": 60,
            "attack_annotations": 480,
            "visible_stance_annotations": 480,
            "case_pairs": 30,
            "protected_preexisting_tracked_files": 224,
            "original_terminal_unknown_episodes": 33,
        },
        "checks": {
            "original_input_bytes_unchanged": True,
            "stage_official_scores_equal_primary": True,
            "no_L_labels": True,
            "frozen_rule_sha_matches": True,
            "all_18_A_flips_selected": True,
            "A_true_to_false_U_transitions": {"True->True": 3, "False->False": 10},
            "guide_local_links_exist": True,
            "figure_hashes_dimensions_svg_valid": True,
        },
        "checked_guide_links": checked_links,
        "figures": figure_info,
        "script_sha256": {str(p.relative_to(ROOT)): sha(p) for p in scripts},
        "products": products,
        "limits": (
            "PNG dimensions/SVG parsing are structural checks; "
            "visual QA and qualitative coding require separate records. "
            "No inference/checker/environment reconstruction."
        ),
    }
    (OUT / "validation-receipt.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "passed": True,
                "coverage": result["coverage"],
                "figures": len(figure_info),
                "guide_links": len(checked_links),
            }
        )
    )


if __name__ == "__main__":
    main()
