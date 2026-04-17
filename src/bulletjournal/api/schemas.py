from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bulletjournal.domain.enums import ArtifactState, RunMode


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AddNotebookNodeOperation(StrictModel):
    type: Literal['add_notebook_node']
    node_id: str
    title: str
    x: int = 80
    y: int = 80
    w: int = 320
    h: int = 220
    template_ref: str | None = None
    source_text: str | None = None
    ui: dict[str, Any] | None = None


class AddFileInputNodeOperation(StrictModel):
    type: Literal['add_file_input_node']
    node_id: str
    title: str
    x: int = 80
    y: int = 80
    w: int = 320
    h: int = 220
    artifact_name: str = 'file'
    ui: dict[str, Any] | None = None


class AddOrganizerNodeOperation(StrictModel):
    type: Literal['add_organizer_node']
    node_id: str
    title: str = 'Organizer'
    x: int = 80
    y: int = 80
    w: int = 160
    h: int = 120
    ui: dict[str, Any] | None = None


class AddAreaNodeOperation(StrictModel):
    type: Literal['add_area_node']
    node_id: str
    title: str = 'Area'
    x: int = 80
    y: int = 80
    w: int = 480
    h: int = 280
    ui: dict[str, Any] | None = None


class AddPipelineTemplateOperation(StrictModel):
    type: Literal['add_pipeline_template']
    template_ref: str
    x: int = 80
    y: int = 80
    node_id_prefix: str | None = None


class AddEdgeOperation(StrictModel):
    type: Literal['add_edge']
    source_node: str
    source_port: str
    target_node: str
    target_port: str


class RemoveEdgeOperation(StrictModel):
    type: Literal['remove_edge']
    edge_id: str


class UpdateNodeLayoutOperation(StrictModel):
    type: Literal['update_node_layout']
    node_id: str
    x: int
    y: int
    w: int | None = None
    h: int | None = None


class UpdateNodeTitleOperation(StrictModel):
    type: Literal['update_node_title']
    node_id: str
    title: str


class UpdateOrganizerPortsOperation(StrictModel):
    type: Literal['update_organizer_ports']
    node_id: str
    ports: list[dict[str, Any]]


class UpdateAreaStyleOperation(StrictModel):
    type: Literal['update_area_style']
    node_id: str
    title_position: str
    color: str
    filled: bool


class DeleteNodeOperation(StrictModel):
    type: Literal['delete_node']
    node_id: str


class UpdateNodeFrozenOperation(StrictModel):
    type: Literal['update_node_frozen']
    node_id: str
    frozen: bool


GraphOperation = Annotated[
    AddNotebookNodeOperation
    | AddFileInputNodeOperation
    | AddOrganizerNodeOperation
    | AddAreaNodeOperation
    | AddPipelineTemplateOperation
    | AddEdgeOperation
    | RemoveEdgeOperation
    | UpdateNodeLayoutOperation
    | UpdateNodeTitleOperation
    | UpdateOrganizerPortsOperation
    | UpdateAreaStyleOperation
    | DeleteNodeOperation
    | UpdateNodeFrozenOperation,
    Field(discriminator='type'),
]


class GraphPatchRequest(StrictModel):
    graph_version: int
    operations: list[GraphOperation]


class RunAction(StrEnum):
    USE_STALE = 'use_stale'
    RUN_UPSTREAM = 'run_upstream'


class RunNodeRequest(StrictModel):
    mode: RunMode
    action: RunAction | None = None


class FileUploadResponse(StrictModel):
    node_id: str
    artifact_name: str = 'file'
    state: str


class SnapshotResponse(StrictModel):
    server_time: str
    project: dict[str, Any]
    graph: dict[str, Any]
    validation_issues: list[dict[str, Any]]
    notices: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    checkpoints: list[dict[str, Any]]
    templates: list[dict[str, Any]]


class NoticeDismissResponse(StrictModel):
    issue_id: str
    status: Literal['dismissed']


class RunAllRequest(StrictModel):
    mode: Literal[RunMode.RUN_STALE] = Field(default=RunMode.RUN_STALE)


class ControllerEnvironmentChangeRequest(StrictModel):
    reason: str
    mark_all_artifacts_stale: bool = True


class ArtifactStateChangeRequest(StrictModel):
    state: ArtifactState


class NodeOutputsStateChangeRequest(StrictModel):
    state: ArtifactState
    only_current_state: ArtifactState | None = None
