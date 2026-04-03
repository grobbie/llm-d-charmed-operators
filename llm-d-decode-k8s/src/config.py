# Copyright 2026 Rob Gibbon
# See LICENSE file for licensing details.

from pydantic import BaseModel, Field

class CharmConfig(BaseModel):
    """Pydantic model for validating charm configuration."""
    model_id: str = Field(alias="model-id", default="")
    hf_token: str = Field(alias="hf-token", default="")
    port: int = Field(default=8000, gt=0, lt=65536)
    gpu_count: int = Field(alias="gpu-count", default=1)
    enable_infiniband: bool = Field(alias="enable-infiniband", default=True)
    extra_args: str = Field(alias="extra-args", default="--gpu-memory-utilization 0.95 --max-model-len 8192")

    model_config = {"populate_by_name": True, "extra": "ignore"}
