#!/usr/bin/env bash
# Build (or rebuild) the operator image from this folder's Containerfile.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
image="localhost/sssf-operator-$(basename "$(git -C "$here" rev-parse --show-toplevel)"):latest"
command -v podman >/dev/null || { echo "podman is not installed; see cookbooks/install.md" >&2; exit 1; }
podman build -t "$image" -f "$here/Containerfile" "$here"
