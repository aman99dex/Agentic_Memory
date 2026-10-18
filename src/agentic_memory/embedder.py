"""Text -> vector, through fastembed (ONNX, no PyTorch). See ADR 0006."""

from collections.abc import Sequence

from fastembed import TextEmbedding

MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384


class Embedder:
    """Loads the model once; every vector it returns is unit length, so dot product = cosine."""

    model_name = MODEL

    def __init__(self) -> None:
        self._model = TextEmbedding(MODEL)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(list(texts))]
