"""Text -> vector, through fastembed (ONNX, no PyTorch). See ADR 0006."""

from collections.abc import Sequence
from typing import Any

MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384


class Embedder:
    """Every vector it returns is unit length, so dot product = cosine.

    The model loads on the first `embed`, not here: a hook that only stores a turn or reads the
    profile never pays the ~1 s load (ADR 0022).
    """

    model_name = MODEL

    def __init__(self) -> None:
        self._model: Any = None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._model is None:
            from fastembed import TextEmbedding  # noqa: PLC0415 - slow import, only when needed

            self._model = TextEmbedding(MODEL)
        return [v.tolist() for v in self._model.embed(list(texts))]
