# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module contains functions to serialize class instances for communication between the
daemon and frontends.

"""

# system imports
import json
import traceback
from enum import Enum
from typing import Dict, Union, Any, Sequence

# external imports
from dropbox.stone_serializers import json_encode  # type: ignore
from dropbox.stone_validators import Struct  # type: ignore

# local imports
from maestral.sync import SyncItem


StoneType = Dict[str, Union[str, float, bool, None]]
ErrorType = Dict[str, Union[str, Sequence[str], None]]


def dropbox_stone_to_dict(obj: Any) -> StoneType:
    """Converts the result of a Dropbox SDK call to a dictionary."""

    obj_string = json_encode(Struct(type(obj)), obj)

    dictionary: StoneType = dict(type=type(obj).__name__)
    dictionary.update(json.loads(obj_string))

    return dictionary


def error_to_dict(err: Exception) -> ErrorType:
    """
    Converts an exception to a dict. Keys will be strings and entries are native Python
    types.

    :param err: Exception to convert.
    :returns: Dictionary where all keys and values are strings. The following keys will
        always be present but may contain emtpy strings: 'type', 'inherits', 'title',
        'traceback', 'title', and 'message'.
    """

    err_dict: ErrorType = dict(
        type=err.__class__.__name__,
        inherits=[base.__name__ for base in err.__class__.__bases__],
        traceback=''.join(traceback.format_exception(err.__class__,
                                                     err, err.__traceback__)),
        title='An unexpected error occurred',
        message='Please restart Maestral to continue syncing.',
    )
    for key, value in err.__dict__.items():

        if value is None:
            err_dict[key] = value
        else:
            err_dict[key] = str(value)

    return err_dict


def sync_item_to_dict(si: SyncItem) -> StoneType:
    serialized = si.__dict__

    for key, value in serialized.items():
        if isinstance(value, Enum):
            value = value.value

        if isinstance(value, (str, int, float)) or value is None:
            serialized[key] = value
        else:
            serialized[key] = str(value)

    return serialized
