# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/. """

from __future__ import division
import logging
from logging.handlers import RotatingFileHandler
from odemis.gui import FG_COLOUR_ERROR, FG_COLOUR_WARNING, FG_COLOUR_DIS, FG_COLOUR_MAIN
import os.path
import sys
import wx
from odemis.gui.util import wxlimit_invocation

LOG_FILE = "odemis-gui.log"

LOG_LINES = 500  # maximum lines in the GUI logger
log = logging.getLogger()  # for compatibility only


def logging_remote_exception(msg, *args):
    """ Same as logging.exception, but also display remote exception info from Pyro """
    logging.error(msg, exc_info=1, *args)

    try:
        ex_type, ex_value, ex_tb = sys.exc_info()
        remote_tb = ex_value._pyroTraceback
        logging.error("Remote exception %s", "".join(remote_tb))
    except AttributeError:
        pass

# monkey patching
logging.exception = logging_remote_exception


def init_logger(level=logging.DEBUG):
    """
    Initializes the logger to some nice defaults
    To be called only once, at the initialisation
    """
    logging.basicConfig(format=" - %(levelname)s \t%(message)s")
    l = logging.getLogger()
    l.setLevel(level)
    frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
    l.handlers[0].setFormatter(logging.Formatter(frm))

    # Create file handler
    # Path to the log file
    logfile_path = os.path.join(os.path.expanduser("~"), LOG_FILE)
    # Formatting string for logging messages to file
    frm = "%(asctime)s %(levelname)-7s %(module)s:%(lineno)d: %(message)s"
    file_format = logging.Formatter(frm)

    # Max 5 log files of 10Mb
    file_handler = RotatingFileHandler(logfile_path, maxBytes=10 * (2 ** 20), backupCount=5)

    file_handler.setFormatter(file_format)
    log.addHandler(file_handler)


def create_gui_logger(log_field, debug_va=None, level_va=None):
    """
    Connect the log output to the text field instead of the standard output
    log_field (wx text field)
    debug_va (Boolean VigilantAttribute)
    """
    # Create gui handler
    frm = "%(asctime)s %(levelname)-7s %(module)-15s: %(message)s"
    gui_format = logging.Formatter(frm, '%H:%M:%S')
    text_field_handler = TextFieldHandler()
    text_field_handler.setTextField(log_field)
    if debug_va is not None:
        text_field_handler.setDebugVA(debug_va)
    if level_va is not None:
        text_field_handler.setLevelVA(level_va)

    text_field_handler.setFormatter(gui_format)
    logging.debug("Switching to GUI logger")

    # remove standard output handler if still there
    for handler in log.handlers:
        if isinstance(handler, logging.StreamHandler):
            log.removeHandler(handler)

    try:
        log.addHandler(text_field_handler)
    except:
        # Use print here because log probably doesn't work
        print("Failed to set-up logging handlers")
        logging.exception("Failed to set-up logging handlers")
        raise


def stop_gui_logger():
    """
    Stop the logger from displaying logs to the GUI.
    Use just before ending the GUI.
    """

    # remove whatever handler was already there
    for handler in log.handlers:
        if isinstance(handler, TextFieldHandler):
            log.removeHandler(handler)


class TextFieldHandler(logging.Handler):
    """ Custom log handler, used to output log entries to a text field. """
    TEXT_STYLES = (
        wx.TextAttr(FG_COLOUR_ERROR, None),
        wx.TextAttr(FG_COLOUR_WARNING, None),
        wx.TextAttr(FG_COLOUR_MAIN, None),
        wx.TextAttr(FG_COLOUR_DIS, None),
    )

    def __init__(self):
        """ Call the parent constructor and initialize the handler """
        logging.Handler.__init__(self)
        self.textfield = None
        self.debug_va = None
        self.level_va = None
        self.cache = {
            0: [],
            1: []
        }
        self.current_cache = 0
        self._locked = False

    def setTextField(self, textfield):
        self.textfield = textfield
        self.textfield.Clear()

    def setDebugVA(self, debug_va):
        self.debug_va = debug_va

    def setLevelVA(self, level_va):
        self.level_va = level_va

    def emit(self, record):
        """ Write a record, in colour, to a text field. """
        if self.textfield is not None:
            if record.levelno >= logging.ERROR:
                text_style = self.TEXT_STYLES[0]
            elif record.levelno == logging.WARNING:
                text_style = self.TEXT_STYLES[1]
            elif record.levelno == logging.INFO:
                text_style = self.TEXT_STYLES[2]
            else:
                text_style = self.TEXT_STYLES[3]

            if self.level_va and record.levelno > self.level_va.value:
                self.level_va.value = record.levelno

            # FIXME: still seems to be possible to completely hog the GUI by
            # logging too much. A way to fix it would be to run the textfield
            # update at a maximum frequency (10Hz), and queue the logs in
            # between.

            # Do the actual writing in a CallAfter, so logging won't interfere
            # with the GUI drawing process.
            self.cache[self.current_cache].append((record, text_style))
            self.write_to_field()

    @wxlimit_invocation(0.2)
    def write_to_field(self):

        if self._locked:
            return
        self._locked = True
        # Cache that we are going to write
        write_cache = self.current_cache
        # Swap caches, so new messages get added to the other cache
        self.current_cache = int(not self.current_cache)

        cache = self.cache[write_cache]
        prev_style = None

        # Process at most the latest 'LOG_LINES' messages
        for record, text_style in cache[-LOG_LINES:]:
            if prev_style != text_style:
                self.textfield.SetDefaultStyle(text_style)
                prev_style = text_style
            self.textfield.AppendText(self.format(record) + "\n")

        nb_lines = self.textfield.GetNumberOfLines()
        nb_old = nb_lines - LOG_LINES
        if nb_old > 0:
            # Removes the characters from position 0 up to and including the Nth line break
            first_new = 0
            txt = self.textfield.Value
            for i in range(nb_old):
                first_new = txt.find('\n', first_new) + 1

            self.textfield.Remove(0, first_new)

        self.cache[write_cache] = []
        self._locked = False
        self.textfield.Refresh()
