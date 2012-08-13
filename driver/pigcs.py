# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
import __version__
import glob
import logging
import model
import os
import serial
import sys
import time

"""
Driver to handle PI's piezo motor controllers that follow the 'GCS' (General
Command Set). In particular it handle the PI E-861 controller. Information can
be found the manual E-861_User_PZ205E121.pdf (p.107). See PIRedStone for the PI C-170.

In a daisy-chain, connected via USB or via RS-232, there must be one
controller with address 1 (=DIP 1111).

The controller support closed-loop mode (i.e., absolute positioning) but only
if it is associated to a sensor (not software detectable). It can also work in 
open-loop mode but to avoid damaging the hardware (which is moved by this
actuator): 
* Do not switch servo on (SVO command)
* Do not send commands for closed-loop motion, like MOV or MVR
* Do not send the open-loop commands OMA and OMR, since they
   use a sensor, too

The controller accepts several baud rate. We choose 38400 (DIP=01) as it's fast
and it seems accepted by every version. Other settings are 8 data, 1 stop, 
no parity.


In open-loop, the controller has 2 ways to move the actuators:
 * Nanostepping: high-speed, and long distance
      1 step ~ 10 μm without load (less with load)
 * Analog: very precise, but moves maximum ~5μm
     "40 volts corresponds to a motion of approx. 3.3μm"
     "20 volts corresponds to a motion of approx. 1μm"

In closed-loop, it's all automagical.

The recommended maximum step frequency is 800 Hz. 

The architecture of the driver relies on three main classes:
 * Controller: represent one controller with one or several axes (E-861 has only one)
 * Bus: represent the whole group of controllers daisy-chained from the same
    serial port. It's also the Actuator interface for the rest of Odemis.
 * ActionManager: handles all the actions (move/stop) sent to the controller so
    that the asynchronous ones are ordered. 
    
In the typical usage, Odemis ask to moveRel() an axis to the Bus. The Bus converts
it into an action, returns a Future and queue the action on the ActionManager.
When the Controller is free, the ActionManager pick the next action and convert
it into a command for the Controller, which sends it to the actual PI controller
and waits for it to finish.  

"""

