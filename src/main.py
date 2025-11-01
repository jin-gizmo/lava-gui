#!/usr/bin/env python3

"""Lava GUI based on Flet."""

# TODO: Heaps. On the upside ... it does work (which, frankly, gets 7 out of 10
#       srtraight away, but ...
#       Code hygiene still needs some work.
#       Also, its a bit of a fusion of an object based approach and functional
#       approach which has resulted in a bunch of random components being passed
#       around in function params. It also has magic numbers embedded in some
#       places and it's not always obvious how they relate to each other.

from __future__ import annotations

import decimal
import json
import os
import re
import threading
import tomllib
import traceback
from argparse import Namespace
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache, partial
from pathlib import Path
from typing import Any

import boto3
import flet as ft
import lava.version
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError
from cachetools.func import ttl_cache
from lava.lavacore import dispatch, get_job_spec, scan_jobs, scan_realms
from lava.lib.aws import s3_split
from lava.lib.misc import json_default

from lib.config import GuiConfig
from lib.utils import format_isodate_difference, suppress_exception

DEBUG = False

# ------------------------------------------------------------------------------
# Event count fetch limits
MAX_EVENTS = 50
DEFAULT_EVENTS = 10

WINDOW_MIN_HEIGHT = 600
WINDOW_MIN_WIDTH = 1400

# Don't permit new dispatch more frequently than this.
DISPATCH_BLACKOUT = timedelta(seconds=10)

# Don't permit new event fetch more frequently than this.
EVENT_BLACKOUT = timedelta(seconds=10)

# Don't permit check for running jobs more frequently than this.
RUNNING_JOBS_BLACKOUT = timedelta(seconds=120)

# Global Variables that are used
BORDER_TRANSPARENT = ft.Colors.TRANSPARENT
TEXTFIELD_WIDTH_SIZE_WORKER = 80
DATA_CELL_INNER_TEXTBOX_PADDING = ft.Padding(left=0, right=0, top=2, bottom=2)
GUI_THEMES = {}

# When searching for running jobs look back this many hours
RUNNING_JOB_LOOKBACK_HOURS = 12

EVENT_STATUS_COLOUR = {
    'starting': '#333333',
    'running': 'blue',
    'complete': '#228822',
    'logging': '#8888ff',
    'retrying': '#ff7700',
    'failed': 'red',
    'rejected': 'red',
    'skipped': '#a9a9a9',
    'action_failed': '#ff9300',
}

if DEBUG:
    debug = print
else:
    # noinspection PyUnusedLocal
    def debug(*args, **kwargs):
        """Do nothing if debug disabled."""
        pass


# Exploit the pyproject.toml file to get some app context information
with open(Path(__file__).parent / 'assets' / 'pyproject.toml', 'rb') as tfp:
    app_info = tomllib.load(tfp)

APP_INFO = '\n'.join(
    (
        '',
        f'GUI Version: {app_info["project"]["version"]} (Flet)',
        f'Lava Version: {lava.version.__version__}',
        '',
        'Flet GUI Created by:',
        *(f'    {author["name"]}' for author in app_info['project']['authors']),
        '',
        f'{app_info["tool"]["flet"]["copyright"]}',
    )
)

# ------------------------------------------------------------------------------
# Some macros for common widgets.
# Defined as partial rather than constants so we can override parameters.
DetailTextStyle = partial(ft.TextStyle, size=GuiConfig().details_font_size, color=ft.Colors.PRIMARY)
DetailTextBoldStyle = partial(
    ft.TextStyle,
    size=GuiConfig().details_font_size,
    color=ft.Colors.PRIMARY,
    weight=ft.FontWeight.BOLD,
)
CodeTextStyle = partial(
    ft.TextStyle, font_family=GuiConfig().code_font, size=GuiConfig().code_font_size
)
JobListText = partial(
    ft.Text,
    font_family=GuiConfig().code_font,
    size=GuiConfig().details_font_size,
    color=ft.Colors.PRIMARY,
)
DetailText = partial(ft.Text, style=DetailTextStyle())
DetailTextBold = partial(ft.Text, style=DetailTextBoldStyle())
HeadingText = partial(
    ft.Text, size=GuiConfig().heading_font_size, color=ft.Colors.PRIMARY, weight=ft.FontWeight.BOLD
)
ColumnHeadingText = partial(
    ft.Text,
    size=GuiConfig().details_font_size,
    color=ft.Colors.SECONDARY,
    weight=ft.FontWeight.BOLD,
)


# ------------------------------------------------------------------------------
KEY_PAGE_REFERENCES: dict[str, Any] = {}


@dataclass
class RealmCache:
    """For returning to a realm to where you left off."""

    last_search: str = ''
    last_selected_job_id: str = None


@dataclass
class ConnectionCache:
    """For holding AWS connections so they don't have to be reopened multiple times."""

    aws_session: boto3.Session = None
    dynamo_db_res: Any = None
    dynamo_db_client: BaseClient = None
    s3_client: BaseClient = None


@dataclass
class ProfileCache:
    """For returning to a profile to where you left off."""

    last_realm: str = None
    realm_cache: dict[str, RealmCache] = field(default_factory=dict)
    conn_cache: ConnectionCache = field(default_factory=ConnectionCache)


# ------------------------------------------------------------------------------
class LavaAwsContext:
    """
    A Singleton class for managing AWS connection attributes within the application.

    When a profile is updated, the old connection is closed, and a new one is initialized.

    Attributes:
        profile: The current AWS profile selected.
        realm: The current realm context.
        run_id: The current run identifier.
        job_id: The job identifier.
        job_list: The list of jobs in the current realm.
        events_list: The list of events associated with a job.
        current_job: The currently selected job.
        aws_session: The active boto3 session for the profile.
        dynamo_db_client: The DynamoDB client for the active connection.
        dynamo_db_res: The DynamoDB resource for the active connection.
        globals: Global values extracted from the job spec.
        aws_config: Configuration options for the AWS connection.
        old_file: Tracks the last generated file for cleanup purposes.
        params: Parameters associated with the selected job.

    """

    _instance = None

    # --------------------------------------------------------------------------
    def __new__(cls, *args, **kwargs):
        """Enforce the Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # --------------------------------------------------------------------------
    # TODO: There is a bunch of unused cruft in here.
    def __init__(self, profile: str = None):
        """Initialize the LavaAwsContext with an optional profile."""
        if not hasattr(self, '_initialized') or not self._initialized:  # noqa
            self.profile = profile
            self.realm = None
            self.run_id = None
            self.job_id = None
            self.job_list = []
            self.events_list = []
            self.current_job = None
            self.aws_session = None
            self.dynamo_db_client = None
            self.dynamo_db_res = None
            self.s3_client = None
            self.globals = {}
            self.old_file = None
            self.params = {}
            self._initialized = True
            proxy = os.getenv('HTTPS_PROXY', GuiConfig().get('https_proxy', ''))
            self.aws_config = Config(proxies=({'http': proxy, 'https': proxy} if proxy else {}))
            self.profile_cache: dict[str, ProfileCache] = {}

        if profile:
            self.set_profile(profile)

    # --------------------------------------------------------------------------
    def set_profile(self, profile):
        """
        Update the AWS profile and initialize a new boto3 session.

        Closes any existing session before creating a new one.
        """
        if self.aws_session:
            self.close()  # Reset connections

        try:
            if profile not in self.profile_cache:
                self.profile_cache[profile] = ProfileCache()

            conn_cache = self.profile_cache[profile].conn_cache

            self.aws_session = conn_cache.aws_session or boto3.Session(profile_name=profile)
            self.dynamo_db_res = conn_cache.dynamo_db_res or self.aws_session.resource(
                'dynamodb', config=self.aws_config
            )
            self.dynamo_db_client = conn_cache.dynamo_db_client or self.aws_session.client(
                'dynamodb', config=self.aws_config
            )
            self.s3_client = conn_cache.s3_client or self.aws_session.client(
                's3', config=self.aws_config
            )
            check_aws_account_access(profile)

            self.profile = profile
            conn_cache.aws_session = self.aws_session
            conn_cache.dynamo_db_client = self.dynamo_db_client
            conn_cache.dynamo_db_res = self.dynamo_db_res
            conn_cache.s3_client = self.s3_client
            debug(f'Initialized AWS connection with profile: {profile}')

        except Exception as e:
            debug(f'Failed to initialize AWS connection for profile {profile}: {e}')
            self.close()
            self.profile = None
            self.profile_cache[profile].conn_cache = ConnectionCache()
            raise e

    # --------------------------------------------------------------------------
    def close(self):
        """Cleanup resources before switching profiles or exiting."""
        debug(f'Closing AWS connection for profile: {self.profile}')
        self.aws_session = None
        self.dynamo_db_res = None
        self.dynamo_db_client = None
        self.s3_client = None

    # --------------------------------------------------------------------------
    def reset(self):
        """Reset all attributes except for the current profile."""
        self.realm = None
        self.run_id = None
        self.job_id = None
        self.job_list = []
        self.events_list = []
        self.current_job = None
        self.globals = {}
        self.old_file = None
        self.params = {}

    # --------------------------------------------------------------------------
    def get(self, item: str, default=None):
        """Retrieve an attribute value by name, or return a default if it doesn't exist."""
        return getattr(self, item, default)


