from collections.abc import MutableMapping


class Omit:
    __slots__ = tuple()
    pass


OMIT = Omit()


class NoOmitDict(dict):
    def __init__(self, obj: dict = None, **kwargs):
        if obj is None:
            obj = kwargs
        super().__init__({k: v for k, v in obj.items() if v is not OMIT})