class Controller(object):
    def __init__(self, ser, address=None, axes=None):
        """
        ser: a serial port (opened)
        address 1<int<16: address as configured on the controller
        If not address is given, it just allows to do some raw commands
        axes (dict int -> boolean): determine which axis will be used and whether
          it will be used closed-loop (True) or open-loop (False). 
        """
        self.serial = ser
        self.address = address
        # did the user asked for a raw access only?
        if address is None:
            return
        if axes is None:
            raise LookupError("Need to have at least one axis configured")
        
        # reinitialise (just in case)
        self.Reboot()
        self.GetErrorNum()
        
        self._channels = self.GetAxes() # available channels (=axes)
        # dict axis -> boolean
        self._hasLimit = dict([(a, self.hasLimitSwitches(a)) for a in self._channels])
        # dict axis -> boolean
        self._hasSensor = dict([(a, self.hasSensor(a)) for a in self._channels])
        
        for a, cl in axes.items():
            if not a in self._channels:
                raise LookupError("Axis %d is not supported by controller %d" % (a, address))
            if cl: # want closed-loop?
                if not self._hasSensor[a]:
                    raise LookupError("Axis %d of controller %d does not support closed-loop mode" % (a, address))
                self.SetServo(a, True)
                # for now we don't handle closed-loop anyway...
                raise NotImplementedError("Closed-loop support not yet implemented")
            else:
                # that should be the default, but for safety we force it
                self.SetServo(a, False)
        
        
        # actually set just before a move
        self._speed_max = 10 # m/s
        self._speed = dict([(a, 1.0) for a in axes]) # m/s
        self._accel_max = 100 # m/s²
        self._accel = dict([(a, 10.0) for a in axes]) # m/s² (both acceleration and deceleration) 
    
    def _sendOrderCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (including the \n if necessary)
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        full_com = "%d %s" % (self.address, com)
        logging.debug("Sending: %s", full_com.encode('string_escape'))
        self.serial.write(full_com)
        
    def _sendQueryCommand(self, com):
        """
        Send a command and return its report (first line sent)
        com (string): the command to send (without address prefix but with \n)
        return (string or list of strings): the report without prefix 
           (e.g.,"0 1") nor newline. If answer is multiline: returns a list of each line 
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        full_com = "%d %s" % (self.address, com)
        logging.debug("Sending: %s", full_com.encode('string_escape'))
        self.serial.write(full_com)
        
        char = self.serial.read() # empty if timeout
        line = ""
        lines = []
        while char:
            if char == "\n":
                if len(line) > 0 and line[-1] == " ":
                    # multiline
                    lines.append(line[:-1])# don't include the space
                    line = ""
                else:
                    # full end
                    lines.append(line)
                    break
            else:
                # normal char
                line += char
            char = self.serial.read()
            
        if not char:
            # TODO try to recover (RBT) and resend the command
            raise IOError("controller %d timeout, not recovered." % self.address)
        
        assert len(lines) > 0
            
        logging.debug("Receive: %s", "\n".join(lines).encode('string_escape'))
        prefix = "0 %d " % self.address
        if not lines[0].startswith(prefix):
            raise IOError("Report prefix unexpected after '%s': '%s'." % (full_com, lines[0]))
        lines[0] = lines[0][len(prefix):]

        if len(lines) == 1:
            return lines[0]
        else:
            return lines
    
    # The following are function directly mapping to the controller commands.
    # In general it should not be need to use them directly from outside this class
    def GetIdentification(self):
        #*IDN? (Get Device Identification):
        #ex: 0 2 (c)2010 Physik Instrumente(PI) Karlsruhe,E-861 Version 7.2.0
        version = self._sendQueryCommand("*IDN?\n")
        return version
    
    def GetSyntaxVersion(self):
        #CSV? (Get Current Syntax Version)
        #GCS version, can be 1.0 (for GCS 1.0) or 2.0 (for GCS 2.0)
        return self._sendQueryCommand("CSV?\n")
    
    def GetAxes(self):
        """
        returns (set of int): all the available axes
        """
        #SAI? (Get List Of Current Axis Identifiers)
        #SAI? ALL: list all axes (included disabled ones)
        answer = self._sendQueryCommand("SAI? ALL\n")
        # TODO check it works with multiple axes
        axes = set([int(a) for a in answer.split(" ")])
        return axes
    
    def GetAvailableCommands(self):
        #HLP? (Get List Of Available Commands)
        # first line starts with \x00
        lines = self._sendQueryCommand("HLP?\n")
        lines[0].lstrip("\x00")
        return lines 

    def GetAvailableParameters(self):
        #HPA? (Get List Of Available Parameters)
        # first line starts with \x00
        lines = self._sendQueryCommand("HPA?\n")
        lines[0].lstrip("\x00")
        return lines 

    def GetRecoderConfig(self):
        """
        you don't need this
        """
        #DRC? (get Data Recorder Configuration)
        return self._sendQueryCommand("DRC?\n")
    
    def hasLimitSwitches(self, axis):
        """
        Report whether the given axis has limit switches (is able to detect 
         the ends of the axis).
        Note: apparently it's just read from a configuration value in flash 
        memory. Can be configured easily with PIMikroMove
        axis (1<int<16): axis number
        """
        #LIM? (Indicate Limit Switches)
        assert((1 <= axis) and (axis <= 16))
        
        answer = self._sendQueryCommand("LIM? %d\n" % axis)
        # 1 => True, 0 => False
        return answer == "1"
 
    def hasSensor(self, axis):
        """
        Report whether the given axis has a sensor (is able to measure the 
         distance travelled). 
        Note: apparently it's just read from a configuration value in flash 
        memory. Can be configured easily with PIMikroMove
        axis (1<int<16): axis number
        """
        # TRS? (Indicate Reference Switch)
        assert((1 <= axis) and (axis <= 16))
        
        answer = self._sendQueryCommand("TRS? %d\n" % axis)
        # 1 => True, 0 => False
        return answer == "1"
 

    def GetMotionStatus(self):
        """
        returns (set of int): the set of moving axes
        """
        # "\x05" (Request Motion Status)
        # hexadecimal number bitmap of which axis is moving => 0 if everything is stopped
        # Ex: 4 => 3rd axis moving
        answer = self._sendQueryCommand("\x05")
        bitmap = int(answer, 16)
        # convert to a set
        i = 1
        mv_axes = set()
        while bitmap > 0:
            if bitmap & 1:
                mv_axes.add(i)
            i += 1
            bitmap = bitmap >> 1
        return mv_axes

    
    def GetStatus(self):
        #SRG? = "\x04" (Query Status Register Value)
        #SRG? 1 1
        #Check status
        # hexadecimal number bitmap of which axis is moving => 0 if everything is stopped
        # Ex: 0x9004
        bitmap = self._sendQueryCommand("\x04")
        assert(bitmap.startswith("0x"))
        value = int(bitmap[2:], 16)
        # TODO change to constants
        return value
    
    # "\x07" (Request Controller Ready Status)

    
    def GetErrorNum(self):
        """
        return (int): the error number (can be negative) of last error
        See p.192 of manual for the error codes
        """
        #ERR? (Get Error Number): get error code of last error
        answer = self._sendQueryCommand("ERR?\n")
        error = int(answer, 10)
        return error
    
    def Reboot(self):
        self._sendOrderCommand("RBT\n")
        time.sleep(1) # give it some time to reboot before it's accessible again

    def RelaxPiezos(self, axis):
        """
        Call relaxing procedure. Reduce voltage, to increase lifetime and needed
          to change between modes
        axis (1<int<16): axis number
        """
        #RNP (Relax PiezoWalk Piezos): reduce voltage when stopped to increase lifetime
        #Also needed to change between nanostepping and analog
        assert(axis in self._channels)
        self._sendOrderCommand("RNP %d\n" % axis)

    def Stop(self):
        """
        Stop immediately motion on all axes
        """
        #STP = "\x24" (Stop All Axes): immediate stop (high deceleration != HLT)
        self._sendOrderCommand("\x24")

    def SetServo(self, axis, activated):
        """
        Activate or de-activate the servo. 
        Note: only activate it if there is a sensor (cf .hasSensor and ._hasSensor)
        axis (1<int<16): axis number
        activated (boolean): True if the servo should be activated (closed-loop)
        """
        #SVO (Set Servo State)
        assert(axis in self._channels)
        
        if activated:
            assert(self._hasSensor[axis])
            state = 1
        else:
            state = 0
        self._sendOrderCommand("SVO %d %d\n" % (axis, state))

    # Functions for relative move in open-loop (no sensor)
    def OLMoveStep(self, axis, steps):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (1<int<16): axis number
        steps (float): number of steps to do (can be a float). If negative, goes
          the opposite direction. 1 step is about 10um.
        """
        #OSM (Open-Loop Step Moving): move using nanostepping
        assert(axis in self._channels)
        if steps == 0:
            return
        self._sendOrderCommand("OSM %d %f\n" % (axis, steps))
        
    
    def SetStepAmplitude(self, axis, amplitude):
        """
        Set the amplitude of one step (in nanostep mode). It affects the velocity
        of OLMoveStep.
        Note: probably it's best to set it to 55 and use OVL to change speed.
        axis (1<int<16): axis number
        amplitude (0<=float<=55): voltage applied (the more the further)
        """
        #SSA (Set Step Amplitude) : for nanostepping 
        assert(axis in self._channels)
        assert((0 <= amplitude) and (amplitude <= 55))
        self._sendOrderCommand("SSA %d %f\n" % (axis, amplitude))

    def OLAnalogDriving(self, axis, amplitude):
        """
        Use analog mode to move the axis by a given amplitude.
        axis (1<int<16): axis number
        amplitude (-55<=float<=55): Amplitude of the move. It's only a small move.
          55 is approximately 5 um.
        """
        #OAD (Open-Loop Analog Driving): move using analog
        assert(axis in self._channels)
        assert((-55 <= amplitude) and (amplitude <= 55))
        self._sendOrderCommand("OAD %d %f\n" % (axis, amplitude))        

    def SetOLVelocity(self, axis, velocity):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (1<int<16): axis number
        velocity (0<float): velocity in step-cycles/s. Default is 200 (~ 0.002 m/s)
        """
        #OVL (Set Open-Loop Velocity)
        assert(axis in self._channels)
        assert(velocity > 0)
        self._sendOrderCommand("OVL %d %f\n" % (axis, velocity))
    
    def SetOLAcceleration(self, axis, value):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (1<int<16): axis number
        value (0<float): acceleration in step-cycles/s. Default is 2000 
        """
        #OAC (Set Open-Loop Acceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("OVL %d %f\n" % (axis, value))
        
    def SetOLDeceleration(self, axis, value):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (1<int<16): axis number
        value (0<float): decelaration in step-cycles/s. Default is 2000 
        """
        #ODC (Set Open-Loop Deceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("OVL %d %f\n" % (axis, value))

#Abs (with sensor = closed-loop):
#MOV (Set Target Position)
#MVR (Set Target Relative To Current Position)
#
#FNL (Fast Reference Move To Negative Limit)
#FPL (Fast Reference Move To Positive Limit)
#FRF (Fast Reference Move To Reference Switch)
#
#POS? (GetRealPosition)
#ONT? (Get On Target State)
#
#TMN? (Get Minimum Commandable Position)
#TMX? (Get Maximum Commandable Position)
#Min-Max position in physical units (μm)
#
#VEL (Set Closed-Loop Velocity)
#ACC (Set Closed-Loop Acceleration)
#DEC (Set Closed-Loop Deceleration)
#
# Different from OSM because they use the sensor and are defined in physical unit.
# Servo must be off! => Probably useless... compared to MOV/MVR
#OMR (Relative Open-Loop Motion)
#OMA (Absolute Open-Loop Motion)
#
    
    def setSpeed(self, axis, speed):
        """
        Changes the move speed of the motor (for the next move).
        Note: in open-loop mode, it's very approximate.
        speed (0<float<5): speed in m/s.
        axis (int 0 or 1): axis to pic  
        """
        assert((0 < speed) and (speed < self._speed_max))
        assert(axis in self._channels)
        self._speed[axis] = speed
    
    
    
    def waitEndMotion(self, axes=None):
        """
        Wait until the motion of all the given axis is finished.
        Note: there is a 5 s timeout
        axes (None or set of int): axes to check whether for move, or all if None
        """
        #TODO use the time, distance, and speed of last move to evaluate the timeout
        # approximately the time for the longest move
        timeout = 5 #s
        end = time.time() + timeout
        
        if axes is None:
            axes = self._channels
        else:
            assert len(self._channels - axes) == 0
        
        while axes & self.GetMotionStatus():
            if time.time() <= end:
                raise IOError("Timeout while waiting for end of motion")
            time.sleep(0.005)
        
    @staticmethod
    def scan(port, max_add=16):
        """
        Scan the serial network for all the PI C-170 available.
        port (string): name of the serial port
        max_add (1<=int<=16): maximum address to scan
        return (dict int -> tuple): addresses of available controllers associated
            to number of axes, and presence of limit switches/sensor
        """
        ser = Controller.openSerialPort(port)
        ctrl = Controller(ser)
        
        logging.info("Serial network scanning for PI-GCS controllers in progress...")
        present = {}
        for i in range(1, max_add+1):
            # ask for controller #i
            logging.debug("Querying address %d", i)

            # is it answering?
            try:
                ctrl.address = i
                axes = {}
                for a in ctrl.GetAxes():
                    axes = {a: ctrl.hasSensor(a)}
                if not axes:
                    logging.info("Found controller %d with no axis", i)
                else:
                    present[i] = axes
#                version = ctrl.GetIdentification()
#                print ctrl.GetAvailableCommands()
#                print ctrl.GetAvailableParameters()
#                print ctrl.GetRecoderConfig()
#                print ctrl.GetMotionStatus()
#                print ctrl.GetStatus()
#                new_ctrl = Controller(ser, i)
#                print new_ctrl._hasLimit
#                print new_ctrl._hasSensor
#                
#                print new_ctrl.SetServo(1, False)
#                print new_ctrl.GetErrorNum()
#                print new_ctrl.GetStatus()
            except IOError:
                pass
        
        ctrl.address = None
        
        return present
    
    @staticmethod
    def openSerialPort(port):
        """
        Opens the given serial port the right way for the PI-E861.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port = port,
            baudrate = 38400,
            bytesize = serial.EIGHTBITS,
            parity = serial.PARITY_NONE,
            stopbits = serial.STOPBITS_ONE,
            timeout = 0.3 #s
        )
        
        return ser


