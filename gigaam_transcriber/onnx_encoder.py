"""ORT-движок Conformer-энкодера GigaAM — тот же текст, что torch, но быстрее (split-device).

Перенос из custom. В ONNX уходит ТОЛЬКО энкодер (Conformer); log-mel препроцессор и greedy
RNN-T декод (``decode_with_confidence``) остаются в torch — поэтому **per-chunk confidence и
точный argmax сохраняются** (в отличие от полнографового ONNX-бэкенда #13). Энкодер
экспортируется штатным ``model.to_onnx()`` один раз и кэшируется (``~/.cache/gigaam``).

БЕЗОПАСНОСТЬ: любая ошибка (нет onnxruntime / сбой экспорта/загрузки) → ``load_onnx_encoder``
возвращает None, вызывающий откатывается на torch (``model.forward``). Не бросает никогда.
Выход argmax-идентичен torch (encoded maxdiff ~1e-5), движок — лишь провенанс.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import torch

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.path.expanduser("~/.cache/gigaam"))


class OnnxEncoder:
    """Drop-in замена ``model.forward(wav_pad, wav_lens) -> (encoded, encoded_len)`` через ORT.

    Препроцессор (mel) — в torch на устройстве модели; Conformer — в ORT (CPU); результат
    возвращается torch-тензорами на ``model._device``/``_dtype`` для неизменного декода."""

    def __init__(self, session, in_names: list[str], out_names: list[str], np_dtype) -> None:
        self._sess = session
        self._in = in_names
        self._out = out_names
        self._np_dtype = np_dtype

    @torch.inference_mode()
    def __call__(self, model, wav_pad: torch.Tensor, wav_lens: torch.Tensor):
        feats, flens = model.preprocessor(wav_pad, wav_lens)  # log-mel в torch (как в forward)
        outs = self._sess.run(
            self._out,
            {
                self._in[0]: feats.detach().contiguous().cpu().numpy().astype(self._np_dtype),
                self._in[1]: flens.detach().cpu().numpy().astype(np.int64),
            },
        )
        # ORT отдаёт CPU/fp32; torch-голова RNN-T живёт на model._device. Возвращаем encoded
        # на устройстве и в dtype модели — точный drop-in (иначе на mps/cuda joint падает
        # "Tensor on cpu but expected on mps"). На cpu .to(...) — no-op.
        encoded = torch.from_numpy(np.ascontiguousarray(outs[0])).to(model._device, model._dtype)
        encoded_len = (
            torch.from_numpy(np.ascontiguousarray(outs[1])).long().reshape(-1).to(model._device)
        )
        return encoded, encoded_len


def load_onnx_encoder(
    model, model_name: str, cache_dir=None, threads: int = 8
) -> OnnxEncoder | None:
    """Экспорт (один раз, кэш) + загрузка энкодера GigaAM как ORT CPU-сессии.

    Возвращает ``OnnxEncoder`` или **None** при ЛЮБОЙ проблеме (вызывающий откатится на
    ``model.forward``). Никогда не бросает."""
    try:
        import onnxruntime as rt
    except Exception as exc:  # noqa: BLE001
        logger.warning("onnxruntime недоступен (%r); откат на torch-энкодер.", exc)
        return None
    try:
        cdir = Path(cache_dir) if cache_dir else _CACHE_DIR
        cdir.mkdir(parents=True, exist_ok=True)
        onnx_path = cdir / f"{model_name}_encoder.onnx"
        # Маркер завершения (как в onnx_backend.ensure_onnx): краш посреди экспорта
        # (OOM/SIGKILL/диск) не оставит .done → следующий запуск переэкспортирует, а не
        # будет вечно падать на загрузке усечённого .onnx (bare exists() этого не ловит).
        marker = cdir / f".{model_name}_encoder.done"
        if not marker.exists():
            logger.info("Экспорт ONNX-энкодера в %s (один раз)...", onnx_path)
            model.to_onnx(str(cdir), dtype=torch.float32)
            marker.write_text("ok")  # пишется ТОЛЬКО после успешного экспорта
        opts = rt.SessionOptions()
        opts.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = int(threads)
        opts.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
        opts.log_severity_level = 3
        sess = rt.InferenceSession(
            str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        in_names = [i.name for i in sess.get_inputs()]
        out_names = [o.name for o in sess.get_outputs()]
        np_dtype = np.float16 if "float16" in sess.get_inputs()[0].type else np.float32
        logger.info("ONNX-энкодер активен (%s).", onnx_path.name)
        return OnnxEncoder(sess, in_names, out_names, np_dtype)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ONNX-энкодер не подготовлен (%r); откат на torch-энкодер.", exc)
        return None
