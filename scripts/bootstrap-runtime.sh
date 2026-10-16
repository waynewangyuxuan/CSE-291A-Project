#!/usr/bin/env bash
# Install one checksum-verified official runtime inside this repository only.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="$project_root/.cache/runtime"
release="b11678"
commit="b6334e37e6ef286fc5091a41b6561b2113d915fb"
archive="llama-${release}-bin-macos-arm64.tar.gz"
expected_sha256="81c98d485d430272895ed216ffd5537b400a36519e9da1fa412c5dbb3d1e0f4c"
archive_path="$runtime_root/downloads/$archive"
url="https://github.com/ggml-org/llama.cpp/releases/download/$release/$archive"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This pinned runtime targets Apple Silicon macOS; no global package was installed." >&2
  exit 1
fi

mkdir -p "$runtime_root/downloads"
if [[ ! -f "$archive_path" ]]; then
  curl --fail --location --retry 3 --output "$archive_path.part" "$url"
  mv "$archive_path.part" "$archive_path"
fi
actual_sha256="$(shasum -a 256 "$archive_path" | awk '{print $1}')"
if [[ "$actual_sha256" != "$expected_sha256" ]]; then
  echo "Runtime archive checksum mismatch: $archive_path" >&2
  echo "Expected $expected_sha256; got $actual_sha256" >&2
  exit 1
fi
tar -xzf "$archive_path" -C "$runtime_root"
cat > "$runtime_root/llama-$release/manifest.json" <<EOF
{
  "project": "ggml-org/llama.cpp",
  "release": "$release",
  "commit": "$commit",
  "platform": "macos-arm64",
  "archive_sha256": "$expected_sha256",
  "source_url": "$url"
}
EOF
"$runtime_root/llama-$release/llama-server" --version
echo "Installed: $runtime_root/llama-$release/llama-server"
