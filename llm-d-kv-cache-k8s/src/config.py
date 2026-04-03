# Copyright 2026 Rob Gibbon
# See LICENSE file for licensing details.

from pydantic import BaseModel, Field

class CharmConfig(BaseModel):
    """Pydantic model for validating charm configuration."""
    model_id: str = Field(alias="model-id", default="")
    hf_token: str = Field(alias="hf-token", default="")
    port: int = Field(default=8000, gt=0, lt=65536)
    indexer_cache_size: int = Field(alias="indexer-cache-size", default=10000, gt=0)
    tokenizer_model_name: str = Field(alias="tokenizer-model-name", default="meta-llama/Meta-Llama-3-8B-Instruct")
    tokenizer_workers_count: int = Field(alias="tokenizer-workers-count", default=4, gt=0)
    tokenizer_local: bool = Field(alias="tokenizer-local", default=False)

    model_config = {"populate_by_name": True, "extra": "ignore"}
