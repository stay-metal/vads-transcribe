"""Per-token/per-chunk confidence для greedy RNN-T (GigaAM) — без правки GigaAM.

Перенесено из custom (zoom_transcriber/confidence.py). Субкласс `RNNTGreedyDecoding`
из `GigaAM/gigaam/decoding.py` переопределяет `decode`, добавляя к
`(text, token_ids, token_frames)` четвёртый элемент — `token_logprobs`:
log-вероятность joint в точке argmax для каждого эмитированного (non-blank) токена.

Выход `RNNTJoint.joint` УЖЕ нормирован log-softmax'ом (`GigaAM/gigaam/decoder.py:47`:
`return self.joint_net(enc + pred).log_softmax(-1)`), поэтому здесь log_softmax повторно
НЕ применяем — берём log-prob выбранного токена напрямую. argmax по log-softmax == argmax
по логитам (монотонно), поэтому **текст бит-в-бит идентичен** апстрим-декодеру (I1).

Почему дубль ~80-строчного цикла, а не патч GigaAM: editable-клон `gigaam` остаётся
нетронутым (апстрим трекаем). Декодер — greedy RNN-T без biasing/hotwords, confidence
читаем «снаружи». Это НАБЛЮДЕНИЕ над декодом, не правка кириллицы; downstream confidence
нужен лишь чтобы помечать кандидатов на «второе мнение» / триаж, а не переписывать вывод.

Best practice (NeMo, см. tmp/research_notes.md): exp(mean(token_logprob)) = геом. среднее
вероятностей = длинонормированный продукт; blank-токены исключены (только non-blank эмиссии).
Альтернатива — энтропийный скор (Tsallis) даёт лучшую детекцию ошибок; оставлено как
возможное улучшение (метод max_prob/argmax — дефолт-безопасная база).

Каждый logprob ∈ (−inf, 0] (log-вероятность ≤ 0).
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import torch
from torch import Tensor

from gigaam.decoder import RNNTHead
from gigaam.decoding import RNNTGreedyDecoding


class ConfidentRNNTGreedyDecoding(RNNTGreedyDecoding):
    """Greedy RNN-T декодер, дополнительно возвращающий per-token logprob.

    Идентичен `RNNTGreedyDecoding`, но `decode` отдаёт
    `(text, token_ids, token_frames, token_logprobs)`, где `token_logprobs[i]` —
    log-softmax логитов joint в argmax-индексе токена `token_ids[i]` (выровнено с
    `token_frames`).
    """

    @torch.inference_mode()
    def decode(  # type: ignore[override]
        self,
        head: "RNNTHead",
        encoded: Tensor,
        enc_len: Tensor,
    ) -> List[Tuple[str, List[int], List[int], List[float]]]:
        """RNN-T greedy decode + per-token logprob (выровнен с token_frames, ≤ 0)."""
        x = encoded.transpose(1, 2)  # [B, T, D]
        B, T, _ = x.shape
        device = x.device

        hyps: List[List[int]] = [[] for _ in range(B)]
        token_frames: List[List[int]] = [[] for _ in range(B)]
        token_logprobs: List[List[float]] = [[] for _ in range(B)]
        last_label: List[Optional[Tensor]] = [None] * B
        dec_state: List[Optional[Tuple[Tensor, Tensor]]] = [None] * B

        def emit_batch(batch_idx: List[int], t: int, fresh: bool) -> List[int]:
            """Один батч-шаг predictor+joint; возвращает сэмплы с non-blank эмиссией."""
            idx = torch.tensor(batch_idx, device=device, dtype=torch.long)
            f = x[idx, t : t + 1, :]  # [b, 1, D]

            if fresh:
                g, hidden = head.decoder.predict(None, None, batch_size=len(batch_idx))
            else:
                labels = torch.cat([last_label[i] for i in batch_idx], dim=0)  # [b, 1]
                state = self._cat_states([dec_state[i] for i in batch_idx])
                g, hidden = head.decoder.predict(
                    labels, state, batch_size=len(batch_idx)
                )

            # joint уже отдаёт log-вероятности (RNNTJoint.joint -> .log_softmax(-1),
            # GigaAM/gigaam/decoder.py:47): повторный log_softmax не нужен. argmax по
            # log-softmax = argmax по логитам, поэтому выбор токена == апстрим-декодер.
            logprobs = head.joint.joint(f, g)[:, 0, 0, :]  # [b, V+1], значения ≤ 0 (log-softmax)
            # max() возвращает И max-logprob, И argmax(k) за ОДИН kernel; индексы argmax те же,
            # что у .argmax() (одинаковое tie-break), поэтому выбор токена == апстрим (текст I1).
            # max(logprobs) == logprobs[row, argmax] — значение logprob идентично.
            maxlp, k = logprobs.max(dim=-1)  # [b], [b]
            emit = k.ne(self.blank_id)

            if not emit.any():
                return []

            hidden_parts = self._split_state(hidden)
            # Снимаем индексы/токены/logprob на CPU ОДНИМ синком на батч-шаг, а не на токен:
            # per-token .item()/float() = device→host sync, доминирующий на MPS/CUDA.
            emit_idx = emit.nonzero(as_tuple=False).squeeze(1).tolist()
            k_cpu = k.tolist()
            maxlp_cpu = maxlp.tolist()
            out = []

            for p in emit_idx:
                bi = batch_idx[p]

                hyps[bi].append(k_cpu[p])
                token_frames[bi].append(t)
                token_logprobs[bi].append(maxlp_cpu[p])
                last_label[bi] = k[p : p + 1].view(1, 1)
                dec_state[bi] = hidden_parts[p]
                out.append(bi)

            return out

        enc_len = enc_len.cpu()
        for t in range(T):
            active = (t < enc_len).nonzero(as_tuple=False).squeeze(1).tolist()
            if not active:
                break

            for _ in range(self.max_symbols):
                if not active:
                    break

                fresh = [i for i in active if dec_state[i] is None]
                stateful = [i for i in active if dec_state[i] is not None]

                next_active = []
                if fresh:
                    next_active.extend(emit_batch(fresh, t, fresh=True))
                if stateful:
                    next_active.extend(emit_batch(stateful, t, fresh=False))

                if not next_active:
                    break

                active = next_active

        return [
            (self.tokenizer.decode(h), h, tf, lp)
            for h, tf, lp in zip(hyps, token_frames, token_logprobs)
        ]


def chunk_confidence(token_logprobs) -> Optional[float]:
    """Confidence чанка ∈ (0, 1] из per-token logprob'ов: ``exp(mean(logprob))`` —
    средняя геометрическая вероятность эмитированного токена (длинонормированный продукт).

    Пусто (чанк не дал ни одного non-blank токена) → ``None``. Чистая функция.
    I1: наблюдение над декодом, не правка текста."""
    if not token_logprobs:
        return None
    return float(math.exp(sum(token_logprobs) / len(token_logprobs)))


def as_confident(decoding: "RNNTGreedyDecoding") -> "ConfidentRNNTGreedyDecoding":
    """Обернуть готовый ``RNNTGreedyDecoding`` в confident-субкласс БЕЗ ре-инстанцирования.

    Новый объект делит ``tokenizer``/``blank_id``/``max_symbols`` исходного декодера (копия
    ``__dict__``) — SentencePiece/словарь не грузятся повторно. Исходный ``decoding`` НЕ
    мутируется (его ``__class__`` не меняется)."""
    if not isinstance(decoding, RNNTGreedyDecoding):
        raise TypeError(
            f"decoding must be RNNTGreedyDecoding, got {type(decoding).__name__}"
        )
    conf = ConfidentRNNTGreedyDecoding.__new__(ConfidentRNNTGreedyDecoding)
    conf.__dict__.update(decoding.__dict__)
    return conf


def decode_with_confidence(
    model, encoded: Tensor, encoded_len: Tensor, wav_lens: Tensor
) -> List[Tuple[str, Optional[float]]]:
    """Декод чанков + per-chunk confidence; текст **бит-в-бит** как у ``model._decode``.

    Если ``model.decoding`` — ``RNNTGreedyDecoding`` (greedy RNN-T, как у v3_e2e_rnnt),
    используем confident-субкласс: выбор токенов (argmax по log-softmax) идентичен апстриму,
    поэтому текст не меняется, а из logprob'ов считаем ``chunk_confidence``. Иначе (CTC и пр.)
    деградируем на ``model._decode`` — текст тот же, confidence ``None``. I1: confidence —
    наблюдение, кириллица GigaAM не трогается."""
    decoding = getattr(model, "decoding", None)
    if isinstance(decoding, RNNTGreedyDecoding):
        conf = as_confident(decoding)
        return [
            (text, chunk_confidence(logprobs))
            for text, _ids, _frames, logprobs in conf.decode(
                model.head, encoded, encoded_len
            )
        ]
    return [
        (text, None)
        for text, _words in model._decode(encoded, encoded_len, wav_lens, False)
    ]
