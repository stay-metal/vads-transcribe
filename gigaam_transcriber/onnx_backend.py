"""
ONNX-бэкенд для ASR GigaAM.

GigaAM умеет экспортировать модель в ONNX и запускать её через ONNX Runtime
(см. gigaam/onnx_utils.py). Этот модуль готовит (экспортирует + опц. квантизует
в int8) и кэширует ONNX-граф, чтобы CLI мог переключаться на ONNX через флаг.

Покрывает только ASR. ONNX Runtime работает на CPU (или CUDA), но НЕ на MPS —
поэтому ONNX-ASR это CPU/CUDA путь (актуален для CPU-only сервера и как
бенчмарк-точка против torch-cpu и mps).
"""

import gc
import glob
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".cache" / "gigaam_onnx"


def provider_for(device: str) -> str:
    """ONNX Runtime провайдер по запрошенному устройству (mps → CPU)."""
    return "CUDAExecutionProvider" if device == "cuda" else "CPUExecutionProvider"


def _quantize_dir(onnx_dir: str) -> None:
    """Динамическая int8-квантизация всех .onnx в директории (замена на месте)."""
    from onnxruntime.quantization import QuantType, quantize_dynamic

    for f in sorted(glob.glob(os.path.join(onnx_dir, "*.onnx"))):
        tmp = f + ".q.onnx"
        try:
            # Только MatMul: Conv→ConvInteger у onnxruntime-CPU без ядра (NOT_IMPLEMENTED)
            quantize_dynamic(f, tmp, weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul"])
            os.replace(tmp, f)
            logger.info(f"int8-квантизация: {os.path.basename(f)}")
        except Exception as e:
            logger.warning(f"Не удалось квантизовать {os.path.basename(f)}: {e}")
            if os.path.exists(tmp):
                os.remove(tmp)


def ensure_onnx(model_name: str, int8: bool = False) -> tuple[str, str]:
    """
    Гарантирует наличие ONNX-графа GigaAM (экспорт + опц. int8), с кэшированием.

    Returns: (onnx_dir, version) — директория и резолвнутое имя модели (напр. v3_e2e_ctc).
    """
    import gigaam

    # Загружаем torch-модель только чтобы экспортировать и узнать резолвнутое имя
    model = gigaam.load_model(model_name, device="cpu", fp16_encoder=False)
    version = model.cfg.model_name

    onnx_dir = CACHE_DIR / (version + ("-int8" if int8 else ""))
    marker = onnx_dir / ".done"

    if not marker.exists():
        onnx_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Экспорт {version} в ONNX → {onnx_dir} ...")
        model.to_onnx(str(onnx_dir))
        if int8:
            _quantize_dir(str(onnx_dir))
        marker.write_text("ok")

    del model
    gc.collect()
    return str(onnx_dir), version


def load_sessions(onnx_dir: str, version: str, device: str):
    """Загрузка ONNX-сессий GigaAM (sessions, model_cfg)."""
    from gigaam.onnx_utils import load_onnx

    return load_onnx(onnx_dir, version, provider=provider_for(device))
