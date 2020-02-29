# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
This module provides user configuration file management features for Spyder.

It is based on the ConfigParser module present in the standard library.
"""

import ast
import os
import os.path as osp
import re
import shutil
import time
import configparser as cp
from threading import RLock
import logging


logger = logging.getLogger(__name__)


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
    Class used to save defaults to a file and as base class for
    UserConfig
    """

    _lock = RLock()

    def __init__(self, path, name, suffix):
        super(DefaultsConfig, self).__init__(interpolation=None)

        self._path = path
        self._name = name
        self._suffix = suffix

        if not osp.isdir(osp.dirname(self._path)):
            os.makedirs(osp.dirname(self._path))

    def _set(self, section, option, value):
        """Private set method"""
        if not self.has_section(section):
            self.add_section(section)
        if not isinstance(value, str):
            value = repr(value)

        super(DefaultsConfig, self).set(section, option, value)

    def save(self):
        """Save config into the associated file."""
        fpath = self.get_config_fpath()

        # See spyder-ide/spyder#1086 and spyder-ide/spyder#1242 for background
        # on why this method contains all the exception handling.

        with self._lock:
            try:
                # The "easy" way
                self.__write_file(fpath)
            except EnvironmentError:
                try:
                    # The "delete and sleep" way
                    if osp.isfile(fpath):
                        os.remove(fpath)

                    time.sleep(0.05)
                    self.__write_file(fpath)
                except Exception:
                    logger.exception('Failed to write user configuration file to disk')

    def __write_file(self, fpath):
        with open(fpath, 'w', encoding='utf-8') as configfile:
            self.write(configfile)

    def get_config_fpath(self):
        """Return the ini file where this configuration is stored."""
        return osp.join(self._path, self._name + self._suffix)

    def set_defaults(self, defaults):
        """Set default values and save to defaults folder location."""
        for section, options in defaults:
            for option in options:
                new_value = options[option]
                self._set(section, option, new_value)


# =============================================================================
# User config class
# =============================================================================