class Bus(model.Actuator):
    """
    Represent a chain of PI controller over a serial port
    """
    def __init__(self, name, role, children, port, axes):
        """
        port (string): name of the serial port to connect to the controllers
        axes (dict string=> 3-tuple(1<=int<=16, 1<=int, boolean): the configuration
         of the network. For each axis name associates the controller address,
         channel, and whether it's closed-loop (absolute positioning) or not.
         Note that even if it's made of several controllers, each controller is 
         _not_ seen as a child from the odemis model point of view.
        """
        # this set ._axes and ._ranges
        model.Actuator.__init__(self, name, role, children=children, axes=axes.keys())
        
        ser = Controller.openSerialPort(port)

        # Prepare initialisation by grouping axes from the same controller
        ac_to_axis = {} # address, channel -> axis name
        controllers = {} # address -> dict (axis -> boolean)
        for axis, (add, channel, isCL) in axes.items():
            if not add in controllers:
                controllers[add] = {}
            elif channel in controllers[add]:
                raise ValueError("Cannot associate multiple axes to controller %d:%d" % (add, channel))
            ac_to_axis[(add, channel)] = axis 
            controllers[add].update({channel: isCL})

        # Init each controller            
        self._axis_to_cc = {} # axis name => (Controller, channel)
        # TODO also a rangesRel : min and max of a step 
        self._position = {}
        speed = {}
        for address, channels in controllers.items():
            try:
                controller = Controller(ser, address, channels)
            except IOError:
                logging.exception("Failed to find a controller with address %d on %s", address, port)
                raise
            except LookupError:
                logging.exception("Failed to initialise controller %d on %s", address, port)
                raise
            for c in channels:
                axis = ac_to_axis[(address, c)]
                self._axis_to_cc[axis] = (controller, c)
            
            # TODO if closed-loop, the ranges should be updated after homing
            # For now we put very large one
            self._ranges[axis] = [0, 1] # m
            
            # TODO move to a known position (0,0) at init?
            # for now we have no idea where we are => in the middle so that we can always move
            # TODO if closed-loop, the positions should be updated after homing
            self._position[axis] = 0.5 # m
            
            # Just to make sure it doesn't go too fast
            speed[axis] = 0.1 # m/s
        
        
        
        # min speed = don't be crazy slow. max speed from hardware spec
        self.speed = model.MultiSpeedVA(speed, range=[10e-6, 0.5], unit="m/s",
                                        setter=self.setSpeed)
        self.setSpeed(speed)
        
        # set HW and SW version
        self._swVersion = "%s (serial driver: %s)" % (__version__.version, self.getSerialDriver(port))
        hwversions = []
        for axis, (ctrl, channel) in self._axis_to_cc.items():
            hwversions += "'%s': %s (GCS %s)" % (axis, ctrl.GetIdentification(), ctrl.GetSyntaxVersion())
        self._hwVersion = ", ".join(hwversions)
    
