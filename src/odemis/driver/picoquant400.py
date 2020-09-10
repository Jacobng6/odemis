# -*- coding: utf-8 -*-
"""
Created on 14 Apr 2016

@author: Éric Piel

Edited on 3 Sept 2020

@editor: Eric Liu
@editor: Jacob Ng

Copyright © 2016 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http:#www.gnu.org/licenses/.
"""

# Make sure the picoquant driver is updated in the Python odemis package
# sudo cp src/odemis/driver/picoquant400.py /usr/local/lib/python3.6/dist-packages/odemis/driver

from __future__ import division

from concurrent import futures
from ctypes import *
import ctypes
from decorator import decorator
import logging
import math
import numpy
from odemis import model, util
from odemis.model import HwError
from odemis.util import TimeoutError
import queue
import random
import threading
import time


# Based on hhdefin.h
MAXDEVNUM = 8  # max num of USB devices

HHMAXINPCHAN = 8  # max num of physicl input channels

BINSTEPSMAX = 26  # get actual number via HH_GetBaseResolution() !

MAXHISTLEN = 65536  # max number of histogram bins/channels (2 ** 16)
MAXLENCODE = 6  # max length code histo mode

MAXHISTLEN_CONT = 8192  # max number of histogram bins in continuous mode
MAXLENCODE_CONT = 3  # max length code in continuous mode

MAXCONTMODEBUFLEN = 262272  # bytes of buffer needed for HH_GetContModeBlock

TTREADMAX = 131072  # 128K event records can be read in one chunk
TTREADMIN = 128  # 128 records = minimum buffer size that must be provided

MODE_HIST = 0
MODE_T2 = 2
MODE_T3 = 3
MODE_CONT = 8

MEASCTRL_SINGLESHOT_CTC = 0  # default
MEASCTRL_C1_GATED = 1
MEASCTRL_C1_START_CTC_STOP = 2
MEASCTRL_C1_START_C2_STOP = 3
# continuous mode only
MEASCTRL_CONT_C1_GATED = 4
MEASCTRL_CONT_C1_START_CTC_STOP = 5
MEASCTRL_CONT_CTC_RESTART = 6

EDGE_RISING = 1
EDGE_FALLING = 0

FEATURE_DLL = 0x0001
FEATURE_TTTR = 0x0002
FEATURE_MARKERS = 0x0004
FEATURE_LOWRES = 0x0008
FEATURE_TRIGOUT = 0x0010

FLAG_OVERFLOW = 0x0001  # histo mode only
FLAG_FIFOFULL = 0x0002
FLAG_SYNC_LOST = 0x0004
FLAG_REF_LOST = 0x0008
FLAG_SYSERROR = 0x0010  # hardware error, must contact support
FLAG_ACTIVE = 0x0020  # measurement is running

SYNCDIVMIN = 1
SYNCDIVMAX = 16

ZCMIN = 0  # mV
ZCMAX = 40  # mV
DISCRMIN = 0  # mV
DISCRMAX = 1000  # mV

CHANOFFSMIN = -99999  # ps
CHANOFFSMAX = 99999  # ps

OFFSETMIN = 0  # ns
OFFSETMAX = 500000  # ns
ACQTMIN = 1  # ms
ACQTMAX = 360000000  # ms  (100*60*60*1000ms = 100h)

STOPCNTMIN = 1
STOPCNTMAX = 4294967295  # 32 bit is mem max

HOLDOFFMIN = 0  # ns
HOLDOFFMAX = 524296  # ns


class HHError(Exception):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(HHError, self).__init__(errno, strerror, *args, **kwargs)
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return self.args[1]


