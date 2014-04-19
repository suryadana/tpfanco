#! /usr/bin/python2.7
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
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
import gobject
import atexit
import dmidecode



class ProfileNotOverriddenException(dbus.DBusException):
    _dbus_error_name = "org.tpfanco.tpfand.ProfileNotOverriddenException"

class Settings(dbus.service.Object):
    """profile and config settings"""

    config_comment = """#
# tp-fancontrol configuration file
#
# Options:
# enabled = [True / False]
# override_profile = [True / False]
#
# sensors= [ibm_thermal/tp_hwmon/libsensors]
#
# Trigger point syntax:
# [sensor-id]. [human readable sensor name] = [temperature]:[fan level] ...
# [fan level] = 0: fan off
# [fan level] = 255: hardware controlled cooling mode
# default rule is used for all unspecified sensors
#
# [sensor-id]. [path to the sensor] : [correction_factor]
#
# hysteresis = [hysteresis temperature difference]
#
# override_profile = True has to be specified before profile parameters
# or trigger points are changed in the configuration file.
# tpfand may regenerate this file at any time. Custom comments will be lost.
#

"""

    ibm_thermal_disabled_sensor_tokens = ["-128", "-1" ,"0", "1", "128"]

    tp_hwmon_disabled_sensor_tokens = ["-128000", "-1000", "0", "1000", "128000"]
    lm_sensors_disabled_sensor_tokens = ["-128000", "-1000", "0", "1000", "128000"]

    max_hwmon_sensors = 16

    default_sensors = {0 : "/sys/devices/virtual/hwmon/hwmon0/temp1_input"}

    current_profile = None
    current_config  = None
    tp_hwmon_sensors = []

    #settings can be manual,machine_profile and custom_profile

    # user options
    enabled = False
    override_profile = False

    # profile / user overrideable options
    sensor_names = { }
    trigger_points = { }
    sensors = ""
    hysteresis = -1
    min_interval_duration = -1
    lm_sensors = {}

    # hardware product info
    product_name = None
    product_id = None
    product_pretty_vendor = None
    product_pretty_name = None
    product_pretty_id = None

    # profile info
    loaded_profiles = [ ]

    # comments for the last loaded profile
    profile_comment = ""

    def __init__(self, bus, path, tpfand_polkit, build, debug, noibmthermal):
        self.tpfand_polkit = tpfand_polkit
        self.build = build
        self.debug = debug
        self.noibmthermal = noibmthermal
        for i in xrange(self.max_hwmon_sensors):
            self.tp_hwmon_sensors.append(build.tp_hwmon + "temp" + str(i+1) + "_input")
        print  self.tp_hwmon_sensors
        #Register sys.exit handler
        atexit.register(self.term_handler)
        # register d-bus name
        dbus.service.Object.__init__(self, bus, path)

        if self.debug:
            print "Debug: Reading configuration file " + self.build.config_path

        #if no configuration file is available, create one
        if not os.path.isfile(self.build.config_path):
            #create new configuraton file
            print "Info: Looks like there's no configuration file available."
            print "      Trying to create one now."
            if self.new_configuration_file():
                print "      New configuration file created successfully"
            else:
                print "Fatal error: A problem occured while creating new configuration file."
                print "             Try to delete " + self.build.config_path + " and restart tpfand"
                sys.exit(1)

        # load and verify configuration file
        _, self.current_config = self.read_file(self.build.config_path)
        # do not continue unless configuration file is valid
        if not self.verify_dataset(self.current_config):
                print "Fatal Error: Configuration file is invalid!"
                print "             Try to delete " + self.build.config_path + " and restart tpfand"
                sys.exit(1)

        # determine machine model
        self.read_model_info()

        # load profile for this machine (if available)
        self.profile_available, profile_path = self.match_profile()
        if self.profile_available:
            if self.debug:
                print "Debug: Profile for this machine is available."
                print "       Trying to load the profile " + str(profile_path)
            success, self.current_profile = self.read_file(profile_path)
            if success and self.verify_dataset(self.current_profile):
                if self.debug:
                    print "       Success!"
            else:
                print "Error: A profile for this machine is available but can't be used."
                print "       The corresponding file is " + str(profile_path)
                self.profile_available = False
                self.current_profile = None


        self.apply_settings()
        print "Fine so far !!!!!"
        #self.load()


    def new_configuration_file(self):
        """detects available sensors and creates new configuration file"""
        sensors=""
        working_sensors={}
        if self.noibmthermal:
          ibm_thermal_on = False
        else:
          ibm_thermal_on, ibm_thermal_working_sensors = self.check_ibm_thermal()
        tp_hwmon_on, tp_hwmon_working_sensors = self.check_tp_hwmon()
        lm_sensors_on, lm_sensors_working_sensors = self.check_lm_sensors(self.default_sensors)

        if ibm_thermal_on:
            sensors="ibm_thermal"
            working_sensors = ibm_thermal_working_sensors
        elif tp_hwmon_on:
            sensors="tp_hwmon"
            working_sensors = tp_hwmon_working_sensors
        elif lm_sensors_on:
            sensors="lm_sensors"
            working_sensors = lm_sensors_working_sensors
        else:
            sensors="lm_sensors"
        try:
            file = open(self.build.config_path, "w")
            file.write(self.config_comment)
            file.write("enabled = False\n")
            file.write("override_profile = False\n")
            file.write("\n")
            file.write("sensors = %s\n" % str(sensors))
            file.write("\n")
            print working_sensors
            for i in xrange(len(working_sensors)):
              if working_sensors[i]:
                file.write("%d. Sensor %d = 0:255\n" % (i,i))
            if sensors=="lm_sensors":
                file.write("\n")
                for i, path in self.default_sensors.iteritems():
                    file.write("%d. %s : lm_sensor\n" % (i,path))
            file.write("\n")
            file.write("hysteresis = 3\n")
            file.write("min_interval_duration = 10\n")
            file.close()
            return True
        except Exception, ex:
            print "Error: Can't create " + self.build.config_path + " : " + str(ex)
            file.close()
        finally:
            try:
                file.close()
            except:
                pass
        return False

    def check_ibm_thermal(self):
        """can we read temperature values via build.ibm_thermal?"""
        available_sensors = {}
        sensors_status = False
        try:
            thermal_file = open(self.build.ibm_thermal, "r")
            elements = thermal_file.readline().split()[1:]
            thermal_file.close()
            for i in xrange(0, len(elements)):
                if elements[i] not in self.ibm_thermal_disabled_sensor_tokens:
                    available_sensors[i] = True
                else:
                    available_sensors[i] = False
        except IOError:
            available_sensors = {}
        if sum(available_sensors.values()) > 0:
            sensors_status = True
        return sensors_status, available_sensors

    def check_tp_hwmon(self):
        """can we read temperature values via tp_hwmon?"""
        available_sensors = {}
        sensors_status = False
        for i in xrange(len(self.tp_hwmon_sensors)):
            try:
                hwmon_file = open(self.tp_hwmon_sensors[i], "r")
                val = hwmon_file.readline().rstrip("\n")
                if val not in self.tp_hwmon_disabled_sensor_tokens:
                    available_sensors[i] = True
                else:
                    available_sensors[i] = False
                hwmon_file.close()
            except IOError:
                available_sensors[i] = False
        if sum(available_sensors.values()) > 0:
            sensors_status = True
        return sensors_status, available_sensors

    def check_lm_sensors(self, sensors_list):
        """can we use sensors from the sensors_list?"""
        available_sensors = {}
        sensors_status = False
        for i in xrange(len(sensors_list)):
            try:
                lm_sensors_file = open(sensors_list[i], "r")
                val = lm_sensors_file.readline().rstrip("\n")
                if val not in self.lm_sensors_disabled_sensor_tokens:
                    available_sensors[i] = True
                else:
                    available_sensors[i] = False
                lm_sensors_file.close()
            except IOError:
                available_sensors[i] = False
        if sum(available_sensors.values()) > 0:
            sensors_status = True
        return sensors_status, available_sensors

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="a{ss}")
    def get_model_info(self):
        """returns hardware model info"""
        return {"vendor": self.product_pretty_vendor,
                "name": self.product_pretty_name,
                "id": self.product_pretty_id,
                "profile_name": self.product_name,
                "profile_id": self.product_id }

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="as")
    def get_loaded_profiles(self):
        """returns a list of the given profiles"""
        return self.loaded_profiles

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="s")
    def get_profile_comment(self):
        """returns the comment for the last loaded profile"""
        if self.override_profile:
            return ""
        else:
            return self.profile_comment

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="b")
    def is_profile_exactly_matched(self):
        """returns True if profile exactly matches hardware"""
        return self.id_match

    def load_profile(self):
        """loads profile from disk"""
        profile_file_list, self.loaded_profiles, self.id_match = self.get_profile_file_list()
        if not self.override_profile:
            self.sensor_names = { }
            self.trigger_points = { }
            self.hysteresis = -1
            self.profile_comment = ""
            for path in profile_file_list:
                try:
                    # only show comment of profile that matches notebook model best
                    self.profile_comment = ""

                    self.read_config(path, False)
                except Exception, ex:
                    print "Error loading ", path, ": ", ex

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="")
    def apply_settings(self):
        """applies settings"""
        dataset = None
        self.override_profile  = self.current_config["override_profile"]
        self.enabled = self.current_config["enabled"]

        if self.override_profile or not self.profile_available:
            dataset = self.current_config
        else:
            dataset = self.current_profile

        self.sensors = dataset["sensors"]
        self.hysteresis = dataset["hysteresis"]
        self.min_interval_duration = dataset["min_interval_duration"]
        self.sensor_names = dataset["sensor_names"]
        self.trigger_points = dataset["trigger_points"]
        self.lm_sensors = dataset["lm_sensors"]
        self.profile_comment = dataset["comment"]

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="")
    def save(self):
        """saves config to disk"""
        self.write_config(self.build.config_path)

    def match_profile(self):
        """returns a profile compatible with this machine (if available)"""
        model_path = self.build.data_dir + "models/by-id/" + self.product_id
        if os.path.isfile(model_path):
            return True, model_path
        else:
            return False, ""


    def get_profile_file_list(self):
        """returns a list of profile files to load for this system"""
        model_dir = self.build.data_dir + "models/"
        product_id_dir = model_dir + "by-id/"
        product_name_dir = model_dir + "by-name/"

        # generic profile
        files = [model_dir + "generic"]
        profiles = [ "generic" ]

        # match parts of product name
        product_path = product_name_dir + self.product_name
        for n in range(len(product_name_dir)+1, len(product_path)):
            path = product_path[0:n]
            if os.path.isfile(path):
                files.append(path)
                profiles.append(path[len(model_dir):])

        # try matching model id
        id_match = False
        model_path = product_id_dir + self.product_id
        if os.path.isfile(model_path):
            files.append(model_path)
            profiles.append(model_path[len(model_dir):])
            id_match = True

        return files, profiles, id_match

    def read_model_info(self):
        """reads model info using dmidecode module"""
        try:
            current_system = dmidecode.system()
            hw_product = current_system["0x0001"]["data"]["Product Name"]
            hw_vendor = current_system["0x0001"]["data"]["Manufacturer"]
            hw_version = current_system["0x0001"]["data"]["Version"]
            self.product_id = (hw_vendor + "_" + hw_product).lower()
            self.product_name = (hw_vendor.lower() + "_" + hw_version.lower()).lower().replace("/", "-").replace(" ", "_")
            self.product_pretty_vendor = hw_vendor
            self.product_pretty_name = hw_version
            self.product_pretty_id = hw_product
        except:
            print "Warning: unable to get your system model from dmidecode"
            self.product_id = ""
            self.product_name = ""
            self.product_pretty_vendor = ""
            self.product_pretty_name = ""
            self.product_pretty_id = ""

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="a{is}")
    def get_sensor_names(self):
        """returns the sensor names"""
        print self.sensor_names
        return self.sensor_names

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="a{is}", out_signature="")
    def set_sensor_names(self, set):
        """sets the sensor names"""
        self.verify_profile_overridden()
        self.sensor_names = set
        self.verify()
        self.save()

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="a{ia{ii}}")
    def get_trigger_points(self):
        """returns the temperature trigger points for the sensors"""
        return self.trigger_points

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="a{ia{ii}}", out_signature="")
    def set_trigger_points(self, set):
        """sets the temperature trigger points for the sensors"""
        self.verify_profile_overridden()
        self.trigger_points = set
        self.verify()
        self.save()

    def verify(self):
        """Verifies that all settings are valid"""
        for n in range(0, self.get_sensor_count()):
            if n not in self.sensor_names or len(self.sensor_names[n].strip()) == 0:
                self.sensor_names[n] = "Sensor " + str(n)
            else:
                self.sensor_names[n] = self.sensor_names[n].replace("=", "-").replace("\n", "")
            if n not in self.trigger_points:
                self.trigger_points[n] = {0: 255}
        for opt in ["hysteresis"]:
            val = eval("self." + opt)
            lmin, lmax = self.get_setting_limits(opt)
            if val < lmin or val > lmax:
                if val < lmin:
                    val = lmin
                if val > lmax:
                    val = lmax
                exec "self." + opt + " = " + str(val)

    def verify_dataset(self, dataset):
        """verifies that all settings are valid"""
        for opt in ["hysteresis", "min_interval_duration"]:
            val = dataset[opt]
            lmin, lmax = self.get_setting_limits(opt)
            if val < lmin or val > lmax:
                print "Fatal Error: " + str(opt) + " can take values between " + str(lmin) + " and " + str(lmax) + " only."
                print "             However, current value is " + str(val)
                return False

        valid_sensors = self.get_setting_limits("sensors")
        if dataset["sensors"] not in valid_sensors:
            print "Fatal Error: sensors can only take one of the following values:"
            print "             " + str(valid_sensors)
            print "             However, current value is " + str(dataset["sensors"])
            return False

        min_temp, max_temp = self.get_setting_limits("temperature")
        valid_fan_speed = self.get_setting_limits("fan_speed")
        for id in xrange(0,len(dataset["trigger_points"])):
            for temp in dataset["trigger_points"][id]:
              fan_speed = dataset["trigger_points"][id][temp]
              if temp < min_temp or temp > max_temp:
                  print "Fatal Error: temperature can take values between " + str(min_temp) + " and " + str(max_temp) + " only."
                  print "             However, one of the values is " + str(temp)
                  return False
              if fan_speed not in valid_fan_speed:
                  print "Fatal Error: fan speed can only take one of the following values:"
                  print "             " + str(valid_fan_speed)
                  print "             However, one of the values is " + str(fan_speed)
                  return False
        return True

    def verify_profile_overridden(self):
        """verifies that override_profile is true, raises ProfileNotOverriddenException if it is not"""
        if not self.override_profile:
            raise ProfileNotOverriddenException()

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="i")
    def get_sensor_count(self):
        """returns the count of sensors"""
        return 16

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="s", out_signature="ad")
    def get_setting_limits(self, opt):
        """returns the limits (min, max) of the given option"""
        if opt == "hysteresis":
            return [0, 10]
        elif opt == "min_interval_duration":
            return [0, 60]
        elif opt == "temperature":
            return [0, 255]
        elif opt == "fan_speed":
            return [0, 2, 3, 4, 5, 6, 7, 8, 255, 256]
        elif opt == "sensors":
            return ["ibm_thermal", "tp_hwmon", "lm_sensors"]
        else:
            return None

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="a{si}")
    def get_settings(self):
        """returns the settings"""
        ret = {"hysteresis": self.hysteresis,
               "min_interval_duration": self.min_interval_duration,
               "enabled": int(self.enabled),
               "override_profile": int(self.override_profile)}
        return ret

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="a{si}", out_signature="")
    def set_settings(self, set):
        """sets the settings"""
        try:
            if "override_profile" in set:
                self.override_profile = bool(set["override_profile"])
            if "enabled" in set:
                self.enabled = bool(set["enabled"])
            if "hysteresis" in set:
                self.verify_profile_overridden()
                self.hysteresis = set["hysteresis"]
        except ValueError, ex:
            print "Error parsing parameters: ", ex
            pass
        finally:
            self.verify()
            self.save()
            if not self.override_profile:
                self.load_profile()
                self.verify()

    def write_config(self, path):
        """Writes a fan profile file"""
        file = open(path, "w")
        file.write(config_comment)
        file.write("enabled = %s\n" % str(self.enabled))
        file.write("override_profile = %s\n" % str(self.override_profile))
        file.write("\n")

        if self.override_profile:
            file.write(self.get_profile_string())

        file.close()

    @dbus.service.method("org.tpfanco.tpfand.Settings", in_signature="", out_signature="s")
    def get_profile_string(self):
        """returns the current profile as a string"""
        res = ""
        # e.g "sensors = ibm_thermal"
        res += "sensors = %s\n" % self.sensors
        # e.g "x. Sensor X = 0:0 55:2 60:255"
        ids = set(self.sensor_names.keys())
        ids.union(set(self.trigger_points.keys()))
        for id in ids:
            if id in self.sensor_names:
                name = self.sensor_names[id]
            else:
                name = ""
            line = str(id) + ". " + name
            if id in self.trigger_points:
                line += " = "
                points = self.trigger_points[id]
                temps = points.keys()
                temps.sort()
                for temp in temps:
                    level = points[temp]
                    line += "%d:%d " % (temp, level)
            res += line + "\n"
        res += "\n"
        # e.g. "1. /sys/devices/virtual/hwmon/hwmon0/temp1_input : lm_sensors"
        for i, path in lm_sensors.iteritems():
            line = str(i) + ". " + str(path) + " : lm_sensors\n"
        res += "\n"
        res += "hysteresis = %d\n" % self.hysteresis
        return res

    def read_file(self, path):
        """reads a fan profile or a config file"""
        current_file = {"enabled" : False, "override_profile" : False , "sensors" : "ibm_thermal", "sensor_names" : [], "trigger_points" : [], "lm_sensors" : {} , "hysteresis" : 0, "min_interval_duration" : 0, "comment" : ""}
        try:
            file = open(path, "r")
        except Exception,ex:
            print "Error:  Can't open " + path
            print ex
            return False, current_file

        for line in file.readlines():
            line = line.split("#")[0].strip()
            if len(line) > 0:
                try:
                    # line contains path to a sensor
                    # e.g. "1. /sys/devices/virtual/hwmon/hwmon0/temp1_input : lm_sensors"
                    if line.count(".")==1 and line.count(":")==1 and line.count("lm_sensors")==1:
                        id, rest = line.split(".", 1)
                        id = id.strip()
                        id = int(id)
                        path, _ = rest.split(":", 1)
                        path  = path.split()
                        path  = path[0]
                        current_file["lm_sensors"][id] = path
                    # line contains temperature thresholds
                    # e.g "10. Sensor 10 = 0:0 56:2 60:4 65:255"
                    elif (line.count(".") and line.count("=") and line.find(".") < line.find("=")) or (line.count(".") and not line.count("=")):
                        id, rest = line.split(".", 1)
                        id = id.strip()
                        id = int(id)
                        # line contains triggers
                        # e.g. "10. Sensor 10 = 0:0 56:2 60:4 65:255"
                        if rest.count("="):
                            name, triggers = rest.split("=", 1)
                            name = name.strip()
                            points = { }
                            for trigger in triggers.strip().split(" "):
                                trigger = trigger.strip()
                                if len(trigger) > 0:
                                    temp, level = trigger.split(":")
                                    temp = int(temp)
                                    points[temp] = int(level)
                            if len(points) > 0:
                                current_file["trigger_points"].append(points)
                            else:
                                current_file["trigger_points"].append({{0:255}})
                            if len(name) > 0:
                                current_file["sensor_names"].append(name)
                            else:
                                current_file["sensor_names"].append("Sensor " + str(id))
                        # line contains only sensor name or only sensor number
                        # e.g. "10. CPU Sensor" or "10." only
                        else:
                            name = rest.strip()
                            if len(name) > 0:
                                current_file["sensor_names"].append(name)
                            else:
                                current_file["sensor_names"].append("Sensor " + str(id))

                    # line contains other stuff
                    elif line.count("="):
                        option, value = line.split("=", 1)
                        option = option.strip()
                        value = value.strip()

                        if option == "enabled":
                            current_file["enabled"] = (value=="True")
                        elif option == "override_profile":
                            current_file["override_profile"] = (value=="True")
                        elif option == "hysteresis":
                            if value.isdigit():
                                current_file["hysteresis"] = int(value)
                        elif option == "min_interval_duration":
                            if value.isdigit():
                                current_file["min_interval_duration"] = int(value)
                        elif option == "sensors":
                            current_file["sensors"] = value
                        elif option == "comment":
                            current_file["comment"] = value.replace("\\n", "\n")
                            # verify that comment is valid unicode, otherwise use Latin1 coding
                            try:
                                unicode(current_file["comment"])
                            except UnicodeDecodeError:
                                current_file["comment"] = current_file["comment"].decode("latin1")
                except Exception, ex:
                    print "Error parsing line: %s" % line
                    print ex
        file.close()
        return True, current_file

    def term_handler(self):
        """Removes the pid file before terminating tpfand"""
        print "Settings module requests emergency exit!"
        try:
            os.remove(self.build.pid_path)
        except Exception, ex:
            #print "Can't delete " +  self.build.pid_path + ". Please do this by hand!"
            #print ex
            pass
        #self.loop.quit()
