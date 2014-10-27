# -*- coding: utf-8 -*-
"""
:created: 2012-10-01
:author: Rinze de Laat
:copyright: © 2012-2013 Rinze de Laat and Éric Piel, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

from __future__ import division

import collections
import logging
import wx

from odemis.acq.stream import RGBCameraStream, BrightfieldStream
from odemis.gui import model
from odemis.gui.cont import tools
from odemis.acq.stream import OpticalStream, EMStream, SpectrumStream, ARStream
from odemis.gui.util import call_after
from odemis.model import VigilantAttributeBase
from odemis.model import MD_PIXEL_SIZE


class ViewController(object):
    """ Manage the display of various viewports in a tab """

    def __init__(self, tab_data, main_frame, viewports, toolbar=None):
        """
        :param tab_data: MicroscopyGUIData -- the representation of the microscope GUI
        :param main_frame: wx.Frame -- the frame which contains the 4 viewports
        :param viewports: [MicroscopeViewport] or OrderedDict(MicroscopeViewport -> {}) -- the
            viewports to update. The first one is the one focused. If it's an OrderedDict, the
            kwargs are passed to the MicroscopeView creation.
            If there are more than 4 viewports, only the first 4 will be made visible and any others
            will be hidden.
        :param toolbar: ToolBar or None-- toolbar to manage the TOOL_ZOOM_FIT tool.

        .. note::
            If a 2x2 viewport grid is present, the first four viewports in the _viewports attribute
            are expected to belong to this grid.

        """

        self._data_model = tab_data
        self._main_data_model = tab_data.main
        self.main_frame = main_frame
        self._toolbar = toolbar

        if isinstance(viewports, collections.OrderedDict):
            self._viewports = viewports.keys()
            self._create_views_fixed(viewports)
        else:
            # create the (default) views
            self._viewports = viewports
            self._create_views_auto()

        # First view is focused
        tab_data.focussedView.value = tab_data.visible_views.value[0]

        # subscribe to layout and view changes
        tab_data.visible_views.subscribe(self._on_visible_views)
        tab_data.viewLayout.subscribe(self._on_view_layout, init=True)
        tab_data.focussedView.subscribe(self._on_focussed_view, init=True)

    @property
    def viewports(self):
        return self._viewports

    def _create_views_fixed(self, viewports):
        """ Create the different views displayed, according to viewtypes
        viewports (OrderedDict (MicroscopeViewport -> kwargs)): cf init

        To be executed only once, at initialisation.
        """
        # FIXME: Since we have 2 different Views at the moments and probably more
        # on the way, it's probably going to be beneficial to explicitly define
        # them in the viewport data

        views = []
        visible_views = []

        for vp, vkwargs in viewports.items():
            # TODO: automatically set some clever values for missing arguments?
            view = model.MicroscopeView(**vkwargs)

            views.append(view)
            if vp.Shown:
                visible_views.append(view)

            vp.setView(view, self._data_model)

        self._data_model.views.value = views
        self._data_model.visible_views.value = visible_views

    def _create_views_auto(self):
        """ Create the different views displayed, according to the current
        microscope.

        To be executed only once, at initialisation.
        """
        # TODO: just get the sizer that will contain the viewports, and
        # create the viewports according to the stream classes.
        # It could be possible to even delete viewports and create new ones
        # when .views changes.
        # When .visible_views changes, but the viewports in the right order
        # in the sizer.

        assert not self._data_model.views.value  # should still be empty

        # If AnalysisTab for Sparc: SEM/Spec/AR/SEM
        if isinstance(self._data_model, model.AnalysisGUIData):
            assert len(self._viewports) >= 4
            # TODO: should be dependent on the type of acquisition, and so updated every time the
            # .file changes
            if self._main_data_model.role == "sparc":
                logging.info("Creating (static) SPARC viewport layout")
                vpv = collections.OrderedDict([
                    (self._viewports[0],  # focused view
                     {"name": "SEM",
                      "stream_classes": EMStream,
                      }),
                    (self._viewports[1],
                     {"name": "Spectrum",
                      "stream_classes": (OpticalStream, SpectrumStream),
                      }),
                    (self._viewports[2],
                     {"name": "Dummy", # will be immediately swapped for AR
                      "stream_classes": (), # Nothing
                     }),
                    (self._viewports[3],
                     {"name": "SEM CL",
                      "stream_classes":
                            (EMStream, OpticalStream, SpectrumStream),
                      }),
                    # Spectrum viewport is *also* needed for Analysis tab in the
                    # Sparc configuration
                    (self._viewports[4],
                     {"name": "Spectrum plot",
                      "stream_classes": SpectrumStream,
                     }),
                    (self._viewports[5],
                     {"name": "Angle resolved",
                      "stream_classes": ARStream,
                     }),
                ])
            else:
                logging.info("Creating generic static viewport layout")
                vpv = collections.OrderedDict([
                    (self._viewports[0],  # focused view
                     {"name": "SEM",
                      "stream_classes": EMStream,
                     }),
                    (self._viewports[1],
                     {"name": "Optical",
                      "stream_classes": (OpticalStream, SpectrumStream),
                     }),
                    (self._viewports[2],
                     {"name": "Combined 1",
                      "stream_classes":
                          (EMStream, OpticalStream, SpectrumStream),
                     }),
                    (self._viewports[3],
                     {"name": "Combined 2",
                      "stream_classes":
                          (EMStream, OpticalStream, SpectrumStream),
                     }),
                    (self._viewports[4],
                     {"name": "Spectrum plot",
                      "stream_classes": SpectrumStream
                     }),
                    # TODO: this one doesn't make sense
                    # (self._viewports[5],
                    #  {"name": "Overview",
                    #   "stream_classes": SpectrumStream
                    #  }),
                ])
            self._create_views_fixed(vpv)
            return

        # Streams tab (or acquisition tab for the Sparc)

        # If both SEM and Optical are present (= SECOM & DELPHI)
        if (
                self._main_data_model.ebeam and
                self._main_data_model.light and
                len(self._viewports) > 4
        ):
            logging.info("Creating combined SEM/Optical viewport layout")
            vpv = collections.OrderedDict([
                (self._viewports[0],  # focused view
                 {"name": "SEM",
                  "stage": self._main_data_model.stage,
                  "focus":  self._main_data_model.ebeam_focus,
                  "stream_classes": EMStream,
                  }),
                (self._viewports[1],
                 {"name": "Optical",
                  "stage": self._main_data_model.stage,
                  "focus": self._main_data_model.focus,
                  "stream_classes": OpticalStream,
                  }),
                (self._viewports[2],
                 {"name": "Combined 1",
                  "stage": self._main_data_model.stage,
                  "focus": self._main_data_model.focus,
                  "stream_classes": (EMStream, OpticalStream),
                  }),
                (self._viewports[3],
                 {"name": "Combined 2",
                  "stage": self._main_data_model.stage,
                  "focus": self._main_data_model.focus,
                  "stream_classes": (EMStream, OpticalStream),
                  }),
            ])

        # If SEM only: all SEM
        # Works also for the Sparc, as there is no other emitter, and we don't
        # need to display anything else anyway
        elif self._main_data_model.ebeam and not self._main_data_model.light:
            logging.info("Creating SEM only viewport layout")
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(self._viewports):
                vpv[viewport] = {"name": "SEM %d" % (i + 1),
                                 "stage": self._main_data_model.stage,
                                 "focus": self._main_data_model.ebeam_focus,
                                 "stream_classes": EMStream,
                                 }

        # If Optical only: all optical
        elif not self._main_data_model.ebeam and self._main_data_model.light:
            logging.info("Creating Optical only viewport layout")
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(self._viewports):
                vpv[viewport] = {"name": "Optical %d" % (i + 1),
                                 "stage": self._main_data_model.stage,
                                 "focus": self._main_data_model.focus,
                                 "stream_classes": OpticalStream,
                                 }
        else:
            logging.warning("No known microscope configuration, creating %d "
                            "generic views", len(self._viewports))
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(self._viewports):
                vpv[viewport] = {
                    "name": "View %d" % (i + 1),
                     "stage": self._main_data_model.stage,
                     "focus": self._main_data_model.focus,
                     "stream_classes": None, # everything
                }

        # Insert a Chamber viewport into the lower left position if a chamber camera is present
        if self._main_data_model.chamber_ccd and self._main_data_model.chamber_light:
            logging.debug("Inserting Chamber viewport")
            vpv[self._viewports[2]] = {
                "name": "Chamber",
                "stage": None,
                "focus": None,
                "stream_classes": (RGBCameraStream, BrightfieldStream),
            }

        # If there are 5 viewports, we'll assume that the last one is an overview camera stream
        if len(self._viewports) == 5:
            logging.debug("Inserting Overview viewport")
            vpv[self._viewports[4]] = {
                "name": "Overview",
                "stream_classes": (RGBCameraStream, BrightfieldStream),
            }

        self._create_views_fixed(vpv)
        # TODO: if chamber camera: br is just chamber, and it's the focussedView

        # Track the mpp of the SEM view in order to set the magnification
        if (self._main_data_model.ebeam and
            isinstance(self._main_data_model.ebeam.horizontalFoV, VigilantAttributeBase)):
            # => Link the SEM FoV with the mpp of the live SEM viewport
            self._viewports[0].track_view_hfw(self._main_data_model.ebeam.horizontalFoV)

    def _viewport_by_view(self, view):
        """ Return the ViewPort associated with the given view """

        for vp in self._viewports:
            if vp.microscope_view == view:
                return vp
        raise IndexError("No ViewPort found for view %s" % view)

    def views_to_viewports(self, views):
        """ Return a list of viewports corresponding to the given views, in the same order """
        viewports = []
        for view in views:
            for viewport in self._viewports:
                if viewport.microscope_view == view:
                    viewports.append(viewport)
                    break
        return viewports

    def _set_visible_views(self, visible_views):
        """ Set the order of the viewports so it will match the list of visible views

        This method should normally ben called when the visible_views VA in the MicroscopeGUIData
        object gets changed.

        """

        return
        msg = "Resetting views to %s"
        msgdata = [str(v) for v in visible_views] if not visible_views is None else "default"
        logging.debug(msg, msgdata)

        containing_window = self._viewports[0].Parent
        gb = containing_window.GetSizer()

        containing_window.Freeze()

        try:
            # Hide and detach all viewports
            for vp in self._viewports:
                gb.Detach(vp)

            # Collect the viewports associated with the visible views
            visible_viewports = self.views_to_viewports(visible_views)

            for pos, vp in zip([(0, 0), (0, 1), (1, 0), (1, 1)], visible_viewports):
                # if gb.CheckForIntersectionPos(pos, (1, 1)):
                #
                #     i = 2
                #     while gb.CheckForIntersectionPos((i, i), (1, 1)):
                #         i += 1
                #
                #     move_item = gb.FindItemAtPosition(pos)
                #     move_item = move_item.GetWindow()
                #     move_item.size_pos = (i, i)
                #     gb.SetItemPosition(move_item, move_item.size_pos)
                #
                # vp.sizer_pos = pos
                gb.Add(vp, pos, flag=wx.EXPAND)
                vp.Show()

            i = 2
            invisible_viewports = [vp for vp in self._viewports if vp not in visible_viewports]
            for viewport in invisible_viewports:
                gb.Add(viewport, (i, i))
                viewport.Hide()
                i += 1

            self._viewports = visible_viewports + invisible_viewports

        finally:
            containing_window.Thaw()

    # def swap_viewports(self, visible_idx, hidden_idx):
    #     """ Swap the positions of viewports denoted by indices visible_idx and
    #     hidden_idx.
    #
    #     It is assumed that visible_idx points to one of the viewports visible in
    #     a 2x2 display, and that hidden_idx is outside this 2x2 layout and
    #     invisible.
    #     """
    #
    #     # Small shorthand local variable
    #     vp = self._viewports
    #
    #     visible_vp = vp[visible_idx]
    #     hidden_vp = vp[hidden_idx]
    #
    #     logging.debug("swapping visible %s and hidden %s",
    #                   visible_vp,
    #                   hidden_vp)
    #
    #     # Get the sizer of the visible viewport
    #     visible_sizer = visible_vp.GetContainingSizer()
    #     # And the one of the invisible one, which should be the containing sizer
    #     # of visible_sizer
    #     hidden_sizer = parent_sizer = hidden_vp.GetContainingSizer()
    #
    #     # Get the sizer position of the visible viewport, so we can use that
    #     # to insert the other viewport
    #     visible_pos = util.get_sizer_position(visible_vp)
    #     hidden_pos = util.get_sizer_position(hidden_vp)
    #
    #     # Get the sizer item for the visible viewport, so we can access its
    #     # sizer properties like proportion and flags
    #     visible_item = parent_sizer.GetItem(visible_vp, recursive=True)
    #     hidden_item = parent_sizer.GetItem(hidden_vp, recursive=True)
    #
    #     # Move hidden viewport to visible sizer
    #     hidden_sizer.Detach(hidden_vp)
    #     visible_sizer.Insert(
    #         visible_pos,
    #         hidden_vp,
    #         proportion=visible_item.GetProportion(),
    #         flag=visible_item.GetFlag(),
    #         border=visible_item.GetBorder())
    #
    #     # Move viewport 1 to the end of the containing sizer
    #     visible_sizer.Detach(visible_vp)
    #     hidden_sizer.Insert(
    #         hidden_pos,
    #         visible_vp,
    #         proportion=hidden_item.GetProportion(),
    #         flag=hidden_item.GetFlag(),
    #         border=hidden_item.GetBorder())
    #
    #     # Only make the 'hidden_vp' visible when we're in 2x2 view or if it's
    #     # the focussed view in a 1x1 view.
    #     if (
    #         self._data_model.viewLayout.value == model.VIEW_LAYOUT_22 or
    #         self._data_model.focussedView.value == visible_vp.microscope_view
    #     ):
    #         # Flip the visibility
    #         visible_vp.Hide()
    #         logging.debug("Hiding %s", visible_vp)
    #         hidden_vp.Show()
    #         logging.debug("Showing %s", hidden_vp)
    #
    #     # Swap the viewports in the viewport list
    #     vp[visible_idx], vp[hidden_idx] = vp[hidden_idx], vp[visible_idx]
    #
    #     # vp[visible_idx].Parent.Layout()
    #     parent_sizer.Layout()

    def _on_visible_views(self, visible_views):
        """ This method is called when the visible views in the data model change """

        logging.debug("Visible view change detected")
        # Test if all provided views are known
        for view in visible_views:
            if view not in self._data_model.views.value:
                raise ValueError("Unknown view %s!" % view)

        self._set_visible_views(visible_views)

        # Ensure the focused view is always one that is visible
        if self._data_model.focussedView.value not in visible_views:
            self._data_model.focussedView.value = visible_views[0]

    def _on_focussed_view(self, view):
        """ Called when another focussed view changes.

        :param view: (MicroscopeView) The newly focussed view

        """

        logging.debug("Changing focus to view %s", view.name.value)

        containing_window = self._viewports[0].Parent
        containing_window.Freeze()

        try:
            try:
                viewport = [vp for vp in self._viewports if vp.microscope_view == view][0]
            except IndexError:
                logging.exception("No associated ViewPort found for view %s", view)
                raise

            if self._data_model.viewLayout.value == model.VIEW_LAYOUT_ONE:
                self._show_viewport(containing_window.GetSizer(), viewport)
                # Enable/disable ZOOM_FIT tool according to view ability
                if self._toolbar:
                    can_fit = hasattr(viewport.canvas, "fit_view_to_content")
                    self._toolbar.enable_button(tools.TOOL_ZOOM_FIT, can_fit)
            else:
                for vp in self._viewports:
                    vp.SetFocus(False)
                viewport.SetFocus(True)

        finally:
            containing_window.Thaw()

    def _show_viewport(self, sizer, visible_viewport=None):
        """ Show the given viewport or show the first four in the 2x2 grid if none is given

        :param sizer: wx.GridBagSizer
        :param viewport: ViewPort


        ..note:
            This method still handles sizers different from the GridBagSizer for backward
            compatibility reasons. That part of the code should be removed at a future point.

        """

        if isinstance(sizer, wx.GridBagSizer):

            positions = [(0, 0), (0, 1), (1, 0), (1, 1)]
            sizer.SetEmptyCellSize((0, 0))

            pos = sizer.GetItemPosition(visible_viewport) if visible_viewport else None

            for apos in positions:
                row, col = apos
                item = sizer.FindItemAtPosition(apos)

                if pos is not None and apos != pos:
                    if item:
                        win = item.GetWindow()
                        win.Hide()

                    if sizer.IsRowGrowable(row) and pos[0] != row:
                        logging.debug("rem grow row %s", row)
                        sizer.RemoveGrowableRow(row)
                    if sizer.IsColGrowable(col) and pos[1] != col:
                        logging.debug("rem grow col %s", col)
                        sizer.RemoveGrowableCol(col)

                else:
                    if item:
                        win = item.GetWindow()
                        win.Show()

                    # Needed to update the number of rows and columns that the sizer sees
                    sizer.Layout()

                    if not sizer.IsRowGrowable(row):
                        logging.debug("add grow row %s", row)
                        sizer.AddGrowableRow(row)
                    if not sizer.IsColGrowable(col):
                        logging.debug("add grow col %s", col)
                        sizer.AddGrowableCol(col)

        else:
            # Assume legacy sizer construction

            if visible_viewport in self._viewports:
                for viewport in self._viewports:
                    if visible_viewport == viewport:
                        viewport.Show()
                        viewport.SetFocus(True)
                    else:
                        viewport.Hide()
                        viewport.SetFocus(False)
            else:
                for viewport in self._viewports[:4]:
                    viewport.Show()
                for viewport in self._viewports[4:]:
                    viewport.Hide()

        sizer.Layout()

    def _on_view_layout(self, layout):
        """ Called when the view layout of the GUI must be changed

        This method only manipulates ViewPort, since the only thing it needs to
        change is the visibility of ViewPorts.

        """

        containing_window = self._viewports[0].Parent
        containing_window.Freeze()

        try:
            if layout == model.VIEW_LAYOUT_ONE:
                logging.debug("Displaying single viewport")
                for viewport in self._viewports:
                    if viewport.microscope_view == self._data_model.focussedView.value:
                        self._show_viewport(containing_window.GetSizer(), viewport)
                        break
                else:
                    raise ValueError("No foccused view found!")

            elif layout == model.VIEW_LAYOUT_22:
                logging.debug("Displaying 2x2 viewport grid")
                self._show_viewport(containing_window.GetSizer(), None)

            elif layout == model.VIEW_LAYOUT_FULLSCREEN:
                raise NotImplementedError()
            else:
                raise NotImplementedError()

        finally:
            containing_window.Thaw()

    def fitViewToContent(self, unused=None):
        """
        Adapts the scale (MPP) of the current view to the content
        """
        # find the viewport corresponding to the current view
        try:
            vp = self._viewport_by_view(self._data_model.focussedView.value)
            vp.canvas.fit_view_to_content()
        except IndexError:
            logging.error("Failed to find the current viewport")
        except AttributeError:
            logging.error("Requested to fit content for a view not able to")

    def focusViewWithStream(self, stream):
        """
        Ensures that the focussed view is one that displays the given stream.
        If the focussed view fits, it will be picked preferably.
        Note: if the stream is not in any view, nothing will happen.
        stream (Stream): the stream to look for
        """
        fv = self._data_model.focussedView.value

        # first try to pick a view which has the stream visible
        pviews = []
        for v in self._data_model.visible_views.value:
            if stream in v.getStreams():
                pviews.append(v)

        if fv in pviews:
            return  # nothing to do
        if pviews:
            self._data_model.focussedView.value = pviews[0]
            return

        # Try to pick a view which is compatible with the stream
        pviews = []
        for v in self._data_model.visible_views.value:
            if isinstance(stream, v.stream_classes):
                pviews.append(v)

        if fv in pviews:
            return # nothing to do
        if pviews:
            self._data_model.focussedView.value = pviews[0]

        logging.debug("Failed to find any view compatible with stream %s", stream.name.value)


