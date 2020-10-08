# -*- coding: utf-8 -*-
import os
import pkg_resources


def copy_metadata(package_name):
    dist = pkg_resources.get_distribution(package_name)
    metadata_dir = dist.egg_info
    return [(metadata_dir, metadata_dir[len(dist.location) + len(os.sep) :])]


# we need package metadata at runtime
datas = copy_metadata("maestral")