class HHDLL(CDLL):
    """
    Subclass of CDLL specific to 'HHLib' library, which handles error codes for
    all the functions automatically.

    **Copied from PHDLL. This should be the same according to demo scripts in the HHLib zip file** ~Eric Liu
    """

    def __init__(self):
        # TODO: also support loading the Windows DLL on Windows
        try:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libhh400.so", RTLD_GLOBAL)
            # libhh400.so
            # hhlib.so
        except OSError:
            logging.error("Check that PicoQuant HHLib is correctly installed")
            raise

    def at_errcheck(self, result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention

        **Copied from the PHLib error codes** ~Eric Liu
        """
        # everything returns 0 on correct usage, and < 0 on error
        if result != 0:
            err_str = create_string_buffer(40)
            self._dll.HH_GetErrorString(err_str, result)
            if result in HHDLL.err_code:
                raise HHError(
                    result,
                    "Call to %s failed with error %s (%d): %s"
                    % (
                        str(func.__name__),
                        HHDLL.err_code[result],
                        result,
                        err_str.value,
                    ),
                )
            else:
                raise HHError(
                    result,
                    "Call to %s failed with error %d: %s"
                    % (str(func.__name__), result, err_str.value),
                )
        return result

    def __getitem__(self, name):
        func = super(HHDLL, self).__getitem__(name)
        #         try:
        #         except Exception:
        #             raise AttributeError("Failed to find %s" % (name,))
        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func

    err_code = {
        -1: "HH_ERROR_DEVICE_OPEN_FAIL",
        -2: "HH_ERROR_DEVICE_BUSY",
        -3: "HH_ERROR_DEVICE_HEVENT_FAIL",
        -4: "HH_ERROR_DEVICE_CALLBSET_FAIL",
        -5: "HH_ERROR_DEVICE_BARMAP_FAIL",
        -6: "HH_ERROR_DEVICE_CLOSE_FAIL",
        -7: "HH_ERROR_DEVICE_RESET_FAIL",
        -8: "HH_ERROR_DEVICE_GETVERSION_FAIL",
        -9: "HH_ERROR_DEVICE_VERSION_MISMATCH",
        -10: "HH_ERROR_DEVICE_NOT_OPEN",
        -16: "HH_ERROR_INSTANCE_RUNNING",
        -17: "HH_ERROR_INVALID_ARGUMENT",
        -18: "HH_ERROR_INVALID_MODE",
        -19: "HH_ERROR_INVALID_OPTION",
        -20: "HH_ERROR_INVALID_MEMORY",
        -21: "HH_ERROR_INVALID_RDATA",
        -22: "HH_ERROR_NOT_INITIALIZED",
        -23: "HH_ERROR_NOT_CALIBRATED",
        -24: "HH_ERROR_DMA_FAIL",
        -25: "HH_ERROR_XTDEVICE_FAIL",
        -26: "HH_ERROR_FPGACONF_FAIL",
        -27: "HH_ERROR_IFCONF_FAIL",
        -28: "HH_ERROR_FIFORESET_FAIL",
        -32: "HH_ERROR_USB_GETDRIVERVER_FAIL",
        -33: "HH_ERROR_USB_DRIVERVER_MISMATCH",
        -34: "HH_ERROR_USB_GETIFINFO_FAIL",
        -35: "HH_ERROR_USB_HISPEED_FAIL",
        -36: "HH_ERROR_USB_VCMD_FAIL",
        -37: "HH_ERROR_USB_BULKRD_FAIL",
        -38: "HH_ERROR_USB_RESET_FAIL",
        -40: "HH_ERROR_LANEUP_TIMEOUT",
        -41: "HH_ERROR_DONEALL_TIMEOUT",
        -42: "HH_ERROR_MODACK_TIMEOUT",
        -43: "HH_ERROR_MACTIVE_TIMEOUT",
        -44: "HH_ERROR_MEMCLEAR_FAIL",
        -45: "HH_ERROR_MEMTEST_FAIL",
        -46: "HH_ERROR_CALIB_FAIL",
        -47: "HH_ERROR_REFSEL_FAIL",
        -48: "HH_ERROR_STATUS_FAIL",
        -49: "HH_ERROR_MODNUM_FAIL",
        -50: "HH_ERROR_DIGMUX_FAIL",
        -51: "HH_ERROR_MODMUX_FAIL",
        -52: "HH_ERROR_MODFWPCB_MISMATCH",
        -53: "HH_ERROR_MODFWVER_MISMATCH",
        -54: "HH_ERROR_MODPROPERTY_MISMATCH",
        -55: "HH_ERROR_INVALID_MAGIC",
        -56: "HH_ERROR_INVALID_LENGTH",
        -57: "HH_ERROR_RATE_FAIL",
        -58: "HH_ERROR_MODFWVER_TOO_LOW",
        -59: "HH_ERROR_MODFWVER_TOO_HIGH",
        -64: "HH_ERROR_EEPROM_F01",
        -65: "HH_ERROR_EEPROM_F02",
        -66: "HH_ERROR_EEPROM_F03",
        -67: "HH_ERROR_EEPROM_F04",
        -68: "HH_ERROR_EEPROM_F05",
        -69: "HH_ERROR_EEPROM_F06",
        -70: "HH_ERROR_EEPROM_F07",
        -71: "HH_ERROR_EEPROM_F08",
        -72: "HH_ERROR_EEPROM_F09",
        -73: "HH_ERROR_EEPROM_F10",
        -74: "HH_ERROR_EEPROM_F11",
    }


# Acquisition control messages
GEN_START = "S"  # Start acquisition
GEN_STOP = "E"  # Don't acquire image anymore
GEN_TERM = "T"  # Stop the generator


class TerminationRequested(Exception):
    """
    Generator termination requested.
    """

    pass


@decorator
def autoretry(f, self, *args, **kwargs):
    """
    Decorator to automatically retry a call to a function (once) if it fails
    with a HHError.
    This is to handle the fact that almost every command seems to potentially
    fail with USB_VCMD_FAIL or BULKRD_FAIL randomly (due to issues on the USB
    connection). Just calling the function again deals with it fine.
    """
    try:
        res = f(self, *args, **kwargs)
    except HHError as ex:
        # TODO: GetFlags() + GetHardwareDebugInfo()
        logging.warning("Will try again after: %s", ex)
        time.sleep(0.1)
        res = f(self, *args, **kwargs)
    return res


class HH400(model.Detector):
    """
    Represents a PicoQuant HydraHarp 400.
    """

    def __init__(
        self,
        name,
        role,
        device=None,
        dependencies=None,
        children=None,
        daemon=None,
        sync_level=None,
        sync_zc=None,
        disc_volt=None,
        zero_cross=None,
        shutter_axes=None,
        **kwargs
    ):
        """
        device (None or str): serial number (eg, 1020345) of the device to use
          or None if any device is fine.
        dependencies (dict str -> Component): shutters components (shutter0 through
         shutter7 are valid)
        children (dict str -> kwargs): the names of the detectors (detector0 through
         detector7 are valid)

        # TODO JN: We're using a superconducting nanowire SPD, not an APD?
        sync_level (0 <= float <= 1.0): discriminator voltage for the laser signal (in V)
        sync_zc (0 <= float <= 40 e-3): zero cross voltage for the laser signal (in V)
        disc_volt (8 (0 <= float <= 1.0)): discriminator voltage for the APD 0 through 8 (in V)
        zero_cross (8 (0 <= float <= 40 e-3)): zero cross voltage for the APD 0 through 8 (in V)
        shutter_axes (dict str -> str, value, value): internal child role of the photo-detector ->
          axis name, position when shutter is closed (ie protected), position when opened (receiving light).

          **Copied from PH300 code with PH300 changed to HH400. It seems the HHDLL library is near identical
          to the PHDLL library, and the functions are defined the same with the change of PH -> HH.
          This will have to be tested**
        """
        if dependencies is None:
            dependencies = {}
        if children is None:
            children = {}

        if device == "fake":
            device = None
            self._dll = FakeHHDLL()
        else:
            self._dll = HHDLL()
        self._idx = self._openDevice(device)

        # Lock to be taken to avoid multi-threaded access to the hardware
        self._hw_access = threading.Lock()

        if disc_volt is None:
            disc_volt = [0, 0, 0, 0, 0, 0, 0, 0]
        if zero_cross is None:
            zero_cross = [0, 0, 0, 0, 0, 0, 0, 0]

        super(HH400, self).__init__(
            name, role, daemon=daemon, dependencies=dependencies, **kwargs
        )

        # TODO: metadata for indicating the range? cf WL_LIST?

        # TODO: do we need TTTR mode?
        self.Initialize(MODE_HIST, 0)
        self._swVersion = self.GetLibraryVersion()
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        mod, partnum, ver = self.GetHardwareInfo()
        sn = self.GetSerialNumber()
        self._hwVersion = "%s %s %s (s/n %s)" % (mod, partnum, ver, sn)
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self._numinput = self.GetNumOfInputChannels()

        logging.info("Opened device %d (%s s/n %s)", self._idx, mod, sn)

        self.Calibrate()

        # TODO: needs to be changeable?
        self.SetOffset(0)

        # To pass the raw count of each detector, we create children detectors.
        # It could also go into just separate DataFlow, but then it's difficult
        # to allow using these DataFlows in a standard way.
        self._detectors = {}
        self._shutters = {}
        self._shutter_axes = shutter_axes or {}
        # The PH300 code only accepts detector0 or detector1
        # This HH400 code should work for up to 8 detectors
        for name, ckwargs in children.items():
            num = int(name.split("detector")[1])
            # TODO JN: This name parsing could be more elegant
            if 0 <= num <= (HHMAXINPCHAN - 1):
                if ("shutter%s" % num) in dependencies:
                    shutter_name = "shutter%s" % num
                else:
                    shutter_name = None
                self._detectors[name] = HH400RawDetector(
                    channel=num,
                    parent=self,
                    shutter_name=shutter_name,
                    daemon=daemon,
                    **ckwargs
                )
                self.children.value.add(self._detectors[name])
            else:
                raise ValueError(
                    "Child %s not recognized, should be detector0 .. detector7." % name
                )

        for name, comp in dependencies.items():
            num = int(name.split("shutter")[1])
            if 0 <= num <= (HHMAXINPCHAN - 1):
                if ("shutter%s" % num) not in shutter_axes.keys():
                    raise ValueError("'shutter%s' not found in shutter_axes" % num)
                self._shutters["shutter%s" % num] = comp
            else:
                raise ValueError(
                    "Dependency %s not recognized, should be shutter0 .. shutter7."
                    % name
                )

        # dwellTime = measurement duration
        dt_rng = (ACQTMIN * 1e-3, ACQTMAX * 1e-3)  # s
        self.dwellTime = model.FloatContinuous(1, dt_rng, unit="s")

        # Indicate first dim is time and second dim is (useless) X (in reversed order)
        self._metadata[model.MD_DIMS] = "XT"
        self._shape = (
            MAXHISTLEN,
            1,
            2 ** 16,
        )  # Histogram is 32 bits, but only return 16 bits info

        # TODO JN: Currently uses same settings for all channels
        # self.SetInputChannelEnable(channel, bool)

        for i, (dv, zc) in enumerate(zip(disc_volt, zero_cross)):
            self.SetInputCFD(i, int(dv * 1000), int(zc * 1000))

            self.inputChannelOffset = model.FloatContinuous(
                0,
                (CHANOFFSMIN * 1e-12, CHANOFFSMAX * 1e-12),
                unit="s",
                setter=self._setInputChannelOffset,
            )
            self._setInputChannelOffset(i, self.inputChannelOffset.value)

        self._actuallen = self.SetHistoLen(MAXLENCODE)

        tresbase, binsteps = self.GetBaseResolution()
        # tresbase is the base resolution in ps (default is 1 ps)
        tres = self.GetResolution()
        # tres is the current resolution in ps
        pxd_ch = {(2 ** i) * (tresbase * 1e-12) for i in range(BINSTEPSMAX)}
        # pxd_ch is an array of available resolutions
        self.pixelDuration = model.FloatEnumerated(
            tres * 1e-12, pxd_ch, unit="s", setter=self._setPixelDuration
        )

        res = self._shape[:2]
        self.resolution = model.ResolutionVA(res, (res, res), readonly=True)

        # Sync signal settings
        self.syncDiv = model.IntEnumerated(
            1, choices={1, 2, 4, 8, 16}, unit="", setter=self._setSyncDiv
        )
        self._setSyncDiv(self.syncDiv.value)

        if (sync_level != None) and (sync_zc != None):
            self.SetSyncCFD(int(sync_level * 1000), int(sync_zc * 1000))

        self.syncChannelOffset = model.FloatContinuous(
            0,
            (CHANOFFSMIN * 1e-12, CHANOFFSMAX * 1e-12),
            unit="s",
            setter=self._setSyncChannelOffset,
        )
        self._setSyncChannelOffset(self.syncChannelOffset.value)

        # Make sure the device is synchronised and metadata is updated

        # Wrapper for the dataflow
        self.data = BasicDataFlow(self)
        # Note: Apparently, the hardware supports reading the data, while it's
        # still accumulating (ie, the acquisition is still running).
        # We don't support this feature for now, and if the user needs to see
        # the data building up, it shouldn't be costly (in terms of overhead or
        # noise) to just do multiple small acquisitions and do the accumulation
        # in software.
        # Alternatively, we could provide a second dataflow that sends the data
        # while it's building up.

        # Queue to control the acquisition thread:
        self._genmsg = queue.Queue()
        self._generator = threading.Thread(
            target=self._acquire, name="HydraHarp 400 acquisition thread"
        )
        self._generator.start()

    def _openDevice(self, sn=None):
        """
        sn (None or str): serial number
        return (0 <= int < 8): device ID
        raises: HwError if the device doesn't exist or cannot be opened
        """
        sn_str = create_string_buffer(8)
        for i in range(MAXDEVNUM):
            try:
                self._dll.HH_OpenDevice(i, sn_str)
            except HHError as ex:
                if ex.errno == -1:  # ERROR_DEVICE_OPEN_FAIL == no device with this idx
                    pass
                else:
                    logging.warning("Failure to open device %d: %s", i, ex)
                continue

            if sn is None or sn_str.value == sn:
                return i
            else:
                logging.info("Skipping device %d, with S/N %s", i, sn_str.value)
        else:
            # TODO: if a HHError happened indicate the error in the message
            raise HwError(
                "No HydraHarp400 found, check the device is turned on and connected to the computer"
            )

    def terminate(self):
        model.Detector.terminate(self)
        self.stop_generate()
        if self._generator:
            self._genmsg.put(GEN_TERM)
            self._generator.join(5)
            self._generator = None
        self.CloseDevice()

    # General Functions
    # These functions work independent from any device.

    def GetLibraryVersion(self):
        """
        This is the only function you may call before HH_Initialize. Use it to ensure compatibility of the library with your own application.
        """
        ver_str = create_string_buffer(8)
        self._dll.HH_GetLibraryVersion(ver_str)
        return ver_str.value.decode("latin1")

    # Device Specific Functions
    # All functions below are device specific and require a device index.

    def CloseDevice(self):
        """
        Closes and releases the device for use by other programs.
        """
        self._dll.HH_CloseDevice(self._idx)

    def Initialize(self, mode, refsource):
        """
        This routine must be called before any of the other routines below can be used. Note that some of them depend on the
        measurement mode you select here. See the HydraHarp manual for more information on the measurement modes.
        mode (MODE_*)
            MODE_HIST = 0
            MODE_T2 = 2
            MODE_T3 = 3
            MODE_CONT = 8
        refsource: reference clock to use
            0 = internal
            1 = external
        """
        logging.debug("Initializing device %d", self._idx)
        self._dll.HH_Initialize(self._idx, mode, refsource)

    # Functions for Use on Initialized Devices
    # All functions below can only be used after HH_Initialize was successfully called.

    def GetHardwareInfo(self):
        mod = create_string_buffer(16)
        partnum = create_string_buffer(8)
        ver = create_string_buffer(8)
        self._dll.HH_GetHardwareInfo(self._idx, mod, partnum, ver)
        return (
            mod.value.decode("latin1"),
            partnum.value.decode("latin1"),
            ver.value.decode("latin1"),
        )

    def GetFeatures(self):
        """
        You do not really need this function. It is mainly for integration in PicoQuant system software such as SymPhoTime in order
        to figure out what capabilities the device has. If you want it anyway, use the bit masks from hhdefin.h to evaluate individual
        bits in the pattern.
        """
        features = c_int()
        self._dll.HH_GetFeatures(self._idx, byref(features))
        return features.value

    def GetSerialNumber(self):
        sn_str = create_string_buffer(8)
        self._dll.HH_GetSerialNumber(self._idx, sn_str)
        return sn_str.value.decode("latin1")

    def GetBaseResolution(self):
        """
        Use the value returned in binsteps as maximum value for the HH_SetBinning function.
        return:
            res (0<=float): min duration of a bin in the histogram (in ps)
        Can calculate binning code (0<=int): binsteps = 2**bincode
        """
        res = c_double()
        binsteps = c_int()
        self._dll.HH_GetBaseResolution(self._idx, byref(res), byref(binsteps))
        global MAXBINSTEPS
        MAXBINSTEPS = binsteps.value
        return res.value, binsteps.value

    def GetNumOfInputChannels(self):
        """
        return:
            numinput (int): the number of installed input channels
        """
        numinput = c_int()
        self._dll.HH_GetNumOfInputChannels(self._idx, byref(numinput))
        return numinput.value

    def GetNumOfModules(self):
        """
        This routine is primarily for maintenance and service purposes. It will typically not be needed by end user applications.
        return:
            nummod (int): the number of installed modules
        """
        nummod = c_int()
        self._dll.HH_GetNumOfModules(self._idx, byref(nummod))
        return nummod.value

    def GetModuleInfo(self, modidx):
        """
        This routine is primarily for maintenance and service purposes. It will typically not be needed by end user applications.

        modidx: module index 0..5
        return:
            modelcode (int): the model of the module identified by modidx
            versioncode (int): the version of the module identified by modidx
        """
        modelcode = c_int()
        versioncode = c_int()
        self._dll.HH_GetModuleInfo(
            self._idx, modidx, byref(modelcode), byref(versioncode)
        )
        return modelcode.value, versioncode.value

    def GetModuleIndex(self, channel):
        """
        This routine is primarily for maintenance and service purposes. It will typically not be needed by end user applications. The
        maximum input channel index must correspond to nchannels-1 as obtained through HH_GetNumOfInputChannels().

        channel (int): index of the identifying input channel 0..nchannels-1
        return:
            modidx (int): the index of the module where the input channel given by channel resides.
        """
        modidx = c_int()
        self._dll.HH_GetModuleIndex(self._idx, channel, byref(modidx))
        return modidx.value

    def GetHardwareDebugInfo(self):
        """
        Use this call to obtain debug information for support enquiries if you detect FLAG_SYSERROR or HH_ERROR_STATUS_FAIL.
        """
        debuginfo = create_string_buffer(65536)
        self._dll.HH_GetHardwareDebugInfo(self._idx, debuginfo)
        return debuginfo.value.decode("latin1")

    def Calibrate(self):
        logging.debug("Calibrating device %d", self._idx)
        self._dll.HH_Calibrate(self._idx)

    def SetSyncDiv(self, div):
        """
        The sync divider must be used to keep the effective sync rate at values ≤ 12.5 MHz. It should only be used with sync
        sources of stable period. Using a larger divider than strictly necessary does not do great harm but it may result in slightly lar -
        ger timing jitter. The readings obtained with HH_GetCountRate are internally corrected for the divider setting and deliver the
        external (undivided) rate. The sync divider should not be changed while a measurement is running.

        div (int): sync rate divider
            (1, 2, 4, .., SYNCDIVMAX)
        """
        assert SYNCDIVMIN <= div <= SYNCDIVMAX
        self._dll.HH_SetSyncDiv(self._idx, div)

    def SetSyncCFD(self, level, zc):
        """
        Changes the Constant Fraction Discriminator for the sync signal

        level (int): CFD discriminator level in millivolts
        zc (0<=int): CFD zero cross in millivolts
        """
        assert DISCRMIN <= level <= DISCRMAX
        assert ZCMIN <= zc <= ZCMAX
        self._dll.HH_SetSyncCFD(self._idx, level, zc)

    def SetSyncChannelOffset(self, value):
        """
        value (int): sync timing offset in ps
        """
        assert CHANOFFSMIN <= value <= CHANOFFSMAX
        self._dll.HH_SetSyncChannelOffset(self._idx, value)

    def SetInputCFD(self, channel, level, zc):
        """
        Changes the Constant Fraction Discriminator for the input signal
        The maximum input channel index must correspond to nchannels-1 as obtained through HH_GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1
        level (int): CFD discriminator level in millivolts
        zc (int): CFD zero cross level in millivolts
        """
        assert DISCRMIN <= level <= DISCRMAX
        assert ZCMIN <= zc <= ZCMAX
        self._dll.HH_SetInputCFD(self._idx, channel, level, zc)

    def SetInputChannelOffset(self, channel, value):
        """
        The maximum input channel index must correspond to nchannels-1 as obtained through HH_GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1
        value (int): channel timing offset in ps
        """
        assert CHANOFFSMIN <= value <= CHANOFFSMAX
        self._dll.HH_SetInputChannelOffset(self._idx, channel, value)

    def SetInputChannelEnable(self, channel, enable):
        """
        The maximum channel index must correspond to nchannels-1 as obtained through HH_GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1
        enable (bool): desired enable state of the input channel
            False (0) = disabled
            True (1) = enabled
        """
        enable = 1 if enable else 0
        self._dll.HH_SetInputChannelEnable(self._idx, channel, enable)

    def SetStopOverflow(self, stop, stopcount):
        """
        This setting determines if a measurement run will stop if any channel reaches the maximum set by stopcount. If
        stop is False (0) the measurement will continue but counts above STOPCNTMAX in any bin will be clipped.

        stop (bool): True if it should stop on reaching the given count
        stopcount (0<int<=2**16-1): count at which to stop
        """
        assert STOPCNTMIN <= stopcount <= STOPCNTMAX
        stop_ovfl = 1 if stop else 0
        self._dll.HH_SetStopOverflow(self._idx, stop_ovfl, stopcount)

    def SetBinning(self, bincode):
        """
        bincode (0<=int): binning code. Binsteps = 2**bodeinc (e.g., bc = 0 for binsteps = 1, bc = 3 for binsteps = 8)

        binning: measurement binning code
            minimum = 0 (smallest, i.e. base resolution)
            maximum = (MAXBINSTEPS-1) (largest)

        the binning code corresponds to repeated doubling, i.e.
            0 = 1x base resolution,
            1 = 2x base resolution,
            2 = 4x base resolution,
            3 = 8x base resolution, and so on.
        """
        assert 0 <= bincode <= BINSTEPSMAX - 1
        self._dll.HH_SetBinning(self._idx, bincode)

    def SetOffset(self, offset):
        """
        This offset must not be confused with the input offsets in each channel that act like a cable delay. In contrast, the offset here
        is subtracted from each start–stop measurement before it is used to either address the histogram channel to be incremented
        (in histogramming mode) or to be stored in a T3 mode record. The offset therefore has no effect in T2 mode and it has no effect
        on the relative timing of laser pulses and photon events. It merely shifts the region of interest where time difference data
        is to be collected. This can be useful e.g. in time-of-flight measurements where only a small time span at the far end of the
        range is of interest.

        offset (int): histogram time offset in ns
        """
        assert OFFSETMIN <= offset <= OFFSETMAX
        self._dll.HH_SetOffset(self._idx, offset)

    def SetHistoLen(self, lencode):
        """
        lencode (int): histogram length code
            minimum = 0
            maximum = MAXLENCODE (default)
        return:
            actuallen (int): the current length (time bin count) of histograms
            calculated as 1024 * (2^lencode)
        """
        actuallen = c_int()
        assert 0 <= lencode <= MAXLENCODE
        self._dll.HH_SetHistoLen(self._idx, lencode, byref(actuallen))
        return actuallen.value

    @autoretry
    def ClearHistMem(self, block):
        """
        block (0 <= int): block number to clear
        """
        assert 0 <= block
        self._dll.HH_ClearHistMem(self._idx, block)

    def SetMeasControl(self, meascontrol, startedge, stopedge):
        """
        meascontrol (int): measurement control code
            0 = MEASCTRL_SINGLESHOT_CTC
            1 = MEASCTRL_C1_GATED
            2 = MEASCTRL_C1_START_CTC_STOP
            3 = MEASCTRL_C1_START_C2_STOP
            4 = MEASCTRL_CONT_C1_GATED
            5 = MEASCTRL_CONT_C1_START_CTC_STOP
            6 = MEASCTRL_CONT_CTC_RESTART
        startedge (int): edge selection code
            0 = falling
            1 = rising
        stopedge (int): edge selection code
            0 = falling
            1 = rising
        """
        assert meascontrol in {0, 6}
        assert startedge in {0, 1}
        assert stopedge in {0, 1}
        self._dll.HH_SetMeasControl(self._idx, meascontrol, startedge, stopedge)

    @autoretry
    def StartMeas(self, tacq):
        """
        tacq (0<int): acquisition time in milliseconds
        """
        assert ACQTMIN <= tacq <= ACQTMAX
        self._dll.HH_StartMeas(self._idx, tacq)

    @autoretry
    def StopMeas(self):
        """
        Can also be used before the acquisition time expires.
        """
        self._dll.HH_StopMeas(self._idx)

    @autoretry
    def CTCStatus(self):
        """
        Reports the status of the acquisition (CTC)

        return:
            ctcstatus (bool): True if the acquisition time has ended
        """
        ctcstatus = c_int()
        self._dll.HH_CTCStatus(self._idx, byref(ctcstatus))
        return ctcstatus.value > 0

    @autoretry
    def GetHistogram(self, channel, clear=False):
        """
        The histogram buffer size actuallen must correspond to the value obtained through HH_SetHistoLen().
        The maximum input channel index must correspond to nchannels-1 as obtained through HH_GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1
        clear (bool): denotes the action upon completing the reading process
            False (0) = keeps the histogram in the acquisition buffer
            True (1) = clears the acquisition buffer
        return:
            chcount (unsigned int): pointer to an array of at least actuallen double words (32bit)
            where the histogram data can be stored
        """
        # TODO JN
        clear_int = 1 if clear else 0
        buf = numpy.empty((1, self._actuallen), dtype=numpy.uint32)
        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        self._dll.HH_GetHistogram(self._idx, buf_ct, channel, clear_int)
        return buf

    def GetResolution(self):
        """
        Current time resolution, taking into account the binning

        return:
            value (0<=float): duration of a bin (in ps)
        """
        res = c_double()
        self._dll.HH_GetResolution(self._idx, byref(res))
        return res.value

    def GetSyncRate(self):
        """
        return:
            syncrate (int): the current sync rate
        """
        syncrate = c_int()
        self._dll.HH_GetSyncRate(self._idx, byref(syncrate))
        return syncrate.value

    @autoretry
    def GetCountRate(self, channel):
        """
        Allow at least 100 ms after HH_Initialize or HH_SetSyncDivider to get a stable rate meter reading.
        Similarly, wait at least 100 ms to get a new reading. This is the gate time of the counters.
        The maximum input channel index must correspond to nchannels-1 as obtained through HH_GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1

        return:
            rate (0<=int): counts/s
        """
        cntrate = c_int()
        with self._hw_access:
            self._dll.HH_GetCountRate(self._idx, channel, byref(cntrate))
        return cntrate.value

    def GetFlags(self):
        """
        Use the predefined bit mask values in hhdefin.h (e.g. FLAG_OVERFLOW) to extract individual bits through a bitwise AND.

        return:
            flags (int): current status flags (a bit pattern)
        """
        flags = c_int()
        self._dll.HH_GetFlags(self._idx, byref(flags))
        return flags.value

    def GetElapsedMeasTime(self):
        """
        This can be used while a measurement is running but also after it has stopped.

        return:
            elapsed (0<=float): time since the measurement started (in s)
        """
        elapsed = c_double()  # in ms
        self._dll.HH_GetElapsedMeasTime(self._idx, byref(elapsed))
        return elapsed.value * 1e-3

    def GetWarnings(self):
        """
        You must call HH_GetCoutRate and HH_GetCoutRate for all channels prior to this call.

        return:
            warnings (int): bitwise encoded (see phdefin.h)
        """
        warnings = c_int()
        self._dll.HH_GetWarnings(self._idx, byref(warnings))
        return warnings.value

    def GetWarningsText(self, warnings):
        """
        warnings (int): integer bitfield obtained from HH_GetWarnings

        return:
            text (str)
        """
        text = create_string_buffer(16384)
        self._dll.HH_GetWarningsText(self._idx, text, warnings)
        return text.value.decode("latin1")

    def GetSyncPeriod(self, period):
        """
        This call only gives meaningful results while a measurement is running and after two sync periods have elapsed.
        The return value is undefined in all other cases. Accuracy is determined by single shot jitter and crystal tolerances.

        return:
            period (float): the sync period in ps
        """
        period = c_double()
        self._dll.HH_GetSyncPeriod(self._idx, byref(period))
        return period.value

        # Special Functions for TTTR Mode

    def ReadFiFo(self, count):
        """
        Warning, the device must be initialised in a special mode (T2 or T3)
        count (int < TTREADMAX): number of values to read
        return ndarray of uint32: can be shorter than count, even 0 length.
          each unint32 is a 'record'. The interpretation of the record depends
          on the mode.

        buffer: pointer to an array of count double words (32bit)
        where the TTTR data can be stored
        must provide space for at least 128 records
        count: number of TTTR records to be fetched
        must be a multiple of 128, max = size of buffer,
        absolute max = TTREADMAX
        nactual: pointer to an integer
        returns the number of TTTR records received

        CPU time during wait for completion will be yielded to other processes / threads.
        Buffer must not be accessed until the function returns.
        USB 2.0 devices: Call will return after a timeout period of ~10 ms, if not all data could be fetched.
        USB 3.0 devices: The transfer operates in chunks of 128 records. The call will return after the number of complete chunks of
        128 records that were available in the FiFo and can fit in the buffer have been transferred. Remainders smaller than the
        chunk size are only transferred when no complete chunks are in the FiFo.
        """
        assert 0 < count < TTREADMAX
        buf = numpy.empty((count,), dtype=numpy.uint32)
        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        nactual = c_int()
        self._dll.HH_ReadFiFo(self._idx, buf_ct, count, byref(nactual))
        # only return the values which were read
        # TODO: if it's really smaller (eg, 0), copy the data to avoid holding all the mem
        return buf[: nactual.value]

    def SetMarkerEdges(self, me0, me1, me2, me3):
        """
        me<n> (int): active edge of marker signal <n>,
            0 = falling
            1 = rising
        """
        self.HH_SetMarkerEdges(self._idx, me0, me1, me2, me3)

    def SetMarkerEnable(self, en0, en1, en2, en3):
        """
        en<n> (int): desired enable state of marker signal <n>,
            0 = disabled,
            1 = enabled
        """
        self._dll.HH_SetMarkerEnable(self._idx, en0, en1, en2, en3)

    def SetMarkerHoldoffTime(self, holdofftime):
        """
        This setting is not normally required but it can be used to deal with glitches on the marker lines. Markers following a previous
        marker within the hold-off time will be suppressed. Note that the actual hold-off time is only approximated to about ±8ns.

        holdofftime (int) hold-off time in ns (0..HOLDOFFMAX)
        """
        assert 0 <= holdofftime <= HOLDOFFMAX
        self._dll.HH_SetMarkerHoldoffTime(self._idx, holdofftime)

    # Special Functions for Continuous Mode

    def GetContModeBlock(self):
        """
        Required buffer size and data structure depends on the number of active input channels and histogram bins.
        Allocate MAXCONTMODEBUFLEN bytes to be on the safe side. The data structure changed slightly in v.3.0 to provide information
        on the number of active input channels and histogram bins. This simplifies accessing the data. See the C demo
        code.

        returns the number of bytes received
        """
        buffer = create_string_buffer(MAXCONTMODEBUFLEN)
        nbytesreceived = c_int()
        self._dll.HH_GetContModeBlock(self._idx, buffer, byref(nbytesreceived))
        return buffer.value.decode("latin"), nbytesreceived.value

    def _setPixelDuration(self, pxd):
        # TODO: delay until the end of an acquisition

        tresbase, binsteps = self.GetBaseResolution()
        b = int(pxd * 1e12 / tresbase)
        # Only accept a power of 2
        bincode = int(math.log(b, 2))
        self.SetBinning(bincode)

        # Update metadata
        pxd = self.GetResolution() * 1e-12  # ps -> s
        tl = numpy.arange(self._shape[0]) * pxd + self.syncChannelOffset.value
        self._metadata[model.MD_TIME_LIST] = tl
        return pxd

    def _setSyncDiv(self, div):
        self.SetSyncDiv(div)
        return div

    def _setSyncChannelOffset(self, offset):
        offset_ps = int(offset * 1e12)
        self.SetSyncChannelOffset(offset_ps)
        offset = offset_ps * 1e-12  # convert the round-down in ps back to s
        tl = numpy.arange(self._shape[0]) * self.pixelDuration.value + offset
        self._metadata[model.MD_TIME_LIST] = tl
        return offset

    def _setInputChannelOffset(self, channel, offset):
        offset_ps = int(offset * 1e12)
        self.SetInputChannelOffset(channel, offset_ps)
        offset = offset_ps * 1e-12  # convert the round-down in ps back to s

        # TODO JN: What is this doing? Just updates metadata
        # tl = numpy.arange(self._shape[0]) * self.pixelDuration.value + offset
        # self._metadata[model.MD_TIME_LIST] = tl
        return offset

    # Acquisition methods
    def start_generate(self):
        self._genmsg.put(GEN_START)
        if not self._generator.is_alive():
            logging.warning("Restarting acquisition thread")
            self._generator = threading.Thread(
                target=self._acquire, name="HydraHarp400 acquisition thread"
            )
            self._generator.start()

    def stop_generate(self):
        self._genmsg.put(GEN_STOP)

    def _get_acq_msg(self, **kwargs):
        """
        Read one message from the acquisition queue
        return (str): message
        raises queue.Empty: if no message on the queue
        """
        msg = self._genmsg.get(**kwargs)
        if msg not in (GEN_START, GEN_STOP, GEN_TERM):
            logging.warning("Acq received unexpected message %s", msg)
        else:
            logging.debug("Acq received message %s", msg)
        return msg

    def _acq_wait_start(self):
        """
        Blocks until the acquisition should start.
        Note: it expects that the acquisition is stopped.
        raise TerminationRequested: if a terminate message was received
        """
        while True:
            msg = self._get_acq_msg(block=True)
            if msg == GEN_TERM:
                raise TerminationRequested()

            # Check if there are already more messages on the queue
            try:
                msg = self._get_acq_msg(block=False)
                if msg == GEN_TERM:
                    raise TerminationRequested()
            except queue.Empty:
                pass

            if msg == GEN_START:
                return

            # Duplicate Stop or trigger
            logging.debug("Skipped message %s as acquisition is stopped", msg)

    def _acq_should_stop(self, timeout=None):
        """
        Indicate whether the acquisition should now stop or can keep running.
        Note: it expects that the acquisition is running.
        timeout (0<float or None): how long to wait to check (if None, don't wait)
        return (bool): True if needs to stop, False if can continue
        raise TerminationRequested: if a terminate message was received
        """
        try:
            if timeout is None:
                msg = self._get_acq_msg(block=False)
            else:
                msg = self._get_acq_msg(timeout=timeout)
            if msg == GEN_STOP:
                return True
            elif msg == GEN_TERM:
                raise TerminationRequested()
        except queue.Empty:
            pass
        return False

    def _acq_wait_data(self, exp_tend, timeout=0):
        """
        Block until a data is received, or a stop message.
        Note: it expects that the acquisition is running.
        exp_tend (float): expected time the acquisition message is received
        timeout (0<=float): how long to wait to check (use 0 to not wait)
        return (bool): True if needs to stop, False if data is ready
        raise TerminationRequested: if a terminate message was received
        """
        now = time.time()
        ttimeout = now + timeout
        while now <= ttimeout:
            twait = max(1e-3, (exp_tend - now) / 2)
            logging.debug("Waiting for %g s", twait)
            if self._acq_should_stop(twait):
                return True

            # Is the data ready?
            if self.CTCStatus():
                logging.debug("Acq complete")
                return False
            now = time.time()

        raise TimeoutError("Acquisition timeout after %g s")

    def _toggle_shutters(self, shutters, open):
        """
        Open/ close protection shutters.
        shutters (list of string): the names of the shutters
        open (boolean): True if shutters should open, False if they should close
        """
        fs = []
        for sn in shutters:
            axes = {}
            logging.debug("Setting shutter %s to %s.", sn, open)
            ax_name, closed_pos, open_pos = self._shutter_axes[sn]
            shutter = self._shutters[sn]
            if open:
                axes[ax_name] = open_pos
            else:
                axes[ax_name] = closed_pos
            try:
                fs.append(shutter.moveAbs(axes))
            except Exception as e:
                logging.error("Toggling shutters failed with exception %s", e)
        for f in fs:
            f.result()

    def _acquire(self):
        """
        Acquisition thread
        Managed via the .genmsg Queue
        """
        # TODO: support synchronized acquisition, so that it's possible to acquire
        #   one image at a time, without opening/closing the shutters in-between.
        #   See avantes for an example.
        try:
            while True:
                # Wait until we have a start (or terminate) message
                self._acq_wait_start()

                # Open protection shutters
                self._toggle_shutters(self._shutters.keys(), True)

                # The demo code prints these out
                self.GetSyncRate()
                for i in range(0, self._numinput):
                    self.GetCountRate(i)
                # Check for warnings after getting count rates
                warnings = self.GetWarnings()
                if warnings != 0:
                    print(self.GetWarningsText(warnings))

                # Stop measurement if any bin fills up
                self.SetStopOverflow(True, STOPCNTMAX)

                # Keep acquiring
                while True:
                    tacq = self.dwellTime.value
                    tstart = time.time()

                    # TODO: only allow to update the setting here (not during acq)
                    md = self._metadata.copy()
                    md[model.MD_ACQ_DATE] = tstart
                    md[model.MD_DWELL_TIME] = tacq

                    # check if any message received before starting again
                    if self._acq_should_stop():
                        break

                    logging.debug("Starting new acquisition")
                    self.ClearHistMem(0)
                    self.StartMeas(int(tacq * 1e3))

                    # Wait for the acquisition to be done or until a stop or
                    # terminate message comes
                    try:
                        if self._acq_wait_data(tstart + tacq, timeout=tacq * 3 + 1):
                            # Stop message received
                            break
                        logging.debug("Acq complete")
                    except TimeoutError as ex:
                        logging.error(ex)
                        # TODO: try to reset the hardware?
                        continue
                    finally:
                        # Must always be called, whether the measurement finished or not
                        self.StopMeas()

                    # Read data and pass it
                    data = self.GetHistogram(0)
                    da = model.DataArray(data, md)
                    self.data.notify(da)
                    # TODO JN: support multiple channels
                    # data = []
                    # for i in range(0, self._numinput):
                    #     data.append( self.GetHistogram(i, 0) )
                    #     da = model.DataArray(data, md)
                    #     self.data.notify(da)

                logging.debug("Acquisition stopped")
                self._toggle_shutters(self._shutters.keys(), False)

                flags = self.GetFlags()
                if flags & FLAG_OVERFLOW > 0:
                    logging.debug("Overflow")

        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception:
            logging.exception("Failure in acquisition thread")
        else:  # code unreachable
            logging.error("Acquisition thread ended without exception")
        finally:
            self._toggle_shutters(self._shutters.keys(), False)

        logging.debug("Acquisition thread ended")

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, kwargs (device)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        dll = HHDLL()
        sn_str = create_string_buffer(8)
        dev = []
        for i in range(MAXDEVNUM):
            try:
                dll.HH_OpenDevice(i, sn_str)
            except HHError as ex:
                if ex.errno == -1:  # ERROR_DEVICE_OPEN_FAIL == no device with this idx
                    continue
                else:
                    logging.warning("Failure to open existing device %d: %s", i, ex)
                    # Still add it

            dev.append(("HydraHarp 400", {"device": sn_str.value}))

        return dev


class HH400RawDetector(model.Detector):
    """
    Represents a raw detector (eg, APD) accessed via PicoQuant HydraHarp 400.
    Cannot be directly created. It must be done via HH400 child.
    **Copied from PH300RawDetector with PH300 changed to HH400** ~Eric Liu
    """

    def __init__(self, name, role, channel, parent, shutter_name=None, **kwargs):
        """
        channel (int): input channel index 0..nchannels-1
        """
        self._channel = channel
        super(HH400RawDetector, self).__init__(name, role, parent=parent, **kwargs)

        self._shape = (2 ** 31,)  # only one point, with (32 bits) int size
        self.data = BasicDataFlow(self)

        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self._generator = None

        self._shutter_name = shutter_name

    def terminate(self):
        self.stop_generate()

    def start_generate(self):
        if self._generator is not None:
            logging.warning("Generator already running")
            return
        # In principle, toggling the shutter values here might interfere with the
        # shutter values set by the acquisition. However, in odemis, we never
        # do both things at the same time, so it is not an issue.
        if self._shutter_name:
            self.parent._toggle_shutters([self._shutter_name], True)
        self._generator = util.RepeatingTimer(
            100e-3, self._generate, "Raw detector reading"  # Fixed rate at 100ms
        )
        self._generator.start()

    def stop_generate(self):
        if self._generator is not None:
            if self._shutter_name:
                self.parent._toggle_shutters([self._shutter_name], False)
            self._generator.cancel()
            self._generator = None

    def _generate(self):
        """
        Read the current detector rate and make it a data
        """
        # update metadata
        metadata = self._metadata.copy()
        metadata[model.MD_ACQ_DATE] = time.time()
        metadata[model.MD_DWELL_TIME] = 100e-3  # s

        # Read data and make it a DataArray
        d = self.parent.GetCountRate(self._channel)
        nd = numpy.array([d], dtype=numpy.int)
        img = model.DataArray(nd, metadata)

        # send the new image (if anyone is interested)
        self.data.notify(img)


class BasicDataFlow(model.DataFlow):
    def __init__(self, detector):
        """
        detector (HH400): the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = detector

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        self._detector.start_generate()

    def stop_generate(self):
        self._detector.stop_generate()


# Only for testing/simulation purpose
# Very rough version that is just enough so that if the wrapper behaves correctly,
# it returns the expected values.


def _deref(p, typep):
    """
    p (byref object)
    typep (c_type): type of pointer
    Use .value to change the value of the object
    """
    # This is using internal ctypes attributes, that might change in later
    # versions. Ugly!
    # Another possibility would be to redefine byref by identity function:
    # byref= lambda x: x
    # and then dereferencing would be also identity function.
    return typep.from_address(addressof(p._obj))


def _val(obj):
    """
    return the value contained in the object. Needed because ctype automatically
    converts the arguments to c_types if they are not already c_type
    obj (c_type or python object)
    """
    if isinstance(obj, ctypes._SimpleCData):
        return obj.value
    else:
        return obj


class FakeHHDLL(object):
    """
    Fake HHDLL. It basically simulates one connected device, which returns
    reasonable values.
    **Copied from FakePHDLL, with PH300 changed to HH400** ~Eric Liu
    """

    # TODO JN: When do I have to use _deref and _val and pointers?

    def __init__(self):
        self._idx = 0
        self._mode = None
        self._refsource = 0
        self._sn = b"10234567"
        self._base_res = 4  # ps
        self._bincode = 0  # binning power
        self._numinput = 2
        self._inputLevel = []
        self._inputZc = []
        self._inputOffset = []
        self._syncRate = 0
        self._syncPeriod = 0

        # start/ (expected) end time of the current acquisition (or None if not started)
        self._acq_start = None
        self._acq_end = None
        self._last_acq_dur = None  # s

    # General Functions
    # These functions work independent from any device.

    def HH_GetErrorString(self, errstring, errcode):
        errstring.value = b"Fake error string"

    def HH_GetLibraryVersion(self, ver_str):
        ver_str.value = b"3.00"

    # Device Specific Functions
    # All functions below are device specific and require a device index.

    def HH_OpenDevice(self, i, sn_str):
        if i == self._idx:
            sn_str.value = self._sn
        else:
            raise HHError(-1, HHDLL.err_code[-1])  # ERROR_DEVICE_OPEN_FAIL

    def HH_CloseDevice(self, i):
        self._mode = None

    def HH_Initialize(self, i, mode, refsource):
        self._mode = mode
        self._refsource = refsource

    # Functions for Use on Initialized Devices
    # All functions below can only be used after HH_Initialize was successfully called.

    def HH_GetHardwareInfo(self, i, mod, partnum, ver):
        mod.value = b"FakeHarp 400"
        partnum.value = b"12345"
        ver.value = b"2.0"

    def HH_GetFeatures(self, i, features):
        # Not needed?
        pass

    def HH_GetSerialNumber(self, i, sn_str):
        sn_str.value = self._sn

    def HH_GetBaseResolution(self, i, p_resolution, p_binsteps):
        resolution = _deref(p_resolution, c_double)
        binsteps = _deref(p_binsteps, c_int)
        resolution.value = self._base_res
        binsteps.value = self._bincode

    def HH_GetNumOfInputChannels(self, i, p_nchannels):
        nchannels = _deref(p_nchannels, c_int)
        nchannels.value = self._numinput

    def HH_GetNumOfModules(self, i, nummod):
        # Not needed?
        pass

    def HH_GetModuleInfo(self, i, modidx, modelcode, versioncode):
        # Not needed?
        pass

    def HH_GetModuleIndex(self, i, channel, modidx):
        # Not needed?
        pass

    def HH_GetHardwareDebugInfo(self, i, debuginfo):
        debuginfo.value = b"Fake hardware debug info"
        # Not needed?

    def HH_Calibrate(self, i):
        pass

    def HH_SetSyncDiv(self, i, div):
        self._syncdiv = _val(div)

    def HH_SetSyncCFD(self, i, level, zc):
        self._syncLevel = _val(level)
        self._syncZc = _val(zc)

    def HH_SetSyncChannelOffset(self, i, value):
        self._syncChannelOffset = _val(value)

    def HH_SetInputCFD(self, i, channel, level, zc):
        self._inputLevel.append(_val(level))
        self._inputZc.append(_val(zc))

    def HH_SetInputChannelOffset(self, i, channel, value):
        self._inputOffset.append(_val(value))

    def HH_SetInputChannelEnable(self, i, channel, enable):
        # Not needed?
        pass

    def HH_SetStopOverflow(self, i, stop_ovfl, stopcount):
        self._stopovfl = _val(stop_ovfl)
        self._stopcount = _val(stopcount)

    def HH_SetBinning(self, i, bincode):
        self._bincode = _val(bincode)

    def HH_SetOffset(self, i, offset):
        self._offset = _val(offset)

    def HH_SetHistoLen(self, i, lencode, p_actuallen):
        self._lencode = _val(lencode)
        actuallen = _deref(p_actuallen, c_int)
        actuallen.value = MAXHISTLEN

    def HH_ClearHistMem(self, i, block):
        self._last_acq_dur = None

    def HH_SetMeasControl(self, i, meascontrol, startedge, stopedge):
        # Not needed?
        pass

    def HH_StartMeas(self, i, tacq):
        if self._acq_start is not None:
            raise HHError(-16, HHDLL.err_code[-16])
        self._acq_start = time.time()
        self._acq_end = self._acq_start + _val(tacq) * 1e-3

    def HH_StopMeas(self, i):
        if self._acq_start is not None:
            self._last_acq_dur = self._acq_end - self._acq_start
        self._acq_start = None
        self._acq_end = None

    def HH_CTCStatus(self, i, p_ctcstatus):
        ctcstatus = _deref(p_ctcstatus, c_int)
        if self._acq_end > time.time():
            ctcstatus.value = 0  # 0 if still running
        #          DEBUG
        #          if random.randint(0, 10) == 0:
        #              raise HHError(-37, HHDLL.err_code[-37])  # bad luck
        else:
            ctcstatus.value = 1

    def HH_GetHistogram(self, i, p_chcount, channel, clear):
        p = cast(p_chcount, POINTER(c_uint32))
        ndbuffer = numpy.ctypeslib.as_array(p, (MAXHISTLEN,))

        # make the max value dependent on the acquisition time
        if self._last_acq_dur is None:
            logging.warning("Simulator detected reading empty histogram")
            maxval = 0
        else:
            dur = min(10, self._last_acq_dur)
            maxval = max(1, int(2 ** 16 * (dur / 10)))  # 10 s -> full scale

        # Old numpy doesn't support dtype argument for randint
        ndbuffer[...] = numpy.random.randint(0, maxval + 1, MAXHISTLEN).astype(
            numpy.uint32
        )

    def HH_GetResolution(self, i, p_resolution):
        resolution = _deref(p_resolution, c_double)
        resolution.value = self._base_res * (2 ** self._bincode)

    def HH_GetSyncRate(self, i, p_syncrate):
        syncrate = _deref(p_syncrate, c_int)
        syncrate.value = self._syncRate

    def HH_GetCountRate(self, i, channel, p_rate):
        rate = _deref(p_rate, c_int)
        rate.value = random.randint(0, 5000)

    def HH_GetFlags(self, i, p_flags):
        flags = _deref(p_flags, c_int)
        flags.value = 1

    def HH_GetElapsedMeasTime(self, i, p_elapsed):
        elapsed = _deref(p_elapsed, c_double)
        if self._acq_start is None:
            elapsed.value = 0
        else:
            elapsed.value = min(self._acq_end, time.time()) - self._acq_start

    def HH_GetWarnings(self, i, p_warnings):
        warnings = _deref(p_warnings, c_int)
        warnings.value = 1

    def HH_GetWarningsText(self, i, text, warnings):
        text.value = b"Fake warning text"

    def HH_GetSyncPeriod(self, i, p_period):
        period = _deref(p_period, c_double)
        period.value = self._syncPeriod

    # Special Functions for TTTR Mode

    def HH_ReadFiFo(self, i, buffer, count, nactual):
        # Not needed?
        pass

    def HH_SetMarkerEdges(self, i, me0, me1, me2, me3):
        # Not needed?
        pass

    def HH_SetMarkerEnable(self, i, en0, en1, en2, en3):
        # Not needed?
        pass

    def HH_SetMarkerHoldoffTime(self, i, holdofftime):
        # Not needed?
        pass

    # Special Functions for Continuous Mode

    def HH_GetContModeBlock(self, i, buffer, nbytesreceived):
        # Not needed?
        pass
