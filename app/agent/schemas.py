"""Typed request/response models for the natural-language analysis MVP."""
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field


class MoleCodeToolRequest(BaseModel):
    """Call the structure.molecode tool. Conversion does not run docking or MD."""

    action: Literal["smiles_to_mermaid", "mermaid_to_smiles", "validate"]
    smiles: Optional[str] = None
    mermaid: Optional[str] = None
    name: str = Field(default="Molecule", max_length=80)


class AgentTaskRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    molecule_ids: list[int] = Field(default_factory=list, max_length=5000)
    top_n: int = Field(default=10, ge=1, le=100)
    include_invalid: bool = False


class TaskCreated(BaseModel):
    task_id: str
    status: str
    status_url: str
    report_url: Optional[str] = None


class AgentTaskStatus(BaseModel):
    task_id: str
    status: str
    prompt: str
    plan: list[dict[str, Any]] = Field(default_factory=list)
    progress: int = 0
    message: str = ""
    error: Optional[str] = None
    report_id: Optional[str] = None
    created_at: str
    updated_at: str


class ReportResponse(BaseModel):
    report_id: str
    task_id: str
    title: str
    status: str
    report: dict[str, Any]

