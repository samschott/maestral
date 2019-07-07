# -*- coding: utf-8 -*-
"""
This module provides user configuration file management features.

It's based on the ConfigParser module (present in the standard library).
"""

# Std imports
import ast
import os
import os.path as osp
import re
import shutil
import time
import configparser as cp

# Local imports
from maestral.config.base import get_conf_path, get_home_dir


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
    def __init__(self, name, subfolder):
        cp.ConfigParser.__init__(self, interpolation=None)
        self.name = name
        self.subfolder = subfolder

    def _set(self, section, option, value, verbose):
        """
        Private set method
        """
        if not self.has_section(section):
            self.add_section(section)
        if not isinstance(value, str):
            value = repr(value)
        if verbose:
            print('%s[ %s ] = %s' % (section, option, value))
        cp.ConfigParser.set(self, section, option, value)

    def _save(self):
        """
        Save config into the associated .ini file
        """
        # See spyder-ide/spyder#1086 and spyder-ide/spyder#1242 for background
        # on why this method contains all the exception handling.
        fname = self.filename()

        def _write_file(fname):
            with open(fname, 'w', encoding='utf-8') as configfile:
                self.write(configfile)

        try:  # the "easy" way
            _write_file(fname)
        except EnvironmentError:
            try:  # the "delete and sleep" way
                if osp.isfile(fname):
                    os.remove(fname)
                time.sleep(0.05)
                _write_file(fname)
            except Exception as e:
                print("Failed to write user configuration file to disk, with "
                      "the exception shown below")
                print(e)

    def filename(self):
        """Create a .ini filename. This .ini files stores the global preferences.
        """
        if self.subfolder is None:
            config_file = osp.join(get_home_dir(), '.%s.ini' % self.name)
            return config_file
        else:
            folder = get_conf_path(self.subfolder)
            # Save defaults in a "defaults" dir of .spyder2 to not pollute it
            if 'defaults' in self.name:
                folder = osp.join(folder, 'defaults')
                if not osp.isdir(folder):
                    os.mkdir(folder)
            config_file = osp.join(folder, '%s.ini' % self.name)
            return config_file

    def set_defaults(self, defaults):
        for section, options in defaults:
            for option in options:
                new_value = options[option]
                self._set(section, option, new_value, False)


# =============================================================================
# User config class
# =============================================================================

