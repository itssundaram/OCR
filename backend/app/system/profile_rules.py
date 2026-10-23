"""
DOCINT - rules: HardwareProfile -> runtime settings.

A pure function (no I/O) so every decision can be unit-tested with a simulated machine
(RTX 5060 Ti 16 GB, L40S 48 GB, CPU-only ...).  Each decision records WHY, which
`python -m app.system.explain` prints.

Precedence (highest first):  explicit env override  >  calibration result  >  rules below  >  safe CPU defaults.

The numbers here are PROVISIONAL starting points chosen from VRAM / cores / RAM; they are not
measurements.  The calibration step (later phase) replaces the provisional ones with measured
values and records them against the machine fingerprint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.system.hardware import HardwareProfile

VRAM_UTILISATION = 0.85            # share of FREE VRAM the app may plan to use
OCR_MODELS_VRAM_EST_MB = 3000      # OCR/layout/table models together (estimate; calibration measures it)
MIN_VLM_BUDGET_MB = 6000           # below this a 3B-7B VLM will not fit comfortably next to the OCR models
WORKER_RAM_EST_MB = 1500           # one CPU worker's working set (estimate)
RESERVE_RAM_MB = 2048

# rule key -> (env var that overrides it)
ENV_OVERRIDES = {
    "device": "DOCINT_DEVICE",
    "gpu_index": "DOCINT_GPU_INDEX",
    "vram_budget_mb": "DOCINT_VRAM_BUDGET_MB",
    "dtype": "DOCINT_DTYPE",
    "micro_batch_pages": "DOCINT_MICRO_BATCH_PAGES",
    "gpu_inference_concurrency": "DOCINT_GPU_CONCURRENCY",
    "ollama_num_ctx": "DOCINT_OLLAMA_NUM_CTX",
    "ocr_onnx_provider": "DOCINT_ONNX_PROVIDER",
    "cpu_workers": "DOCINT_CPU_WORKERS",
    "api_workers": "DOCINT_API_WORKERS",
    "page_threads": "DOCINT_PAGE_THREADS",
    "ocr_cache_max_mb": "DOCINT_OCR_CACHE_MB",
}


@dataclass
class RuntimeProfile:
    fingerprint: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)   # rules | calibration | env
    warnings: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)          # things a human should know / do

    def to_dict(self) -> dict:
        return {"fingerprint": self.fingerprint, "values": self.values, "reasons": self.reasons,
                "sources": self.sources, "warnings": self.warnings, "advice": self.advice}


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _coerce(like: Any, raw: str) -> Any:
    if isinstance(like, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        return int(float(raw))
    if isinstance(like, float):
        return float(raw)
    return raw


def derive_runtime_profile(
    hw: HardwareProfile,
    env: Mapping[str, str] | None = None,
    calibration: Mapping[str, Any] | None = None,
) -> RuntimeProfile:
    env = env or {}
    rp = RuntimeProfile(fingerprint=hw.fingerprint, warnings=list(hw.warnings))
    V, R = rp.values, rp.reasons

    def put(key: str, value: Any, why: str) -> None:
        V[key], R[key], rp.sources[key] = value, why, "rules"

    # ── GPU selection ─────────────────────────────────────────────────────────
    usable = [g for g in hw.gpus]
    gpu_ok = bool(usable)
    if usable and hw.torch.installed:
        if hw.torch.device_support == "unsupported" or not hw.torch.cuda_build or hw.torch.smoke_ok is False:
            gpu_ok = False
            rp.advice.append("The GPU is present but the installed PyTorch cannot use it; CPU will be used for "
                             "PyTorch models until a matching CUDA build is installed.")
    if gpu_ok:
        g = max(usable, key=lambda x: x.free_mb)        # most FREE memory wins (another process may hold some)
        put("device", "cuda", f"{g.name} detected ({g.total_mb} MB total, {g.free_mb} MB free)")
        put("gpu_index", g.index, "GPU with the most free VRAM")
        budget = int(g.free_mb * VRAM_UTILISATION)
        put("vram_budget_mb", budget, f"{int(VRAM_UTILISATION * 100)}% of FREE VRAM ({g.free_mb} MB), not of total")
        cc = g.compute_capability or (0, 0)
        if cc >= (8, 0):
            put("dtype", "bfloat16", f"compute capability {cc[0]}.{cc[1]} supports bf16")
        else:
            put("dtype", "float16", f"compute capability {cc[0]}.{cc[1]} has no bf16; fp16")
        put("fp8_allowed", cc >= (8, 9), "FP8 needs Ada (8.9) / Hopper / Blackwell" if cc >= (8, 9)
            else "FP8 is not supported on this architecture")
        put("gpu_services", len(usable), "one model service per GPU (models loaded once per GPU)")
        vlm_budget = max(0, budget - OCR_MODELS_VRAM_EST_MB)
        put("vlm_vram_budget_mb", vlm_budget, f"budget minus ~{OCR_MODELS_VRAM_EST_MB} MB estimated for OCR/layout/table models")
        if vlm_budget < MIN_VLM_BUDGET_MB:
            rp.advice.append(f"Only ~{vlm_budget} MB of VRAM is left for the VLM; use a small VLM or free GPU memory.")
        # context window and parallel VLM requests grow with what is left after the OCR models
        put("ollama_num_ctx", 8192 if vlm_budget < 9000 else 16384,
            "smaller context when little VRAM is left for the VLM KV-cache" if vlm_budget < 9000
            else "enough VRAM for the default 16k context")
        put("gpu_inference_concurrency", 1 if budget < 20000 else 2,
            "provisional: serialise GPU model calls on a small card" if budget < 20000
            else "provisional: allow two concurrent GPU model calls on a large card")
        put("micro_batch_pages", 2 if budget < 8000 else 4 if budget < 20000 else 8 if budget < 32000 else 16,
            "provisional batch size from VRAM budget (calibration replaces it with a measured value)")
        providers = hw.onnx_providers
        if "TensorrtExecutionProvider" in providers and g.total_mb >= 24000:
            put("ocr_onnx_provider", "tensorrt", "TensorRT available and the card is large; engine cache is per GPU")
        elif "CUDAExecutionProvider" in providers:
            put("ocr_onnx_provider", "cuda", "CUDA execution provider available")
        else:
            put("ocr_onnx_provider", "cpu", "no CUDA ONNX Runtime provider installed (pip install onnxruntime-gpu)")
            rp.advice.append("onnxruntime has no CUDA provider: ONNX OCR models would run on CPU.")
    else:
        put("device", "cpu", "no usable GPU" if not hw.gpus else "GPU present but not usable by PyTorch")
        put("gpu_services", 0, "no GPU service")
        put("vram_budget_mb", 0, "no GPU")
        put("dtype", "float32", "CPU")
        put("fp8_allowed", False, "CPU")
        put("ollama_num_ctx", 8192, "CPU inference: keep the context small")
        put("gpu_inference_concurrency", 0, "no GPU to guard")
        put("micro_batch_pages", 1, "CPU")
        put("ocr_onnx_provider", "cpu", "CPU")

    # ── CPU / RAM ─────────────────────────────────────────────────────────────
    cores = max(1.0, hw.cpu_limit)
    mem = hw.mem_limit_mb or hw.mem_available_mb or hw.mem_total_mb or 4096
    api = _clamp(int(cores // 4), 1, 4)
    put("api_workers", api, f"1 per 4 usable cores ({cores:.1f}), max 4; API workers hold no models")
    gpu_svc = V.get("gpu_services") or 0
    spare = int(cores) - api - gpu_svc - 1
    by_cpu = max(1, spare)
    by_ram = max(1, int((mem - RESERVE_RAM_MB) // WORKER_RAM_EST_MB))
    put("cpu_workers", min(by_cpu, by_ram, 16),
        f"min(cores left after API/GPU service/headroom = {by_cpu}, RAM for workers = {by_ram}); "
        "each runs Tesseract single-threaded")
    put("page_threads", _clamp(int(cores // 2), 2, 8), "pages read in parallel; Tesseract is single-threaded per process")
    put("tesseract_threads", 1, "OMP_THREAD_LIMIT=1: parallelism comes from processes, not from inside Tesseract")
    put("ocr_cache_max_mb", _clamp(int(hw.disk_free_mb * 0.2), 512, 20480) if hw.disk_free_mb else 2048,
        "20% of free disk, between 0.5 and 20 GB")

    # ── services ─────────────────────────────────────────────────────────────
    if not hw.tesseract:
        rp.warnings.append("Tesseract was not found; set TESSERACT_CMD.")
    sv = hw.services or {}
    if sv.get("ollama") and not sv["ollama"].get("reachable"):
        rp.warnings.append("Ollama is not reachable at the configured URL; VLM fallbacks will fail.")
    if sv.get("redis") and not sv["redis"].get("reachable"):
        rp.warnings.append("Redis is not reachable; uploads will be refused (no silent in-process fallback).")

    # ── calibration (measured) ────────────────────────────────────────────────
    for k, v in (calibration or {}).items():
        if k in V:
            V[k], R[k], rp.sources[k] = v, "measured by calibration on this machine", "calibration"

    # ── explicit env overrides win ────────────────────────────────────────────
    for key, var in ENV_OVERRIDES.items():
        if key in V and env.get(var) not in (None, ""):
            try:
                V[key] = _coerce(V[key], env[var])
                R[key], rp.sources[key] = f"set by {var}", "env"
            except ValueError:
                rp.warnings.append(f"{var}={env[var]!r} is not valid for {key}; ignored.")
    return rp