class OverviewController(object):
    """ Small class to connect stage history and overview canvas together """

    def __init__(self, tab_data, overview_canvas):

        self._data_model = tab_data
        self.overview_canvas = overview_canvas

        if tab_data.main.stage:
            tab_data.main.stage.position.subscribe(self.on_stage_pos_change)

    @call_after
    def on_stage_pos_change(self, p_pos):
        """ Store the new position in the overview history when the stage moves """

        p_size = self.calc_stream_size()
        p_center = (p_pos['x'], p_pos['y'])

        # If the 'new' position is identical to the last one in the history, ignore
        # TODO: do not care about the p_size, and override,
        if (self._data_model.stage_history.value and
            (p_center, p_size) == self._data_model.stage_history.value[-1]):
            return

        # If max length reached, remove the oldest
        while len(self._data_model.stage_history.value) > 2000:
            logging.info("Discarding old stage position")
            self._data_model.stage_history.value.pop(0)

        self._data_model.stage_history.value.append((p_center, p_size))

    def calc_stream_size(self):
        """ Calculate the physical size of the current view """

        p_size = None

        # Calculate the stream size if the the ebeam is active
        for strm in self._data_model.streams.value:
            if strm.is_active and isinstance(strm, EMStream):
                image = strm.image.value
                if image is not None:
                    pixel_size = image.metadata.get(MD_PIXEL_SIZE, None)
                    if pixel_size is not None:
                        x, y, _ = image.shape
                        p_size = (x * pixel_size[0], y * pixel_size[1])

                        # TODO: tracking doesn't work, since the  pixel size
                        # might not be updated before `track_hfw_history` is
                        # called

                        # for view in self._tab_data_model.views.value:
                        #     if strm in view.stream_tree:
                        #         view.mpp.subscribe(self.track_hfw_history)
                        #         break

                        break
        return p_size


