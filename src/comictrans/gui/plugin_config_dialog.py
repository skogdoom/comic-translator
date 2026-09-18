"""Configure Plugins: what each installed plugin is set to, and whether it runs at all.

One list, everything Rescan Plugins would have found — a plugin that loaded
and one that did not, side by side, rather than the second going unmentioned.
Selecting a row shows what there is to show: a working plugin's declared
version if it named one, its own settings and its active checkbox, or a
broken one's error and nothing else, since there is nothing to configure on
a plugin that never became one. The version is shown nowhere else —
``plugins.py`` reads nothing but text out of it.

Fields write straight through to ``QSettings`` as they are edited, the same
as the header dialog and the region inspector — there is nothing to apply
and nothing to cancel. The one thing this reports rather than just writes is
the active checkbox: flipping it changes what Plugins itself offers to run,
so :attr:`changed` tells the window to rebuild that menu. A settings value
changing does not — the next run reads it fresh, and rebuilding the menu
over a keystroke in a text field would mean re-importing every plugin while
someone is still typing.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..plugins import FailedPlugin, LoadedPlugin
from . import plugin_settings
from .preferences import SettingsStore

_PLUGIN_ROLE = 0x100

_SETTINGS_FIELD_MIN_WIDTH = 260
"""Wider than a ``QLineEdit``'s own size hint — about seventeen characters —
which is all it asks for on macOS, where ``QFormLayout`` defaults to
``FieldsStayAtSizeHint`` and holds the field at that width instead of
stretching it to the form's own. Not an attempt to fit every value in full:
a plugin's default can always be longer than this, the same as any other
text field in this window. Just enough that the common case is not clipped
the moment the dialog opens."""


class PluginConfigDialog(QDialog):
    changed = Signal()
    """A plugin's active state changed, so the run menu needs rebuilding."""

    def __init__(
        self,
        settings: SettingsStore | None,
        plugins: Sequence[LoadedPlugin | FailedPlugin],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle(self.tr("Configure Plugins"))

        self._list = QListWidget()
        disabled_text = self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        for plugin in plugins:
            shown = plugin.name if isinstance(plugin, LoadedPlugin) else plugin.path.name
            item = QListWidgetItem(shown)
            item.setData(_PLUGIN_ROLE, plugin)
            if isinstance(plugin, FailedPlugin):
                item.setForeground(disabled_text)
                item.setToolTip(plugin.error)
            self._list.addItem(item)
        self._list.currentItemChanged.connect(self._on_selected)

        self._detail = QVBoxLayout()

        body = QHBoxLayout()
        body.addWidget(self._list, 1)
        body.addLayout(self._detail, 2)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(buttons)
        self.setMinimumSize(480, 320)

        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._show_nothing_installed()

    def _clear_detail(self) -> None:
        while self._detail.count():
            item = self._detail.takeAt(0)
            if item is None:  # count() just said otherwise
                continue
            widget = item.widget()
            if widget is not None:
                # Detached immediately, not just scheduled for deletion: a
                # widget only taken off the layout is still this dialog's
                # child until deleteLater's deferred cleanup actually runs,
                # so a search for "the" checkbox could still find the one
                # that belonged to whatever was shown before this.
                widget.setParent(None)
                widget.deleteLater()
            child_layout = item.layout()
            if child_layout is not None:
                _clear_layout(child_layout)

    def _show_nothing_installed(self) -> None:
        self._clear_detail()
        label = QLabel(self.tr("No plugins are installed."))
        label.setWordWrap(True)
        self._detail.addWidget(label)

    def _on_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        self._clear_detail()
        if current is None:
            return
        plugin = current.data(_PLUGIN_ROLE)
        if isinstance(plugin, FailedPlugin):
            self._show_failed(plugin)
        else:
            self._show_loaded(plugin)

    def _show_failed(self, plugin: FailedPlugin) -> None:
        label = QLabel(
            self.tr("{0} could not be loaded:\n{1}").format(plugin.path.name, plugin.error)
        )
        label.setWordWrap(True)
        self._detail.addWidget(label)
        self._detail.addStretch(1)

    def _show_loaded(self, plugin: LoadedPlugin) -> None:
        if plugin.version:
            version_label = QLabel(self.tr("Version {0}").format(plugin.version))
            self._detail.addWidget(version_label)

        active = QCheckBox(self.tr("Active"))
        active.setChecked(plugin_settings.is_active(self._settings, plugin))
        active.toggled.connect(lambda checked, p=plugin: self._on_active_toggled(p, checked))
        self._detail.addWidget(active)

        if plugin.settings:
            values = plugin_settings.resolved_settings(self._settings, plugin)
            form = QFormLayout()
            for field in plugin.settings:
                edit = QLineEdit(values[field.key])
                edit.setMinimumWidth(_SETTINGS_FIELD_MIN_WIDTH)
                edit.textChanged.connect(
                    lambda text, p=plugin, key=field.key: self._on_setting_changed(p, key, text)
                )
                form.addRow(field.label, edit)
            self._detail.addLayout(form)
        else:
            note = QLabel(self.tr("Nothing to configure."))
            note.setWordWrap(True)
            self._detail.addWidget(note)
        self._detail.addStretch(1)

    def _on_active_toggled(self, plugin: LoadedPlugin, checked: bool) -> None:
        plugin_settings.set_active(self._settings, plugin, checked)
        self.changed.emit()

    def _on_setting_changed(self, plugin: LoadedPlugin, key: str, text: str) -> None:
        plugin_settings.set_setting(self._settings, plugin, key, text)


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:  # count() just said otherwise
            continue
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


__all__ = ["PluginConfigDialog"]
