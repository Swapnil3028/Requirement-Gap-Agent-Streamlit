"""Local LangChain and ChromaDB index for requirement sections."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents.common import Requirement


class RequirementRAGError(Exception):
    """Raised when the local requirement vector store cannot be used."""


@dataclass(frozen=True)
class RetrievedSection:
    """A requirement section retrieved from the persistent local index."""

    requirement: Requirement
    chunk_index: int


def requirement_key(requirement: Requirement) -> str:
    """Return a stable key that distinguishes same-named source documents."""
    source = requirement.source_path or requirement.source
    return f"{source}::{requirement.identifier}"


class RequirementRAG:
    """Index and retrieve requirement sections using local Chroma embeddings."""

    def __init__(
        self,
        persist_directory: str | Path,
        chunk_size: int = 800,
        chunk_overlap: int = 120,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be between zero and chunk_size")

        try:
            from langchain_chroma import Chroma
            from langchain_text_splitters import RecursiveCharacterTextSplitter
        except ImportError as exc:
            raise RequirementRAGError(
                "RAG dependencies are missing. Install them with: "
                "python -m pip install -r requirements.txt"
            ) from exc

        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            add_start_index=True,
        )
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(self.persist_directory))
            self._vector_store = Chroma(
                collection_name="requirement_sections",
                client=client,
            )
            self._client = client
        except Exception as exc:
            raise RequirementRAGError(
                f"Could not open local ChromaDB at '{self.persist_directory}': {exc}"
            ) from exc

    def close(self) -> None:
        """Release the local ChromaDB client and its file handles."""
        try:
            self._client.close()
        except Exception as exc:
            raise RequirementRAGError(f"Could not close local ChromaDB: {exc}") from exc

    def index_requirements(self, requirements: Sequence[Requirement]) -> int:
        """Replace indexed sections for the given sources and return chunk count."""
        from langchain_core.documents import Document

        source_paths = {
            requirement.source_path or requirement.source
            for requirement in requirements
        }
        try:
            for source_path in source_paths:
                self._vector_store.delete(where={"source_path": source_path})

            documents = []
            identifiers = []
            for requirement in requirements:
                source_path = requirement.source_path or requirement.source
                base_metadata = {
                    "source": requirement.source,
                    "source_path": source_path,
                    "location": requirement.location,
                    "requirement_id": requirement.identifier,
                    "requirement_key": requirement_key(requirement),
                }
                source_document = Document(
                    page_content=requirement.text,
                    metadata=base_metadata,
                )
                for chunk_index, chunk in enumerate(
                    self._splitter.split_documents([source_document])
                ):
                    chunk.metadata["chunk_index"] = chunk_index
                    chunk_id = hashlib.sha256(
                        (
                            f"{base_metadata['requirement_key']}:{chunk_index}:"
                            f"{chunk.page_content}"
                        ).encode("utf-8")
                    ).hexdigest()
                    documents.append(chunk)
                    identifiers.append(chunk_id)

            if documents:
                self._vector_store.add_documents(documents, ids=identifiers)
        except Exception as exc:
            raise RequirementRAGError(
                f"Could not index requirement documents: {exc}"
            ) from exc
        return len(documents)

    def retrieve_related_sections(
        self,
        requirement: Requirement,
        k: int = 3,
    ) -> list[RetrievedSection]:
        """Retrieve the nearest sections, excluding the query requirement itself."""
        if k <= 0:
            raise ValueError("k must be greater than zero")
        try:
            documents = self._vector_store.similarity_search(
                requirement.text,
                k=k,
                filter={
                    "requirement_key": {
                        "$ne": requirement_key(requirement),
                    }
                },
            )
        except Exception as exc:
            raise RequirementRAGError(
                f"Could not retrieve related sections for "
                f"'{requirement.identifier}': {exc}"
            ) from exc

        results = []
        for document in documents:
            metadata = document.metadata
            results.append(
                RetrievedSection(
                    requirement=Requirement(
                        identifier=str(metadata["requirement_id"]),
                        source=str(metadata["source"]),
                        location=str(metadata["location"]),
                        text=document.page_content,
                        source_path=str(metadata["source_path"]),
                    ),
                    chunk_index=int(metadata["chunk_index"]),
                )
            )
        return results

    def retrieve_for_requirements(
        self,
        requirements: Sequence[Requirement],
        k: int = 3,
    ) -> dict[str, list[RetrievedSection]]:
        """Retrieve related sections for every requirement by its stable key."""
        return {
            requirement_key(requirement): self.retrieve_related_sections(requirement, k)
            for requirement in requirements
        }