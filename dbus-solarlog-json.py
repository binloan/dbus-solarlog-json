#!/usr/bin/env python
 
# import normal packages
import platform 
import json
import logging
import sys
import os
import sys
import csv
if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject
import sys
import time
import requests # for http GET
import configparser # for config/ini file
 
# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService


class DbusSolarlogJsonService:
  def __init__(self, servicename, paths, productname='SolarLog JSON', connection='Solarlog HTTP JSON service'):
    config = self._getConfig()
    deviceinstance = int(config['DEFAULT']['Deviceinstance'])
    customname = config['DEFAULT']['CustomName']
    
    self._dbusservice = VeDbusService("{}.http_{:02d}".format(servicename, deviceinstance))
    self._paths = paths
    
    logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))
    
    # Create the management objects, as specified in the ccgx dbus-api document
    self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
    self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
    self._dbusservice.add_path('/Mgmt/Connection', connection)
    
    # Create the mandatory objects
    self._dbusservice.add_path('/DeviceInstance', deviceinstance)
    #self._dbusservice.add_path('/ProductId', 16) # value used in ac_sensor_bridge.cpp of dbus-cgwacs
    self._dbusservice.add_path('/ProductId', 0xFFFF) # id assigned by Victron Support from SDM630v2.py
    self._dbusservice.add_path('/ProductName', productname)
    self._dbusservice.add_path('/CustomName', customname)    
    self._dbusservice.add_path('/Connected', 1)
    
    self._dbusservice.add_path('/Latency', None)    
    self._dbusservice.add_path('/FirmwareVersion', 0.1)
    self._dbusservice.add_path('/HardwareVersion', 0)
    self._dbusservice.add_path('/Position', 0) # normaly only needed for pvinverter
    self._dbusservice.add_path('/Serial', '1234')
    self._dbusservice.add_path('/UpdateIndex', 0)
    self._dbusservice.add_path('/StatusCode', 0)  # Dummy path so VRM detects us as a PV-inverter.
    
    # add path values to dbus
    for path, settings in self._paths.items():
      self._dbusservice.add_path(
        path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)

    # last update
    self._lastUpdate = 0

    # add _update function 'timer'
    gobject.timeout_add(5000, self._update) # pause 5s before the next request
    
    # add _signOfLife 'timer' to get feedback in log every 5minutes
    gobject.timeout_add(self._getSignOfLifeInterval()*60*1000, self._signOfLife)
 
  
  def _getConfig(self):
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config;
 
 
  def _getSignOfLifeInterval(self):
    config = self._getConfig()
    value = config['DEFAULT']['SignOfLifeLog']
    
    if not value: 
        value = 0
    
    return int(value)
  
  
  def _getSolarlogStatusUrl(self):
    config = self._getConfig()
    accessType = config['DEFAULT']['AccessType']
    
    if accessType == 'OnPremise': 
        URL = "http://%s:%s@%s/getjp" % (config['ONPREMISE']['Username'], config['ONPREMISE']['Password'], config['ONPREMISE']['Host'])
        URL = URL.replace(":@", "")
    else:
        raise ValueError("AccessType %s is not supported" % (config['DEFAULT']['AccessType']))
    
    return URL
    
 
  def _getSolarlogData(self):
    URL = self._getSolarlogStatusUrl()
    meter_r = requests.post(url = URL, data= json.dumps({"782":None}), headers={"Content-Type":"application/json"})
    
    meter_t = requests.post(url = URL, data= json.dumps({"801":{"170":None}}), headers={"Content-Type":"application/json"})
    	
    # check for response
    if not meter_r:
        raise ConnectionError("No response from Solarlog - %s" % (URL))
    
    meter_data = [meter_r.json(),meter_t.json()]

    # check for Json
    if not meter_data[0]:
        raise ValueError("Converting response to JSON failed")
    
    
    return meter_data
 
 
  def _signOfLife(self):
    logging.info("--- Start: sign of life ---")
    logging.info("Last _update() call: %s" % (self._lastUpdate))
    logging.info("Last '/Ac/Power': %s" % (self._dbusservice['/Ac/Power']))
    logging.info("--- End: sign of life ---")
    return True
 
  def _update(self):   
    try:
       #get data from Solarlog
       meter_data = self._getSolarlogData()
              
       config = self._getConfig()
       
       #Solarlog Device PAC Mapping 
       #changed to work with single three phase inverter
       pac1 = int(meter_data[0]['782']['0'])
       pac = pac1 / 3
       l1p = pac
       l2p = pac
       l3p = pac
      
       total = meter_data[1]['801']['170']['109']
       #send data to DBus
       
       voltage = int(meter_data[1]['801']['170']['103'])
       if (voltage==0): 
           voltage=230

       self._dbusservice['/Ac/Current'] = round(pac/voltage,2)
       self._dbusservice['/Ac/L1/Current'] = round(l1p/voltage,2)
       self._dbusservice['/Ac/L2/Current'] = round(l2p/voltage,2)
       self._dbusservice['/Ac/L3/Current'] = round(l3p/voltage,2)
       self._dbusservice['/Ac/L1/Voltage'] = voltage
       self._dbusservice['/Ac/L2/Voltage'] = voltage
       self._dbusservice['/Ac/L3/Voltage'] = voltage
       self._dbusservice['/Ac/Power'] = pac1
       self._dbusservice['/Ac/L1/Power'] = l1p
       self._dbusservice['/Ac/L2/Power'] = l2p
       self._dbusservice['/Ac/L3/Power'] = l3p

