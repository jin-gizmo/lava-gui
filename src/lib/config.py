"""GUI config management."""

from __future__ import annotations

from configparser import ConfigParser, NoOptionError, NoSectionError
from pathlib import Path
from typing import Any

# The values are a tuple containing the default value and a converter from string.
USER_CONFIGURABLE_DEFAULTS = {
    'current_theme': ('Light Theme', str),
    'details_font_size': (10, int),
    'heading_font_size': (11, int),
    'json_indent': (4, int),
    'https_proxy': ('', str),
    'window_height': (800, float),
    'window_width': (1400, float),
    'code_font': ('Consolas', str),
    'code_font_size': (11, int),
    'expander_icon': ('keyboard_arrow_down', lambda s: str(s).upper()),
}


# ------------------------------------------------------------------------------
class GuiConfig:
    """
    GUI configuration.

    This reads config from `~/.lava/config.cfg`. Only the `GUI` key is used.
    This is a singleton.
    """

    _config: ConfigParser = None
    cfg_key = 'GUI'
    cfg_file = Path.home() / '.lava' / 'lava.cfg'
    _instance: GuiConfig = None
    _initialised = False

    # --------------------------------------------------------------------------
    def __new__(cls):
        """Make sure this is a singleton."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # --------------------------------------------------------------------------
    def __init__(self):
        """Load config from ~/.lava/lava.cfg."""

        if self._initialised:
            return
        self._initialised = True

        self._config = ConfigParser()
        if self.cfg_file.is_file():
            self._config.read(self.cfg_file)

        for key, (default, _) in USER_CONFIGURABLE_DEFAULTS.items():
            self.set_default(key, default)

    # --------------------------------------------------------------------------
    def _write(self):
        """Write config to filesystem."""

        if not self.cfg_file.parent.exists():
            self.cfg_file.parent.mkdir(parents=True)

        with open(self.cfg_file, 'w') as configfile:
            self._config.write(configfile)

    # --------------------------------------------------------------------------
    def __repr__(self):
        """Get string representation."""

        return repr(self._config)

    # ------------------------------------------------------------------------------
    def __str__(self):
        """Get string representation."""

        return str(self._config)

    # --------------------------------------------------------------------------
    def get(self, item: str, default=None):
        """Get an item from config."""

        try:
            return self[item]
        except KeyError:
            return default

    # --------------------------------------------------------------------------
    def set_default(self, item: str, value: Any):
        """Set a default value for a key and return the current value."""

        try:
            current_value = self._config.get(self.cfg_key, item)
        except (NoSectionError, NoOptionError):
            self.set(item, str(value))
            current_value = value

        return current_value

    # --------------------------------------------------------------------------
    def set(self, key: str, value: str = None):  # noqa: A003
        """Set an item in config and save to file."""
        if value is None:
            # If value is None, we assume we want to remove the key from the config.
            if self._config.has_section(self.cfg_key):
                self._config.remove_option(self.cfg_key, key)
                self._write()
        else:
            # If value is provided, set the key-value pair in the config.
            if not self._config.has_section(self.cfg_key):
                self._config.add_section(self.cfg_key)
            self._config.set(self.cfg_key, key, value)
            self._write()

    # --------------------------------------------------------------------------
    def __getattr__(self, item: str):
        """Get an attribute from the config."""

        try:
            v = self._config.get(self.cfg_key, item)
        except (NoSectionError, NoOptionError):
            raise AttributeError(item)

        #  Apply a type conversion if we have one, If that fails, return the
        # default or the raw value.
        if item in USER_CONFIGURABLE_DEFAULTS:
            # noinspection PyBroadException
            try:
                return USER_CONFIGURABLE_DEFAULTS[item][1](v)
            except Exception as e:
                print(f'{item}: {e}')
                return USER_CONFIGURABLE_DEFAULTS[item][0]
        return v

    # --------------------------------------------------------------------------
    def __getitem__(self, item: str):
        """Get an item from config."""

        try:
            v = self._config.get(self.cfg_key, item)
        except (NoSectionError, NoOptionError):
            raise KeyError(item)

        # Apply a type conversion if we have one, If that fails, return the
        # default or the raw value.
        if item in USER_CONFIGURABLE_DEFAULTS:
            # noinspection PyBroadException
            try:
                return USER_CONFIGURABLE_DEFAULTS[item][1](v)
            except Exception as e:
                print(f'{item}: {e}')
                return USER_CONFIGURABLE_DEFAULTS[item][0]
        return v
