"""
Embedding generation for code snippets.
"""

from typing import List
from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Generate embedding for text."""
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        pass


class OllamaEmbeddings(EmbeddingProvider):
    """Ollama's embedding provider."""

    def __init__(self, model: str = "nomic-embed-text"):
        """Initialize Ollama embeddings.

        Args:
            model: Ollama embedding model (default: nomic-embed-text)
        """
        self.model = model

        try:
            import ollama

            self.client = ollama
        except ImportError:
            raise ImportError("ollama package not found. Install with: pip install ollama")

    def embed(self, text: str) -> List[float]:
        """Generate embedding for text."""
        response = self.client.embeddings(model=self.model, prompt=text)
        embedding = response["embedding"]
        return embedding

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        return [self.embed(text) for text in texts]


class MockEmbeddings(EmbeddingProvider):
    """Mock embeddings provider for testing (generates deterministic vectors)."""

    def __init__(self, dimension: int = 768):
        """Initialize mock embeddings.

        Args:
            dimension: Embedding dimension
        """
        self.dimension = dimension

    def embed(self, text: str) -> List[float]:
        """Generate mock embedding."""
        import hashlib

        # Generate deterministic embedding based on text hash
        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
        return [(hash_val >> i) % 2 - 0.5 for i in range(self.dimension)]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate mock embeddings for multiple texts."""
        return [self.embed(text) for text in texts]


def get_embedding_provider(
    provider: str = "ollama", model: str = "nomic-embed-text"
) -> EmbeddingProvider:
    """Get embedding provider instance.

    Args:
        provider: Provider name ("ollama" or "mock")
        model: Ollama model name

    Returns:
        EmbeddingProvider instance
    """
    if provider == "ollama":
        return OllamaEmbeddings(model=model)
    elif provider == "mock":
        return MockEmbeddings()
    else:
        raise ValueError(f"Unknown embedding provider: {provider}")
