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


def dropbox_stone_to_dict(obj):
    """Converts the result of a Dropbox SDK call to a dict."""

    dictionary = dict(type=obj.__class__.__name__)

    obj_string = json_encode(Struct(obj.__class__), obj)
    dictionary.update(json.loads(obj_string))

    return remove_tags(dictionary)


def maestral_error_to_dict(err):

    dictionary = dict(
        type=err.__class__.__name__,
        inherits=[str(b) for b in err.__class__.__bases__],
        cause=err.__cause__,
        traceback=traceback.format_exception(err.__class__, err, err.__traceback__)
    )
    dictionary.update(err.__dict__)

    return dictionary


def remove_tags(dictionary):

    new_dict = dict(dictionary)

    for key, value in dictionary.items():
        if key == ".tag":
            del new_dict[key]
        elif isinstance(value, dict):
            new_dict[key] = remove_tags(value)

    return new_dict


def flatten_dict(dictionary):

    while any(isinstance(v, dict) for v in dictionary.values()):
        dictionary = _flatten_dict_once(dictionary)

    return dictionary


def _flatten_dict_once(dictionary):

    new_dict = dict(dictionary)

    for key, val in dictionary.items():
        if isinstance(val, dict):
            for k, v in val.items():
                new_key = "{}: {}".format(key, k)
                new_dict[new_key] = v
            del new_dict[key]

    return new_dict
