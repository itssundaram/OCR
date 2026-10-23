"""
DOCINT - hardware discovery.

Reads what the machine actually has (GPUs, free VRAM, driver / CUDA versions, CPU and
memory limits including container limits, disk, ONNX providers, and whether the installed
PyTorch build can really run on the installed GPU) and returns one plain
``HardwareProfile``.  Nothing here changes settings; ``profile_rules`` turns the profile
into settings.

Every probe is best-effort: a missing tool is recorded as a warning, never an exception,
so the same code runs on a laptop with no GPU and on the GPU server.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# compute capability -> architecture family
_ARCH_BY_CC = {
    (7, 0): "volta", (7, 5): "turing",
    (8, 0): "ampere", (8, 6): "ampere", (8, 7): "ampere",
    (8, 9): "ada",
    (9, 0): "hopper",
    (10, 0): "blackwell", (10, 1): "blackwell", (10, 3): "blackwell",
    (12, 0): "blackwell", (12, 1): "blackwell",
}


def arch_family(cc: tuple[int, int] | None) -> str:
    if not cc:
        return "unknown"
    if cc in _ARCH_BY_CC:
        return _ARCH_BY_CC[cc]
    major = cc[0]
    return {7: "volta", 8: "ampere", 9: "hopper", 10: "blackwell", 12: "blackwell"}.get(major, "unknown")


@dataclass
class GpuInfo:
    index: int
    name: str
    total_mb: int
    free_mb: int
    used_mb: int
    compute_capability: tuple[int, int] | None = None
    arch: str = "unknown"
    uuid: str = ""


@dataclass
class TorchInfo:
    installed: bool = False
    version: str = ""
    cuda_build: str | None = None          # CUDA toolkit this wheel was built with
    cuda_available: bool = False
    arch_list: list[str] = field(default_factory=list)
    device_support: str = "n/a"            # native | ptx_jit | unsupported | n/a
    smoke_ok: bool | None = None           # tiny matmul on the GPU (None = not run)
    smoke_error: str = ""


@dataclass
class HardwareProfile:
    gpus: list[GpuInfo] = field(default_factory=list)
    driver_version: str = ""
    driver_cuda_version: str = ""          # highest CUDA the DRIVER supports (nvidia-smi header)
    gpu_source: str = "none"               # nvml | nvidia-smi | none
    torch: TorchInfo = field(default_factory=TorchInfo)
    onnx_providers: list[str] = field(default_factory=list)
    cpu_logical: int = 1
    cpu_physical: int = 1
    cpu_limit: float = 1.0                 # cores usable (cgroup quota applied)
    mem_total_mb: int = 0
    mem_available_mb: int = 0
    mem_limit_mb: int | None = None        # cgroup limit if any
    disk_free_mb: int = 0
    tesseract: str = ""
    services: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    detected_at: float = 0.0
    fingerprint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── pure parsers (unit-tested) ────────────────────────────────────────────────
def parse_cc(text: str) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d+)\.(\d+)\s*$", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_nvidia_smi_csv(text: str) -> list[GpuInfo]:
    """Output of: nvidia-smi --query-gpu=index,name,uuid,memory.total,memory.used,memory.free,compute_cap
    --format=csv,noheader,nounits"""
    gpus: list[GpuInfo] = []
    for line in (text or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            idx, name, uuid = int(parts[0]), parts[1], parts[2]
            total, used, free = int(float(parts[3])), int(float(parts[4])), int(float(parts[5]))
        except ValueError:
            continue
        cc = parse_cc(parts[6]) if len(parts) > 6 else None
        gpus.append(GpuInfo(index=idx, name=name, uuid=uuid, total_mb=total, used_mb=used, free_mb=free,
                            compute_capability=cc, arch=arch_family(cc)))
    return gpus


def parse_cuda_version_header(text: str) -> str:
    m = re.search(r"CUDA Version:\s*([\d.]+)", text or "")
    return m.group(1) if m else ""


def torch_device_support(arch_list: list[str], cc: tuple[int, int] | None) -> str:
    """Can this torch build run kernels on a device of compute capability `cc`?

    native      - the wheel ships SASS for that exact sm_XY
    ptx_jit     - only PTX for an older arch: works, but JIT-compiles on first use (slow start)
    unsupported - nothing usable: torch raises 'no kernel image is available'
    """
    if not cc or not arch_list:
        return "n/a"
    sm = {a for a in arch_list if a.startswith("sm_")}
    compute = [a for a in arch_list if a.startswith("compute_")]
    want = f"sm_{cc[0]}{cc[1]}"
    if want in sm:
        return "native"
    # sm_90 SASS does not run on sm_120; same-major minor-compatible SASS only within a major (<= minor)
    for a in sm:
        try:
            n = int(a[3:])
        except ValueError:
            continue
        if n // 10 == cc[0] and n % 10 <= cc[1] and cc[0] < 10:
            return "native"
    for a in compute:
        try:
            n = int(a[8:])
        except ValueError:
            continue
        if (n // 10, n % 10) <= cc:
            return "ptx_jit"
    return "unsupported"


def parse_cgroup_cpu_max(text: str) -> float | None:
    """cgroup v2 cpu.max: '<quota|max> <period>' -> cores."""
    parts = (text or "").split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        return float(parts[0]) / float(parts[1])
    except (ValueError, ZeroDivisionError):
        return None


def parse_cgroup_mem(text: str) -> int | None:
    t = (text or "").strip()
    if not t or t == "max":
        return None
    try:
        v = int(t)
    except ValueError:
        return None
    return v // (1024 * 1024) if v < (1 << 60) else None


def make_fingerprint(hw: "HardwareProfile") -> str:
    """Stable id of 'this kind of machine': changes when the GPU, driver, torch build or CPU/RAM shape changes."""
    key = {
        "gpus": [(g.name, g.total_mb, g.compute_capability) for g in hw.gpus],
        "driver": hw.driver_version,
        "torch": (hw.torch.version, hw.torch.cuda_build),
        "cpu": (hw.cpu_logical, round(hw.cpu_limit, 1)),
        "mem_gb": round((hw.mem_limit_mb or hw.mem_total_mb) / 1024),
    }
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:12]


# ── probes ────────────────────────────────────────────────────────────────────
def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def _visible_filter(gpus: list[GpuInfo]) -> list[GpuInfo]:
    cvd = os.environ.get("CUDA_VISIBLE_DEVICES")
    if cvd is None or cvd.strip() == "":
        return gpus
    if cvd.strip() in ("-1", "none", "NoDevFiles"):
        return []
    wanted = [x.strip() for x in cvd.split(",") if x.strip()]
    by_idx = {str(g.index): g for g in gpus}
    by_uuid = {g.uuid: g for g in gpus if g.uuid}
    picked = [by_idx.get(w) or by_uuid.get(w) for w in wanted]
    return [g for g in picked if g]


def _probe_nvml(hw: HardwareProfile) -> bool:
    try:
        import pynvml  # nvidia-ml-py
    except Exception:
        return False
    try:
        pynvml.nvmlInit()
        try:
            drv = pynvml.nvmlSystemGetDriverVersion()
            hw.driver_version = drv.decode() if isinstance(drv, bytes) else str(drv)
            try:
                v = pynvml.nvmlSystemGetCudaDriverVersion_v2()
                hw.driver_cuda_version = f"{v // 1000}.{(v % 1000) // 10}"
            except Exception:
                pass
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(h)
                name = name.decode() if isinstance(name, bytes) else str(name)
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                try:
                    cc = tuple(pynvml.nvmlDeviceGetCudaComputeCapability(h))
                except Exception:
                    cc = None
                try:
                    uuid = pynvml.nvmlDeviceGetUUID(h)
                    uuid = uuid.decode() if isinstance(uuid, bytes) else str(uuid)
                except Exception:
                    uuid = ""
                hw.gpus.append(GpuInfo(
                    index=i, name=name, uuid=uuid, total_mb=int(mem.total // 2**20),
                    used_mb=int(mem.used // 2**20), free_mb=int(mem.free // 2**20),
                    compute_capability=cc, arch=arch_family(cc),
                ))
        finally:
            pynvml.nvmlShutdown()
        hw.gpu_source = "nvml"
        return True
    except Exception as exc:
        hw.warnings.append(f"NVML probe failed: {exc}")
        hw.gpus.clear()
        return False


def _probe_nvidia_smi(hw: HardwareProfile) -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    out = _run([exe, "--query-gpu=index,name,uuid,memory.total,memory.used,memory.free,compute_cap,driver_version",
                "--format=csv,noheader,nounits"])
    if not out:  # older drivers have no compute_cap column
        out = _run([exe, "--query-gpu=index,name,uuid,memory.total,memory.used,memory.free,driver_version",
                    "--format=csv,noheader,nounits"])
        gpus = parse_nvidia_smi_csv(out)
        for ln, g in zip(out.strip().splitlines(), gpus):
            hw.driver_version = ln.split(",")[-1].strip()
    else:
        gpus = parse_nvidia_smi_csv(out)
        first = out.strip().splitlines()[0].split(",")
        if len(first) > 7:
            hw.driver_version = first[7].strip()
    if not gpus:
        return False
    hw.gpus.extend(gpus)
    hw.driver_cuda_version = parse_cuda_version_header(_run([exe]))
    hw.gpu_source = "nvidia-smi"
    return True


def _probe_torch(hw: HardwareProfile, smoke: bool) -> None:
    import importlib.util
    t = hw.torch
    if importlib.util.find_spec("torch") is None:
        return
    try:
        import torch
    except Exception as exc:
        hw.warnings.append(f"torch is installed but failed to import: {exc}")
        return
    t.installed, t.version = True, str(torch.__version__)
    t.cuda_build = getattr(torch.version, "cuda", None)
    try:
        t.cuda_available = bool(torch.cuda.is_available())
    except Exception:
        t.cuda_available = False
    try:
        t.arch_list = list(torch.cuda.get_arch_list()) if t.cuda_build else []
    except Exception:
        t.arch_list = []
    if hw.gpus:
        cc = hw.gpus[0].compute_capability
        t.device_support = torch_device_support(t.arch_list, cc) if t.cuda_build else "unsupported"
        if t.cuda_build is None:
            hw.warnings.append("PyTorch is a CPU-only build but a GPU is present; install a CUDA build of torch.")
        elif t.device_support == "unsupported":
            hw.warnings.append(
                f"PyTorch {t.version} (CUDA {t.cuda_build}) has no kernels for {hw.gpus[0].name} "
                f"(sm_{cc[0]}{cc[1]}); install a newer CUDA build (RTX 50-series needs CUDA 12.8+).")
        elif t.device_support == "ptx_jit":
            hw.warnings.append(
                f"PyTorch has no native kernels for sm_{cc[0]}{cc[1]}; it will JIT-compile from PTX (slow first start).")
    if smoke and t.cuda_available:
        try:
            a = torch.randn(64, 64, device="cuda")
            _ = (a @ a).sum().item()
            t.smoke_ok = True
        except Exception as exc:
            t.smoke_ok, t.smoke_error = False, str(exc)[:200]
            hw.warnings.append(f"GPU smoke test failed: {t.smoke_error}")


def _probe_cpu_mem(hw: HardwareProfile) -> None:
    hw.cpu_logical = os.cpu_count() or 1
    hw.cpu_physical = hw.cpu_logical
    try:
        import psutil
        hw.cpu_physical = psutil.cpu_count(logical=False) or hw.cpu_logical
        vm = psutil.virtual_memory()
        hw.mem_total_mb, hw.mem_available_mb = int(vm.total // 2**20), int(vm.available // 2**20)
    except Exception:
        pass
    hw.cpu_limit = float(hw.cpu_logical)
    try:  # container limits win over host numbers
        q = parse_cgroup_cpu_max(Path("/sys/fs/cgroup/cpu.max").read_text())
        if q:
            hw.cpu_limit = min(hw.cpu_limit, q)
        lim = parse_cgroup_mem(Path("/sys/fs/cgroup/memory.max").read_text())
        if lim:
            hw.mem_limit_mb = lim
            hw.mem_available_mb = min(hw.mem_available_mb or lim, lim)
    except Exception:
        pass


def _probe_services(hw: HardwareProfile) -> None:
    from app.core.config import settings
    hw.tesseract = settings.TESSERACT_CMD or shutil.which("tesseract") or ""
    # Ollama
    try:
        import urllib.request
        with urllib.request.urlopen(settings.OLLAMA_BASE_URL.rstrip("/") + "/api/tags", timeout=1.5) as r:
            data = json.loads(r.read().decode() or "{}")
        hw.services["ollama"] = {"reachable": True, "models": [m.get("name") for m in data.get("models", [])][:20]}
    except Exception:
        hw.services["ollama"] = {"reachable": False}
    # Redis
    try:
        import redis
        c = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
        c.ping()
        hw.services["redis"] = {"reachable": True}
    except Exception:
        hw.services["redis"] = {"reachable": False}


def detect_hardware(*, smoke: bool = False, probe_services: bool = True, probe_torch: bool = True,
                    data_dir: str | os.PathLike | None = None) -> HardwareProfile:
    t0 = time.perf_counter()
    hw = HardwareProfile(detected_at=time.time())
    if not _probe_nvml(hw):
        _probe_nvidia_smi(hw)
    hw.gpus = _visible_filter(hw.gpus)
    _probe_cpu_mem(hw)
    try:
        hw.disk_free_mb = int(shutil.disk_usage(data_dir or ".").free // 2**20)
    except Exception:
        pass
    if probe_torch:
        _probe_torch(hw, smoke)
    try:
        import onnxruntime
        hw.onnx_providers = list(onnxruntime.get_available_providers())
    except Exception:
        pass
    if probe_services:
        _probe_services(hw)
    if not hw.gpus:
        hw.warnings.append("No NVIDIA GPU detected - running on CPU.")
    hw.fingerprint = make_fingerprint(hw)
    logger.info("hardware_detected", gpus=[(g.name, g.total_mb, g.free_mb) for g in hw.gpus], source=hw.gpu_source,
                cpu=hw.cpu_limit, torch=hw.torch.version or None, torch_cuda=hw.torch.cuda_build,
                device_support=hw.torch.device_support, ms=int((time.perf_counter() - t0) * 1000),
                fingerprint=hw.fingerprint)
    return hw
