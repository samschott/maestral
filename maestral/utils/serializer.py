# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
# system imports
import json
import traceback

# external packages
from dropbox.stone_serializers import json_encode
from dropbox.stone_validators import Struct


def _remove_tags(dictionary):

    new_dict = dict(dictionary)

    for key, value in dictionary.items():
        if key == '.tag':
            del new_dict[key]
        elif isinstance(value, dict):
            new_dict[key] = _remove_tags(value)

    return new_dict


def dropbox_stone_to_dict(obj):
    """Converts the result of a Dropbox SDK call to a dictionary."""

    dictionary = dict(type=obj.__class__.__name__)

    obj_string = json_encode(Struct(obj.__class__), obj)
    dictionary.update(json.loads(obj_string))

    return _remove_tags(dictionary)


def error_to_dict(err):
    """"
    Converts an exception to a dict. Keys will be strings and entries are native Python
    types.

    :param Exception err: Exception to convert.
    :returns: Dictionary where all keys are strings and all items are native Python types.
        The following keys will always be present but may contain emtpy strings: 'type',
        'inherits', 'title', 'traceback', 'title', and 'message'.
    :rtype: dict
    """

    dictionary = dict(
        type=err.__class__.__name__,
        inherits=[b.__name__ for b in err.__class__.__bases__],
        traceback="".join(traceback.format_exception(err.__class__, err, err.__traceback__)),
        title='An unexpected error occurred',
        message='Please restart Maestral to continue syncing.',
    )
    for name, value in err.__dict__.items():
        dictionary[str(name)] = str(value)

    return dictionary
