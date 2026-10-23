"""
python -m app.system.explain            human-readable: what was detected and what was chosen, and why
python -m app.system.explain --json     the same as JSON
python -m app.system.explain --smoke    also run a tiny matmul on the GPU
python -m app.system.explain --get api_workers   print one value (used by start.sh)
"""
from __future__ import annotations

import json
import sys

from app.system.runtime import get_runtime


def render(hw, rp) -> str:
    L: list[str] = []
    L.append(f"Machine fingerprint: {hw.fingerprint}")
    if hw.gpus:
        for g in hw.gpus:
            cc = f"{g.compute_capability[0]}.{g.compute_capability[1]}" if g.compute_capability else "?"
            L.append(f"GPU {g.index}: {g.name}  {g.total_mb} MB total / {g.free_mb} MB free  "
                     f"compute {cc} ({g.arch})")
        L.append(f"Driver {hw.driver_version or '?'}  (driver supports CUDA up to {hw.driver_cuda_version or '?'}) "
                 f"via {hw.gpu_source}")
    else:
        L.append("GPU: none")
    t = hw.torch
    if t.installed:
        L.append(f"PyTorch {t.version}  built for CUDA {t.cuda_build or 'none (CPU build)'}  "
                 f"cuda_available={t.cuda_available}  device support: {t.device_support}"
                 + (f"  smoke test: {'ok' if t.smoke_ok else 'FAILED'}" if t.smoke_ok is not None else ""))
    else:
        L.append("PyTorch: not installed")
    L.append(f"ONNX Runtime providers: {', '.join(hw.onnx_providers) or 'none'}")
    L.append(f"CPU: {hw.cpu_logical} logical / {hw.cpu_physical} physical, usable {hw.cpu_limit:.1f}   "
             f"RAM: {hw.mem_total_mb} MB total, {hw.mem_available_mb} MB available"
             + (f", container limit {hw.mem_limit_mb} MB" if hw.mem_limit_mb else ""))
    L.append(f"Free disk: {hw.disk_free_mb} MB   Tesseract: {hw.tesseract or 'not found'}")
    for name, s in (hw.services or {}).items():
        L.append(f"{name}: {'reachable' if s.get('reachable') else 'NOT reachable'}")
    L.append("")
    L.append("Chosen settings")
    width = max((len(k) for k in rp.values), default=10)
    for k, v in rp.values.items():
        L.append(f"  {k:<{width}} = {str(v):<10} [{rp.sources.get(k, '')}]  {rp.reasons.get(k, '')}")
    for title, items in (("Warnings", rp.warnings), ("Advice", rp.advice)):
        if items:
            L.append("")
            L.append(title)
            L.extend(f"  - {i}" for i in dict.fromkeys(items))
    return "\n".join(L)


def main(argv: list[str]) -> int:
    smoke = "--smoke" in argv
    hw, rp = get_runtime(refresh=True, smoke=smoke, probe_services="--no-services" not in argv)
    if "--get" in argv:
        key = argv[argv.index("--get") + 1]
        print(rp.values.get(key, ""))
        return 0
    if "--json" in argv:
        print(json.dumps({"hardware": hw.to_dict(), "runtime": rp.to_dict()}, indent=2, default=str))
    else:
        print(render(hw, rp))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
