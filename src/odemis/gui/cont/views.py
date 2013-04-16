# -*- coding: utf-8 -*-
"""
Created on 1 Oct 2012

@author: Rinze de Laat

Copyright © 2012-2013 Rinze de Laat and Éric Piel, Delmic

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

from __future__ import division

import logging
from collections import namedtuple

import wx

from odemis.gui import instrmodel
from odemis.gui.model import OPTICAL_STREAMS, EM_STREAMS
from odemis.gui.model.stream import SEMStream, BrightfieldStream, FluoStream

# TODO: The next comments were copied from instrmodel. Read/implement/remove
# viewport controller (to be merged with stream controller?)
# Creates the 4 microscope views at init, with the right names, depending on
#   the available microscope hardware.
# (The 4 viewports canvas are already created, the main interface connect
#   them to the view, by number)
# In charge of switching between 2x2 layout and 1 layout.
# In charge of updating the view focus
# In charge of updating the view thumbnails???
# In charge of ensuring they all have same zoom and center position
# In charge of applying the toolbar actions on the right viewport
# in charge of changing the "hair-cross" display

class ViewController(object):
    """ Manages the microscope view updates, change of viewport focus, etc.
    """

    def __init__(self, micgui, main_frame, viewports=None):
        """
        micgui (MicroscopeModel) -- the representation of the microscope GUI
        main_frame: (wx.Frame) -- the frame which contains the 4 viewports
        """

        self._microscope = micgui
        self._main_frame = main_frame

        # list of all the viewports (widgets that show the views)
        if viewports:
            self._viewports = viewports
        else:
            self._viewports = [main_frame.vp_secom_tl, main_frame.vp_secom_tr,
                               main_frame.vp_secom_bl, main_frame.vp_secom_br]

        # create the (default) views and set focussedView
        self._createViews()

        # subscribe to layout and view changes
        self._microscope.viewLayout.subscribe(self._onViewLayout, init=True)
        self._microscope.focussedView.subscribe(self._onView, init=False)

    def _createViews(self):
        """
        Create the different views displayed, according to the current microscope.
        To be executed only once, at initialisation.
        """

        # If SEM only: all SEM
        if self._microscope.ebeam and not self._microscope.light:
            logging.info("Creating SEM only viewport layout")
            i = 1
            for viewport in self._viewports:
                view = instrmodel.MicroscopeView(
                            "SEM %d" % i,
                            self._microscope.stage,
                            focus0=None, # TODO: SEM focus or focus1?
                            stream_classes=(SEMStream,)
                         )
                viewport.setView(view, self._microscope)
                i += 1
            #print dir(self._viewports[0])
            self._microscope.focussedView.value = self._viewports[0].mic_view

        # If Optical only: all Optical
        # TODO: first one is brightfield only?
        elif not self._microscope.ebeam and self._microscope.light:
            logging.info("Creating Optical only viewport layout")
            i = 1
            for viewport in self._viewports:
                view = instrmodel.MicroscopeView(
                            "Optical %d" % i,
                            self._microscope.stage,
                            focus0=self._microscope.focus,
                            stream_classes=(BrightfieldStream, FluoStream)
                         )
                viewport.setView(view, self._microscope)
                i += 1
            self._microscope.focussedView.value = self._viewports[0].mic_view

        # If both SEM and Optical: SEM/Optical/2x combined
        elif self._microscope.ebeam and self._microscope.light:
            logging.info("Creating combined SEM/Optical viewport layout")

            view = instrmodel.MicroscopeView(
                        "SEM",
                        self._microscope.stage,
                        focus0=None, # TODO: SEM focus
                        stream_classes=EM_STREAMS
                     )
            self._viewports[0].setView(view, self._microscope)
            self._microscope.sem_view = view


            view = instrmodel.MicroscopeView(
                        "Optical",
                        self._microscope.stage,
                        focus0=self._microscope.focus,
                        stream_classes=OPTICAL_STREAMS
                     )
            self._viewports[1].setView(view, self._microscope)
            self._microscope.optical_view = view


            view = instrmodel.MicroscopeView(
                        "Combined 1",
                        self._microscope.stage,
                        focus0=self._microscope.focus,
                        focus1=None, # TODO: SEM focus
                     )
            self._viewports[2].setView(view, self._microscope)
            self._microscope.combo1_view = view


            view = instrmodel.MicroscopeView(
                        "Combined 2",
                        self._microscope.stage,
                        focus0=self._microscope.focus,
                        focus1=None, # TODO: SEM focus
                     )
            self._viewports[3].setView(view, self._microscope)
            self._microscope.combo2_view = view

            # Start off with the 2x2 view
            # Focus defaults to the top right viewport
            self._microscope.focussedView.value = self._viewports[1].mic_view

        else:
            logging.warning("No known microscope configuration, creating 4 generic views")
            i = 1
            for viewport in self._viewports:
                view = instrmodel.MicroscopeView(
                            "View %d" % i,
                            self._microscope.stage,
                            focus0=self._microscope.focus
                         )
                viewport.setView(view, self._microscope)
                i += 1
            self._microscope.focussedView.value = self._viewports[0].mic_view

        # TODO: if chamber camera: br is just chamber, and it's the focussedView


    def _onView(self, view):
        """
        Called when another view is focused
        """
        logging.debug("Changing focus to view %s", view.name.value)
        layout = self._microscope.viewLayout.value

        self._main_frame.pnl_tab_secom_streams.Freeze()

        for viewport in self._viewports:
            if viewport.mic_view == view:
                viewport.SetFocus(True)
                if layout == instrmodel.VIEW_LAYOUT_ONE:
                    viewport.Show()
            else:
                viewport.SetFocus(False)
                if layout == instrmodel.VIEW_LAYOUT_ONE:
                    viewport.Hide()

        if layout == instrmodel.VIEW_LAYOUT_ONE:
            self._main_frame.pnl_tab_secom_streams.Layout() # resize viewport

        self._main_frame.pnl_tab_secom_streams.Thaw()

    def _onViewLayout(self, layout):
        """
        Called when the view layout of the GUI must be changed
        """
        # only called when changed
        self._main_frame.pnl_tab_secom_streams.Freeze()

        if layout == instrmodel.VIEW_LAYOUT_ONE:
            logging.debug("Showing only one view")
            # TODO resize all the viewports now, so that there is no flickering
            # when just changing view
            for viewport in self._viewports:
                if viewport.mic_view == self._microscope.focussedView.value:
                    viewport.Show()
                else:
                    viewport.Hide()

        elif layout == instrmodel.VIEW_LAYOUT_22:
            logging.debug("Showing all views")
            for viewport in self._viewports:
                viewport.Show()

        elif layout == instrmodel.VIEW_LAYOUT_FULLSCREEN:
            raise NotImplementedError()
        else:
            raise NotImplementedError()

        self._main_frame.pnl_tab_secom_streams.Layout()  # resize the viewports
        self._main_frame.pnl_tab_secom_streams.Thaw()


class ViewSelector(object):
    """
    This class controls the view selector buttons and labels associated with
    them.
    """

    def __init__(self, micgui, main_frame):
        """
        micgui (MicroscopeModel): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self._microscope_gui = micgui
        self._main_frame = main_frame

        # TODO: should create buttons according to micgui views

        # btn -> (viewport, label)
        ViewportLabel = namedtuple('ViewportLabel', ['vp', 'lbl'])

        self.buttons = {main_frame.btn_secom_view_all:
                            ViewportLabel(None, main_frame.lbl_secom_view_all), # 2x2 layout
                        main_frame.btn_secom_view_tl:
                            ViewportLabel(main_frame.vp_secom_tl, main_frame.lbl_secom_view_tl),
                        main_frame.btn_secom_view_tr:
                            ViewportLabel(main_frame.vp_secom_tr, main_frame.lbl_secom_view_tr),
                        main_frame.btn_secom_view_bl:
                            ViewportLabel(main_frame.vp_secom_bl, main_frame.lbl_secom_view_bl),
                        main_frame.btn_secom_view_br:
                            ViewportLabel(main_frame.vp_secom_br, main_frame.lbl_secom_view_br)}

        for btn in self.buttons:
            btn.Bind(wx.EVT_BUTTON, self.OnClick)

        # subscribe to layout and view changes
        # FIXME: viewLayout disabled, because it was sending wrong (integer)
        # views to _onView
        #self._microscope_gui.viewLayout.subscribe(self._onView, init=True)
        #self._microscope_gui.focussedView.subscribe(self._onView, init=True)

        # subscribe to thumbnails
        self._subscriptions = [] # list of functions
        for btn in [self._main_frame.btn_secom_view_tl, self._main_frame.btn_secom_view_tr,
                    self._main_frame.btn_secom_view_bl, self._main_frame.btn_secom_view_br]:
            def onThumbnail(im, btn=btn): # save btn in scope
                btn.set_overlay(im)

            self.buttons[btn].vp.mic_view.thumbnail.subscribe(onThumbnail, init=True)
            # keep ref of the functions so that they are not dropped
            self._subscriptions.append(onThumbnail)

            # also subscribe for updating the 2x2 button
            self.buttons[btn].vp.mic_view.thumbnail.subscribe(self._update22Thumbnail)
        #self._update22Thumbnail(None)

        # subscribe to change of name
        for btn, view_label in self.buttons.items():
            if view_label.vp is None: # 2x2 layout
                view_label.lbl.SetLabel("Overview")
                continue

            def onName(name, view_label=view_label): # save view_label
                view_label.lbl.SetLabel(name)

            view_label.vp.mic_view.name.subscribe(onName, init=True)
            self._subscriptions.append(onName)

        # Select the overview by default
        # Fixme: should be related to the layout in MicroscopeModel and/or the
        # focussed viewport. ('None' selects the overview button)
        self.toggleButtonForView(None)

    def toggleButtonForView(self, mic_view):
        """
        Toggle the button which represents the view and untoggle the other ones
        mic_view (MicroscopeView or None): the view, or None if the first button
                                           (2x2) is to be toggled
        Note: it does _not_ change the view
        """
        for b, vl in self.buttons.items():
            # 2x2 => vp is None / 1 => vp exists and vp.view is the view
            if (vl.vp is None and mic_view is None) or (vl.vp and vl.vp.mic_view == mic_view):
                b.SetToggle(True)
            else:
                if vl.vp:
                    logging.debug("untoggling button of view %s", vl.vp.mic_view.name.value)
                else:
                    logging.debug("untoggling button of view All")
                b.SetToggle(False)

    def _update22Thumbnail(self, im):
        """
        Called when any thumbnail is changed, to recompute the 2x2 thumbnail of
        the first button.
        im (unused)
        """
        # Create an image from the 4 thumbnails in a 2x2 layout with small border
        btn_all = self._main_frame.btn_secom_view_all
        border_width = 2 # px
        size = max(1, btn_all.overlay_width), max(1, btn_all.overlay_height)
        size_sub = (max(1, (size[0] - border_width) // 2),
                    max(1, (size[1] - border_width) // 2))
        # starts with an empty image with the border colour everywhere
        im_22 = wx.EmptyImage(*size, clear=False)
        im_22.SetRGBRect(wx.Rect(0, 0, *size), *btn_all.GetBackgroundColour().Get())

        for i, btn in enumerate([self._main_frame.btn_secom_view_tl, self._main_frame.btn_secom_view_tr,
                                 self._main_frame.btn_secom_view_bl, self._main_frame.btn_secom_view_br]):

            im = self.buttons[btn].vp.mic_view.thumbnail.value
            if im:
                # im doesn't have the same aspect ratio as the actual thumbnail
                # => rescale and crop on the center
                # Rescale to have the smallest axis as big as the thumbnail
                rsize = list(size_sub)
                if (size_sub[0] / im.Width) > (size_sub[1] / im.Height):
                    rsize[1] = int(im.Height * (size_sub[0] / im.Width))
                else:
                    rsize[0] = int(im.Width * (size_sub[1] / im.Height))
                sim = im.Scale(*rsize, quality=wx.IMAGE_QUALITY_HIGH)

                # crop to the right shape
                lt = ((size_sub[0] - sim.Width)//2, (size_sub[1] - sim.Height)//2)
                sim.Resize(size_sub, lt)

                # compute placement
                y, x = divmod(i, 2)
                # copy im in the right place
                im_22.Paste(sim, x * (size_sub[0] + border_width), y * (size_sub[1] + border_width))
            else:
                # black image
                # Should never happen
                pass #sim = wx.EmptyImage(*size_sub)

        # set_overlay will rescale to the correct button size
        btn_all.set_overlay(im_22)

    def _onView(self, view):
        """
        Called when another view is focused, or viewlayout is changed
        """

        logging.debug("View changed")

        try:
            if view is not None:
                assert isinstance(view, instrmodel.MicroscopeView)
        except AssertionError:
            logging.exception("Wrong type of view parameter! %s", view)
            raise

        # TODO when changing from 2x2 to a view non focused, it will be called
        # twice in row. => optimise to not do it twice

        self.toggleButtonForView(view)

        # if layout is 2x2 => do nothing (first button is selected by _onViewLayout)
        # if self._microscope_gui.viewLayout.value == instrmodel.VIEW_LAYOUT_22:
        #     # otherwise (layout is 2x2) => select the first button
        #     self.toggleButtonForView(None)
        # else:
        #     # otherwise (layout is 1) => select the right button
        #     self.toggleButtonForView(view)


    def OnClick(self, evt):
        """
        Navigation button click event handler

        Show the related view(s) and sets the focus if needed.
        """

        # The event does not need to be 'skipped' because
        # the button will be toggled when the event for value change is received.

        btn = evt.GetEventObject()
        viewport = self.buttons[btn].vp

        if viewport is None:
            logging.debug("Overview button click")
            self.toggleButtonForView(None)
            # 2x2 button
            # When selecting the overview, the focussed viewport should not change
            self._microscope_gui.viewLayout.value = instrmodel.VIEW_LAYOUT_22
        else:
            logging.debug("View button click")
            self.toggleButtonForView(viewport.mic_view)
            # It's preferable to change the view before the layout so that
            # if the layout was 2x2 with another view focused, it doesn't first
            # display one big view, and immediately after changes to another view.
            self._microscope_gui.focussedView.value = viewport.mic_view
            self._microscope_gui.viewLayout.value = instrmodel.VIEW_LAYOUT_ONE
