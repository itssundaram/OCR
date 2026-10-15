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
BACKEND_PADDLE_WHL = REPO_BACKEND / "data" / "models" / "paddleocr" / "whl"
USER_HF_HUB = Path.home() / ".cache" / "huggingface" / "hub"
USER_PADDLE_WHL = Path.home() / ".paddleocr" / "whl"

# 1. HuggingFace Surya models
SURYA_MODELS = [
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

# 2. PaddleOCR models & URLs
PADDLE_MODELS = {
    "en_PP-OCRv3_det_infer": {
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_det_infer.tar",
        "subpath": "det/en",
    },
    "en_PP-OCRv4_rec_infer": {
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv4/english/en_PP-OCRv4_rec_infer.tar",
        "subpath": "rec/en",
    },
    "ch_ppocr_mobile_v2.0_cls_infer": {
        "url": "https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_cls_infer.tar",
        "subpath": "cls",
    },
    "ch_PP-OCRv4_det_infer": {
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv4/chinese/ch_PP-OCRv4_det_infer.tar",
        "subpath": "det/ch",
    },
    "ch_PP-OCRv4_rec_infer": {
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv4/chinese/ch_PP-OCRv4_rec_infer.tar",
        "subpath": "rec/ch",
    },
    "ch_ppstructure_mobile_v2.0_SLANet_infer": {
        "url": "https://paddleocr.bj.bcebos.com/ppstructure/models/slanet/ch_ppstructure_mobile_v2.0_SLANet_infer.tar",
        "subpath": "table",
    },
    "picodet_lcnet_x1_0_fgd_layout_cdla_infer": {
        "url": "https://paddleocr.bj.bcebos.com/ppstructure/models/layout/picodet_lcnet_x1_0_fgd_layout_cdla_infer.tar",
        "subpath": "layout",
    },
    "rec_latex_ocr_infer": {
        "url": "https://paddleocr.bj.bcebos.com/contribution/rec_latex_ocr_infer.tar",
        "subpath": "formula",
    },
}

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

def download_paddle():
    print("\n" + "="*60)
    print(" [1/2] Downloading & Configuring PaddleOCR Models")
    print("="*60)
    BACKEND_PADDLE_WHL.mkdir(parents=True, exist_ok=True)
    USER_PADDLE_WHL.mkdir(parents=True, exist_ok=True)
    
    tmp_dir = REPO_BACKEND / "data" / "models" / "paddleocr" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for model_name, info in PADDLE_MODELS.items():
        url = info["url"]
        subpath = info["subpath"]
        
        # Check if already present in user cache or backend
        target_user = USER_PADDLE_WHL / subpath / model_name
        target_backend = BACKEND_PADDLE_WHL / subpath / model_name
        flat_backend = BACKEND_PADDLE_WHL / model_name
        flat_user = USER_PADDLE_WHL / model_name
        
        if (target_user / "inference.pdmodel").exists() or (target_backend / "inference.pdmodel").exists() or (flat_backend / "inference.pdmodel").exists():
            print(f"[EXISTS] {model_name}")
        else:
            tar_path = tmp_dir / f"{model_name}.tar"
            print(f"[DOWNLOADING] {model_name} from {url}...")
            try:
                urllib.request.urlretrieve(url, tar_path)
                print(f"[EXTRACTING] {model_name}...")
                with tarfile.open(tar_path) as tar:
                    tar.extractall(path=tmp_dir)
                
                # The extracted folder might be model_name or directly inside tmp_dir
                extracted = tmp_dir / model_name
                if not extracted.exists():
                    print(f"[WARN] Extracted path {extracted} not found directly, checking tmp_dir")
                else:
                    target_backend.parent.mkdir(parents=True, exist_ok=True)
                    if target_backend.exists():
                        shutil.rmtree(target_backend)
                    shutil.move(str(extracted), str(target_backend))
                tar_path.unlink(missing_ok=True)
            except Exception as e:
                print(f"[ERROR] Failed downloading {model_name}: {e}")

        # Ensure flat and subpath links exist in both locations
        for base_dir in [BACKEND_PADDLE_WHL, USER_PADDLE_WHL]:
            loc_sub = base_dir / subpath / model_name
            loc_flat = base_dir / model_name
            
            # Find whichever actually has the files
            source = None
            if loc_sub.exists() and any(loc_sub.iterdir()):
                source = loc_sub
            elif loc_flat.exists() and any(loc_flat.iterdir()):
                source = loc_flat
            elif (USER_PADDLE_WHL / subpath / model_name).exists():
                source = USER_PADDLE_WHL / subpath / model_name
            elif (BACKEND_PADDLE_WHL / subpath / model_name).exists():
                source = BACKEND_PADDLE_WHL / subpath / model_name

            if source:
                loc_sub.parent.mkdir(parents=True, exist_ok=True)
                if not loc_sub.exists():
                    try:
                        loc_sub.symlink_to(source, target_is_directory=True)
                    except Exception:
                        shutil.copytree(source, loc_sub)
                if not loc_flat.exists():
                    try:
                        loc_flat.symlink_to(source, target_is_directory=True)
                    except Exception:
                        shutil.copytree(source, loc_flat)

    # Clean up tmp
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("[OK] PaddleOCR models configured successfully.")

def download_surya():
    print("\n" + "="*60)
    print(" [2/2] Downloading & Configuring Surya Hugging Face Models")
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
    print("\n--- PaddleOCR Models in backend/data/models/paddleocr/whl/ ---")
    for m in PADDLE_MODELS:
        p1 = BACKEND_PADDLE_WHL / m
        p2 = USER_PADDLE_WHL / m
        status1 = "PRESENT" if p1.exists() else "MISSING"
        status2 = "PRESENT" if p2.exists() else "MISSING"
        print(f" - {m:<45}: Backend: {status1:<8} | User Cache: {status2}")

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
    download_paddle()
    download_surya()
    verify_all()