#        self._action_mgr = ActionMgr()
#        self._action_mgr.start()
    
    def getSerialDriver(self, name):
        """
        return (string): the name of the serial driver used for the given port
        """
        # In linux, can be found as link of /sys/class/tty/tty*/device/driver
        if sys.platform.startswith('linux'):
            path = "/sys/class/tty/" + os.path.basename(name) + "/device/driver"
            try:
                return os.path.basename(os.readlink(path))
            except OSError:
                return "Unknown"
        else:
            return "Unknown"
    
    def setSpeed(self, value):
        """
        value (dict string-> float): speed for each axis
        returns (dict string-> float): the new value
        """
        for axis, v in value.items():
            controller, channel = self._axis_to_cc[axis]
            controller.setSpeed(channel, v)
        return value
    
    def getPosition(self):
        # TODO: for closed-loop axes, use ctrl.getPosition
        return self._position
    
    @staticmethod
    def scan(port=None):
        """
        port (string): name of the serial port. If None, all the serial ports are tried
        returns (list of 2-tuple): name, args (port, axes(channel -> CL?)
        Note: it's obviously not advised to call this function if moves on the motors are ongoing
        """ 
        if port:
            ports = [port]
        else:
            if os.name == "nt":
                ports = ["COM" + str(n) for n in range (0,8)]
            else:
                ports = glob.glob('/dev/ttyS?*') +  glob.glob('/dev/ttyUSB?*')
        
        axes_names = "xyzabcdefghijklmnopqrstuvw"
        found = []  # (list of 2-tuple): name, args (port, axes(channel -> CL?)
        for p in ports:
            try:
                controllers = Controller.scan(p)
            except serial.SerialException:
                # not possible to use this port? next one!
                continue
            
            if controllers:
                axis_num = 0
                arg = {}
                for add, axes in controllers.items():
                    for a, cl in axes.items():
                        arg[axes_names[axis_num]] = (add, a, cl)
                        axis_num += 1
                found.append(("Actuator " + os.path.basename(p),
                             {"port": p, "axes": arg}))
        
        return found
    
    
#addresses = Controller.scan("/dev/ttyUSB0", max_add=1)
#addresses = Bus.scan()
#print addresses
stage = Bus("test", "stage", children=None, port="/dev/ttyUSB0", axes={"x":(1,1,False)})
print stage.swVersion

