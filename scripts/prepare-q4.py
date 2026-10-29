"""Derive audited Q4_K_M weights from the pinned Phase 1 F16 artifact."""

import json
import subprocess
from pathlib import Path

from agent_tradeoffs.model_artifacts import inspect_gguf
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runtime import download_model, load_runtime_config, sha256_file


def main():
    root = Path.cwd()
    source = load_runtime_config(root / "configs/runtime-qwen3-8b-f16.toml")
    verified = download_model(source)
    target = root / ".cache/models/qwen3-8b-derived-q4/Qwen3-8B-Q4_K_M.gguf"
    provenance_path = target.with_suffix(".provenance.json")
    if target.exists() and provenance_path.exists():
        previous = json.loads(provenance_path.read_text())
        if previous["source_sha256"] == verified["sha256"] and previous["sha256"] == sha256_file(
            target
        ):
            print(json.dumps(previous, indent=2))
            return
        raise ValueError("Existing derived Q4 differs from its provenance")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".gguf.part")
    binary = root / ".cache/runtime/llama-b11678/llama-quantize"
    command = [str(binary), str(source.model_path), str(partial), "Q4_K_M", "8"]
    log = root / "artifacts/model-preparation/derived-q4.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as handle:
        subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, check=True)
    audit = inspect_gguf(partial)
    if audit["metadata"]["general.file_type"] != 15 or audit["parameter_count"] != 8190735360:
        raise ValueError("Unexpected quantized model structure")
    digest = sha256_file(partial)
    partial.replace(target)
    provenance = {
        "kind": "quantized_gguf",
        "repo_id": source.model_artifact.repo_id,
        "revision": source.model_artifact.revision,
        "filename": target.name,
        "sha256": digest,
        "source_sha256": verified["sha256"],
        "source_path": str(source.model_path),
        "source_provenance": verified["provenance"],
        "weight_quantization": "Q4_K_M",
        "quantizer_sha256": sha256_file(binary),
        "command": command,
        "gguf_audit": audit,
    }
    atomic_json(provenance_path, provenance)
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
