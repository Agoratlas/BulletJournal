from __future__ import annotations

from bulletjournal.services import (
    ArtifactService,
    AssetPrepareService,
    AssetService,
    CheckpointService,
    DashboardService,
    EventService,
    GraphService,
    ProjectService,
    RunService,
    TemplateService,
)


class ServiceContainer:
    def __init__(self) -> None:
        self.event_service = EventService()
        self.template_service = TemplateService()
        self.project_service = ProjectService(self.event_service, self.template_service)
        self.graph_service = GraphService(self.project_service)
        self.asset_service = AssetService(self.project_service)
        self.asset_prepare_service = AssetPrepareService(self.project_service)
        self.dashboard_service = DashboardService(self.project_service)
        self.project_service.dashboard_service = self.dashboard_service
        self.artifact_service = ArtifactService(self.project_service)
        self.run_service = RunService(self.project_service)
        self.project_service.run_service = self.run_service
        self.checkpoint_service = CheckpointService(self.project_service)


def get_container(app):
    return app.state.container
