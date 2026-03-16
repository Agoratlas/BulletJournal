__version__ = '0.1.0'


def __getattr__(name: str):
    if name == 'create_app':
        from bulletjournal.api.app import create_app

        return create_app
    raise AttributeError(name)


__all__ = ['create_app']
