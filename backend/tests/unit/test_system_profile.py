"""Hardware parsing + the settings rules, on simulated machines (no GPU needed)."""
import pytest

from app.system.hardware import (
    GpuInfo, HardwareProfile, TorchInfo, arch_family, make_fingerprint, parse_cgroup_cpu_max, parse_cgroup_mem,
    parse_cuda_version_header, parse_nvidia_smi_csv, torch_device_support,
)
from app.system.profile_rules import derive_runtime_profile

# sample nvidia-smi rows (format: index,name,uuid,total,used,free,compute_cap,driver)
SMI_5060TI = "0, NVIDIA GeForce RTX 5060 Ti, GPU-aaaa, 16311, 700, 15400, 12.0, 580.105.08"
SMI_L40S = "0, NVIDIA L40S, GPU-bbbb, 46068, 300, 45700, 8.9, 580.105.08"

ARCHS_CU126 = ["sm_50", "sm_60", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90", "compute_90"]
ARCHS_CU128 = ARCHS_CU126 + ["sm_100", "sm_120", "compute_120"]


def machine(smi, *, torch_archs=None, cuda_build="12.8", cores=16, mem_mb=64000, disk_mb=500000,
            onnx=("CUDAExecutionProvider", "CPUExecutionProvider"), **kw):
    hw = HardwareProfile(cpu_logical=cores, cpu_physical=cores, cpu_limit=float(cores), mem_total_mb=mem_mb,
                         mem_available_mb=mem_mb - 4000, disk_free_mb=disk_mb, onnx_providers=list(onnx),
                         tesseract="/usr/bin/tesseract", **kw)
    if smi:
        hw.gpus = parse_nvidia_smi_csv(smi)
    if torch_archs is not None:
        cc = hw.gpus[0].compute_capability if hw.gpus else None
        hw.torch = TorchInfo(installed=True, version="2.9.0", cuda_build=cuda_build, cuda_available=True,
                             arch_list=torch_archs, device_support=torch_device_support(torch_archs, cc))
    hw.fingerprint = make_fingerprint(hw)
    return hw


# ── parsers ───────────────────────────────────────────────────────────────────
def test_parse_smi_and_arch():
    g = parse_nvidia_smi_csv(SMI_5060TI)[0]
    assert (g.name, g.total_mb, g.free_mb, g.compute_capability, g.arch) == ("NVIDIA GeForce RTX 5060 Ti", 16311, 15400, (12, 0), "blackwell")
    assert parse_nvidia_smi_csv(SMI_L40S)[0].arch == "ada"
    assert parse_nvidia_smi_csv("garbage\n") == []
    assert arch_family((9, 0)) == "hopper" and arch_family(None) == "unknown"


def test_cuda_header_and_cgroup():
    assert parse_cuda_version_header("| NVIDIA-SMI 580.105.08  Driver Version: 580.105.08  CUDA Version: 13.0 |") == "13.0"
    assert parse_cgroup_cpu_max("200000 100000") == 2.0 and parse_cgroup_cpu_max("max 100000") is None
    assert parse_cgroup_mem("8589934592") == 8192 and parse_cgroup_mem("max") is None


@pytest.mark.parametrize("archs,cc,expect", [
    (ARCHS_CU126, (12, 0), "ptx_jit"),            # RTX 50-series on a CUDA 12.6 wheel: only PTX
    (ARCHS_CU128, (12, 0), "native"),
    (ARCHS_CU126, (8, 9), "native"),              # L40S runs sm_86/sm_80 SASS (same major, lower minor)
    (["sm_90"], (8, 9), "unsupported"),
    ([], (8, 9), "n/a"),
])
def test_torch_device_support(archs, cc, expect):
    assert torch_device_support(archs, cc) == expect


# ── rules on simulated machines ───────────────────────────────────────────────
def test_rtx_5060_ti_16gb_with_matching_torch():
    rp = derive_runtime_profile(machine(SMI_5060TI, torch_archs=ARCHS_CU128, cores=12, mem_mb=32000))
    v = rp.values
    assert v["device"] == "cuda" and v["gpu_index"] == 0 and v["dtype"] == "bfloat16"
    assert v["vram_budget_mb"] == int(15400 * 0.85)
    assert v["fp8_allowed"] is True                               # compute 12.0 >= 8.9
    assert v["ollama_num_ctx"] == 16384                           # 13090 - 3000 = ~10 GB left for the VLM
    assert v["gpu_inference_concurrency"] == 1 and v["micro_batch_pages"] == 4
    assert v["ocr_onnx_provider"] == "cuda"
    assert v["gpu_services"] == 1 and v["api_workers"] == 3
    assert v["cpu_workers"] == min(12 - 3 - 1 - 1, (32000 - 2048) // 1500)
    assert all(v["tesseract_threads"] == 1 for _ in [0])
    assert not rp.advice


def test_l40s_48gb_scales_up():
    rp = derive_runtime_profile(machine(SMI_L40S, torch_archs=ARCHS_CU126, cores=32, mem_mb=256000,
                                        onnx=("TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider")))
    v = rp.values
    assert v["vram_budget_mb"] == int(45700 * 0.85)
    assert v["micro_batch_pages"] == 16 and v["gpu_inference_concurrency"] == 2
    assert v["ocr_onnx_provider"] == "tensorrt"
    assert v["api_workers"] == 4 and v["cpu_workers"] == 16       # capped at 16
    assert v["vlm_vram_budget_mb"] == v["vram_budget_mb"] - 3000


def test_same_code_gives_different_settings_on_the_two_servers():
    small = derive_runtime_profile(machine(SMI_5060TI, torch_archs=ARCHS_CU128)).values
    big = derive_runtime_profile(machine(SMI_L40S, torch_archs=ARCHS_CU126)).values
    assert big["vram_budget_mb"] > 2.5 * small["vram_budget_mb"]
    assert big["micro_batch_pages"] > small["micro_batch_pages"]


def test_blackwell_with_old_torch_falls_back_and_says_why():
    hw = machine(SMI_5060TI, torch_archs=["sm_80", "sm_86", "sm_90"], cuda_build="12.1")   # no PTX for 12.0 either
    rp = derive_runtime_profile(hw)
    assert rp.values["device"] == "cpu"
    assert any("PyTorch" in a for a in rp.advice)


def test_cpu_only_machine():
    rp = derive_runtime_profile(machine(None, cores=8, mem_mb=16000, torch_archs=None, onnx=("CPUExecutionProvider",)))
    v = rp.values
    assert v["device"] == "cpu" and v["gpu_services"] == 0 and v["dtype"] == "float32"
    assert v["gpu_inference_concurrency"] == 0 and v["micro_batch_pages"] == 1
    assert v["cpu_workers"] >= 1 and v["api_workers"] == 2


def test_container_limits_cap_workers():
    hw = machine(None, cores=64, mem_mb=512000, torch_archs=None)
    hw.cpu_limit, hw.mem_limit_mb = 4.0, 8192
    v = derive_runtime_profile(hw).values
    assert v["api_workers"] == 1 and v["cpu_workers"] <= 4


def test_free_vram_not_total_is_used():
    smi = "0, NVIDIA L40S, GPU-c, 46068, 30000, 16000, 8.9, 580"      # another process holds 30 GB
    v = derive_runtime_profile(machine(smi, torch_archs=ARCHS_CU126)).values
    assert v["vram_budget_mb"] == int(16000 * 0.85)


def test_picks_gpu_with_most_free_memory():
    smi = SMI_L40S.replace("45700", "5000") + "\n1, NVIDIA L40S, GPU-d, 46068, 300, 45000, 8.9, 580"
    rp = derive_runtime_profile(machine(smi, torch_archs=ARCHS_CU126))
    assert rp.values["gpu_index"] == 1 and rp.values["gpu_services"] == 2


def test_precedence_env_over_calibration_over_rules():
    hw = machine(SMI_L40S, torch_archs=ARCHS_CU126)
    rp = derive_runtime_profile(hw, env={"DOCINT_MICRO_BATCH_PAGES": "3"}, calibration={"micro_batch_pages": 12, "dtype": "float16"})
    assert rp.values["micro_batch_pages"] == 3 and rp.sources["micro_batch_pages"] == "env"
    assert rp.values["dtype"] == "float16" and rp.sources["dtype"] == "calibration"
    assert rp.sources["vram_budget_mb"] == "rules"
    bad = derive_runtime_profile(hw, env={"DOCINT_CPU_WORKERS": "lots"})
    assert any("DOCINT_CPU_WORKERS" in w for w in bad.warnings)


def test_fingerprint_changes_with_hardware():
    a = machine(SMI_5060TI, torch_archs=ARCHS_CU128)
    b = machine(SMI_L40S, torch_archs=ARCHS_CU126)
    assert a.fingerprint != b.fingerprint and a.fingerprint == machine(SMI_5060TI, torch_archs=ARCHS_CU128).fingerprint


def test_apply_respects_explicit_settings(monkeypatch):
    from app.core.config import settings
    from app.system import runtime
    rp = derive_runtime_profile(machine(SMI_L40S, torch_archs=ARCHS_CU126, cores=32))
    saved = {a: getattr(settings, a) for a in ("DEVICE", "WORKER_COUNT", "GPU_INFERENCE_CONCURRENCY", "OLLAMA_NUM_CTX",
                                               "FAST_PAGE_THREADS", "GPU_ENABLED", "SURYA_DEVICE")}
    try:
        monkeypatch.setattr(type(settings), "model_fields_set", property(lambda self: {"WORKER_COUNT"}), raising=False)
        applied = runtime.apply_runtime_profile(rp)
        assert "WORKER_COUNT" not in applied                       # operator-set: untouched
        assert applied["DEVICE"] == "cuda" and applied["GPU_INFERENCE_CONCURRENCY"] == 2
        assert settings.SURYA_DEVICE == "cuda" and settings.GPU_ENABLED is True
    finally:
        for a, v in saved.items():
            object.__setattr__(settings, a, v) if False else setattr(settings, a, v)


def test_explain_renders():
    from app.system.explain import render
    hw = machine(SMI_5060TI, torch_archs=ARCHS_CU128)
    text = render(hw, derive_runtime_profile(hw))
    assert "RTX 5060 Ti" in text and "vram_budget_mb" in text and "[rules]" in text
