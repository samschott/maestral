"""
This module contains functions to serialize class instances for communication between
the daemon and frontends.
"""

# system imports
import json
import traceback
from enum import Enum
from typing import Dict, Union, List, TYPE_CHECKING

# external imports
from dropbox.stone_serializers import json_encode
from dropbox.stone_validators import Struct

# local imports
from . import sanitize_string
from ..database.orm import Model

if TYPE_CHECKING:
    from dropbox.stone_base import Struct as StoneStruct


# TODO: Use TypedDict where appropriate.

SerializedObjectType = Dict[str, Union[str, List[str], None]]
"""Type definition for object returned from the public API"""


def dropbox_stone_to_dict(obj: "StoneStruct") -> SerializedObjectType:
    """Converts the result of a Dropbox SDK call to a dictionary."""

    obj_string = json_encode(Struct(type(obj)), obj)

    dictionary: SerializedObjectType = dict(type=type(obj).__name__)
    dictionary.update(json.loads(obj_string))

    return dictionary


def error_to_dict(err: Exception) -> SerializedObjectType:
    """
    Converts an exception to a dict. Keys will be strings and entries are native Python
    types.

    :param err: Exception to convert.
    :returns: Dictionary where all keys and values are strings. The following keys will
        always be present but may contain empty strings: 'type', 'inherits', 'title',
        'traceback', 'title', and 'message'.
    """

    serialized: SerializedObjectType = dict(
        type=err.__class__.__name__,
        inherits=[base.__name__ for base in err.__class__.__bases__],
        traceback="".join(
            traceback.format_exception(err.__class__, err, err.__traceback__)
        ),
        title="An unexpected error occurred",
        message="Please restart Maestral to continue syncing.",
    )
    for key, value in err.__dict__.items():

        if value is None:
            serialized[key] = value
        elif isinstance(value, str):
            serialized[key] = sanitize_string(value)
        else:
            serialized[key] = str(value)

    return serialized


def orm_type_to_dict(event: Model) -> SerializedObjectType:
    """
    Converts a SyncEvent to a dict. Keys will be strings and entries are native Python
    types.

    :param event: SyncEvent to convert.
    :returns: Serialized SyncEvent.
    """

    attributes = [x for x in dir(event) if not x.startswith("_") and x != "metadata"]

    serialized = {}
    keep_types = (int, float, type(None))

    for attr_name in attributes:
        value = getattr(event, attr_name)

        if isinstance(value, Enum):
            serialized[attr_name] = value.value
        elif isinstance(value, str):
            serialized[attr_name] = sanitize_string(value)
        elif isinstance(value, keep_types):
            serialized[attr_name] = value
        else:
            serialized[attr_name] = str(value)

    return serialized
