#!/usr/bin/env python3
import os
import sys
import shutil
import tarfile
import urllib.request
from pathlib import Path

# Paths
REPO_BACKEND = Path("/home/ubuntu/Desktop/docint-ocr/repo-git/backend")
BACKEND_HF_HUB = REPO_BACKEND / "data" / "models" / "huggingface" / "hub"
USER_HF_HUB = Path.home() / ".cache" / "huggingface" / "hub"

# 1. HuggingFace models (Surya + the TATR table-structure model)
SURYA_MODELS = [
    "microsoft/table-transformer-structure-recognition",
    "vikp/surya_det3",
    "vikp/surya_rec2",
    "vikp/surya_layout3",
    "vikp/surya_tablerec",
    "datalab-to/surya_layout2",
    "datalab-to/surya-ocr-2-gguf",
    "vikp/surya_rec",
    "vikp/surya_layout",
    "datalab-to/surya_layout",
]

def sync_directories(src: Path, dst: Path):
    """Recursively sync src to dst using hardlinks where possible to conserve space."""
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel_root = Path(root).relative_to(src)
        target_root = dst / rel_root
        target_root.mkdir(parents=True, exist_ok=True)
        for f in files:
            src_file = Path(root) / f
            dst_file = target_root / f
            if not dst_file.exists():
                try:
                    os.link(src_file, dst_file)
                except OSError:
                    shutil.copy2(src_file, dst_file)

def download_surya():
    print("\n" + "="*60)
    print(" [1/1] Downloading & Configuring Surya Hugging Face Models")
    print("="*60)
    from huggingface_hub import snapshot_download

    BACKEND_HF_HUB.mkdir(parents=True, exist_ok=True)
    USER_HF_HUB.mkdir(parents=True, exist_ok=True)

    # First, link/copy any models that are already in ~/.cache/huggingface/hub into backend
    if USER_HF_HUB.exists():
        print(f"[SYNC] Syncing existing hub cache from {USER_HF_HUB} -> {BACKEND_HF_HUB}")
        sync_directories(USER_HF_HUB, BACKEND_HF_HUB)

    for repo_id in SURYA_MODELS:
        model_dir_name = "models--" + repo_id.replace("/", "--")
        backend_model_dir = BACKEND_HF_HUB / model_dir_name
        
        print(f"\n[SURYA] Checking {repo_id}...")
        try:
            # Download directly with cache_dir pointing to BACKEND_HF_HUB
            # and ignore symlink errors if any
            path = snapshot_download(
                repo_id=repo_id,
                cache_dir=str(BACKEND_HF_HUB.parent),
                resume_download=True,
                max_workers=4
            )
            print(f"[OK] {repo_id} -> {path}")
        except Exception as e:
            print(f"[ERROR] Failed downloading {repo_id}: {e}")

    # Sync back to ~/.cache/huggingface/hub so standard tools also find them
    print(f"\n[SYNC] Syncing models to {USER_HF_HUB}...")
    sync_directories(BACKEND_HF_HUB, USER_HF_HUB)
    print("[OK] Surya models configured successfully.")

def verify_all():
    print("\n" + "="*60)
    print(" VERIFICATION AUDIT REPORT")
    print("="*60)
    print("\n--- Surya Models in backend/data/models/huggingface/hub/ ---")
    for m in SURYA_MODELS:
        folder = "models--" + m.replace("/", "--")
        p1 = BACKEND_HF_HUB / folder
        p2 = USER_HF_HUB / folder
        status1 = "PRESENT" if p1.exists() else "MISSING"
        status2 = "PRESENT" if p2.exists() else "MISSING"
        print(f" - {m:<30}: Backend: {status1:<8} | User Cache: {status2}")
    print("="*60 + "\n")

if __name__ == "__main__":
    download_surya()
    verify_all()
