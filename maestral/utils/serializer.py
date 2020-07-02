# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module contains functions to serialize class instances for communication between the
daemon and frontends.

"""

# system imports
import traceback
from typing import Dict, Union, Any, Sequence

# external imports
from dropbox.stone_serializers import json_compat_obj_encode  # type: ignore
from dropbox.stone_validators import Struct  # type: ignore


StoneType = Dict[str, Union[str, float, bool]]
ErrorType = Dict[str, Union[str, Sequence[str], None]]


def dropbox_stone_to_dict(obj: Any) -> StoneType:
    """Converts the result of a Dropbox SDK call to a dictionary."""

    serialized = json_compat_obj_encode(Struct(obj.__class__), obj)
    serialized['type'] = type(obj).__name__
    serialized.move_to_end('type', last=False)

    return serialized


def error_to_dict(err: Exception) -> ErrorType:
    """
    Converts an exception to a dict. Keys will be strings and entries are native Python
    types.

    :param Exception err: Exception to convert.
    :returns: Dictionary where all keys and values are strings. The following keys will
        always be present but may contain emtpy strings: 'type', 'inherits', 'title',
        'traceback', 'title', and 'message'.
    :rtype: dict(str, str)
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
            err_dict[key] = None
        else:
            err_dict[key] = str(value)

    return err_dict
