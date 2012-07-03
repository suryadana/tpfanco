#! /usr/bin/python
# -*- coding: utf8 -*-
#
# tpfanco - controls the fan-speed of IBM/Lenovo ThinkPad Notebooks
# Copyright (C) 2011-2012 Vladyslav Shtabovenko
# Copyright (C) 2007-2009 Sebastian Urban
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import sys
import os
import os.path
import time
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
import gobject
import atexit

class UnavailableException(dbus.DBusException):
    _dbus_error_name = "org.tpfanco.tpfand.UnavailableException"

class Control(dbus.service.Object):
    """fan controller"""

    # poll time
    poll_time = 3500
    # kernel watchdog time
    # the thinkpad_acpi watchdog accepts intervals between 1 and 120 seconds
    # for safety reasons one shouldn't use values higher than 5 seconds
    watchdog_time = 5
    # value that temperature has to fall below to slow down fan
    current_trip_temps = { }
    # current fan speeds required by sensor readings
    current_trip_speeds = { }
    # interval variables
    current_time = 0.
    interval_start = 0.
    interval_end = 0

    def __init__(self, bus, path, act_settings, tpfand_polkit, build):
        self.act_settings = act_settings
        self.tpfand_polkit = tpfand_polkit
        self.build = build
        self.debug = self.act_settings.debug
        #Register sys.exit handler
        atexit.register(self.term_handler)
        # register d-bus name
        dbus.service.Object.__init__(self, bus, path)
        #intervals
        self.current_time = time.time() * 1000
        self.interval_end = self.current_time + self.act_settings.min_interval_duration*1000
        # start our main loop
        self.repoll(1)

    def repoll(self, interval):
        """calls poll again after interval msecs"""
        ival = int(interval)
        # make sure that we always repoll before the watchdog timer runs out
        if ival < 1:
            ival = 1000
        if ival > self.watchdog_time * 1000:
            ival = self.watchdog_time * 1000

        gobject.timeout_add(ival, self.poll)

    def poll(self):
        """main fan control routine"""
        # get the current fan level
        fan_state = self.get_fan_state()

        # get current time in milliseconds
        self.current_time = time.time() * 1000

        if self.debug:
            print
            print "\nDebug: " + str(time.strftime("%H:%M:%S")) + " - Polling the sensors"
            print "       Current fan level: " + str(fan_state["level"]) + " (" + str(fan_state["rpm"]) + " RPM)"
            print "       Minimal interval duration: "+ str(round(self.act_settings.min_interval_duration,0)) +" sec"
            print "       Current time (epoche time) in msec: ", str(round(self.current_time,0))
            print "       Interval end in msec: " + str(round(self.interval_end,0))
            print "       Difference: " + str(round(self.interval_end-self.current_time,0)) + " msec"

        if self.act_settings.enabled:

            # read thermal data
            try:
                temps = self.get_temperatures()
            except Exception, ex:
                # temperature read failed
                if self.act_settings.debug:
                    print "Debug: Reading temperature values failed: " + str(ex)
                self.set_speed(255)
                self.repoll(self.poll_time)
                return False

            new_speed = 0
            if self.debug:
                print "       Current sensor values:"
            for id in xrange(len(temps)):
                temp = temps[id]
                if temp != 0:
                    points = self.act_settings.trigger_points[id]
                    speed = 0
                    if self.debug:
                        print "           Sensor " + str(id) +": " + str(temp)
                    # check if temperature is above hysteresis shutdown point
                    if id in self.current_trip_temps:
                        if temp >= self.current_trip_temps[id]:
                            speed = self.current_trip_speeds[id]
                        else:
                            del self.current_trip_temps[id]
                            del self.current_trip_speeds[id]

                    # check if temperature is over trigger point
                    for trigger_temp, trigger_speed in points.iteritems():
                        if temp >= trigger_temp and speed < trigger_speed:
                            self.current_trip_temps[id] = trigger_temp - self.act_settings.hysteresis
                            self.current_trip_speeds[id] = trigger_speed
                            speed = trigger_speed

                    new_speed = max(new_speed, speed)

            # if current interval is not over yet and new fan speed is lower than the current
            # one, keep the old speed
            if int(new_speed) < int(fan_state["level"]):
                if self.debug:
                    print "       New speed " + str(new_speed) + " is lower than the current one " + str(fan_state["level"])
                if self.current_time < self.interval_end:
                    if self.debug:
                        print "       Since current interval is not over yet, keep the old speed unchanged"
                    new_speed = int(fan_state["level"])
                else:
                    if self.debug:
                        print "       Difference: " + str(round(self.interval_end-self.current_time,0))
                        print "       Since current interval is already over, apply the new speed"
                    self.current_time = time.time() * 1000
                    self.interval_end = self.current_time + self.act_settings.min_interval_duration*1000

            elif int(new_speed) == int(fan_state["level"]):
                if self.debug:
                    print "       New speed " + str(new_speed) + " is equal to the current one " + str(fan_state["level"])
                    print "       Just start a new interval."
                self.current_time = time.time() * 1000
                self.interval_end = self.current_time + self.act_settings.min_interval_duration*1000

            else:
                if self.debug:
                    print "       New speed " + str(new_speed) + " is higher than the current one " + str(fan_state["level"])
                    print "       Apply new speed and start a new interval"
                self.current_time = time.time() * 1000
                self.interval_end = self.current_time + self.act_settings.min_interval_duration*1000

            if self.debug:
                print "       Trying to set fan level to " + str(new_speed) + ":"
            # set fan speed
            self.set_speed(new_speed)
            self.repoll(self.poll_time)
        else:
            # fan control disabled
            self.set_speed(255)
            self.repoll(self.poll_time)
        # remove current timer
        return False

    def set_speed(self, speed):
        """sets the fan speed (0=off, 2-8=normal, 254=disengaged, 255=ec, 256=full-speed)"""
        fan_state = self.get_fan_state()
        try:
            if self.debug:
                print "           Rearming fan watchdog timer (+" + str(self.watchdog_time) + " sec)"
                print "           Current fan level is " + str(fan_state["level"])
            fanfile = open(self.build.ibm_fan, "w")
            fanfile.write("watchdog %d" % self.watchdog_time)
            fanfile.flush()
            if speed == fan_state["level"]:
                if self.debug:
                    print "           -> Keeping the current fan level unchanged"
            else:
                if self.debug:
                    print "           -> Setting fan level to " + str(speed)
                if speed == 0:
                    fanfile.write("disable")
                else:
                    fanfile.write("enable")
                    fanfile.flush()
                    if speed == 254:
                        fanfile.write("level disengaged")
                    if speed == 255:
                        fanfile.write("level auto")
                    elif speed == 256:
                        fanfile.write("level full-speed")
                    else:
                        fanfile.write("level %d" % (speed - 1))
            fanfile.flush()
        except IOError:
            # sometimes write fails during suspend/resume
            pass
        finally:
            try:
                fanfile.close()
            except:
                pass

    @dbus.service.method("org.tpfanco.tpfand.Control", in_signature="", out_signature="s")
    def get_version(self):
        return self.build.version

    @dbus.service.method("org.tpfanco.tpfand.Control", in_signature="", out_signature="ai")
    def get_temperatures(self):
        """returns list of current sensor readings"""
        elements = []
        if self.debug:
            print "       Sensors used: " + self.act_settings.sensors
        if self.act_settings.sensors == "ibm_thermal":
            try:
                thermal_file = open(self.build.ibm_thermal, "r")
                val = thermal_file.readline().split()[1:]
                for i in xrange(len(val)):
                    if val[i] not in self.act_settings.ibm_thermal_disabled_sensor_tokens:
                        elements.append(int(val[i]))
                    else:
                        elements.append(0)
                thermal_file.close()
                return elements
            except IOError, e:
                # sometimes read fails during suspend/resume
                raise UnavailableException(e.message)
                if self.debug:
                    print "Debug: There was a problem reading " + self.build.ibm_thermal
            finally:
                try:
                    thermal_file.close()
                except:
                    pass

        elif self.act_settings.sensors == "tp_hwmon":
            for i in xrange(len(self.act_settings.tp_hwmon_sensors)):
                try:
                    hwmon_file = open(self.act_settings.tp_hwmon_sensors[i], "r")
                    val = hwmon_file.readline().rstrip("\n")
                    if val not in self.act_settings.tp_hwmon_disabled_sensor_tokens:
                        elements.append(int(float(val)/1000.))
                    else:
                        elements.append(0)
                    hwmon_file.close()
                except IOError, e:
                    # sensor is not available
                    elements.append(0)
                    if self.debug:
                        print "Debug: Sensor "+ self.act_settings.tp_hwmon_sensors[i] + " is not available"
                finally:
                    try:
                        hwmon_file.close()
                    except:
                        pass
            return elements

        elif self.act_settings.sensors == "lm_sensors":
            for id, path in self.act_settings.lm_sensors.iteritems():
                try:
                    lm_sensor_file = open(path, "r")
                    val = lm_sensor_file.readline().rstrip("\n")
                    if val not in act_settings.lm_sensors_disabled_sensor_tokens:
                        elements.append(int(float(val)/1000.))
                    else:
                        elements.append(0)
                    lm_sensor_file.close()
                except IOError, e:
                    # sensor is not available
                    elements.append(0)
                    if self.debug:
                        print "Debug: Sensor "+ lm_sensor_file + " is not available"
                finally:
                    try:
                        lm_sensor_file.close()
                    except:
                        pass
            return elements
        else:
            print "Fatal error: Unknown sensors source."
            sys.exit(1)

    @dbus.service.method("org.tpfanco.tpfand.Control", in_signature="", out_signature="a{si}")
    def get_fan_state(self):
        """Returns current (fan_level, fan_rpm)"""
        try:
            fanfile = open(self.build.ibm_fan, "r")
            for line in fanfile.readlines():
                key, value = line.split(":")
                if key == "speed":
                    rpm = int(value.strip())
                if key == "level":
                    value = value.strip()
                    if value == "0":
                        level = 0
                    elif value == "auto":
                        level = 255
                    elif value == "disengaged" or value == "full-speed":
                        level = 256
                    elif int(value) in range(1,7):
                        level = int(value) + 1
                    else:
                        print "Fatal error: Encountered unknown fan level: " + value
                        sys.exit(1)

            return {"level": level, "rpm": rpm }
        except Exception, e:
            raise UnavailableException(e.message)
        finally:
            try:
                fanfile.close()
            except:
                pass

    @dbus.service.method("org.tpfanco.tpfand.Control", in_signature="", out_signature="")
    def reset_trips(self):
        """resets current trip points, should be called after config change"""
        self.current_trip_speeds = { }
        self.current_trip_temps = { }

    @dbus.service.method("org.tpfanco.tpfand.Control", in_signature="", out_signature="a{ii}")
    def get_trip_temperatures(self):
        """returns the current hysteresis temperatures for all sensors"""
        return self.current_trip_temps

    @dbus.service.method("org.tpfanco.tpfand.Control", in_signature="", out_signature="a{ii}")
    def get_trip_fan_speeds(self):
        """returns the current hysteresis fan speeds for all sensors"""
        return self.current_trip_speeds

    def term_handler(self):
        """Removes the pid file before terminating tpfand"""
        print "Control module requests emergency exit!"
        try:
            os.remove(self.build.pid_path)
        except Exception, ex:
            #print "Can't delete " +  self.build.pid_path + ". Please do this by hand!"
            #print ex
            pass
        #self.loop.quit()
