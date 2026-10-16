# Phase 1 model provenance

Phase 1 uses original Qwen3-8B BF16 weights converted to F16 GGUF, with F32
normalization tensors and F16 KV cache. No integer weight quantization is applied.
BF16 → F16 is a floating point format conversion, so this baseline should be
described as **unquantized F16**, rather than bit-identical BF16 or FP32.

The runtime context allocation is fixed at **32,768 tokens** before the first
formal run. The 64 GB machine can accommodate the F16 weights and this F16 KV
cache. This larger allocation reduces context overflow as a confounder in the
full-context baseline. Phase 2 can apply explicit smaller context budgets;
Phase 1 uses the full history without a truncation or summarization policy.

The canonical source is [`Qwen/Qwen3-8B` at
`b968826d9c46dd6066d109eabc6255188de91218`](https://huggingface.co/Qwen/Qwen3-8B/tree/b968826d9c46dd6066d109eabc6255188de91218).
All five original safetensors shards are verified against their official LFS
SHA256 digests before a tensor is used. Tokenizer/configuration files are checked
against canonical Git blob IDs or LFS SHA256. The exact pins live in
`src/agent_tradeoffs/model_artifacts.py`.

The conversion runs the Qwen3 implementation from the same [llama.cpp commit
`b6334e37e6ef286fc5091a41b6561b2113d915fb`](https://github.com/ggml-org/llama.cpp/tree/b6334e37e6ef286fc5091a41b6561b2113d915fb)
as the inference binary. The source archive SHA256 is checked and its original
files are re-extracted before conversion. A small adapter supplies verified
original tensors from RAM to the unchanged upstream converter. This avoids
keeping a second 16 GB model copy on disk. It does not derive F16 weights from
the earlier Q4 pilot model.

```bash
uv run --group modelprep python scripts/prepare-f16.py
```

The converter needs approximately 16.4 GB final disk space plus an 800 MiB reserve.
It downloads each full original shard into RAM (largest approximately 4 GB),
checks SHA256, then writes tensors directly to the final artifact's `.part`
file. Interrupted conversion restarts; completed artifacts are reused only if
their checksum matches the provenance manifest. Model files and source caches
remain ignored beneath `.cache/`.

Before promotion, every GGUF tensor's storage type, parameter count, and file
bounds are audited. The derived `.provenance.json` records source hashes,
conversion versions, adapter hash, tensor audit, and final artifact SHA256.
The final SHA256 is pinned in `configs/runtime-qwen3-8b-f16.toml`.

The completed artifact was verified on 2026-10-15:

| Property | Verified value |
| --- | --- |
| SHA256 | `c09c8f49a6a56b0165f2d12517835e29dafdbc904d5940ce5983f1ada2485bad` |
| File size | 16,388,044,256 bytes |
| Parameters | 8,190,735,360 |
| Tensor storage | 254 F16 tensors; 145 F32 tensors; 0 quantized tensors |
| Native context limit | 40,960 tokens |

Preparation evidence is retained in
`artifacts/model-preparation/qwen3-8b-f16.log`; the machine-readable provenance
is beside the model in `.cache/models/qwen3-8b-f16/`.
