from pydantic import BaseModel, Field, model_validator
from typing import Literal

class TaskRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=12000)
    molecule_asset_id: str | None = None
    receptor_asset_id: str | None = None
    ligand_asset_ids: list[str] = Field(default_factory=list, max_length=2000)
    md_tpr_asset_id: str | None = None
    md_bundle_asset_id: str | None = None
    top_n: int = Field(default=100, ge=1, le=10000)
    ro5_only: bool | None = None
    max_molecular_weight: float | None = Field(default=None, gt=0, le=100000)
    max_logp: float | None = Field(default=None, ge=-20, le=50)
    min_qed: float | None = Field(default=None, ge=0, le=1)
    vina_center_x: float | None = Field(default=None, ge=-1000, le=1000)
    vina_center_y: float | None = Field(default=None, ge=-1000, le=1000)
    vina_center_z: float | None = Field(default=None, ge=-1000, le=1000)
    vina_box_size: float = Field(default=25.0, ge=1.0, le=100.0)
    vina_box_size_x: float | None = Field(default=None, gt=0, le=100.0)
    vina_box_size_y: float | None = Field(default=None, gt=0, le=100.0)
    vina_box_size_z: float | None = Field(default=None, gt=0, le=100.0)
    vina_exhaustiveness: int = Field(default=8, ge=1, le=128)
    vina_modes: int = Field(default=9, ge=1, le=100)
    vina_cpu: int = Field(default=1, ge=1, le=64)
    use_gpu: bool = True
    gromacs_threads: int = Field(default=8, ge=1, le=256)
    timeout_seconds: int = Field(default=86400, ge=60, le=604800)

class TaskResponse(BaseModel):
    task_id: str
    operation: str
    status: str
    progress: int
    message: str
    status_url: str | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    report_url: str | None = None