# ------------------------------------------------------------------------------
class GuiTheme:
    """Class to create Themes."""

    # --------------------------------------------------------------------------
    def __init__(
        self,
        name: str = None,
        primary: str = '#FF5722',
        secondary: str = '#03A9F4',
        background: str = '#FFFFFF',
        surface: str = '#000000',
        error: str = '#F44336',
        on_primary: str = '#FFFFFF',
        on_secondary: str = '#000000',
        on_background: str = '#000000',
        on_surface: str = '#000000',
        on_error: str = '#FFFFFF',
        base_theme: ft.Theme = None,
        visual_density: ft.VisualDensity = ft.VisualDensity.COMFORTABLE,
        use_material3: bool = True,
        font_family: str = None,
        page_transitions=None,
        data_table_theme: ft.DataTableTheme = None,
        button_theme: ft.ButtonTheme = None,
        text_theme: ft.TextTheme = None,
        icon_theme: ft.IconTheme = None,
        card_theme: ft.CardTheme = None,
        dialog_theme: ft.DialogTheme = None,
        markdown_code_theme: ft.MarkdownCodeTheme = None,
        **additional_themes,
    ):
        """
        Initialize a GuiTheme instance with options for a Flet theme.

        Args:
            name (str): The name of the theme.
            primary (str): Primary color of the theme.
            secondary (str): Secondary color of the theme.
            background (str): Background color of the theme.
            surface (str): Surface color of the theme.
            error (str): Error color of the theme.
            on_primary (str): Color for text/icons on primary color.
            on_secondary (str): Color for text/icons on secondary color.
            on_background (str): Color for text/icons on background.
            on_surface (str): Color for text/icons on surface.
            on_error (str): Color for text/icons on error color.
            base_theme (ft.Theme): Theme class object that will be default for this GuiTheme.
            visual_density (ft.VisualDensity): Density for visual components.
            use_material3 (bool): Whether to use Material 3 design.
            font_family (str): Font family for the theme.
            page_transitions (ft.PageTransitionsTheme): Page transition theme.
            data_table_theme (ft.DataTableTheme): DataTable specific theme.
            button_theme (ft.ButtonTheme): Button specific theme.
            text_theme (ft.TextTheme): Text specific theme.
            icon_theme (ft.IconTheme): Icon specific theme.
            card_theme (ft.CardTheme): Card specific theme.
            dialog_theme (ft.DialogTheme): Dialog specific theme.
            markdown_code_theme (ft.MarkdownCodeTheme): Markdown code specific theme.
            additional_themes (dict): Additional theme options for components (e.g., sliders).

        """
        self.name = name
        self.primary = primary
        self.secondary = secondary
        self.background = background
        self.surface = surface
        self.error = error
        self.on_primary = on_primary
        self.on_secondary = on_secondary
        self.on_background = on_background
        self.on_surface = on_surface
        self.on_error = on_error
        self.base_theme = base_theme
        self.visual_density = visual_density
        self.use_material3 = use_material3
        self.font_family = font_family
        self.page_transitions = page_transitions
        self.data_table_theme = data_table_theme
        self.button_theme = button_theme
        self.text_theme = text_theme
        self.icon_theme = icon_theme
        self.card_theme = card_theme
        self.dialog_theme = dialog_theme
        self.markdown_code_theme = markdown_code_theme
        self.additional_themes = additional_themes

    # --------------------------------------------------------------------------
    def update_theme(self):
        """Add the Theme to GuiTheme object."""
        color_scheme = ft.ColorScheme(
            primary=self.primary,
            secondary=self.secondary,
            background=self.background,
            surface=self.surface,
            error=self.error,
            on_primary=self.on_primary,
            on_secondary=self.on_secondary,
            on_background=self.on_background,
            on_surface=self.on_surface,
            on_error=self.on_error,
        )

        self.base_theme = ft.Theme(
            color_scheme=color_scheme,
            visual_density=self.visual_density,
            use_material3=self.use_material3,
            font_family=self.font_family,
            page_transitions=self.page_transitions,
            data_table_theme=self.data_table_theme,
            button_theme=self.button_theme,
            text_theme=self.text_theme,
            icon_theme=self.icon_theme,
            card_theme=self.card_theme,
            dialog_theme=self.dialog_theme,
            **self.additional_themes,  # Include additional themes for specific components
        )
        GUI_THEMES[f'{self.name}'] = self


# Don't be fooled by things like Flet's ElevatedButtonTheme. Useless.
# They don't connect to anything and can't be passed to an ElevatedButton.
# All we need here is a container for some common attributes.
blue_button_style = Namespace(
    button_color='#BEE9E4',
    highlight_color='#000000',
    height=50,
    min_width=100,
    padding=10,
)

light_theme_gui_theme = GuiTheme(
    name='Light Theme',
    primary='#000000',
    secondary='#03A9F4',  # Bright blue for secondary color
    background='#FFFFFF',  # Pure white background for the overall page
    on_surface='#000000',
    surface='#FAFAFA',  # Light grey surface color for cards, etc.
    on_primary='#000000',  # Black text/icons on primary (good contrast)
    on_secondary='#73eb7c',  # Used to show selected items
    on_background='#000000',
    markdown_code_theme=ft.MarkdownCodeTheme.ATOM_ONE_LIGHT,
)
light_theme_gui_theme.update_theme()

dark_theme_gui_theme = GuiTheme(
    name='Dark Theme',
    primary='white',
    secondary='blue',
    background='#36454F',
    on_surface='white',
    surface='#292c33',
    on_primary='red',
    on_secondary='#26612a',  # used to show selected items
    on_background='yellow',
    markdown_code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
)
dark_theme_gui_theme.update_theme()


# ------------------------------------------------------------------------------
class LavaJobsPanel(ft.ListView):
    """Lava job list with single selected item."""

    page: ft.Page = None
    on_job_click = None
    selected_job: ft.Control = None

    # --------------------------------------------------------------------------
    def __init__(self, on_job_click=None, page=None, **kwargs):
        """Initialize LavaJobs instance."""
        super().__init__(**kwargs)  # Pass only valid kwargs to ft.ListView
        self.page = page
        self.on_job_click = on_job_click  # Store the callback for job selection
        self.original_job_list = []

    # --------------------------------------------------------------------------
    def set_original_job_list(self, job_list: list[str]):
        """Update the original unfiltered job list."""
        self.original_job_list = job_list.copy()

    # --------------------------------------------------------------------------
    def update_job_list(self, new_jobs: list):
        """Update the displayed job list and syncs the original job list."""

        self.controls.clear()
        for count, job in enumerate(new_jobs):
            self.controls.append(self._job_item(job, count))

        self.page.update(self)

    # --------------------------------------------------------------------------
    def on_select(self, e: ft.ControlEvent | None, selected_job_id: str = None):
        """When a specific job is selected."""

        prev_selected = self.selected_job
        if prev_selected is not None:
            prev_selected.bgcolor = None

        if (
            e is None or e.control is None or e.control.content is None
        ) and selected_job_id is None:
            return

        selected_job_control = None
        if selected_job_id is not None:
            for control in self.controls:
                if control.content and control.content.value == selected_job_id:
                    selected_job_control = control
                    break

        if selected_job_control is None and e is None:
            return

        self.selected_job = selected_job_control or e.control
        self.selected_job.bgcolor = ft.Colors.ON_SECONDARY
        self.page.update(self)
        self.selected_job.update()

        current_connection = LavaAwsContext()

        # Retrieve the selected job item and its data
        content = self.selected_job.content.value

        current_connection.current_job = content
        if self.on_job_click:
            self.on_job_click(content)

    # --------------------------------------------------------------------------
    def on_hover(self, e: ft.ControlEvent):
        """Handle hover on/off over a job name in the job list."""

        with suppress(Exception):
            colour = ft.Colors.ON_SECONDARY if e.control == self.selected_job else None
            if colour is None:
                colour = ft.Colors.SECONDARY if e.data == 'true' else None
            e.control.bgcolor = colour
            e.control.update()

    # --------------------------------------------------------------------------
    def _job_item(self, text, ind) -> ft.Container:
        """
        Create a widget for a job in the job list.

        :param text:    Text of the item (job name)
        :param ind:     Index.
        :return:        The job name container.
        """

        return ft.Container(
            JobListText(text),
            bgcolor=ft.Colors.SURFACE,
            data=(ind, (DetailText(text))),
            on_hover=self.on_hover,
            on_click=self.on_select,
        )


# ------------------------------------------------------------------------------
class SettingsDialog:
    """Class to create the Setting related dialog."""

    # --------------------------------------------------------------------------
    def __init__(self, page: ft.Page, themes: list[GuiTheme], apply_theme_callback):
        """Initialise SettingsDialog instance."""
        self.page = page
        self.themes = themes
        self.apply_theme_callback = apply_theme_callback

        self.theme_dropdown = ft.Dropdown(
            label='Select Theme',
            label_style=DetailTextStyle(),
            text_style=DetailTextStyle(),
            color=ft.Colors.PRIMARY,
            bgcolor=ft.Colors.SURFACE,
            options=[
                ft.dropdown.Option(key=str(i), text=f'{themes[i].name}') for i in range(len(themes))
            ],
        )

        # Create the apply button
        self.apply_button = ft.ElevatedButton(
            text='Apply',
            on_click=self.apply_theme,
            bgcolor=blue_button_style.button_color,
            color=blue_button_style.highlight_color,
        )

        # Credit Box
        self.credit_box = ft.Container(
            border=ft.border.all(1, color=BORDER_TRANSPARENT),
            bgcolor=ft.Colors.SURFACE,
            content=ft.Column(controls=[DetailTextBold(APP_INFO)]),
            height=75,
        )

        # Create the dialog
        self.dialog = ft.AlertDialog(
            title=ft.Text('Theme Settings', color=ft.Colors.PRIMARY),
            modal=False,
            bgcolor=ft.Colors.SURFACE,
            content=ft.Column(
                controls=[
                    self.theme_dropdown,
                    self.apply_button,
                    self.credit_box,
                ],
                spacing=10,
            ),
            actions_alignment=ft.MainAxisAlignment.END,
        )

    # --------------------------------------------------------------------------
    def open_dialog(self):
        """Open a dialog."""

        self.page.open(self.dialog)

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def apply_theme(self, e: ft.ControlEvent):
        """Apply a theme to the application."""

        selected_index = int(self.theme_dropdown.value)
        if 0 <= selected_index < len(self.themes):
            self.apply_theme_callback(self.themes[selected_index])
            self.dialog.open = False
            debug(f'This is surface colour: {self.page.theme.color_scheme.surface}')
            self.page.update()


