from .base import OCRPipeline

_REGISTRY: dict[str, type[OCRPipeline]] = {}

def register_pipeline(name: str):
    def decorator(cls):
        _REGISTRY[name] = cls
        return cls
    return decorator

def get_pipeline(name: str) -> OCRPipeline:
    cls = _REGISTRY.get(name)
    if not cls:
        raise ValueError(f"Pipeline '{name}' not registered.")
    return cls()

def list_pipelines() -> list[str]:
    return list(_REGISTRY.keys())
