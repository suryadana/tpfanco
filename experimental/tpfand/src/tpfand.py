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
if not ("/usr/share/pyshared" in sys.path):
    sys.path.append("/usr/share/pyshared")
if not ("/usr/lib/python2.7/site-packages" in sys.path):
    sys.path.append("/usr/lib/python2.7/site-packages")

import os
import os.path
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
import signal
import gobject
from tpfand import build, settings, control, polkit

class Tpfand(object):

    quiet = False
    debug = False
    noibmthermal = False

    def __init__(self,quiet,debug,noibmthermal):
        self.quiet = quiet
        self.debug = debug
        self.noibmthermal = noibmthermal


        self.start_fan_control()
    def start_fan_control(self):
        """ daemon start function """
        if not self.quiet:
            print "tpfand " + build.version
            print "Copyright (C) 2011-2012 Vladyslav Shtabovenko"
            print "Copyright (C) 2007-2009 Sebastian Urban"
            print "This program comes with ABSOLUTELY NO WARRANTY"
            print
            print "WARNING: THIS PROGRAM MAY DAMAGE YOUR COMPUTER."
            print "         PROCEED ONLY IF YOU KNOW HOW TO MONITOR SYSTEM TEMPERATURE."
            print

        if self.debug:
            print "Running in debug mode"

        if not self.is_system_suitable():
            print "Fatal error: unable to set fanspeed or enable watchdog"
            print "             Please make sure you are root and a recent"
            print "             thinkpad_acpi module is loaded with fan_control=1"
            print "             If thinkpad_acpi is already loaded, check that"
            print "             " + build.ibm_fan + " exists. Thinkpad models"
            print "             that don't have this file are not supported."
            sys.exit(1)

        if os.path.isfile(build.pid_path):
            print "Fatal error: already running or " + build.pid_path + " left behind"
            sys.exit(1)

        # go into daemon mode
        self.daemonize()

    def daemonize(self):
        """ don't go into daemon mode if debug mode is active """
        if not debug:
            """go into daemon mode"""
            # from: http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/66012
            # do the UNIX double-fork magic, see Stevens' "Advanced
            # Programming in the UNIX Environment" for details (ISBN 0201563177)
            try:
                pid = os.fork()
                if pid > 0:
                    # exit first parent
                    sys.exit(0)
            except OSError, e:
                print >>sys.stderr, "fork #1 failed: %d (%s)" % (e.errno, e.strerror)
                sys.exit(1)

            # decouple from parent environment
            os.chdir("/")
            os.setsid()
            os.umask(0)

            # do second fork
            try:
                pid = os.fork()
                if pid > 0:
                    sys.exit(0)
            except OSError, e:
                print >>sys.stderr, "fork #2 failed: %d (%s)" % (e.errno, e.strerror)
                sys.exit(1)

        # write pid file
        try:
            pidfile = open(build.pid_path, "w")
            pidfile.write(str(os.getpid()) + "\n")
            pidfile.close()
        except IOError:
            print >>sys.stderr, "could not write pid-file: ", build.pid_path
            sys.exit(1)
        # start the daemon main loop
        self.daemon_main()

    def daemon_main(self):
        """daemon entry point"""

         # register SIGTERM, SIGINT and sys.exit handlers
        signal.signal(signal.SIGTERM, self.term_handler)
        signal.signal(signal.SIGINT, self.term_handler)
        # register d-bus service
        gobject.threads_init()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.system_bus = dbus.SystemBus()
        bus_name = dbus.service.BusName(build.dbus_tpfand_name, self.system_bus)
        # initialize policy kit
        self.policy_kit = self.system_bus.get_object("org.freedesktop.PolicyKit1","/org/freedesktop/PolicyKit1/Authority")
        self.pk_authority = dbus.Interface(self.policy_kit, "org.freedesktop.PolicyKit1.Authority")
        self.tpfand_polkit = polkit.Polkit(self.system_bus,self.policy_kit,self.pk_authority)


        # create and load configuration
        act_settings = settings.Settings(self.system_bus,"/Settings",self.tpfand_polkit,build,self.debug,self.noibmthermal)

        # create controller
        self.controller = control.Control(self.system_bus,"/Control",act_settings,self.tpfand_polkit,build)

        #star glib main loop
        self.loop = gobject.MainLoop()
        self.loop.run()

    def is_system_suitable(self):
        """returns True if fan speed setting and watchdog are supported by kernel and
           we have write permissions"""
        try:
          fanfile = open(build.ibm_fan, "w")
          fanfile.write("level auto")
          fanfile.flush()
          fanfile.close()
          fanfile = open(build.ibm_fan, "w")
          fanfile.write("watchdog 5")
          fanfile.flush()
          fanfile.close()
          return True
        except IOError:
          return False

    def term_handler(self,signum, frame):
        """Handles SIGTERM and SIGINT"""
        self.controller.set_speed(255)
        print ""
        print "Terminating tpfand ..."
        try:
            os.remove(build.pid_path)
        except Exception, ex:
            #print "Can't delete " +  build.pid_path + ". Please do this by hand!"
            #print ex
            pass
        self.loop.quit()
        print "done!"

if __name__ == "__main__":
    quiet = False
    debug = False
    noibmthermal = False
    recreate_config = False
    if "--quiet" in sys.argv:
        quiet = True
    if "--debug" in sys.argv:
        debug = True
    if "--noibmthermal" in sys.argv:
        noibmthermal = True

    app = Tpfand(quiet,debug,noibmthermal)
