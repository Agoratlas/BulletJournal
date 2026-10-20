from typing import Any


class DashboardVersionConflictError(Exception):
    def __init__(self, latest_dashboard: dict[str, Any]) -> None:
        super().__init__('Dashboard version conflict.')
        self.latest_dashboard = latest_dashboard


class BulletJournalError(Exception):
    pass


class ProjectError(BulletJournalError):
    pass


class InvalidRequestError(BulletJournalError):
    pass


class NotFoundError(BulletJournalError):
    pass


class GraphValidationError(BulletJournalError):
    pass


class TombstoneExpiredError(BulletJournalError):
    pass


class ArtifactError(BulletJournalError):
    pass


class ArtifactPendingError(ArtifactError):
    pass


class ArtifactInterfaceError(BulletJournalError):
    pass


class RunConflictError(BulletJournalError):
    pass


class RunCancelledError(BulletJournalError):
    pass


class NotebookSessionError(BulletJournalError):
    pass


class ProjectValidationError(BulletJournalError):
    pass


class UnauthorizedError(BulletJournalError):
    pass