# ------------------------------------------------------------------------------
class JobLogsContent(ft.Column):
    """Class that contains all contents of Job Logs."""

    # --------------------------------------------------------------------------
    def __init__(self, theme: ft.Theme):
        """Initialise Job LogsContent instance."""

        super().__init__()
        self.current_run_id = None
        self.events_log_list = {}
        self.events_list: list[dict] = []
        self.previous_selected_row_index = None

        self.data_row_height = GuiConfig().details_font_size + 6
        self.heading_row_height = GuiConfig().heading_font_size + 6

        # Dropdown for max events
        self.max_events_dropdown = ft.Dropdown(
            label='Max Events',
            options=[ft.dropdown.Option(str(i)) for i in range(DEFAULT_EVENTS, MAX_EVENTS + 1, 10)],
            color=ft.Colors.PRIMARY,
            border_color=ft.Colors.PRIMARY,
            bgcolor=ft.Colors.SURFACE,
            value=str(DEFAULT_EVENTS),
            width=110,
            expand=True,
            text_style=DetailTextStyle(),
            label_style=DetailTextStyle(),
        )

        # Fetch events button
        self.fetch_events_button = ft.ElevatedButton(
            text='Fetch Events',
            on_click=self.fetch_events,
            bgcolor=blue_button_style.button_color,
            color=blue_button_style.highlight_color,
            tooltip='Retrieve recent entries from the events table',
        )

        # Download stderr button
        self.download_content_button = ft.ElevatedButton(
            text='Download Log Details',
            on_click=self.download_content,
            bgcolor=blue_button_style.button_color,
            color=blue_button_style.highlight_color,
            tooltip='Download content that is currently in the Log Details textbox',
        )

        # Log options dropdown
        self.log_options_dropdown = ft.Dropdown(
            label='Log Options',
            border_color=ft.Colors.PRIMARY,
            bgcolor=ft.Colors.SURFACE,
            color=ft.Colors.PRIMARY,
            focused_border_color=ft.Colors.PRIMARY,
            options=[],
            text_style=DetailTextStyle(),
            label_style=DetailTextStyle(),
            on_change=self.handle_log_option_change,
            width=300,
            expand=True,
        )

        self.log_text_field = ft.Markdown(
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            code_theme=theme.color_scheme.on_surface,
            code_style_sheet=ft.MarkdownStyleSheet(code_text_style=CodeTextStyle()),
        )

        self.log_text_field_scrollable = ft.ListView(
            controls=[self.log_text_field],
            height=500,
            spacing=0,
            padding=ft.padding.all(4),
        )

        # Job logs table
        self.job_logs_table = self.create_logs_data_table()

        # Black magic here.
        logs_table_height = (
            (self.data_row_height + 2) * 10
            + self.heading_row_height
            + GuiConfig().heading_font_size  # For the "Job Logs" header
            + 20  # For luck
        )

        # Arrange components with scrollable container
        self.controls = [
            ft.Container(
                content=ft.Column(
                    controls=[
                        # Row for dropdowns and action buttons
                        ft.Container(
                            content=ft.Row(
                                controls=[
                                    ft.Container(
                                        content=self.max_events_dropdown, padding=ft.padding.all(4)
                                    ),
                                    ft.Container(
                                        content=self.fetch_events_button, padding=ft.padding.all(4)
                                    ),
                                    ft.Container(
                                        content=ft.Row(
                                            controls=[
                                                ft.Container(
                                                    content=self.log_options_dropdown,
                                                    padding=ft.padding.all(4),
                                                    expand=True,
                                                    width=300,
                                                ),
                                                self.download_content_button,
                                            ],
                                            alignment=ft.MainAxisAlignment.START,
                                        ),
                                        expand=True,
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.START,
                                spacing=10,  # Add consistent spacing between elements
                            ),
                        ),
                        # Job Logs Table Section
                        ft.Container(
                            content=ft.Column(
                                controls=[
                                    HeadingText('Job Logs', text_align=ft.TextAlign.CENTER),
                                    self.job_logs_table,
                                ],
                                scroll=ft.ScrollMode.ALWAYS,
                            ),
                            # Add padding around the logs table for better separation
                            padding=ft.padding.all(10),
                            border=ft.border.all(1, color=BORDER_TRANSPARENT),
                            border_radius=8,
                            height=logs_table_height,
                            margin=ft.margin.only(bottom=10),
                        ),
                        ft.Container(
                            content=ft.Column(
                                controls=[
                                    HeadingText('Log Details', text_align=ft.TextAlign.CENTER),
                                    self.log_text_field_scrollable,
                                ],
                            ),
                            padding=ft.padding.all(10),  # Padding for visual separation
                            border=ft.border.all(1, ft.Colors.PRIMARY),
                            border_radius=8,
                            expand=True,
                        ),
                    ],
                    scroll=ft.ScrollMode.ALWAYS,  # Enable scrolling for the entire column
                    spacing=15,  # Add consistent spacing between sections
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                expand=True,  # Allow the container to expand and fit the available space
                padding=ft.padding.all(10),
            )
        ]

    # --------------------------------------------------------------------------
    def create_logs_data_table(self) -> ft.DataTable:
        """Create a DataTable using the theme for styling."""

        return ft.DataTable(
            data_text_style=DetailTextStyle(),
            border=ft.border.all(1, ft.Colors.PRIMARY),
            border_radius=5,
            horizontal_lines=ft.BorderSide(1, ft.Colors.PRIMARY),
            # hide internal column borders
            vertical_lines=ft.BorderSide(0, ft.Colors.TRANSPARENT),
            show_checkbox_column=True,
            horizontal_margin=20,
            column_spacing=20,
            data_row_max_height=self.data_row_height,
            data_row_min_height=self.data_row_height,
            heading_row_height=self.heading_row_height,
            columns=[
                ft.DataColumn(
                    ColumnHeadingText('Dispatch Time'),
                    heading_row_alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.DataColumn(
                    ColumnHeadingText('Event Time'),
                    heading_row_alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.DataColumn(
                    ColumnHeadingText('Elapsed'),
                    heading_row_alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.DataColumn(
                    ColumnHeadingText('Status'),
                    heading_row_alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            rows=[],
        )

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def fetch_events(self, e: ft.ControlEvent = None):
        """Fetch events for the selected job and populate the DataTable."""
        current_connection = LavaAwsContext()
        job_id = current_connection.current_job
        max_events = int(self.max_events_dropdown.value)

        if not job_id:
            show_error_popup(self.page, 'No job selected.')
            return

        realm = current_connection.realm
        if not realm:
            show_error_popup(self.page, 'Realm not selected.')
            return

        try:
            db_events_table = {
                realm: current_connection.dynamo_db_res.Table(f'lava.{realm}.events')
            }
            self.events_list = get_events_for_job(
                job_id=job_id,
                events_table=db_events_table[realm],
                limit=max_events,
            )

            # Create log URIs for each run_id
            self.events_log_list = get_event_logs_for_jobs(self.events_list)

            # Update the DataTable with event data
            self.job_logs_table.rows = [
                ft.DataRow(
                    cells=[
                        ft.DataCell(
                            ft.Container(
                                content=DetailText(ev.get('ts_dispatch', '')),
                                data=index,
                                on_click=self.row_click_handler,
                            ),
                        ),
                        ft.DataCell(
                            ft.Container(
                                content=DetailText(ev.get('ts_event', '')),
                                data=index,
                                on_click=self.row_click_handler,
                            )
                        ),
                        ft.DataCell(
                            ft.Container(
                                content=DetailText(
                                    suppress_exception(
                                        format_isodate_difference,
                                        ev.get('ts_dispatch'),
                                        ev.get('ts_event'),
                                        exc_return='',
                                    ),
                                ),
                                data=index,
                                on_click=self.row_click_handler,
                                alignment=ft.alignment.center_right,
                            )
                        ),
                        ft.DataCell(
                            ft.Container(
                                content=DetailText(
                                    ev.get('status', ''),
                                    color=EVENT_STATUS_COLOUR.get(ev.get('status')),
                                ),
                                data=index,
                                on_click=self.row_click_handler,
                            ),
                        ),
                    ],
                )
                for index, ev in enumerate(self.events_list)
            ]
            self.update()
        except Exception as ex:
            show_error_popup(self.page, f'An error occurred while fetching events: {ex}')

    # --------------------------------------------------------------------------
    def row_click_handler(self, e: ft.ControlEvent):
        """Handle row click and populate log options and log details on the Job Logs Tab."""

        row_index = e.control.data
        clicked_event = self.events_list[row_index]
        self.current_run_id = clicked_event['run_id']

        if self.previous_selected_row_index is None:
            # Nothing clicked before so no need to change any colours back
            # Change current to our Selected Colour then change previous to current clicked one
            self.job_logs_table.rows[row_index].color = ft.Colors.ON_SECONDARY
            self.previous_selected_row_index = row_index
        else:
            # This means something was selected prior so needs to change that one colour back to bg
            # then change the new selected one to Selected Colour
            # Then change previous selecte value to current clicked one
            self.job_logs_table.rows[self.previous_selected_row_index].color = (
                ft.ColorScheme.background
            )
            self.job_logs_table.rows[row_index].color = ft.Colors.ON_SECONDARY
            self.previous_selected_row_index = row_index

        # Populate the log dropdown
        self.log_options_dropdown.options = [ft.dropdown.Option('Event Log')]
        if self.current_run_id in self.events_log_list:
            self.log_options_dropdown.options.extend(
                [ft.dropdown.Option(log) for log in self.events_log_list[self.current_run_id]]
            )

        event_spec = json.dumps(
            clicked_event, indent=GuiConfig().json_indent, sort_keys=True, default=json_default
        )
        highlighted_event_spec = '```json\n' + event_spec
        self.log_text_field.value = highlighted_event_spec
        self.log_options_dropdown.value = 'Event Log'  # Reset dropdown selection
        self.update()

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def handle_log_option_change(self, e: ft.ControlEvent):
        """Handle log option change and display selected log."""

        selected_log = self.log_options_dropdown.value
        if not self.current_run_id:
            show_error_popup(self.page, 'No run ID selected.')
            return

        if selected_log == 'Event Log':
            # Display event details
            event = next((ev for ev in self.events_list if ev['run_id'] == self.current_run_id), {})
            event_spec = json.dumps(
                event, indent=GuiConfig().json_indent, sort_keys=True, default=json_default
            )
            highlighted_event_spec = '```json\n' + event_spec
            self.log_text_field.value = highlighted_event_spec

        else:
            s3_uri = self.events_log_list[self.current_run_id].get(selected_log, '')
            if not s3_uri:
                show_error_popup(self.page, f'No log found for {selected_log}.')
                return

            try:
                bucket, s3_key = s3_split(s3_uri)
                s3_client = LavaAwsContext().s3_client
                data = s3_client.get_object(Bucket=bucket, Key=s3_key)
                contents = data['Body'].read().decode('utf-8')
                formatted_contents = '```json\n' + contents
                self.log_text_field.value = formatted_contents

            except Exception as ex:
                show_error_popup(self.page, f'Failed to fetch log: {ex}')

        self.update()

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def download_content(self, e: ft.ControlEvent):
        """Download the contents of the log_text_field as a text file."""
        current_option = self.log_options_dropdown.value
        # Ensure there is content to download
        if self.log_text_field.value is None:
            show_error_popup(self.page, 'No log content. Please select a log.')
            return

        if not self.log_text_field.value.strip():
            show_error_popup(self.page, 'No log content to download. Please select a log.')
            return

        try:
            # Get the default downloads directory
            downloads_folder = Path.home() / 'Downloads'

            # Construct a filename
            file_name = f"{current_option}-{self.current_run_id or 'unknown'}.txt"
            file_path = downloads_folder / file_name

            # Clearing up our content format before downloading.
            clean_content = re.sub(r'^```(?:json)?\n', '', self.log_text_field.value)

            # If file does not exist in downloads, make a new file and write there
            if not file_path.exists():
                with open(file_path, 'w') as file:
                    file.write(clean_content)
                show_success_popup(self.page, f'Log saved to: {file_path}')

            # If file already exists, we need to know if the file is just alone or
            # if there is a numbered version of it already
            else:
                base_name, ext = file_path.stem, file_path.suffix
                max_number = 0

                for existing_file in downloads_folder.glob(f'{base_name}_*{ext}'):
                    if existing_file.stem.startswith(base_name):
                        try:
                            number = existing_file.stem[len(base_name) + 1 :]
                            max_number = max(max_number, int(number))
                        except ValueError:
                            continue
                new_file_name = f'{base_name}_{max_number + 1}{ext}'
                new_file_path = downloads_folder / new_file_name
                with open(new_file_path, 'w') as file:
                    file.write(clean_content)
                show_success_popup(self.page, f'log saved to: {new_file_path}')

        except Exception as ex:
            # Handle unexpected errors
            show_error_popup(self.page, f'Failed to save log: {ex}')

    # --------------------------------------------------------------------------
    def clear(self):
        """Clear all the data from the dropdowns, text fields, and DataTable."""

        # Clear the run ID and event logs
        self.current_run_id = None
        self.events_log_list = {}
        self.events_list = []

        # Reset max events dropdown
        self.max_events_dropdown.value = DEFAULT_EVENTS
        self.log_options_dropdown.options = []
        self.log_options_dropdown.value = None

        self.log_text_field.value = ''

        self.job_logs_table.rows = []

        self.update()


# ------------------------------------------------------------------------------
class JobDispatchContent(ft.Column):
    """Class to create all the contents that will be under job dispatch tab."""

    # --------------------------------------------------------------------------
    def __init__(self, theme: ft.Theme, page):
        """Initialise JobDispatchContent instance."""
        super().__init__()

        self.page = page
        details_font_size = GuiConfig().details_font_size
        heading_font_size = GuiConfig().heading_font_size

        # Current job text field
        self.current_job_textfield = ft.TextField(
            label='Current Job',
            text_style=DetailTextStyle(),
            label_style=DetailTextBoldStyle(),
            read_only=True,
            border_color=ft.Colors.PRIMARY,
            width=500,
        )

        # Job worker text field
        self.job_worker_textfield = ft.TextField(
            label='Worker',
            text_style=DetailTextStyle(),
            label_style=DetailTextBoldStyle(),
            read_only=True,
            border_color=ft.Colors.PRIMARY,
            width=TEXTFIELD_WIDTH_SIZE_WORKER,
        )

        # Dispatch job Run ID text field
        self.dispatch_job_run_id_textfield = ft.TextField(
            label='Dispatch Job Run ID',
            text_style=DetailTextStyle(),
            label_style=DetailTextBoldStyle(),
            read_only=True,
            border_color=ft.Colors.PRIMARY,
            width=250,
        )

        # Dispatch job button
        self.dispatch_job_button = ft.ElevatedButton(
            text='Dispatch',
            bgcolor=blue_button_style.button_color,
            color=blue_button_style.highlight_color,
            on_click=self.handle_dispatch_job_click,
            tooltip='Dispatch the job with the displayed globals and parameters',
        )

        # Fetch the log detail of the job that we just dispatched
        self.fetch_recent_job_log_button = ft.ElevatedButton(
            text='Fetch Log',
            bgcolor=blue_button_style.button_color,
            color=blue_button_style.highlight_color,
            on_click=self.handle_fetch_log_details_click,
            tooltip='Fetch job log from the events table.',
        )

        # Status Icon to display the status of the Job Run
        self.status_icon = ft.TextField(
            value='Status',
            color=ft.Colors.PRIMARY,
            text_style=DetailTextStyle(),
            label_style=DetailTextBoldStyle(),
            width=120,
            text_align=ft.TextAlign.CENTER,
            border_color=ft.Colors.PRIMARY,
            expand=True,
            expand_loose=True,
            read_only=True,
        )

        # Globals table
        self.args_table = ft.DataTable(
            data_row_min_height=details_font_size + 6,
            heading_row_height=heading_font_size + 6,
            border=ft.border.all(1, ft.Colors.PRIMARY),
            border_radius=5,
            vertical_lines=ft.BorderSide(1, ft.Colors.PRIMARY),
            horizontal_lines=ft.BorderSide(1, ft.Colors.PRIMARY),
            columns=[
                ft.DataColumn(ft.Container(ColumnHeadingText('Global Key'), width=100)),
                ft.DataColumn(ft.Container(ColumnHeadingText('Global Value'), width=400)),
            ],
            rows=[],
        )

        # Add row button for globals table
        self.add_globals_row_button = ft.IconButton(
            icon=ft.Icons.ADD,
            tooltip='Add Global',
            on_click=self.add_globals_row,
            bgcolor=theme.color_scheme.secondary,
            icon_color=ft.Colors.PRIMARY,
            width=30,  # Smaller width
            height=30,  # Smaller height
            icon_size=10,  # Smaller icon size
        )

        self.delete_globals_row_button = ft.IconButton(
            icon=ft.Icons.REMOVE,
            tooltip='Delete Latest Global',
            on_click=self.delete_globals_row,
            bgcolor=theme.color_scheme.error,
            icon_color=ft.Colors.PRIMARY,
            width=30,  # Smaller width
            height=30,  # Smaller height
            icon_size=10,  # Smaller icon size
        )

        self.delete_params_row_button = ft.IconButton(
            icon=ft.Icons.REMOVE,
            tooltip='Delete Latest Parameter',
            on_click=self.delete_params_row,
            bgcolor=theme.color_scheme.error,
            icon_color=ft.Colors.PRIMARY,
            width=30,  # Smaller width
            height=30,  # Smaller height
            icon_size=10,  # Smaller icon size
        )

        # Parameters table
        self.params_table = ft.DataTable(
            data_row_min_height=details_font_size + 6,
            heading_row_height=heading_font_size + 6,
            border=ft.border.all(1, ft.Colors.PRIMARY),
            border_radius=5,
            vertical_lines=ft.BorderSide(1, ft.Colors.PRIMARY),
            horizontal_lines=ft.BorderSide(1, ft.Colors.PRIMARY),
            columns=[
                ft.DataColumn(ft.Container(ColumnHeadingText('Parameter Key'), width=100)),
                ft.DataColumn(ft.Container(ColumnHeadingText('Parameter Value'), width=400)),
            ],
            rows=[],
        )

        # Add row button for parameters table
        self.add_params_row_button = ft.IconButton(
            icon=ft.Icons.ADD,
            tooltip='Add Parameter',
            on_click=self.add_params_row,
            bgcolor=theme.color_scheme.secondary,
            icon_color=ft.Colors.PRIMARY,
            width=30,  # Smaller width
            height=30,  # Smaller height
            icon_size=10,  # Smaller icon size
        )

        self.dispatch_job_log_details_markdown = ft.Markdown(
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            code_theme=theme.color_scheme.on_surface,
            code_style_sheet=ft.MarkdownStyleSheet(
                code_text_style=ft.TextStyle(size=details_font_size)
            ),
        )

        self.dispatch_job_details_markdown_scrollable = ft.ListView(
            controls=[self.dispatch_job_log_details_markdown],
            height=500,
            expand=False,
            spacing=0,
            padding=ft.padding.all(4),
        )

        self.latest_dispatch_time = datetime.fromtimestamp(0)
        self.latest_fetch_time = datetime.fromtimestamp(0)

        self.original_globals_data = None
        self.original_params_data = None
        # Assemble controls
        expander_icon_name = GuiConfig().expander_icon
        if not hasattr(ft.Icons, expander_icon_name):
            expander_icon_name = ft.Icons.KEYBOARD_ARROW_DOWN
        self.globals_expansion_icon = ft.Icon(name=expander_icon_name, color=ft.Colors.PRIMARY)
        self.parameters_expansion_icon = ft.Icon(name=expander_icon_name, color=ft.Colors.PRIMARY)
        self.controls = [
            ft.Container(
                content=ft.Column(
                    controls=[
                        # Current job row (job details and worker)
                        ft.Container(
                            content=ft.Row(
                                controls=[
                                    self.current_job_textfield,
                                    self.job_worker_textfield,
                                ],
                                spacing=10,
                                alignment=ft.MainAxisAlignment.START,
                            ),
                            margin=ft.margin.only(bottom=20),  # Add some margin for separation
                        ),
                        # Globals section with table and buttons
                        ft.ExpansionTile(
                            title=HeadingText('Globals'),
                            affinity=ft.TileAffinity.LEADING,
                            leading=self.globals_expansion_icon,
                            dense=True,
                            bgcolor=ft.Colors.SURFACE,
                            icon_color=ft.Colors.PRIMARY,
                            collapsed_icon_color=ft.Colors.PRIMARY,
                            controls=[
                                ft.Container(
                                    padding=ft.Padding(0, 0, 0, 0),
                                    content=ft.Column(
                                        controls=[
                                            ft.Row(
                                                controls=[
                                                    self.add_globals_row_button,
                                                    self.delete_globals_row_button,
                                                ],
                                                spacing=5,  # Smaller spacing for buttons
                                                alignment=ft.MainAxisAlignment.START,
                                            ),
                                            self.args_table,
                                        ],
                                    ),
                                    margin=ft.margin.only(bottom=20),
                                ),
                            ],
                        ),
                        # Parameters section with table and buttons
                        ft.ExpansionTile(
                            title=HeadingText('Parameters'),
                            affinity=ft.TileAffinity.LEADING,
                            leading=self.parameters_expansion_icon,
                            dense=True,
                            bgcolor=ft.Colors.SURFACE,
                            icon_color=ft.Colors.PRIMARY,
                            collapsed_icon_color=ft.Colors.PRIMARY,
                            controls=[
                                ft.Container(
                                    padding=ft.Padding(0, 0, 0, 0),
                                    content=ft.Column(
                                        controls=[
                                            ft.Row(
                                                controls=[
                                                    self.add_params_row_button,
                                                    self.delete_params_row_button,
                                                ],
                                                spacing=5,  # Smaller spacing for buttons
                                                alignment=ft.MainAxisAlignment.START,
                                            ),
                                            self.params_table,
                                        ],
                                    ),
                                    margin=ft.margin.only(bottom=20),
                                ),
                            ],
                        ),
                        ft.Container(
                            content=ft.Column(
                                controls=[
                                    ft.Container(
                                        content=ft.Row(
                                            controls=[
                                                self.dispatch_job_button,
                                                self.dispatch_job_run_id_textfield,
                                                self.fetch_recent_job_log_button,
                                                self.status_icon,
                                            ]
                                        ),
                                        alignment=ft.alignment.center_left,  # Center the button
                                        margin=ft.margin.only(top=20),
                                    ),
                                    ft.Container(
                                        content=ft.Column(
                                            controls=[
                                                HeadingText("Dispatched Job's Log Details"),
                                                self.dispatch_job_details_markdown_scrollable,
                                            ],
                                        ),
                                        padding=ft.padding.all(10),  # Padding for visual separation
                                        border=ft.border.all(1, ft.Colors.PRIMARY),
                                        border_radius=8,
                                        expand=True,
                                    ),
                                ],
                                scroll=ft.ScrollMode.ALWAYS,
                                spacing=15,
                                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            ),
                            expand=True,
                            margin=ft.margin.only(bottom=20),
                        ),
                    ],
                    scroll=ft.ScrollMode.ALWAYS,
                    spacing=15,  # Consistent spacing between sections
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                expand=True,
                padding=ft.padding.all(10),
            )
        ]

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def delete_globals_row(self, e: ft.ControlEvent) -> None:
        """Delete the latest row from the globals table."""
        if self.args_table.rows:
            self.args_table.rows.pop()  # Remove the latest row
            self.update()

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def delete_params_row(self, e: ft.ControlEvent) -> None:
        """Delete the latest row from the parameters table."""
        if self.params_table.rows:
            self.params_table.rows.pop()
            self.update()

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def handle_dispatch_job_click(self, e: ft.ControlEvent) -> None:
        """Handle dispatching the job with the provided parameters and globals."""

        current_connection = LavaAwsContext()
        try:
            # Get current job ID and worker
            job_id = self.current_job_textfield.value.strip()
            worker = self.job_worker_textfield.value.strip()

            if not job_id:
                show_error_popup(self.page, 'No job selected.')
                return
            if not worker:
                show_error_popup(self.page, 'No worker specified.')
                return

            # Checking if we are click-spamming
            last_dispatch_time = self.latest_dispatch_time
            now = datetime.now()
            if now - last_dispatch_time < DISPATCH_BLACKOUT:
                show_error_popup(
                    self.page,
                    (
                        'Dispatches are rate limited. You can resume your frenzy in'
                        f' {int((last_dispatch_time + DISPATCH_BLACKOUT - now).total_seconds())}'
                        ' seconds'
                    ),
                )
                return

            # Extract global variables
            globals_ = {}
            if self.args_table.rows:
                globals_ = {
                    row.cells[0].content.value: row.cells[1].content.value
                    for row in self.args_table.rows
                }

            if globals_:
                for key in globals_:
                    value = globals_[key]
                    try:
                        new_value = json.loads(value)
                        globals_[key] = new_value
                    except json.decoder.JSONDecodeError as e:  # noqa
                        if value.lower() == 'true':
                            value = True
                        elif value.lower() == 'false':
                            value = False
                        elif value.lower() == 'none':
                            value = None
                        elif value.strip().lstrip('-').replace('.', '', 1).isdigit():
                            value = decimal.Decimal(value)
                        globals_[key] = value

            # Extract parameters
            params = {}
            if self.params_table.rows:
                params = {
                    row.cells[0].content.value: row.cells[1].content.value
                    for row in self.params_table.rows
                }

            if params:
                for key in params:
                    value = params[key]
                    try:
                        new_value = json.loads(value)
                        params[key] = new_value
                    except json.decoder.JSONDecodeError as e:  # noqa
                        if value.lower() == 'true':
                            value = True
                        elif value.lower() == 'false':
                            value = False
                        elif value.lower() == 'none':
                            value = None
                        elif value.strip().lstrip('-').replace('.', '', 1).isdigit():
                            value = decimal.Decimal(value)
                        params[key] = value

            run_id = dispatch(
                realm=current_connection.realm,
                job_id=job_id,
                worker=worker,
                params=params,
                globals_=globals_,
                aws_session=current_connection.aws_session,
            )
            self.dispatch_job_run_id_textfield.value = run_id
            self.dispatch_job_log_details_markdown.value = ''
            self.latest_dispatch_time = now
            self.status_icon.value = 'Status'
            self.status_icon.color = ft.Colors.PRIMARY

            self.update()

        except Exception as ex:
            show_error_popup(self.page, f'Error dispatching job: {ex}')

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def handle_fetch_log_details_click(self, e: ft.ControlEvent):
        """Handle fetching the logs details from the logs table."""

        current_connection = LavaAwsContext()
        realm = current_connection.realm
        job_id = self.current_job_textfield.value.strip()
        run_id = self.dispatch_job_run_id_textfield.value.strip()

        # Checking if a job is selected or not
        if job_id == '' or job_id is None:
            show_error_popup(self.page, 'No job selected.')
            return
        # Checking if there is a run id or not
        if run_id == '' or run_id is None:
            show_error_popup(self.page, 'No run ID.')
            return

        now = datetime.now()
        # This checks if we are spam-clicking.
        if now - self.latest_fetch_time < EVENT_BLACKOUT:
            show_error_popup(
                self.page,
                (
                    'Event log scans are rate limited. You can resume your frenzy in'
                    f' {int((self.latest_fetch_time + EVENT_BLACKOUT - now).total_seconds())}'
                    ' seconds'
                ),
            )
            return

        db_events_table = {realm: current_connection.dynamo_db_res.Table(f'lava.{realm}.events')}

        events_list = get_events_for_job(
            job_id=job_id, events_table=db_events_table[realm], limit=10
        )
        # Use the events_list to find the job that has run_id here, shows an error
        # if job is not found that it may not exist or to check in job logs for more details
        for event in events_list:
            if event['run_id'] == run_id:  # which means this run exists
                current_status = event['status']
                debug(current_status)
                event_spec = json.dumps(
                    event, indent=GuiConfig().json_indent, sort_keys=True, default=json_default
                )
                highlighted_event_spec = '```json\n' + event_spec
                self.dispatch_job_log_details_markdown.value = highlighted_event_spec
                self.check_status(current_status)
                self.update()
                continue  # leave loop early if we found the run we seek

        self.latest_fetch_time = now

    # --------------------------------------------------------------------------
    def check_status(self, current_status: str):
        """Check the status of the job run to colourise the status for display."""
        self.status_icon.color = EVENT_STATUS_COLOUR.get(current_status, 'black')
        self.status_icon.value = current_status

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def add_globals_row(self, e: ft.ControlEvent):
        """Add a new editable row to the globals table."""

        details_font_size = GuiConfig().details_font_size
        new_row = ft.DataRow(
            cells=[
                ft.DataCell(
                    ft.TextField(
                        border_color=BORDER_TRANSPARENT,
                        height=18,
                        text_size=details_font_size,
                        color=ft.Colors.PRIMARY,
                        multiline=True,
                        content_padding=DATA_CELL_INNER_TEXTBOX_PADDING,
                        text_align=ft.TextAlign.LEFT,
                    ),
                ),
                ft.DataCell(
                    ft.TextField(
                        width=300,
                        height=18,
                        content_padding=DATA_CELL_INNER_TEXTBOX_PADDING,
                        border_color=BORDER_TRANSPARENT,
                        text_size=details_font_size,
                        text_align=ft.TextAlign.LEFT,
                        color=ft.Colors.PRIMARY,
                        multiline=True,
                    )
                ),
            ],
        )
        self.args_table.rows.append(new_row)
        self.update()

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def add_params_row(self, e: ft.ControlEvent):
        """Add a new editable row to the parameters table."""
        details_font_size = GuiConfig().details_font_size
        new_row = ft.DataRow(
            cells=[
                ft.DataCell(
                    ft.TextField(
                        border_color=BORDER_TRANSPARENT,
                        height=18,
                        text_size=details_font_size,
                        color=ft.Colors.PRIMARY,
                        multiline=True,
                        content_padding=DATA_CELL_INNER_TEXTBOX_PADDING,
                        text_align=ft.TextAlign.LEFT,
                    )
                ),
                ft.DataCell(
                    ft.TextField(
                        border_color=BORDER_TRANSPARENT,
                        height=18,
                        text_size=details_font_size,
                        color=ft.Colors.PRIMARY,
                        multiline=True,
                        content_padding=DATA_CELL_INNER_TEXTBOX_PADDING,
                        text_align=ft.TextAlign.LEFT,
                    )
                ),
            ]
        )
        self.params_table.rows.append(new_row)
        self.update()

    # --------------------------------------------------------------------------
    @staticmethod
    def process_extra_params(extra_params: str) -> dict[str, Any]:
        """Process extra parameters from the text field."""
        params = {}
        for line in extra_params.split('\n'):
            if '==' in line:
                key, value = line.split('==', 1)
                params[key.strip()] = value.strip()
        return params

    # --------------------------------------------------------------------------
    def populate_tables(self, globals_data: dict[str, Any], params_data: dict[str, Any]):
        """Populate the DataTables with global and parameter data."""

        self.globals_expansion_icon.color = (
            ft.Colors.SECONDARY if globals_data else ft.Colors.PRIMARY
        )
        self.parameters_expansion_icon.color = (
            ft.Colors.SECONDARY if params_data else ft.Colors.PRIMARY
        )

        self.args_table.rows = [
            ft.DataRow(
                cells=[
                    ft.DataCell(
                        ft.TextField(
                            value=str(key),
                            text_style=DetailTextStyle(),
                            multiline=True,
                            max_lines=2,
                            border_color=BORDER_TRANSPARENT,
                            content_padding=DATA_CELL_INNER_TEXTBOX_PADDING,
                        )
                    ),
                    ft.DataCell(
                        ft.TextField(
                            value=json.dumps(value) if type(value) in (list, dict) else str(value),
                            text_style=DetailTextStyle(),
                            multiline=True,
                            max_lines=2,
                            border_color=BORDER_TRANSPARENT,
                            content_padding=DATA_CELL_INNER_TEXTBOX_PADDING,
                        )
                    ),
                ]
            )
            for key, value in globals_data.items()
        ]

        self.params_table.rows = [
            ft.DataRow(
                cells=[
                    ft.DataCell(
                        ft.TextField(
                            value=str(key),
                            text_style=DetailTextStyle(),
                            multiline=True,
                            max_lines=2,  # Set maximum visible lines before scrolling
                            border_color=BORDER_TRANSPARENT,
                            content_padding=DATA_CELL_INNER_TEXTBOX_PADDING,
                        )
                    ),
                    ft.DataCell(
                        ft.TextField(
                            value=json.dumps(value) if type(value) in (list, dict) else str(value),
                            text_style=DetailTextStyle(),
                            multiline=True,
                            max_lines=2,  # Set maximum visible lines before scrolling
                            border_color=BORDER_TRANSPARENT,
                            content_padding=DATA_CELL_INNER_TEXTBOX_PADDING,
                        )
                    ),
                ]
            )
            for key, value in params_data.items()
        ]
        self.update()


# ------------------------------------------------------------------------------
class JobsRunning(ft.Column):
    """Class for Jobs that are currently running."""

    # --------------------------------------------------------------------------
    def __init__(self, lava_aws_context: LavaAwsContext, page):
        """
        Initialize a JobsRunning instance.

        :param lava_aws_context: The AWS connection instance to interact with a
                                 lava realm.
        :param page:            The Flet page instance.

        """

        super().__init__()
        self.lava_aws_context = lava_aws_context
        self.page = page
        self.latest_scan_time = datetime.fromtimestamp(0)

        # Button to fetch running jobs
        self.fetch_jobs_button = ft.ElevatedButton(
            text='Fetch Running Jobs',
            bgcolor=blue_button_style.button_color,
            color=blue_button_style.highlight_color,
            on_click=self.display_running_jobs,
        )

        self.jobs_table = ft.DataTable(
            data_text_style=DetailTextStyle(),
            border=ft.border.all(1, ft.Colors.PRIMARY),
            border_radius=5,
            vertical_lines=ft.BorderSide(1, ft.Colors.PRIMARY),
            show_checkbox_column=True,
            data_row_max_height=18,
            data_row_min_height=18,
            heading_row_height=18,
            columns=[
                ft.DataColumn(
                    ColumnHeadingText('Job ID'),
                    heading_row_alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.DataColumn(
                    ColumnHeadingText('Run ID'),
                    heading_row_alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            rows=[],
        )
        self.scan_in_progress = False

        button_container = ft.Container(
            content=ft.Row(
                controls=[self.fetch_jobs_button], alignment=ft.MainAxisAlignment.CENTER
            ),
            padding=ft.padding.all(10),
        )

        # Layout
        self.controls = [
            button_container,
            ft.Container(
                alignment=ft.alignment.top_center,
                bgcolor=ft.Colors.SURFACE,
                content=HeadingText(
                    'This is an expensive operation. Use it sparingly.', color='red'
                ),
            ),
            ft.Container(
                alignment=ft.alignment.top_center,
                content=ft.Column(
                    controls=[HeadingText('Currently running jobs'), self.jobs_table],
                    scroll=ft.ScrollMode.ALWAYS,
                ),
                # Add padding around the logs table for better separation
                padding=ft.padding.all(10),
                border=ft.border.all(1, color=BORDER_TRANSPARENT),
                border_radius=8,
                height=248,
                margin=ft.margin.only(bottom=10),  # size of split between this and container below
            ),
        ]

    # --------------------------------------------------------------------------
    # noinspection PyUnusedLocal
    def display_running_jobs(self, e: ft.ControlEvent):
        """Fetch and display currently running jobs."""

        if self.scan_in_progress:
            show_error_popup(self.page, 'A scan is already in progress. Chill.')
            return

        now = datetime.now()
        if now - self.latest_scan_time < RUNNING_JOBS_BLACKOUT:
            show_error_popup(
                self.page,
                (
                    'This operation is expensive. Really expensive.'
                    ' It involves a full table scan on the events table.'
                    '\n\n'
                    ' So back off.'
                    '\n\n'
                    f' {int((self.latest_scan_time + RUNNING_JOBS_BLACKOUT - now).total_seconds())}'
                    ' seconds to go ... and then take it easy. Seriously.'
                ),
            )
            return

        self.scan_in_progress = True

        try:
            self.controls[2].content.controls[0].value = 'Finding Currently Running Jobs'
            self.jobs_table.rows.clear()
            self.update()

            running_jobs = self.get_currently_running_jobs()

            if not running_jobs:
                self.controls[2].content.controls[0].value = 'No running jobs found'
                self.jobs_table.rows.clear()
                self.update()
            else:
                # Populate the ListView with running jobs
                self.controls[2].content.controls[0].value = 'Currently running jobs'
                self.update()
                self.jobs_table.rows = [
                    ft.DataRow(
                        cells=[
                            ft.DataCell(DetailText(item[0], selectable=True)),
                            ft.DataCell(DetailText(item[1], selectable=True)),
                        ],
                    )
                    for item in running_jobs
                ]
                self.update()

            self.scan_in_progress = False
            self.jobs_table.update()
        except Exception as e:  # noqa
            show_error_popup(self.page, f'There was an error fetching running jobs: {e}')
            self.scan_in_progress = False
        finally:
            self.latest_scan_time = now

    # --------------------------------------------------------------------------
    def get_currently_running_jobs(self) -> list[tuple[str, str]]:
        """
        Identify jobs in a running state that started within the last 12 hours.

        Returns:
        - List of tuples (job ID, run ID) for jobs currently running.

        """
        end_time = datetime.now().astimezone()
        start_time = end_time - timedelta(hours=RUNNING_JOB_LOOKBACK_HOURS)

        # AWS connection and DynamoDB setup
        realm = self.lava_aws_context.realm
        dynamo_db = self.lava_aws_context.dynamo_db_res
        table_name = f'lava.{realm}.events'
        table = dynamo_db.Table(table_name)

        # ISO format times for query
        start_iso = start_time.isoformat()
        end_iso = end_time.isoformat()

        currently_running_jobs = []

        try:
            # Scan for items where the status is "running" within the last 12 hours
            items = []
            response = table.scan(
                ProjectionExpression='job_id , run_id , ts_dispatch, #status',
                FilterExpression=(
                    '#status = :running_status AND ts_dispatch BETWEEN :start_ts AND :end_ts'
                ),
                ExpressionAttributeNames={'#status': 'status'},  # Alias for reserved keyword
                ExpressionAttributeValues={
                    ':running_status': 'running',
                    ':start_ts': start_iso,
                    ':end_ts': end_iso,
                },
                TotalSegments=4,
                Segment=3,
            )
            items += response.get('Items', [])
            while 'LastEvaluatedKey' in response:
                response = table.scan(
                    ProjectionExpression='job_id , run_id , ts_dispatch, #status',
                    FilterExpression=(
                        '#status = :running_status AND ts_dispatch BETWEEN :start_ts AND :end_ts'
                    ),
                    ExpressionAttributeNames={'#status': 'status'},  # Alias for reserved keyword
                    ExpressionAttributeValues={
                        ':running_status': 'running',
                        ':start_ts': start_iso,
                        ':end_ts': end_iso,
                    },
                    ExclusiveStartKey=response['LastEvaluatedKey'],
                    TotalSegments=4,
                    Segment=3,
                )
                items += response.get('Items', [])

            for item in items:
                job_id = item.get('job_id')
                run_id = item.get('run_id')
                currently_running_jobs.append((job_id, run_id))
            return currently_running_jobs
        except Exception as e:
            show_error_popup(self.page, f'There was an error fetching running jobs: {e}')
            return []


# ------------------------------------------------------------------------------
def show_error_popup(page: ft.Page, message: str, title: str = 'Error'):
    """Display an error popup with the given message."""
    dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text(title),
        content=ft.Text(message, selectable=True),
        actions=[
            ft.Button(
                text='OK',
                color=blue_button_style.highlight_color,
                bgcolor=blue_button_style.button_color,
                on_click=lambda e: page.close(dlg),
            )
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.open(dlg)


# ------------------------------------------------------------------------------
def show_success_popup(page: ft.Page, message: str, title: str = 'Success'):
    """Display a success popup with the given message."""
    dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text(title),
        content=ft.Text(message, selectable=True),
        actions=[
            ft.Button(
                text='OK',
                color=blue_button_style.highlight_color,
                bgcolor=blue_button_style.button_color,
                on_click=lambda e: page.close(dlg),
            )
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.open(dlg)


# ------------------------------------------------------------------------------
def get_events_for_job(
    job_id: str, events_table, limit: int = DEFAULT_EVENTS, status: str | None = None
) -> list[dict[str, Any]]:
    """
    Retrieve event data for the specified job_id.

    :param job_id:          Job identifier
    :param events_table:    DynamoDB events table .
    :param limit:           Maximum number of events to fetch.
    :param status:          If not None, only get events with the given status.

    :return:                A list of events.
    """

    query_args = {
        'IndexName': 'job_id-tu_event-index',
        'KeyConditionExpression': '#job_id = :job_id',
        'ExpressionAttributeNames': {'#job_id': 'job_id'},
        'ExpressionAttributeValues': {':job_id': job_id},
        'ScanIndexForward': False,
    }

    if limit and limit > 0:
        query_args['Limit'] = min(limit, MAX_EVENTS)

    if status:
        query_args['FilterExpression'] = '#status = :status'
        query_args['ExpressionAttributeNames']['#status'] = 'status'
        # noinspection PyTypeChecker
        query_args['ExpressionAttributeValues'][':status'] = {'S': status}

    try:
        return events_table.query(**query_args)['Items']
    except KeyError:
        raise Exception(f'Event information for {job_id} not found')


# ------------------------------------------------------------------------------
def get_event_logs_for_jobs(event_list: list[dict]) -> dict[str, Any]:
    """Search the event body for the mention of stdout or stderr files and return the S3 URIs."""

    log_files = {}
    pattern_logs = r"(?<=('stderr': '|'stdout': '|'output': '))s3://[A-Za-z0-9_./-]+"
    pattern_out = r's3://[A-Za-z0-9_.:/-]+\.out'

    for event in event_list:
        log_files_for_event = {}

        # Find matches for both patterns
        for pattern in [pattern_logs, pattern_out]:
            for log in re.finditer(pattern, repr(event)):
                log_uri = log.group(0)
                log_files_for_event[log_uri.split('/')[-1]] = log_uri

        log_files[event['run_id']] = log_files_for_event

    return log_files


# ------------------------------------------------------------------------------
def get_job_globals(job_spec_s: str) -> dict:
    """Get job globals from a JSON formatted job spec."""

    job_spec = json.loads(job_spec_s)
    return job_spec.get('globals', {})


# ------------------------------------------------------------------------------
def get_job_params(job_spec_s: str) -> dict:
    """Get job parameters from a JSON formatted job spec."""

    job_spec = json.loads(job_spec_s)
    return job_spec.get('parameters', {})


# ------------------------------------------------------------------------------
def can_access_realm(realm: str, dynamo_db: BaseClient) -> bool:
    """Check if the user has access to the given realm."""

    debug('Checking access to realm', realm, end=' ')
    # noinspection PyBroadException
    try:
        dynamo_db.describe_table(TableName=f'lava.{realm}.jobs')
        debug('OK')
        return True
    except Exception:
        debug('nope')
        return False


# ------------------------------------------------------------------------------
def check_aws_account_access(profile_name: str):
    """
    Check if the specified AWS profile is able to access the account.

    :raise Exception: If AWS profile cannot be used to access the account or the
                    realms table in the account.
    """

    current_connection = LavaAwsContext()
    aws_session = current_connection.aws_session or boto3.Session(profile_name=profile_name)
    try:
        aws_session.client('sts').get_caller_identity()
    except ClientError as e:
        try:
            raise Exception(e.response['Error']['Message'])
        except KeyError:
            raise e

    # Make sure we can get to the realms table
    dynamodb = current_connection.dynamo_db_client or aws_session.client('dynamodb')
    try:
        dynamodb.describe_table(TableName='lava.realms')
    except Exception as e:
        debug(e)
        raise Exception(
            'The specified AWS account either doesn\'t have lava installed'
            ' or you can\'t access it.'
        )


# ------------------------------------------------------------------------------
@cache
def accessible_realms(profile: str) -> list:
    """Get realms that are accessible for the given profile."""
    debug(f'Scanning accessible realms for profile: {profile}')

    current_connection = LavaAwsContext()
    aws_session = current_connection.aws_session or boto3.Session(profile_name=profile)
    dynamo_db = current_connection.dynamo_db_client or aws_session.client('dynamodb')
    try:
        return [
            realm
            for realm in scan_realms(aws_session=aws_session)
            if can_access_realm(realm, dynamo_db)
        ]
    except Exception as e:
        debug(f'Error scanning realms: {e}')
        return []


@ttl_cache(maxsize=3, ttl=15)
def fetch_job_list(
    realm: str,
    attributes: Iterable[str] | None = None,
    aws_session: boto3.Session = None,
):
    """Handle job fetch is redone too frequently."""
    return scan_jobs(realm=realm, attributes=attributes, aws_session=aws_session)


# ------------------------------------------------------------------------------
def create_dropdown(
    label,
    on_change=None,
):
    """Create a default dropdown."""
    return ft.Dropdown(
        label=label,
        label_style=DetailTextStyle(),
        options=[],
        width=150,
        on_change=on_change,
        text_style=DetailTextStyle(),
        border_color=ft.Colors.PRIMARY,
        bgcolor=ft.Colors.SURFACE,
        autofocus=True,
    )


# ------------------------------------------------------------------------------
def create_tab_content(tab_title, content):
    """Create a default tab content."""
    return ft.Tab(
        text=tab_title,
        content=ft.Container(
            content=ft.Card(
                color=ft.Colors.SURFACE,
                height=800,
                elevation=4,
                content=ft.Column(controls=content, scroll=ft.ScrollMode.ALWAYS),
            ),
            padding=ft.padding.all(10),
        ),
    )


# ------------------------------------------------------------------------------
class SearchBar(ft.TextField):
    """Search bar field class."""

    def __init__(self, label: str, lava_jobs: LavaJobsPanel, *args, **kwargs):
        """
        Create a search bar that filters the job list in `lava_jobs` based on a query.

        :param label:      Label for the search bar.
        :param lava_jobs:  The LavaJobs instance to update.
        """

        # Flet falls in a heap if it has to update a control too quickly (corrupt
        # internal state and assertion errors!!). On a realm with a lot of jobs,
        # the incremental search function can force too many large updates on the
        # job list panel and flet shits itself. To fix this we introduce a little
        # delay to try to capture multiple keystrokes rather than update on each one.

        super().__init__(
            *args,
            label=label,
            label_style=DetailTextStyle(),
            text_size=GuiConfig().details_font_size,
            hint_text='Search jobs...',
            hint_style=DetailTextStyle(),
            on_change=self.handle_search,  # Trigger filtering when the user types or backspaces
            expand=True,
            suffix_icon=ft.Icons.SEARCH,
            border_color=ft.Colors.PRIMARY,
            color=ft.Colors.PRIMARY,
            **kwargs,
        )

        self.search_timer: threading.Timer | None = None
        self.lava_jobs = lava_jobs

    # --------------------------------------------------------------------------
    def handle_search(self, e: ft.ControlEvent):
        """Catch the keystroke in the search box and initiate a delayed update."""

        # Cancel previous timer
        if self.search_timer:
            self.search_timer.cancel()

        # Set new timer
        self.search_timer = threading.Timer(0.3, self.perform_search, args=[e.control.value])
        self.search_timer.start()

    # --------------------------------------------------------------------------
    def perform_search(self, query: str):
        """Perform the actual search after a delay pending more keystrokes."""
        try:
            search_query = query.lower().strip()
            if search_query is not None:
                connecton_context = LavaAwsContext()
                profile = connecton_context.profile
                realm = connecton_context.realm
                realm_cache = connecton_context.profile_cache[profile].realm_cache[realm]
                realm_cache.last_search = search_query

                filtered_jobs = [
                    job for job in self.lava_jobs.original_job_list if search_query in job.lower()
                ]
            else:
                filtered_jobs = self.lava_jobs.original_job_list

            self.lava_jobs.update_job_list(filtered_jobs)
        except Exception as ex:
            debug(f'Searching query failed: {type(ex)}: {ex}')
            if DEBUG:
                traceback.print_exc()


# ------------------------------------------------------------------------------
# TODO: This should be clearing other elements in the GUI when profile changes
def handle_profile_change(
    selected_profile: str,
    page: ft.Page,
    realm_dropdown,
    lava_jobs_panel: LavaJobsPanel,
    job_details_markdown: ft.Markdown,
    refresh_page: bool = False,
):
    """
    Handle profile change event, update the LavaAwsContext instance.

    Also refresh the realm dropdown based on the selected profile.
    """

    lava_jobs_panel.update_job_list([])
    realm_dropdown.options = []
    realm_dropdown.update()
    job_details_markdown.value = ''
    job_details_markdown.update()
    os.environ['AWS_PROFILE'] = selected_profile

    current_connection = LavaAwsContext()

    try:
        current_connection.set_profile(selected_profile)
    except Exception as e:
        show_error_popup(
            page, title='Warning', message=f'Access problem for profile: {selected_profile} - {e}'
        )
        return

    accessible_realms_list = sorted(accessible_realms(selected_profile))
    realm_dropdown.text_style = DetailTextStyle()

    realm_options = [ft.dropdown.Option(text=realm) for realm in accessible_realms_list]
    realm_dropdown.options = realm_options
    # Setting the aws_profile in config as the profile that was selected
    # so that we will start with last profile when we reenter application.
    gui_config = GuiConfig()
    gui_config.set('aws_profile', selected_profile)

    profile_cache = current_connection.profile_cache[selected_profile]

    debug('Found previously selected realm:', profile_cache.last_realm)
    event = ft.ControlEvent('', '', '', realm_dropdown, page)
    if profile_cache.last_realm is not None:
        realm_dropdown.value = profile_cache.last_realm
    else:
        realm_dropdown.value = accessible_realms_list[0] if realm_options else None

    if realm_dropdown.value is not None:
        handle_realm_change(event, page, lava_jobs_panel, KEY_PAGE_REFERENCES['search_bar'])

    # TODO: This is not working - seems to be a bug with Dropdown components
    realm_dropdown.update()
    if refresh_page:
        page.update()
    debug(f'Profile changed to: {selected_profile}')


# ------------------------------------------------------------------------------
def handle_realm_change(
    e: ft.ControlEvent,
    page: ft.Page,
    lava_jobs_panel: LavaJobsPanel,
    search_bar: ft.Control,
    refresh_page: bool = False,
):
    """Handle realm change event and update the job list."""

    current_connection = LavaAwsContext()  # Access the singleton instance
    realm = e.control.value
    if realm is None:
        return
    current_connection.realm = realm

    profile = current_connection.profile
    profile_cache = current_connection.profile_cache[profile]
    if realm not in profile_cache.realm_cache:
        profile_cache.realm_cache[realm] = RealmCache()
    realm_cache = profile_cache.realm_cache[realm]

    profile_cache.last_realm = realm

    lava_jobs_panel.update_job_list(['Loading ...'])
    try:
        # Scan jobs for the selected realm
        job_list = sorted(fetch_job_list(realm, aws_session=current_connection.aws_session))
        current_connection.job_list = job_list
        lava_jobs_panel.set_original_job_list(job_list)  # Update the unfiltered job list

        # If realm cache has a last search, apply it
        if realm_cache.last_search is not None:
            search_bar.value = realm_cache.last_search

        # Checking if there is a search query in search bar.
        search_query = search_bar.value.lower().strip()
        if search_query:
            filtered_jobs = [
                job for job in lava_jobs_panel.original_job_list if search_query in job.lower()
            ]
        else:
            filtered_jobs = lava_jobs_panel.original_job_list
        lava_jobs_panel.update_job_list(filtered_jobs)
    except Exception as ex:
        # Handle exceptions and display an error popup
        show_error_popup(page, f'Could not scan jobs: {ex}')

    else:
        # If realm cache has a previously selected job, apply it
        if realm_cache.last_selected_job_id is not None:
            debug('Found previously selected job:', realm_cache.last_selected_job_id)
            lava_jobs_panel.on_select(None, realm_cache.last_selected_job_id)
            job_list_view_click(
                realm_cache.last_selected_job_id,
                page,
                KEY_PAGE_REFERENCES['job_details_markdown'],
                KEY_PAGE_REFERENCES['job_worker_textfield'],
                KEY_PAGE_REFERENCES['job_dispatch_content'],
                KEY_PAGE_REFERENCES['job_logs_content'],
            )

    # Refresh the page
    if refresh_page:
        page.update()


# ------------------------------------------------------------------------------
def job_list_view_click(
    job, page, job_details_markdown, job_worker_textfield, job_dispatch_content, job_logs_content
):
    """
    Handle job list view item click and update job details content and dispatch content.

    Args:
        job (str): The selected job name.
        page (ft.Page): The Flet page object.
        job_details_markdown (ft.Markdown): The text field to display job details.
        job_worker_textfield (ft.TextField): The text field to display worker details.
        job_dispatch_content (JobDispatchContent): The JobDispatchContent instance to update.
        job_logs_content (JobLogsContent): The JobLogsContent instance to update.

    """
    current_connection = LavaAwsContext()
    realm = current_connection.realm
    profile = current_connection.profile

    job_logs_content.clear()

    if not realm or not profile:
        show_error_popup(page, 'Realm or profile is not selected.')
        return

    dynamo_db = current_connection.dynamo_db_res
    db_jobs_table = {realm: dynamo_db.Table(f'lava.{realm}.jobs')}
    current_connection.current_job = job

    try:
        # When selected job is changed, store in realm cache
        realm_cache = current_connection.profile_cache[profile].realm_cache[realm]
        realm_cache.last_selected_job_id = job

        # Fetch job specification
        job_spec = get_job_spec(job, jobs_table=db_jobs_table[realm])
        current_connection.job_spec = job_spec

        # Update worker text field
        job_worker_textfield.value = job_spec.get('worker', 'N/A')

        # Job Details Tab
        # JSON formatting syntax
        job_spec_s = json.dumps(
            job_spec, indent=GuiConfig().json_indent, sort_keys=True, default=json_default
        )
        highlighted_job_spec_s = '```json\n' + job_spec_s

        job_details_markdown.value = highlighted_job_spec_s

        # Update JobDispatchContent fields
        job_dispatch_content.current_job_textfield.value = job
        job_dispatch_content.job_worker_textfield.value = job_spec.get('worker', 'N/A')

        # Extract globals and parameters of the job that was clicked
        job_globals = get_job_globals(job_spec_s)
        original_job_params = get_job_params(job_spec_s)

        job_dispatch_content.populate_tables(job_globals, original_job_params)
        job_dispatch_content.original_globals_data = (
            job_globals  # Assign the OG globals data for comparison
        )
        job_dispatch_content.original_params_data = (
            original_job_params  # Assign the OG Params data for comparison
        )

        job_dispatch_content.dispatch_job_run_id_textfield.value = ''
        job_dispatch_content.dispatch_job_log_details_markdown.value = ''

        job_dispatch_content.status_icon.value = 'Status'
        job_dispatch_content.status_icon.color = ft.Colors.PRIMARY

        # As new job is selected, fetch some log entries for it.
        job_logs_content.fetch_events()
        # reset the previous row index of job_logs to prevent index error when switching jobs
        job_logs_content.previous_selected_row_index = None

        page.update(
            job_worker_textfield,
            job_details_markdown,
            job_dispatch_content,
            job_logs_content,
        )

    except Exception as e:
        show_error_popup(page, f'Failed to fetch job details: {e}')


# ------------------------------------------------------------------------------
def set_profiles(profile_dropdown):
    """Fetch and set AWS profiles in the profile dropdown."""
    profiles = boto3.session.Session().available_profiles
    profile_dropdown.options = [
        ft.dropdown.Option(
            text=prof,
        )
        for prof in profiles
    ]
    profile_dropdown.update()


# ------------------------------------------------------------------------------
def main(page: ft.Page):
    """Show time."""

    gui_config = GuiConfig()

    # When Flet starts the main window it does an immediate resize to effectively
    # subtract the O/S provided window chrome (title bar etc). We need to allow
    # for this in resize calculations or everytime the app is started, it will
    # shrink in size by that amount. These chrome size values are calculated
    # just after the initial page update which will trigger a window resize event.
    window_chrome_height = -1
    window_chrome_width = -1

    # --------------------------------------------------------------------------
    def apply_theme(selected_theme: GuiTheme):
        """Apply a new theme selection.."""
        page.theme = selected_theme.base_theme
        page.bgcolor = selected_theme.base_theme.color_scheme.background
        job_details_markdown.code_theme = selected_theme.markdown_code_theme
        job_logs_content.log_text_field.code_theme = selected_theme.markdown_code_theme
        job_dispatch_content.dispatch_job_log_details_markdown.code_theme = (
            selected_theme.markdown_code_theme
        )
        help_content.code_theme = selected_theme.markdown_code_theme

        # setting the current_theme in config file
        key_of_selected_theme = next(
            key for key, value in GUI_THEMES.items() if value == selected_theme
        )
        gui_config.set('current_theme', key_of_selected_theme)
        page.update()

    # --------------------------------------------------------------------------
    def page_resized(e: ft.WindowResizeEvent):
        """Handle window resize."""
        nonlocal window_chrome_height, window_chrome_width

        if window_chrome_height == -1:
            window_chrome_height = gui_config.window_height - page.height
            window_chrome_width = gui_config.window_width - page.width
            return
        gui_config.set('window_width', str(int(e.width) + window_chrome_width))
        gui_config.set('window_height', str(int(e.height) + window_chrome_height))

    # --------------------------------------------------------------------------
    # Get config and set defaults if needed
    details_font_size = gui_config.details_font_size
    page.on_resized = page_resized

    gui_theme_name = gui_config.current_theme
    if gui_theme_name not in GUI_THEMES:
        debug(f'Theme {gui_theme_name} is not supported. so we changed it to our default.')
        gui_config.set('current_theme', GuiConfig.defaults['current_theme'][0])
        gui_theme_name = gui_config.current_theme

    initial_theme = GUI_THEMES.get(gui_theme_name)
    page.theme_mode = ft.ThemeMode.LIGHT
    page.title = app_info['tool']['flet']['product']

    page.theme = GUI_THEMES.get(gui_theme_name).base_theme  # setting the theme from config file
    page.bgcolor = page.theme.color_scheme.background

    # Initial opening window size so it looks ready to use without full screen
    page.window.min_height = WINDOW_MIN_HEIGHT
    page.window.min_width = WINDOW_MIN_WIDTH
    page.window.height = gui_config.window_height
    page.window.width = gui_config.window_width

    current_connection = LavaAwsContext()

    # This needs to be updated manually anytime you add a new theme
    themes = [light_theme_gui_theme, dark_theme_gui_theme]

    settings_dialog = SettingsDialog(page, themes, apply_theme)
    jobs_running = JobsRunning(lava_aws_context=current_connection, page=page)

    # Initialize components
    job_details_markdown = ft.Markdown(
        selectable=True,
        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        code_theme=initial_theme.markdown_code_theme,
        code_style_sheet=ft.MarkdownStyleSheet(code_text_style=CodeTextStyle()),
        on_tap_link=lambda event: page.launch_url(event.data),
    )

    job_worker_textfield = ft.TextField(
        label='Worker', multiline=True, value='', expand=True, text_size=details_font_size
    )

    job_dispatch_content = JobDispatchContent(theme=page.theme, page=page)

    job_logs_content = JobLogsContent(theme=page.theme)

    # Apply initial markdown code theme to job_logs_content and job_dispatch_content
    if hasattr(job_logs_content, 'log_text_field'):
        job_logs_content.log_text_field.code_theme = initial_theme.markdown_code_theme
    if hasattr(job_dispatch_content, 'dispatch_job_log_details_markdown'):
        job_dispatch_content.dispatch_job_log_details_markdown.code_theme = (
            initial_theme.markdown_code_theme
        )

    lava_jobs_panel = LavaJobsPanel(
        padding=ft.Padding(left=5, right=5, top=0, bottom=0),
        expand=True,
        first_item_prototype=True,
        divider_thickness=1,
        on_job_click=lambda job: job_list_view_click(
            job,
            page,
            job_details_markdown,
            job_worker_textfield,
            job_dispatch_content,
            job_logs_content,
        ),
    )

    # Profile and Realm dropdowns
    profile_dropdown = create_dropdown(
        'Profile',
        lambda event: handle_profile_change(
            event.control.value,
            page,
            realm_dropdown,
            lava_jobs_panel,
            job_details_markdown,
            refresh_page=True,
        ),
    )

    realm_dropdown = create_dropdown(
        'Realm', lambda event: handle_realm_change(event, page, lava_jobs_panel, search_bar)
    )

    search_bar = SearchBar('Search Jobs', lava_jobs_panel)

    # Hold a reference to each of the key page elements
    KEY_PAGE_REFERENCES['page'] = page
    KEY_PAGE_REFERENCES['profile_dropdown'] = profile_dropdown
    KEY_PAGE_REFERENCES['realm_dropdown'] = realm_dropdown
    KEY_PAGE_REFERENCES['search_bar'] = search_bar
    KEY_PAGE_REFERENCES['lava_jobs_panel'] = lava_jobs_panel
    KEY_PAGE_REFERENCES['job_details_markdown'] = job_details_markdown
    KEY_PAGE_REFERENCES['job_details_markdown'] = job_details_markdown
    KEY_PAGE_REFERENCES['job_worker_textfield'] = job_worker_textfield
    KEY_PAGE_REFERENCES['job_dispatch_content'] = job_dispatch_content
    KEY_PAGE_REFERENCES['job_logs_content'] = job_logs_content

    settings_button = ft.IconButton(
        icon=ft.Icons.SETTINGS,
        tooltip='Change Theme',
        on_click=lambda event: settings_dialog.open_dialog(),
    )
    help_dir = Path(__file__).parent / 'assets' / 'help'
    help_text = '\n'.join(p.read_text(encoding='utf-8') for p in sorted(help_dir.glob('*.md')))
    help_content = ft.Markdown(
        value=help_text,
        auto_follow_links=True,
        expand=True,
        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        code_theme=initial_theme.markdown_code_theme,
        selectable=True,
        code_style_sheet=ft.MarkdownStyleSheet(
            code_text_style=CodeTextStyle(color=ft.Colors.PRIMARY)
        ),
        md_style_sheet=ft.MarkdownStyleSheet(
            h1_text_style=ft.TextStyle(
                color=ft.Colors.SECONDARY, size=details_font_size + 4, weight=ft.FontWeight.BOLD
            ),
            h1_padding=ft.padding.only(top=20, bottom=20),
            h2_text_style=ft.TextStyle(
                color=ft.Colors.SECONDARY, size=details_font_size + 2, weight=ft.FontWeight.BOLD
            ),
            h3_text_style=ft.TextStyle(
                color=ft.Colors.SECONDARY, size=details_font_size, weight=ft.FontWeight.BOLD
            ),
            h4_text_style=ft.TextStyle(
                color=ft.Colors.PRIMARY, size=details_font_size, weight=ft.FontWeight.BOLD
            ),
            p_text_style=DetailTextStyle(),
            list_bullet_text_style=DetailTextStyle(),
            table_head_text_style=DetailTextBoldStyle(),
            table_body_text_style=DetailTextStyle(),
            code_text_style=CodeTextStyle(color=ft.Colors.PRIMARY),
            strong_text_style=DetailTextBoldStyle(),
        ),
    )

    help_container = ft.Container(
        padding=ft.Padding(left=5, right=5, top=3, bottom=3), expand=True, content=help_content
    )

    # Tabs
    tabs = ft.Tabs(
        divider_color='#CCCCCC',
        label_color='blue',
        indicator_color='blue',
        unselected_label_color=ft.Colors.PRIMARY,
        selected_index=0,  # Default to Job Details tab
        tabs=[
            create_tab_content('Job Details', [job_details_markdown]),
            create_tab_content('Job Dispatch', [job_dispatch_content]),
            create_tab_content('Job Logs', [job_logs_content]),
            create_tab_content('Jobs Currently Running', [jobs_running]),
            create_tab_content('Help', [help_container]),
        ],
    )

    left_hand_panel = ft.Container(
        width=650,
        expand=True,
        content=ft.Card(
            color=ft.Colors.SURFACE,
            elevation=4,
            content=ft.Column(
                controls=[
                    # This first row is a fixed height - should NOT scroll
                    ft.Row(
                        controls=[
                            profile_dropdown,
                            realm_dropdown,
                            search_bar,
                            settings_button,
                        ],
                        alignment=ft.MainAxisAlignment.START,
                        spacing=20,
                        expand=False,
                    ),
                    # This second row should expand to fill vertically
                    ft.Row(
                        controls=[
                            ft.Container(
                                ink=True,
                                ink_color='#000000',
                                content=ft.Column(
                                    controls=[lava_jobs_panel],
                                    expand=True,
                                ),
                                expand=True,
                                width=600,
                            )
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        vertical_alignment=ft.CrossAxisAlignment.START,  # Add this line
                        expand=True,
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                spacing=20,
            ),
        ),
    )

    # Add components to the page
    page.add(
        ft.Container(
            margin=ft.margin.only(bottom=10),
            expand=1,
            content=ft.Row(
                controls=[
                    left_hand_panel,
                    ft.VerticalDivider(color='#CCCCCC'),
                    ft.Container(expand=True, content=tabs),
                ],
                alignment=ft.MainAxisAlignment.START,
            ),
        )
    )

    profile_from_config = gui_config.get('aws_profile')

    # 1st time user so their config file will not have aws_profile variable so we add it.
    if profile_from_config is None or profile_from_config == '':
        gui_config.set('aws_profile', '')

    # Set profiles and update the page
    set_profiles(profile_dropdown)

    # User may have aws_profile value that is incompatible.
    if profile_from_config is not None and profile_from_config != '':
        try:
            handle_profile_change(
                profile_from_config, page, realm_dropdown, lava_jobs_panel, job_details_markdown
            )
            profile_dropdown.value = profile_from_config
        except Exception as ex:
            gui_config.set('aws_profile', 'default')
            handle_profile_change(
                'default', page, realm_dropdown, lava_jobs_panel, job_details_markdown
            )
            profile_dropdown.value = 'default'
            debug(f'Exception: {ex}. So we swapped to default.')

    profile_dropdown.update()
    page.update()


# ------------------------------------------------------------------------------
ft.app(target=main)
