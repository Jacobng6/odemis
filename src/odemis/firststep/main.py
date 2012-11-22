#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@author: Éric Piel

Copyright © 2012 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import logging
import os.path
import sys
import threading
import traceback
import wx
import Pyro4.errors

from odemis import __version__, model
from odemis.firststep import main_xrc, instrmodel
from odemis.gui.xmlh import odemis_get_resources


class FirstStepApp(wx.App):
    """ This is FirstStep GUI's main application class
    """

    def __init__(self):
        # Replace the standard 'get_resources' with our augmented one, that
        # can handle more control types. See the xhandler package for more info.
        main_xrc.get_resources = odemis_get_resources

        # Constructor of the parent class
        # ONLY CALL IT AT THE END OF :py:method:`__init__` BECAUSE OnInit will
        # be called
        # and it needs the attributes defined in this constructor!
        wx.App.__init__(self, redirect=True)

    def OnInit(self):
        """ Application initialization, automatically run from the :wx:`App`
        constructor.
        """

        try:
            self.microscope = model.getMicroscope()
            self.mic_mgr = instrmodel.MicroscopeMgr(self.microscope)
        except (IOError, Pyro4.errors.CommunicationError), e:
            logging.exception("Failed to connect to back-end")
            msg = ("FirstStep could not connect to the Odemis back-end:\n\n"
                   "{0}\n\n"
                   "Launch GUI anyway?").format(e)

            answer = wx.MessageBox(msg,
                                   "Connection error",
                                    style=wx.YES|wx.NO|wx.ICON_ERROR)
            if answer == wx.NO:
                sys.exit(1)

        # Load the main frame
        self.main_frame = main_xrc.xrcfr_main(None)

        #self.main_frame.Bind(wx.EVT_CHAR, self.on_key)

        logging.info("Starting FirstStep")
        self.init_gui()

        # Application successfully launched
        return True

    def init_gui(self):
        """ This method binds events to menu items and initializes
        GUI controls """

        try:
            # Add frame icon
            ib = wx.IconBundle()
            # TODO icon
            ib.AddIconFromFile(os.path.join(self._module_path(),
                                            "img/icon128.png"),
                                            wx.BITMAP_TYPE_ANY)
            self.main_frame.SetIcons(ib)

            # Menu events
            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_quit.GetId(),
                        self.on_close_window)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_about.GetId(),
                        self.on_about)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_halt.GetId(),
                        self.on_stop_axes)


            # The escape accelerator has to be added manually, because for some
            # reason, the 'ESC' key will not register using XRCED.
            accel_tbl = wx.AcceleratorTable([
                (wx.ACCEL_NORMAL, wx.WXK_ESCAPE,
                 self.main_frame.menu_item_halt.GetId())
            ])

            self.main_frame.SetAcceleratorTable(accel_tbl)

            self.main_frame.Bind(wx.EVT_CLOSE, self.on_close_window)

            self.main_frame.Show()
            #self.main_frame.Raise()
            #self.main_frame.Refresh()

            # Need to bind buttons?

        except Exception:  #pylint: disable=W0703
            self.excepthook(*sys.exc_info())

    def init_config(self):
        """ Initialize GUI configuration """
        # TODO: Process GUI configuration here
        pass

    def _module_path(self):
        encoding = sys.getfilesystemencoding()
        return os.path.dirname(unicode(__file__, encoding))

    def on_stop_axes(self, evt):
        if self.mic_mgr:
            self.mic_mgr.stopMotion()
        else:
            evt.Skip()

    def on_about(self, evt):
        message = ("%s\nVersion %s.\n\n%s.\nLicensed under the %s." %
                   (__version__.name,
                    __version__.version,
                    __version__.copyright,
                    __version__.license))
        dlg = wx.MessageDialog(self.main_frame, message,
                               "About " + __version__.shortname, wx.OK)
        dlg.ShowModal() # blocking
        dlg.Destroy()

    def on_close_window(self, evt=None): #pylint: disable=W0613
        """ This method cleans up and closes the GUI. """

        logging.info("Exiting FirstStep")
        
        self.on_stop_axes(None)

        self.main_frame.Destroy()
        sys.exit(0)

    def excepthook(self, type, value, trace): #pylint: disable=W0622
        """ Method to intercept unexpected errors that are not caught
        anywhere else and redirects them to the logger. """
        exc = traceback.format_exception(type, value, trace)
        logging.error("".join(exc))


def installThreadExcepthook():
    """ Workaround for sys.excepthook thread bug
    http://spyced.blogspot.com/2007/06/workaround-for-sysexcepthook-bug.html

    Call once from ``__main__`` before creating any threads.
    If using psyco, call

    """
    init_old = threading.Thread.__init__
    def init(self, *args, **kwargs):
        init_old(self, *args, **kwargs)
        run_old = self.run
        def run_with_except_hook(*args, **kw):
            try:
                run_old(*args, **kw)
            except (KeyboardInterrupt, SystemExit):
                raise
            except:
                sys.excepthook(*sys.exc_info())
        self.run = run_with_except_hook
    threading.Thread.__init__ = init

def main():
    # Create application
    app = FirstStepApp()
    # Change exception hook so unexpected exception
    # get caught by the logger
    backup_excepthook, sys.excepthook = sys.excepthook, app.excepthook

    # Start the application
    app.MainLoop()
    app.Destroy()

    sys.excepthook = backup_excepthook

if __name__ == '__main__':
    installThreadExcepthook()
    main()
