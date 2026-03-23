"""
Embedding generation for code snippets.
"""

import os
from typing import List, Optional
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


class OpenAIEmbeddings(EmbeddingProvider):
    """OpenAI's embedding provider."""

    def __init__(self, model: str = "text-embedding-3-small", api_key: Optional[str] = None):
        """Initialize OpenAI embeddings.

        Args:
            model: Model to use (text-embedding-3-small or text-embedding-3-large)
            api_key: OpenAI API key (uses OPENAI_API_KEY env var if not provided)
        """
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        if not self.api_key:
            raise ValueError(
                "OpenAI API key not found. "
                "Set OPENAI_API_KEY environment variable or pass api_key parameter."
            )

        try:
            import openai

            self.client = openai.OpenAI(api_key=self.api_key)
        except ImportError:
            raise ImportError("openai package not found. Install with: pip install openai")

    def embed(self, text: str) -> List[float]:
        """Generate embedding for text."""
        response = self.client.embeddings.create(model=self.model, input=text)
        return response.data[0].embedding

    def embed_batch(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        embeddings = []

        # Process in batches to avoid rate limits
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = self.client.embeddings.create(model=self.model, input=batch)

            # Sort by index to maintain order
            batch_embeddings = sorted(response.data, key=lambda x: x.index)
            embeddings.extend([item.embedding for item in batch_embeddings])

        return embeddings


class MockEmbeddings(EmbeddingProvider):
    """Mock embeddings provider for testing (generates random vectors)."""

    def __init__(self, dimension: int = 1536):
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
    provider: str = "openai", model: str = "text-embedding-3-small"
) -> EmbeddingProvider:
    """Get embedding provider instance.

    Args:
        provider: Provider name ("openai" or "mock")
        model: Model name (for OpenAI provider)

    Returns:
        EmbeddingProvider instance
    """
    if provider == "openai":
        return OpenAIEmbeddings(model=model)
    elif provider == "mock":
        return MockEmbeddings()
    else:
        raise ValueError(f"Unknown embedding provider: {provider}")