class UserConfig(DefaultsConfig):
    """
    UserConfig class, based on ConfigParser.

    Parameters
    ----------
    path: str
        Configuration file will be saved to this path.
    defaults: {} or [(str, {}),]
        Dictionary containing options *or* list of tuples (sec_name, options)
    load: bool
        If a previous configuration file is found, load will take the values
        from this existing file, instead of using default values.
    version: str
        version of the configuration file in 'major.minor.micro' format.
    backup: bool
        A backup will be created on version changes and on initial setup.
    raw_mode: bool
        If `True` do not apply any automatic conversion on values read from
        the configuration.
    remove_obsolete: bool
        If `True`, values that were removed from the configuration on version
        change, are removed from the saved configuration file.

    Notes
    -----
    The 'get' and 'set' arguments number and type differ from the reimplemented
    methods. 'defaults' is an attribute and not a method.
    """
    DEFAULT_SECTION_NAME = 'main'

    def __init__(self, path, name, defaults=None, load=True, version=None,
                 backup=False, raw_mode=False, remove_obsolete=False, suffix='.ini'):
        """UserConfig class, based on ConfigParser."""
        super(UserConfig, self).__init__(path=path, name=name, suffix=suffix)

        self._load = load
        self._version = self._check_version(version)
        self._backup = backup
        self._raw = 1 if raw_mode else 0
        self._remove_obsolete = remove_obsolete

        self._defaults_folder = 'defaults'
        self._backup_folder = 'backups'
        self._backup_suffix = '.bak'
        self._defaults_name_prefix = 'defaults'

        # This attribute is overriding a method from cp.ConfigParser
        self.defaults = self._check_defaults(defaults)

        if backup:
            self._make_backup()

        if load:
            # If config file already exists, it overrides Default options
            previous_fpath = self.get_previous_config_fpath()
            self._load_from_ini(previous_fpath)
            old_version = self.get_version(version)
            self._old_version = old_version

            # Save new defaults
            self._save_new_defaults(self.defaults)

            # Updating defaults only if major/minor version is different
            major_ver = self._get_major_version(version)
            major_old_ver = self._get_major_version(self._old_version)

            minor_ver = self._get_minor_version(version)
            minor_old_ver = self._get_minor_version(self._old_version)

            if minor_ver != minor_old_ver:

                if backup:
                    self._make_backup(version=old_version)

                self.apply_configuration_patches(old_version=old_version)

                # Remove deprecated options if major version has changed
                if remove_obsolete and major_ver != major_old_ver:
                    self._remove_deprecated_options(old_version)

                # Set new version number
                self.set_version(version, save=False)

            if defaults is None:
                # If no defaults are defined set file settings as default
                self.set_as_defaults()

    # --- Helpers and checkers -----------------------------------------------------------
    @staticmethod
    def _get_minor_version(version):
        """Return the 'major.minor' components of the version."""
        return version[:version.rfind('.')]

    @staticmethod
    def _get_major_version(version):
        """Return the 'major' component of the version."""
        return version[:version.find('.')]

    @staticmethod
    def _check_version(version):
        """Check version is compliant with format."""
        if version is not None:
            regex_check = re.match(r'^(\d+).(\d+).(\d+)$', version)
            if regex_check is None:
                raise ValueError('Version number {} is incorrect - must be in '
                                 'major.minor.micro format'.format(version))

        return version

    def _check_defaults(self, defaults):
        """Check if defaults are valid and update defaults values."""
        if defaults is None:
            defaults = [(self.DEFAULT_SECTION_NAME, {})]
        elif isinstance(defaults, dict):
            defaults = [(self.DEFAULT_SECTION_NAME, defaults)]
        elif isinstance(defaults, list):
            # Check is a list of tuples with strings and dictionaries
            for sec, options in defaults:
                assert isinstance(sec, str)
                assert isinstance(options, dict)
                for opt, _ in options.items():
                    assert isinstance(opt, str)
        else:
            raise ValueError('`defaults` must be a dict or a list of tuples!')

        # This attribute is overriding a method from cp.ConfigParser
        self.defaults = defaults

        if defaults is not None:
            self.reset_to_defaults(save=False)

        for sec, options in defaults:
            if sec == self.DEFAULT_SECTION_NAME:
                options['version'] = self._version

        return defaults

    @classmethod
    def _check_section_option(cls, section, option):
        """Check section and option types."""
        if section is None:
            section = cls.DEFAULT_SECTION_NAME
        elif not isinstance(section, str):
            raise RuntimeError("Argument 'section' must be a string")

        if not isinstance(option, str):
            raise RuntimeError("Argument 'option' must be a string")

        return section

    def _make_backup(self, version=None, old_version=None):
        """
        Make a backup of the configuration file.

        If `old_version` is `None` a normal backup is made. If `old_version`
        is provided, then the backup was requested for minor version changes
        and appends the version number to the backup file.
        """
        fpath = self.get_config_fpath()
        fpath_backup = self.get_backup_fpath_from_version(
            version=version, old_version=old_version)
        path = os.path.dirname(fpath_backup)

        if not osp.isdir(path):
            os.makedirs(path)

        try:
            shutil.copyfile(fpath, fpath_backup)
        except IOError:
            pass

    def _load_from_ini(self, fpath):
        """Load config from the associated file found at `fpath`."""

        with self._lock:
            try:
                self.read(fpath, encoding='utf-8')
            except cp.MissingSectionHeaderError:
                logger.error('File contains no section headers.')

    def _load_old_defaults(self, old_version):
        """Read old defaults."""
        old_defaults = cp.ConfigParser()
        fpath = self.get_defaults_fpath_from_version(old_version)
        old_defaults.read(fpath)
        return old_defaults

    def _save_new_defaults(self, defaults):
        """Save new defaults."""
        path, name = self.get_defaults_path_name_from_version()
        new_defaults = DefaultsConfig(path=path, name=name, suffix=self._suffix)
        if not osp.isfile(new_defaults.get_config_fpath()):
            new_defaults.set_defaults(defaults)
            new_defaults.save()

    def _update_defaults(self, defaults, old_version):
        """Update defaults after a change in version."""
        old_defaults = self._load_old_defaults(old_version)
        for section, options in defaults:
            for option in options:
                new_value = options[option]
                try:
                    old_val = old_defaults.get(section, option)
                except (cp.NoSectionError, cp.NoOptionError):
                    old_val = None

                if old_val is None or str(new_value) != old_val:
                    self._set(section, option, new_value)

    def _remove_deprecated_options(self, old_version):
        """
        Remove options which are present in the file but not in defaults.
        """
        for section in self.sections():
            for option, _ in self.items(section, raw=self._raw):
                if self.get_default(section, option) is NoDefault:
                    try:
                        self.remove_option(section, option)
                        if len(self.items(section, raw=self._raw)) == 0:
                            self.remove_section(section)
                    except cp.NoSectionError:
                        self.remove_section(section)

    # --- Compatibility API --------------------------------------------------------------

    def get_previous_config_fpath(self):
        """Return the last configuration file used if found."""
        return self.get_config_fpath()

    def get_config_fpath_from_version(self, version=None):
        """
        Return the configuration path for given version.

        If no version is provided, it returns the current file path.
        """
        return self.get_config_fpath()

    def get_backup_fpath_from_version(self, version=None, old_version=None):
        """
        Get backup location based on version.

        `old_version` can be used for checking compatibility whereas `version`
        relates to adding the version to the file name.

        To be reimplemented if versions changed backup location.
        """
        fpath = self.get_config_fpath()
        path = osp.join(osp.dirname(fpath), self._backup_folder)
        new_fpath = osp.join(path, osp.basename(fpath))
        if version is None:
            backup_fpath = '{}{}'.format(new_fpath, self._backup_suffix)
        else:
            backup_fpath = "{}-{}{}".format(new_fpath, version, self._backup_suffix)
        return backup_fpath

    def get_defaults_path_name_from_version(self, old_version=None):
        """
        Get defaults location based on version.

        To be reimplemented if versions changed defaults location.
        """
        version = old_version if old_version else self._version
        defaults_path = osp.join(osp.dirname(self.get_config_fpath()),
                                 self._defaults_folder)
        if version is None:
            name = '{}-{}'.format(self._defaults_name_prefix, self._name)
        else:
            name = '{}-{}-{}'.format(self._defaults_name_prefix, self._name, version)
        if not osp.isdir(defaults_path):
            os.makedirs(defaults_path)

        return defaults_path, name

    def get_defaults_fpath_from_version(self, old_version=None):
        """
        Get defaults location based on version.

        To be reimplemented if versions changed defaults location.
        """
        defaults_path, name = self.get_defaults_path_name_from_version(old_version)

        return osp.join(defaults_path, name + self._suffix)

    def apply_configuration_patches(self, old_version=None):
        """
        Apply any patch to configuration values on version changes.

        To be reimplemented if patches to configuration values are needed.
        """
        pass

    # --- Public API ---------------------------------------------------------------------
    def get_version(self, version='0.0.0'):
        """Return configuration (not application!) version."""
        return self.get(self.DEFAULT_SECTION_NAME, 'version', version)

    def set_version(self, version='0.0.0', save=True):
        """Set configuration (not application!) version."""
        version = self._check_version(version)
        self.set(self.DEFAULT_SECTION_NAME, 'version', version, save=save)

    def reset_to_defaults(self, save=True, section=None):
        """Reset config to Default values."""
        for sec, options in self.defaults:
            if section is None or section == sec:
                for option in options:
                    value = options[option]
                    self._set(sec, option, value)
        if save:
            self.save()

    def set_as_defaults(self):
        """Set defaults from the current config."""
        self.defaults = []
        for section in self.sections():
            secdict = {}
            for option, value in self.items(section, raw=self._raw):
                secdict[option] = value
            self.defaults.append((section, secdict))

    def get_default(self, section, option):
        """
        Get default value for a given `section` and `option`.

        This is useful for type checking in `get` method.
        """
        section = self._check_section_option(section, option)
        for sec, options in self.defaults:
            if sec == section:
                if option in options:
                    value = options[option]
                    break
        else:
            value = NoDefault

        return value

    def get(self, section, option, default=NoDefault):
        """
        Get an option.

        Parameters
        ----------
        section: str
            Section name. If `None` is provide use the default section name.
        option: str
            Option name for `section`.
        default:
            Default value (if not specified, an exception will be raised if
            option doesn't exist).
        """
        section = self._check_section_option(section, option)

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

        value = super(UserConfig, self).get(section, option, raw=self._raw)

        default_value = self.get_default(section, option)
        if isinstance(default_value, bool):
            value = ast.literal_eval(value)
        elif isinstance(default_value, float):
            value = float(value)
        elif isinstance(default_value, int):
            value = int(value)
        else:
            try:
                # Lists, tuples, None, ...
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                pass

        if default_value is not NoDefault and type(default_value) is not type(value):
            logger.error(f'Inconsistent config type for [{section}][{option}]. '
                         f'Expected {default_value.__class__.__name__} but '
                         f'got {value.__class__.__name__}.')

        return value

    def set_default(self, section, option, default_value):
        """
        Set Default value for a given `section`, `option`.

        If no defaults exist, no default is created. To be able to set
        defaults, a call to set_as_defaults is needed to create defaults
        based on current values.
        """
        section = self._check_section_option(section, option)
        for sec, options in self.defaults:
            if sec == section:
                options[option] = default_value

    def set(self, section, option, value, save=True):
        """
        Set an `option` on a given `section`.

        If section is None, the `option` is added to the default section.
        """
        section = self._check_section_option(section, option)
        default_value = self.get_default(section, option)

        if default_value is NoDefault:
            default_value = value
            self.set_default(section, option, default_value)

        if isinstance(default_value, bool):
            value = bool(value)
        elif isinstance(default_value, float):
            value = float(value)
        elif isinstance(default_value, int):
            value = int(value)
        # elif isinstance(default_value, list):
        #     value = list(value)
        # elif isinstance(default_value, tuple):
        #     value = tuple(value)
        elif not isinstance(default_value, str):
            value = repr(value)

        self._set(section, option, value)
        if save:
            self.save()

    def remove_section(self, section):
        """Remove `section` and all options within it."""
        super(UserConfig, self).remove_section(section)
        self.save()

    def remove_option(self, section, option):
        """Remove `option` from `section`."""
        super(UserConfig, self).remove_option(section, option)
        self.save()

    def cleanup(self):
        """Remove files associated with config."""
        fpath = self.get_config_fpath()

        backup_path = osp.join(self._path, self._backup_folder)
        defaults_path = osp.join(self._path, self._defaults_folder)

        os.remove(fpath)

        for file in os.scandir(backup_path):
            if file.name.startswith(self._name):
                os.remove(file.path)

        for file in os.scandir(defaults_path):
            if file.name.startswith(f'{self._defaults_name_prefix}-{self._name}'):
                os.remove(file.path)

        # clean up backup and defaults files from previous version of maestral
        for file in os.scandir(self._path):
            if file.is_file():
                if (self._backup_suffix in file.name
                        or self._defaults_name_prefix in file.name):
                    os.remove(file.path)
