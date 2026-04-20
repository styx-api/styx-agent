#!/usr/bin/env bash
# Clone the upstream source repos that the Explorer needs.
# Shallow clones (--depth 1) to keep size small; we never need git history.
# Idempotent: skips any target directory that already exists.

set -eu

cd "$(dirname "$0")/.."

repos_dir="repos"
mkdir -p "$repos_dir"

clone_if_missing() {
    local name="$1"
    local url="$2"
    local dest="$repos_dir/$name"
    if [ -d "$dest" ]; then
        echo "[skip] $name already cloned at $dest"
        return
    fi
    echo "[clone] $name <- $url"
    git clone --depth 1 "$url" "$dest"
}

clone_if_missing afni       https://github.com/afni/afni.git
clone_if_missing ants       https://github.com/ANTsX/ANTs.git
clone_if_missing freesurfer https://github.com/freesurfer/freesurfer.git

# FSL has no single public GitHub repo; source is distributed via
# https://fsl.fmrib.ox.ac.uk/fsl/fslwiki. Add per-tool clones here once
# upstream locations are settled.
# clone_if_missing fsl/bet2 <URL>

echo "Done. Cloned repos live under $repos_dir/."
