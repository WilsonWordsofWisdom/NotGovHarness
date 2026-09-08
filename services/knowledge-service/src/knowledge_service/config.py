from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "knowledge-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/knowledge"

    # hybrid: POST /documents requires a verified knowledge:write token; queries/reads require
    # any authenticated caller. See
    # docs/superpowers/specs/2026-09-08-knowledge-rag-harness-design.md.
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "platform"
    pg_password: str = "platform"
    pg_dbname: str = "knowledge"
    pg_table_name: str = "chunks"

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dims: int = 384
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    top_k_retrieve: int = 10
    top_k_rerank: int = 3

    minio_endpoint: str = "localhost:9090"
    minio_access_key: str = "minio"
    minio_secret_key: str = "miniosecret123"
    minio_bucket: str = "knowledge"
    minio_secure: bool = False

    llm_gateway_url: str = "http://localhost:8099"
    llm_gateway_model: str = "qwen3.8"

    # This service's own OAuth2 client (D-062/D-067) — a real registered identity, not a borrowed
    # simulated-principal (D-030), since knowledge-service is a real platform service calling
    # another.
    identity_url: str = "http://localhost:8090"
    knowledge_client_id: str = "knowledge-service"
    knowledge_client_secret: str = "knowledge-service-dev-secret"
