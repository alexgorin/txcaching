# -*- coding: utf-8 -*-

import copy
import cPickle as pickle

_REGISTRY = {}


def serialize(args, kwargs):
    """Serialize arguments"""

    return pickle.dumps((args, kwargs))


def deserialize(dump):
    """Deserialize arguments"""

    return pickle.loads(dump)


def func_id(func, class_name=""):
    """Function unique identifier."""

    return "_".join((func.__module__, class_name, func.__name__))


def all():
    """Get copy of the registry. For debug only."""

    return copy.deepcopy(_REGISTRY)


def register(key, func, args=(), kwargs={}, class_name=""):
    """Add function call to the registry"""

    func = func_id(func, class_name=class_name)
    args_dump = serialize(args, kwargs)

    if func not in _REGISTRY:
        _REGISTRY[func] = []

    if not filter(lambda item: item["args"] == args_dump, _REGISTRY[func]):
        _REGISTRY[func].append({
            "key": key,
            "args": args_dump,
        })


def remove(func):
    """Remove function from registry"""

    func = func_id(func)
    del _REGISTRY[func]


def keys(func):
    """All the keys which have been added by the function"""

    func = func_id(func)
    if func not in _REGISTRY:
        return []
    return [item["key"] for item in _REGISTRY[func]]


def key(func, args=(), kwargs={}):
    """Key which has been added by the function with particular arguments."""

    klass = getattr(func, "im_class", None)
    class_name = getattr(klass, "__name__", "")
    func = func_id(func, class_name=class_name)
    if func not in _REGISTRY:
        return None

    keys = [item["key"] for item in _REGISTRY[func] if item["args"] == serialize(args, kwargs)]
    if not keys:
        return None
    return keys[0]


def clear():
    """Clear the registry"""

    _REGISTRY.clear()