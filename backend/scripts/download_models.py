"""
Download the model weights this project needs, skipping what is already there (Windows, Linux, macOS).

    cd backend
    (activate the venv)
    python scripts/download_models.py            # download what is missing   (needs internet)
    python scripts/download_models.py --check    # only report                (works offline)

Weights go to MODEL_CACHE_DIR/huggingface (default backend/data/models/huggingface) - the folder the app points
TRANSFORMERS_CACHE at, with the `models--org--name` folders directly inside it. Also checks Tesseract and Ollama.

Air-gapped install: run this on any PC that has internet, then copy the whole backend/data/models folder to the
same place on the offline machine (USB / file share). The offline machine never needs to run the download.
Only the surya / tesseract / learned_anchor pipelines use these weights; fast_ocr needs none of them.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

# (repo id, what uses it)
HF_MODELS = [
    ("vikp/surya_det3", "Surya text detection (surya + tesseract pipelines)"),
    ("vikp/surya_rec2", "Surya text recognition (surya pipeline)"),
    ("vikp/surya_layout3", "Surya layout (surya + tesseract pipelines)"),
    ("vikp/surya_order", "Surya reading order"),
    ("microsoft/table-transformer-structure-recognition", "TATR table structure (fallback for pages with no table found by geometry)"),
    ("microsoft/trocr-large-handwritten", "handwriting re-read of low-confidence boxes"),
]
SKIP = ["*.h5", "*.msgpack", "*.ot", "tf_model*", "flax_model*", "*.onnx", "*.tflite", "rust_model*"]


def cache_dir() -> Path:
    env = os.environ.get("MODEL_CACHE_DIR")
    if not env:
        envfile = BACKEND / ".env"
        if envfile.exists():
            for line in envfile.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("MODEL_CACHE_DIR="):
                    env = line.split("=", 1)[1].strip().strip('"')
    base = Path(env) if env else BACKEND / "data" / "models"
    if not base.is_absolute():
        base = BACKEND / base
    return (base / "huggingface").resolve()


def _manifest(dest: Path, repo: str) -> Path:
    return dest / "_manifest" / (repo.replace("/", "--") + ".json")


def present(dest: Path, repo: str) -> bool:
    """True only when EVERY file of the repo (as listed on the Hub at download time) is in the snapshot.

    A folder that merely contains some files is not enough: a snapshot missing preprocessor_config.json makes
    transformers fall back to the network ("couldn't connect to huggingface.co") even though the weights are there.
    A model with no manifest was never verified, so it counts as incomplete and gets (re)checked by the downloader.
    """
    import json

    mf = _manifest(dest, repo)
    snaps = dest / ("models--" + repo.replace("/", "--")) / "snapshots"
    if not mf.is_file() or not snaps.is_dir():
        return False
    try:
        files = json.loads(mf.read_text(encoding="utf-8"))["files"]
    except Exception:
        return False
    return any(all((snap / f).is_file() for f in files) for snap in snaps.iterdir() if snap.is_dir())


def fetch_repo(dest: Path, repo: str) -> list[str]:
    """Download one repo completely, then verify against the Hub file list. Returns the files still missing."""
    import fnmatch
    import json

    from huggingface_hub import HfApi, hf_hub_download, snapshot_download

    info = HfApi().model_info(repo)
    files = [s.rfilename for s in (info.siblings or []) if not any(fnmatch.fnmatch(s.rfilename, pat) for pat in SKIP)]
    snap = Path(snapshot_download(repo_id=repo, cache_dir=str(dest), ignore_patterns=SKIP))
    for f in files:  # fetch any file the bulk download skipped, one by one
        if not (snap / f).is_file():
            print(f"  re-fetching {f}", flush=True)
            hf_hub_download(repo_id=repo, filename=f, cache_dir=str(dest), force_download=True)
    missing = [f for f in files if not (snap / f).is_file()]
    if not missing:
        mf = _manifest(dest, repo)
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"repo": repo, "revision": info.sha, "files": files}, indent=1), encoding="utf-8")
    return missing


def ollama_model() -> str:
    env = os.environ.get("AI_MODEL")
    envfile = BACKEND / ".env"
    if not env and envfile.exists():
        for line in envfile.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("AI_MODEL="):
                env = line.split("=", 1)[1].strip().strip('"')
    return env or "llama3.2-vision"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, download nothing")
    ap.add_argument("--dest", help="folder for the weights (default: MODEL_CACHE_DIR/huggingface, i.e. backend/data/models/huggingface)")
    args = ap.parse_args()
    dest = Path(args.dest).resolve() if args.dest else cache_dir()
    # Everything stays inside the project folder: point every Hugging Face location (cache, token/xet scratch,
    # temp) at `dest` BEFORE huggingface_hub is imported, so nothing is written to C:\Users\<you>\.cache.
    for var in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
        os.environ[var] = str(dest)
    os.environ["HF_XET_CACHE"] = str(dest / "_xet")
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    os.environ["TMPDIR"] = os.environ["TEMP"] = os.environ["TMP"] = str(dest / "_tmp")
    (dest / "_tmp").mkdir(parents=True, exist_ok=True)
    print(f"Model folder: {dest}\n")
    missing = [(r, why) for r, why in HF_MODELS if not present(dest, r)]
    for r, why in HF_MODELS:
        print(f"  {'present' if present(dest, r) else 'INCOMPLETE':10} {r}   ({why})")

    if missing and not args.check:
        os.environ.pop("HF_HUB_OFFLINE", None)
        try:
            import huggingface_hub  # noqa: F401
        except ImportError:
            print("\nhuggingface_hub is not installed in this environment (activate the backend venv first).")
            return 1
        for repo, _ in missing:
            print(f"\nDownloading {repo} ...", flush=True)
            try:
                gone = fetch_repo(dest, repo)
                print("  ok (all files verified)" if not gone else f"  INCOMPLETE, still missing: {gone}")
            except Exception as exc:
                print(f"  FAILED: {exc}")
        print("\nAfter download:")
        for r, _ in HF_MODELS:
            print(f"  {'present' if present(dest, r) else 'INCOMPLETE':10} {r}")

    print("\nOther requirements")
    tess = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not tess:
        for cand in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if Path(cand).exists():
                tess = cand
    if tess:
        try:
            langs = subprocess.run([tess, "--list-langs"], capture_output=True, text=True, timeout=20).stdout.split()
        except Exception:
            langs = []
        print(f"  Tesseract: {tess}  (eng: {'yes' if 'eng' in langs else 'NO'}, osd: {'yes' if 'osd' in langs else 'NO - page rotation detection needs it'})")
    else:
        print("  Tesseract: NOT FOUND  ->  Windows: winget install -e --id UB-Mannheim.TesseractOCR | Linux: apt install tesseract-ocr   (then set TESSERACT_CMD in .env if it is not on PATH)")
    model = ollama_model()
    ollama = shutil.which("ollama")
    if not ollama:
        print("  Ollama: NOT FOUND  ->  Windows: winget install -e --id Ollama.Ollama | Linux: https://ollama.com/download  (only the surya / tesseract / learned_anchor pipelines and fast_ocr's field repair use it)")
    else:
        try:
            listed = subprocess.run([ollama, "list"], capture_output=True, text=True, timeout=20).stdout
        except Exception:
            listed = ""
        have = model.split(":")[0] in listed and (":" not in model or model in listed)
        print(f"  Ollama model '{model}': {'present' if have else 'MISSING  ->  ollama pull ' + model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