#       Total from Json ?
#       self._dbusservice['/Ac/Energy/Forward'] = total / 1000.0
#       self._dbusservice['/Ac/L1/Energy/Forward'] = total / 1000.0 /3
#       self._dbusservice['/Ac/L2/Energy/Forward'] = total / 1000.0 /3
#       self._dbusservice['/Ac/L3/Energy/Forward'] = total / 1000.0 /3 
       
 
       # Get 3p total energy from file
       with open('/data/dbus-solarlog-json/counter.txt') as csv_file:
           csv_reader = csv.reader(csv_file, delimiter=';')
           line_count = 0
           for row in csv_reader:
               if line_count == 0:
                   l1=(row[0])
                   l2=(row[1])
                   l3=(row[2])

       l1c = float(l1) + float(l1p) * 5 * 1/3600 / 1000
       l2c = float(l2) + float(l2p) * 5 * 1/3600 / 1000
       l3c = float(l3) + float(l3p) * 5 * 1/3600 / 1000

       self._dbusservice['/Ac/Energy/Forward'] = l1c + l2c + l3c
       self._dbusservice['/Ac/L1/Energy/Forward'] = l1c
       self._dbusservice['/Ac/L2/Energy/Forward'] = l2c
       self._dbusservice['/Ac/L3/Energy/Forward'] = l3c

       # write 3p total energy to file as kwh.       
       with open('/data/dbus-solarlog-json/counter.txt', mode='w') as counter_file:
           counter_writer = csv.writer(counter_file, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
           counter_writer.writerow([l1c, l2c, l3c])

        
       # increment UpdateIndex - to show that new data is available
       index = self._dbusservice['/UpdateIndex'] + 1  # increment index
       if index > 255:   # maximum value of the index
         index = 0       # overflow from 255 to 0
       self._dbusservice['/UpdateIndex'] = index

       #update lastupdate vars
       self._lastUpdate = time.time()              
    except Exception as e:
       logging.critical('Error at %s', '_update', exc_info=e)
       
    # return true, otherwise add_timeout will be removed from GObject - see docs http://library.isr.ist.utl.pt/docs/pygtk2reference/gobject-functions.html#function-gobject--timeout-add
    return True
 
  def _handlechangedvalue(self, path, value):
    logging.debug("someone else updated %s to %s" % (path, value))
    return True # accept the change
 


def main():
  #configure logging
  logging.basicConfig(      format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S',
                            level=logging.INFO,
                            handlers=[
                                logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                                logging.StreamHandler()
                            ])
 
  try:
      logging.info("Start");
  
      from dbus.mainloop.glib import DBusGMainLoop
      # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
      DBusGMainLoop(set_as_default=True)
     
      #formatting 
      _kwh = lambda p, v: (str(round(v, 2)) + 'KWh')
      _a = lambda p, v: (str(round(v, 1)) + 'A')
      _w = lambda p, v: (str(round(v, 1)) + 'W')
      _v = lambda p, v: (str(round(v, 1)) + 'V')   
     
      #start our main-service
      pvac_output = DbusSolarlogJsonService(
        servicename='com.victronenergy.pvinverter',
        paths={
          '/Ac/Energy/Forward': {'initial': None, 'textformat': _kwh}, # energy produced by pv inverter
          '/Ac/Power': {'initial': 0, 'textformat': _w},
          
          '/Ac/Current': {'initial': 0, 'textformat': _a},
          '/Ac/Voltage': {'initial': 0, 'textformat': _v},
          
          '/Ac/L1/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L2/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L3/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L1/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L2/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L3/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L2/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L3/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L1/Energy/Forward': {'initial': None, 'textformat': _kwh},
          '/Ac/L2/Energy/Forward': {'initial': None, 'textformat': _kwh},
          '/Ac/L3/Energy/Forward': {'initial': None, 'textformat': _kwh},
        })
     
      logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
      mainloop = gobject.MainLoop()
      mainloop.run()            
  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)
if __name__ == "__main__":
  main()