# noinspection PyTypeChecker
class UserConfig(DefaultsConfig):
    """
    UserConfig class, based on ConfigParser
    name: name of the config
    defaults: dictionnary containing options
              *or* list of tuples (section_name, options)
    version: version of the configuration file (X.Y.Z format)
    subfolder: configuration file will be saved in %home%/subfolder/%name%.ini
    Note that 'get' and 'set' arguments number and type
    differ from the overriden methods
    """
    DEFAULT_SECTION_NAME = 'main'

    def __init__(self, name, defaults=None, load=True, version=None,
                 subfolder=None, backup=False, raw_mode=False,
                 remove_obsolete=False):
        DefaultsConfig.__init__(self, name, subfolder)
        self.raw = 1 if raw_mode else 0
        if (version is not None and
                re.match(r'^(\d+).(\d+).(\d+)$', version) is None):
            raise ValueError("Version number %r is incorrect - must be in X.Y.Z format" % version)
        if isinstance(defaults, dict):
            defaults = [(self.DEFAULT_SECTION_NAME, defaults)]
        self.defaults = defaults
        if defaults is not None:
            self.reset_to_defaults(save=False)
        fname = self.filename()
        if backup:
            try:
                shutil.copyfile(fname, "%s.bak" % fname)
            except IOError:
                pass
        if load:
            # If config file already exists, it overrides Default options:
            self.load_from_ini()
            old_ver = self.get_version(version)
            _major = lambda _t: _t[:_t.find('.')]
            _minor = lambda _t: _t[:_t.rfind('.')]
            # Save new defaults
            self._save_new_defaults(defaults, version, subfolder)
            # Updating defaults only if major/minor version is different
            if _minor(version) != _minor(old_ver):
                if backup:
                    try:
                        shutil.copyfile(fname, "%s-%s.bak" % (fname, old_ver))
                    except IOError:
                        pass
                self._update_defaults(defaults, old_ver)

                # Remove deprecated options if major version has changed
                if remove_obsolete or _major(version) != _major(old_ver):
                    self._remove_deprecated_options(old_ver)
                # Set new version number
                self.set_version(version, save=False)
            if defaults is None:
                # If no defaults are defined, set .ini file settings as default
                self.set_as_defaults()

    def get_version(self, version='0.0.0'):
        """Return configuration (not application!) version"""
        return self.get(self.DEFAULT_SECTION_NAME, 'version', version)

    def set_version(self, version='0.0.0', save=True):
        """Set configuration (not application!) version"""
        self.set(self.DEFAULT_SECTION_NAME, 'version', version, save=save)

    def load_from_ini(self):
        """
        Load config from the associated .ini file
        """
        try:
            self.read(self.filename(), encoding='utf-8')
        except cp.MissingSectionHeaderError:
            print("Warning: File contains no section headers.")

    def _load_old_defaults(self, old_version):
        """Read old defaults"""
        old_defaults = cp.ConfigParser()
        path = osp.dirname(self.filename())
        path = osp.join(path, 'defaults')
        old_defaults.read(osp.join(path, 'defaults-'+old_version+'.ini'))
        return old_defaults

    def _save_new_defaults(self, defaults, new_version, subfolder):
        """Save new defaults"""
        new_defaults = DefaultsConfig(name='defaults-'+new_version,
                                      subfolder=subfolder)
        if not osp.isfile(new_defaults.filename()):
            new_defaults.set_defaults(defaults)
            new_defaults._save()

    def _update_defaults(self, defaults, old_version, verbose=False):
        """Update defaults after a change in version"""
        old_defaults = self._load_old_defaults(old_version)
        for section, options in defaults:
            for option in options:
                new_value = options[option]
                try:
                    old_value = old_defaults.get(section, option)
                except (cp.NoSectionError, cp.NoOptionError):
                    old_value = None
                if old_value is None or str(new_value) != old_value:
                    self._set(section, option, new_value, verbose)

    def _remove_deprecated_options(self, old_version):
        """
        Remove options which are present in the .ini file but not in defaults
        """
        old_defaults = self._load_old_defaults(old_version)
        for section in old_defaults.sections():
            for option, _ in old_defaults.items(section, raw=self.raw):
                if self.get_default(section, option) is NoDefault:
                    try:
                        self.remove_option(section, option)
                        if len(self.items(section, raw=self.raw)) == 0:
                            self.remove_section(section)
                    except cp.NoSectionError:
                        self.remove_section(section)

    def cleanup(self):
        """
        Remove .ini file associated to config
        """
        os.remove(self.filename())

    def set_as_defaults(self):
        """
        Set defaults from the current config
        """
        self.defaults = []
        for section in self.sections():
            secdict = {}
            for option, value in self.items(section, raw=self.raw):
                secdict[option] = value
            self.defaults.append((section, secdict))

    def reset_to_defaults(self, save=True, verbose=False, section=None):
        """
        Reset config to Default values
        """
        for sec, options in self.defaults:
            if section is None or section == sec:
                for option in options:
                    value = options[option]
                    self._set(sec, option, value, verbose)
        if save:
            self._save()

    def _check_section_option(self, section, option):
        """
        Private method to check section and option types
        """
        if section is None:
            section = self.DEFAULT_SECTION_NAME
        elif not isinstance(section, str):
            raise RuntimeError("Argument 'section' must be a string")
        if not isinstance(option, str):
            raise RuntimeError("Argument 'option' must be a string")
        return section

    def get_default(self, section, option):
        """
        Get Default value for a given (section, option)
        -> useful for type checking in 'get' method
        """
        section = self._check_section_option(section, option)
        for sec, options in self.defaults:
            if sec == section:
                if option in options:
                    return options[option]
        else:
            return NoDefault

    def get(self, section, option, default=NoDefault):
        """
        Get an option
        section=None: attribute a default section name
        default: default value (if not specified, an exception
        will be raised if option doesn't exist)
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

        value = cp.ConfigParser.get(self, section, option, raw=self.raw)
        # Use type of default_value to parse value correctly
        default_value = self.get_default(section, option)
        if isinstance(default_value, bool):
            value = ast.literal_eval(value)
        elif isinstance(default_value, float):
            value = float(value)
        elif isinstance(default_value, int):
            value = int(value)
        else:
            try:
                # lists, tuples, ...
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                pass
        return value

    def set_default(self, section, option, default_value):
        """
        Set Default value for a given (section, option)
        -> called when a new (section, option) is set and no default exists
        """
        section = self._check_section_option(section, option)
        for sec, options in self.defaults:
            if sec == section:
                options[option] = default_value

    def set(self, section, option, value, verbose=False, save=True):
        """
        Set an option
        section=None: attribute a default section name
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
        elif not isinstance(default_value, str):
            value = repr(value)
        self._set(section, option, value, verbose)
        if save:
            self._save()

    def remove_section(self, section):
        cp.ConfigParser.remove_section(self, section)
        self._save()

    def remove_option(self, section, option):
        cp.ConfigParser.remove_option(self, section, option)
        self._save()