class ViewSelector(object):
    """ This class controls the view selector buttons and labels associated with them.
    """

    def __init__(self, tab_data, main_frame, buttons, viewports=None):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope
            GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        buttons (OrderedDict : btn -> (viewport, label)): 5 buttons and the
            viewport (for the image) and label (for changing the name)
            associated.
            The first button has no viewport, for the 2x2 view.
        """
        self._data_model = tab_data

        self.buttons = buttons
        self.viewports = viewports

        for btn in self.buttons:
            btn.Bind(wx.EVT_BUTTON, self.OnClick)

        self._subscriptions = {}
        self._subscribe()

        # subscribe to layout and view changes
        self._data_model.viewLayout.subscribe(self._on_layout_change)
        self._data_model.visible_views.subscribe(self._on_visible_views_change)
        self._data_model.focussedView.subscribe(self._on_focus_change, init=True)

    def _subscribe(self):

        # Explicitly unsubscribe the current event handlers
        for btn, (vp, _) in self.buttons.items():
            if btn in self._subscriptions:
                vp.microscope_view.thumbnail.unsubscribe(self._subscriptions[btn]["thumb"])
                vp.microscope_view.name.unsubscribe(self._subscriptions[btn]["label"])
        # Clear the subscriptions
        self._subscriptions = {}

        # subscribe to change of name
        for btn, (vp, lbl) in self.buttons.items():
            if vp is None:  # 2x2 layout
                lbl.SetLabel("All")
                continue

            @call_after
            def on_thumbnail(im, btn=btn):  # save btn in scope
                # import traceback
                # traceback.print_stack()
                btn.set_overlay_image(im)

            vp.microscope_view.thumbnail.subscribe(on_thumbnail, init=True)
            # keep ref of the functions so that they are not dropped
            self._subscriptions[btn] = {"thumb": on_thumbnail}

            # also subscribe for updating the 2x2 button
            vp.microscope_view.thumbnail.subscribe(self._update_22_thumbnail)

            def on_name(name, lbl=lbl):  # save lbl in scope
                lbl.SetLabel(name)

            btn.Freeze()
            vp.microscope_view.name.subscribe(on_name, init=True)
            btn.Parent.Layout()
            btn.Thaw()

            self._subscriptions[btn]["label"] = on_name

    def toggleButtonForView(self, microscope_view):
        """
        Toggle the button which represents the view and untoggle the other ones
        microscope_view (MicroscopeView or None): the view, or None if the first
                                    button (2x2) is to be toggled
        Note: it does _not_ change the view
        """
        for b, (vp, _) in self.buttons.items():
            # 2x2 => vp is None / 1 => vp exists and vp.view is the view
            if ((vp is None and microscope_view is None) or
                (vp and vp.microscope_view == microscope_view)):
                b.SetToggle(True)
            else:
                if vp:
                    logging.debug("untoggling button of view %s",
                                  vp.microscope_view.name.value)
                else:
                    logging.debug("untoggling button of view All")
                b.SetToggle(False)

    @call_after
    def _update_22_thumbnail(self, im):
        """
        Called when any thumbnail is changed, to recompute the 2x2 thumbnail of
        the first button.
        im (unused)
        """
        # Create an image from the 4 thumbnails in a 2x2 layout with small
        # border. The button without a viewport attached is assumed to be the
        # one assigned to the 2x2 view
        btn_all = [b for b, (vp, _) in self.buttons.items() if vp is None][0]
        border_width = 2  # px
        size = max(1, btn_all.thumbnail_size.x), max(1, btn_all.thumbnail_size.y)
        size_sub = (max(1, (size[0] - border_width) // 2),
                    max(1, (size[1] - border_width) // 2))
        # starts with an empty image with the border colour everywhere
        im_22 = wx.EmptyImage(*size, clear=False)
        im_22.SetRGBRect(wx.Rect(0, 0, *size),
                         *btn_all.GetBackgroundColour().Get())

        i = 0

        for vp, _ in self.buttons.values():
            if vp is None:  # 2x2 layout
                continue

            im = vp.microscope_view.thumbnail.value
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
                lt = ((size_sub[0] - sim.Width) // 2,
                      (size_sub[1] - sim.Height) // 2)
                sim.Resize(size_sub, lt)

                # compute placement
                y, x = divmod(i, 2)
                # copy im in the right place
                im_22.Paste(sim,
                            x * (size_sub[0] + border_width),
                            y * (size_sub[1] + border_width))
            else:
                # black image
                # Should never happen
                pass  #sim = wx.EmptyImage(*size_sub)

            i += 1

        # set_overlay_image will rescale to the correct button size
        btn_all.set_overlay_image(im_22)

    def _on_visible_views_change(self, visible_views):
        """ When the visible views change, this method makes sure that each
        button is associated with the correct viewport
        """
        if self.viewports:
            new_viewports = set([vp for vp in self.viewports
                                 if vp.microscope_view in visible_views])
            btn_viewports = set([vp for _, (vp, _) in self.buttons.items()
                                 if vp is not None])

            # Get the new viewport from new_viewports
            new_viewport = new_viewports - btn_viewports
            # Get the missing viewport from btn_viewports
            old_viewport = btn_viewports - new_viewports

            if len(new_viewport) == 1 and len(old_viewport) == 1:
                new_viewport = new_viewport.pop()
                old_viewport = old_viewport.pop()

                for b, (vp, lbl) in self.buttons.items():
                    if vp == old_viewport:
                        # Remove the subscription of the old viewport
                        old_viewport.microscope_view.thumbnail.unsubscribe(
                                                self._subscriptions[b]["thumb"])
                        old_viewport.microscope_view.name.unsubscribe(
                                                self._subscriptions[b]["label"])
                        self.buttons[b] = (new_viewport, lbl)
                        self._subscribe()
                        break
            else:
                raise ValueError("Wrong number of ViewPorts found!")
        else:
            logging.warn("Could not handle view change, viewports unknown!")

    def _on_layout_change(self, unused):
        """ Called when another view is focused, or viewlayout is changed """
        logging.debug("Updating view selector")

        # TODO when changing from 2x2 to a view non focused, it will be called
        # twice in row. => optimise to not do it twice

        if self._data_model.viewLayout.value == model.VIEW_LAYOUT_22:
            # (layout is 2x2) => select the first button
            self.toggleButtonForView(None)
        else:
            # otherwise (layout is 1) => select the right button
            self.toggleButtonForView(self._data_model.focussedView.value)

    _on_focus_change = _on_layout_change

    def OnClick(self, evt):
        """
        Navigation button click event handler

        Show the related view(s) and sets the focus if needed.
        """

        # The event does not need to be 'skipped' because
        # the button will be toggled when the event for value change is
        # received.

        btn = evt.GetEventObject()
        viewport = self.buttons[btn][0]

        if viewport is None:
            # 2x2 button
            # When selecting the overview, the focussed viewport should not
            # change
            self._data_model.viewLayout.value = model.VIEW_LAYOUT_22
        else:
            # It's preferable to change the view before the layout so that
            # if the layout was 2x2 with another view focused, it doesn't first
            # display one big view, and immediately after changes to another
            # view.
            self._data_model.focussedView.value = viewport.microscope_view
            self._data_model.viewLayout.value = model.VIEW_LAYOUT_ONE
