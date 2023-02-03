#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
This module provides user configuration file management and is mostly copied from the
config module of the Spyder IDE.
"""

from __future__ import annotations

import ast
import os
import os.path as osp
import shutil
import copy
import logging
import configparser as cp
from threading import RLock
from typing import Iterator, Any, Dict, TypeVar, MutableSet

from packaging.version import Version


logger = logging.getLogger(__name__)

_DefaultsType = Dict[str, Dict[str, Any]]
_T = TypeVar("_T")

# =============================================================================
# Auxiliary classes
# =============================================================================


class NoDefault:
    pass


# =============================================================================
# Defaults class
# =============================================================================


class DefaultsConfig(cp.ConfigParser):
    """
    Class used to save defaults to a file and as base class for UserConfig.
    """

    def __init__(self, path: str) -> None:
        super().__init__(interpolation=None)

        dirname, basename = osp.split(path)
        filename, ext = osp.splitext(basename)

        self._path = path
        self._dirname = dirname
        self._filename = filename
        self._suffix = ext

    def _set(self, section: str, option: str, value: Any) -> None:
        """Private set method"""
        if not self.has_section(section):
            self.add_section(section)
        if not isinstance(value, str):
            value = repr(value)

        super().set(section, option, value)

    def save(self) -> None:
        """Save config into the associated file."""
        os.makedirs(self._dirname, exist_ok=True)

        with open(self.config_path, "w", encoding="utf-8") as configfile:
            self.write(configfile)

    @property
    def config_path(self) -> str:
        """The ini file where this configuration is stored."""
        return self._path


# =============================================================================
# User config class
# =============================================================================


class UserConfig(DefaultsConfig):
    """
    UserConfig class, based on ConfigParser. This class is safe to use from different
    threads but must not be used from different processes!

    :param path: Configuration file will be saved to this path.
    :param defaults: Dictionary containing options.
    :param version: Version of the configuration file.
    :param backup: Whether to create a backup on version changes and on initial setup.
    :param remove_obsolete: If `True`, values that were removed from the configuration
        on version change, are removed from the saved configuration file.

    .. note:: The ``get`` and ``set`` arguments number and type differ from the
        reimplemented methods.
    """

    DEFAULT_SECTION_NAME = "main"

    def __init__(
        self,
        path: str,
        defaults: _DefaultsType | None = None,
        load: bool = True,
        version: Version = Version("0.0.0"),
        backup: bool = False,
        remove_obsolete: bool = False,
    ) -> None:
        super().__init__(path=path)

        self._lock = RLock()

        self._load = load
        self._backup = backup
        self._remove_obsolete = remove_obsolete

        self.default_config = self._set_defaults(version, defaults)

        # Set all values to defaults. They may be overwritten later
        # when loading form file.
        self.reset_to_defaults(save=False)

        self._backup_folder = "backups"
        self._backup_suffix = "bak"

        if backup:
            self._make_backup()

        if load:
            # If config file already exists, it overrides Default options.
            self._load_from_ini(self.config_path)

            try:
                old_version = self.get_version()
            except cp.NoOptionError:
                old_version = version

            # Updating defaults only if major/minor version is different.

            if version != old_version:
                if backup:
                    self._make_backup(old_version)

                self.apply_configuration_patches(old_version)

                # Remove deprecated options if major version has changed.
                if remove_obsolete and version.major > old_version.major:
                    self.remove_deprecated_options(save=False)

                # Set new version number.
                self.set_version(version, save=False)

            # Save any changes back to file.
            self.save()

    # --- Helpers and checkers ---------------------------------------------------------

    def _set_defaults(
        self, version: Version, defaults: _DefaultsType | None
    ) -> _DefaultsType:
        """
        Check if defaults are valid and update defaults values.

        :param version: The config version.
        :param defaults: New default config values.
        """
        if defaults:
            defaults = copy.deepcopy(defaults)
        else:
            defaults = {}

        if UserConfig.DEFAULT_SECTION_NAME not in defaults:
            defaults[UserConfig.DEFAULT_SECTION_NAME] = {}

        self.default_config = defaults
        self.default_config[UserConfig.DEFAULT_SECTION_NAME]["version"] = str(version)

        return self.default_config

    def _make_backup(self, version: Version | None = None) -> None:
        """
        Make a backup of the configuration file.

        :param version: If a version is provided, it will be appended to the backup
            file name.
        """
        os.makedirs(self._dirname, exist_ok=True)
        backup_path = self.backup_path_for_version(version)

        try:
            shutil.copyfile(self.config_path, backup_path)
        except OSError:
            pass

    def _load_from_ini(self, path: str) -> None:
        """
        Loads the configuration from the given path. Overwrites any current values
        stored in memory.

        :param path: Path of config file to load.
        """
        with self._lock:
            try:
                self.read(path, encoding="utf-8")
            except cp.MissingSectionHeaderError:
                logger.error("File contains no section headers.")

    def remove_deprecated_options(self, save: bool = True) -> None:
        """
        Remove options which are present in the file but not in defaults.
        """
        for section in self.sections():
            for option, _ in self.items(section, raw=True):
                if self.get_default(section, option) is NoDefault:
                    try:
                        self.remove_option(section, option, save)
                        if len(self.items(section, raw=True)) == 0:
                            self.remove_section(section)
                    except cp.NoSectionError:
                        self.remove_section(section, save)

    # --- Compatibility API ------------------------------------------------------------

    def backup_path_for_version(self, version: Version | None) -> str:
        """
        Get backup location based on version.

        :param version: The version of the backup, if any.
        :returns: The back for the backup file.
        """
        directory = osp.join(self._dirname, self._backup_folder)

        if version:
            filename = f"{self._filename}-{str(version)}"
        else:
            filename = self._filename

        name = f"{filename}.{self._suffix}.{self._backup_suffix}"

        return osp.join(directory, name)

    def apply_configuration_patches(self, old_version: Version) -> None:
        """
        Apply any patch to configuration values on version changes.

        To be reimplemented if patches to configuration values are needed.

        :param old_version: Old config version to patch.
        """
        pass

    # --- Public API -------------------------------------------------------------------

    def get_version(self) -> Version:
        """
        Get the current config version.

        :returns: Configuration (not application!) version.
        """
        with self._lock:
            version_str = self.get(UserConfig.DEFAULT_SECTION_NAME, "version")
            return Version(version_str)

    def set_version(self, version: Version, save: bool = True) -> None:
        """
        Set configuration (not application!) version.

        :param version: New version to set.
        :param save: Whether to save changes to drive.
        """
        with self._lock:
            self.set(
                UserConfig.DEFAULT_SECTION_NAME, "version", str(version), save=save
            )

    def reset_to_defaults(
        self,
        section: str | None = None,
        save: bool = True,
    ) -> None:
        """
        Reset config to default values.

        :param section: The section to reset. If not given, reset all sections.
        :param save: Whether to save the changes to the drive.
        """
        with self._lock:
            for sec, options in self.default_config.items():
                if section is None or section == sec:
                    for option in options:
                        value = options[option]
                        self._set(sec, option, value)
            if save:
                self.save()

    def get_default(self, section: str, option: str) -> Any:
        """
        Get default value for a given ``section`` and ``option``.

        This is useful for type checking in `get` method.

        :param section: Section to search for option.
        :param option: Config option.
        :returns: Config value or None if section / option do not exist.
        """
        with self._lock:
            secdict = self.default_config.get(section, {})
            return secdict.get(option, NoDefault)

    def get(self, section: str, option: str, default: Any = NoDefault) -> Any:  # type: ignore
        """
        Get an option.

        :param section: Config section to search in.
        :param option: Config option to get.
        :param default: Default value to fall back to if not present.
        :returns: Config value.
        :raises cp.NoSectionError: if the section does not exist.
        :raises cp.NoOptionError: if the option does not exist and no default is given.
        """
        with self._lock:
            if not self.has_section(section):
                if default is NoDefault:
                    raise cp.NoSectionError(section)
                else:
                    self.add_section(section)

            if not self.has_option(section, option):
                if default is NoDefault:
                    raise cp.NoOptionError(option, section)
                else:
                    self.set(section, option, default)
                    return default

            raw_value: str = super().get(section, option, raw=True)
            default_value = self.get_default(section, option)
            value: Any

            if isinstance(default_value, str):
                value = raw_value
            else:
                try:
                    value = ast.literal_eval(raw_value)
                except (SyntaxError, ValueError):
                    value = raw_value

            if default_value is not NoDefault:
                if type(default_value) is not type(value):
                    logger.error(
                        f"Inconsistent config type for [{section}][{option}]. "
                        f"Expected {default_value.__class__.__name__} but "
                        f"got {value.__class__.__name__}."
                    )

            return value

    def set_default(self, section: str, option: str, default_value: Any) -> None:
        """
        Set Default value for a given `section`, `option`.

        If the section or option does not exist, it will be created.
        """
        with self._lock:
            if section not in self.default_config:
                self.default_config[section] = {}

            self.default_config[section][option] = default_value

    def set(self, section: str, option: str, value: Any, save: bool = True) -> None:  # type: ignore
        """
        Set an ``option` on a given ``section``.

        If section is None, the ``option`` is added to the default section.

        :param section: Config section to search in.
        :param option: Config option to set.
        :param value: Config value.
        :param save: Whether to save the changes to the drive.
        """
        with self._lock:
            default_value = self.get_default(section, option)

            if default_value is NoDefault:
                default_value = value
                self.set_default(section, option, default_value)

            if isinstance(default_value, float) and isinstance(value, int):
                value = float(value)

            if type(default_value) is not type(value):
                raise ValueError(
                    f"Inconsistent type for config value [{section}][{option}]. "
                    f"Expected {default_value.__class__.__name__} but "
                    f"got {value.__class__.__name__}."
                )

            self._set(section, option, value)

            if save:
                self.save()

    def remove_section(self, section: str, save: bool = True) -> bool:
        """
        Remove ``section`` and all options within it.

        :param section: Section to remove from the config file.
        :param save: Whether to save the changes to the drive.
        :returns: Whether the section was removed successfully.
        """
        with self._lock:
            res = super().remove_section(section)
            if save:
                self.save()
            return res

    def remove_option(self, section: str, option: str, save: bool = True) -> bool:
        """
        Remove ``option`` from ``section``.

        :param section: Section to look for the option.
        :param option: Option to remove from the config file.
        :param save: Whether to save the changes to the drive.
        :returns: Whether the section was removed successfully.
        """
        with self._lock:
            res = super().remove_option(section, option)
            if save:
                self.save()
            return res

    def cleanup(self) -> None:
        """Remove files associated with config and reset to defaults."""
        with self._lock:
            self.reset_to_defaults(save=False)
            backup_path = osp.join(self._dirname, self._backup_folder)

            # remove config file
            try:
                os.remove(self.config_path)
            except FileNotFoundError:
                pass

            # remove saved backups
            if osp.isdir(backup_path):
                for file in os.scandir(backup_path):
                    if file.name.startswith(self._filename):
                        try:
                            os.remove(file.path)
                        except FileNotFoundError:
                            pass


# ======================================================================================
# Wrapper classes
# ======================================================================================


class PersistentMutableSet(MutableSet[_T]):
    """Wraps a list in our state file as a Mapping

    :param conf: UserConfig instance to store the set.
    :param section: Section name in state file.
    :param option: Option name in state file.
    """

    def __init__(self, conf: UserConfig, section: str, option: str) -> None:
        super().__init__()
        self.section = section
        self.option = option
        self._conf = conf
        self._lock = RLock()

    def __iter__(self) -> Iterator[_T]:
        with self._lock:
            return iter(self._conf.get(self.section, self.option))

    def __contains__(self, entry: Any) -> bool:
        with self._lock:
            return entry in self._conf.get(self.section, self.option)

    def __len__(self) -> int:
        with self._lock:
            return len(self._conf.get(self.section, self.option))

    def add(self, entry: _T) -> None:
        with self._lock:
            state_list = self._conf.get(self.section, self.option)
            state_list = set(state_list)
            state_list.add(entry)
            self._conf.set(self.section, self.option, list(state_list))

    def discard(self, entry: _T) -> None:
        with self._lock:
            state_list = self._conf.get(self.section, self.option)
            state_list = set(state_list)
            state_list.discard(entry)
            self._conf.set(self.section, self.option, list(state_list))

    def update(self, *others: _T) -> None:
        with self._lock:
            state_list = self._conf.get(self.section, self.option)
            state_list = set(state_list)
            state_list.update(*others)
            self._conf.set(self.section, self.option, list(state_list))

    def difference_update(self, *others: _T) -> None:
        with self._lock:
            state_list = self._conf.get(self.section, self.option)
            state_list = set(state_list)
            state_list.difference_update(*others)
            self._conf.set(self.section, self.option, list(state_list))

    def clear(self) -> None:
        """Clears all elements."""
        with self._lock:
            self._conf.set(self.section, self.option, [])

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(section='{self.section}',"
            f"option='{self.option}', entries={list(self)})>"
        )
