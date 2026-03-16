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
