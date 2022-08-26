"""Application exporter"""

import os
import time
from prometheus_client import start_http_server, Gauge, Enum, Info
import datetime
import binascii
import sys
import decimal
import re
import crcmod.predefined
import serial
import json
import random
import logging

crc16 = crcmod.predefined.mkPredefinedCrcFun('crc16')

LOG_LEVEL = os.getenv("LOG_LEVEL", "WARN")

PROMETHEUS_PREFIX = os.getenv("PROMETHEUS_PREFIX", "openweathermap")
PROMETHEUS_PORT   = int(os.getenv("PROMETHEUS_PORT", "9003"))

pool_frequency = int(os.getenv("POOL_FREQUENCY", "60"))
device = os.getenv("P1_DEVICE", "/dev/ttyUSB0")
baudrate = int(os.getenv("P1_BAUDRATE", 115200))

LOGFORMAT = '%(asctime)-15s %(message)s'

logging.basicConfig(level=LOG_LEVEL, format=LOGFORMAT)
LOG = logging.getLogger("openweathermap-export")

class SmartMeter(object):

    def __init__(self, device, baudrate, *args, **kwargs):
        try:
            self.serial = serial.Serial(
                device,
                kwargs.get('baudrate', baudrate),
                timeout=10,
                bytesize=serial.SEVENBITS,
                parity=serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE
            )
        except (serial.SerialException,OSError) as e:
            raise SmartMeterError(e)
        else:
            self.serial.setRTS(False)
            self.port = self.serial.name

        f = open('p1.json', "r")
        self._datadetails = json.load(f)
        f.close()

    def connect(self):
        if not self.serial.isOpen():
            self.serial.open()
            self.serial.setRTS(False)

    def disconnect(self):
        if self.serial.isOpen():
            self.serial.close()

    def connected(self):
        return self.serial.isOpen()

    def read_one_packet(self):
        datagram = b''
        lines_read = 0
        startFound = False
        endFound = False
        max_lines = 35 #largest known telegram has 35 lines

        while not startFound or not endFound:
            try:
                line = self.serial.readline()
            except Exception as e:
                raise SmartMeterError(e)

            lines_read += 1

            if re.match(b'.*(?=/)', line):
                startFound = True
                endFound = False
                datagram = line.lstrip()
            elif re.match(b'(?=!)', line):
                endFound = True
                datagram = datagram + line
            else:
                datagram = datagram + line

            # TODO: build in some protection for infinite loops

        return P1Packet(self._datadetails, datagram)

class SmartMeterError(Exception):
    pass

class P1PacketError(Exception):
    pass

class P1Packet(object):
    _datagram = ''
    _datadetails = None
    _keys = {}

    def __init__(self, _datadetails, datagram):
        self._datadetails =_datadetails
        self._datagram = datagram

        self.validate()
        self.split()

    def getItems(self):
        return self.self._keys

    def __getitem__(self, key):
        return self.self._keys[key]


    def get_float(self, regex, default=None):
        result = self.get(regex, None)
        if not result:
            return default
        return float(self.get(regex, default))

    def get_int(self, regex, default=None):
        result = self.get(regex, None)
        if not result:
            return default
        return int(result)


    def get(self, regex, default=None):
        results = re.search(regex, self._datagram, re.MULTILINE)
        if not results:
            return default
        return results.group(1).decode('ascii')


    def validate(self):
        pattern = re.compile(b'\r\n(?=!)')
        for match in pattern.finditer(self._datagram):
            packet = self._datagram[:match.end() + 1]
            checksum = self._datagram[match.end() + 1:]

        if checksum.strip():
            given_checksum = int('0x' + checksum.decode('ascii').strip(), 16)
            calculated_checksum = crc16(packet)

            if given_checksum != calculated_checksum:
                raise P1PacketError('P1Packet with invalid checksum found')

    def split(self):
        self._keys = {}
        pattern = re.compile(b'(.*?)\\((.*?)\\)\r\n')
        for match in pattern.findall(self._datagram):
            key = match[0].decode("utf-8")
            if key in self._datadetails:
                if 'key' in self._datadetails[key]:
                    LOG.info("found: " + key + " = " + match[1].decode("utf-8") + " : "+ self._datadetails[key]['value'])

                    fieldname = self._datadetails[key]['key']
                    prometheus = self._datadetails[key]['prometheus']
                    source = self._datadetails[key]['source']

                    value = match[1].decode("utf-8")
                    splitted = value.split("(")
                    if len(splitted) > 1:
                        value = splitted[1]

                    if 'unit' in self._datadetails[key]:
                        value = value.replace(self._datadetails[key]['unit'], "")

                    if 'type' in self._datadetails[key]:
                        if self._datadetails[key]['type'] == "float":
                            value = float(value)
                    if 'calculate' in self._datadetails[key]:
                        for cal in self._datadetails[key]["calculate"]:
                            if cal not in self._keys:
                                self._keys[cal] = 0

                            if self._datadetails[key]["calculate"][cal] == "add":
                                self._keys[cal] = self._keys[cal] + value

                            if self._datadetails[key]["calculate"][cal] == "minus":
                                self._keys[cal] = self._keys[cal] - value

                        LOG.info(self._keys[cal])

                    LOG.info(fieldname)
                    LOG.info(prometheus)
                    LOG.info(value)
                    LOG.info(source)
                    self._keys[fieldname] = { 'fieldname': fieldname, 'prometheus': prometheus, 'value': value, 'source': source }
            else:
                LOG.warn("not found: " + key + " = " + match[1].decode("utf-8"))

    def __str__(self):
        return self._datagram.decode('ascii')

class AppMetrics:
    """
    Representation of Prometheus metrics and loop to fetch and transform
    application metrics into Prometheus metrics.
    """

    def __init__(self, PROMETHEUS_PREFIX='', pool_frequency=5, device='/dev/ttyUSB0', baudrate=115200):
        self.gas_value=0
        if PROMETHEUS_PREFIX != '':
            PROMETHEUS_PREFIX = PROMETHEUS_PREFIX + "_"

        self.PROMETHEUS_PREFIX = PROMETHEUS_PREFIX
        self.pool_frequency = pool_frequency

        self.meter = SmartMeter(device, baudrate)

        self.prometheus = {}
        for record in self.meter._datadetails: 
            if 'key' in self.meter._datadetails[record]:
                if self.meter._datadetails[record]['prometheus'] == "Info":
                    self.prometheus[self.meter._datadetails[record]['key']] = Info(PROMETHEUS_PREFIX + self.meter._datadetails[record]['key'], self.meter._datadetails[record]['value'])
                if self.meter._datadetails[record]['prometheus'] == "Gauge":
                    self.prometheus[self.meter._datadetails[record]['key']] = Gauge(PROMETHEUS_PREFIX + self.meter._datadetails[record]['key'], self.meter._datadetails[record]['value'])
        
    def run_metrics_loop(self):
        """Metrics fetching loop"""

        while True:
            self.fetch()
            time.sleep(self.pool_frequency)

    def fetch(self):
        """
        Get metrics from application and refresh Prometheus metrics with
        new values.
        """

        values = self.meter.read_one_packet()

        if values._keys["GAS_READING"]:
            
            if self.gas_value > 0:
                if values._keys["GAS_READING"] > 0:
                    values._keys["GAS_DELTA"] = values._keys["GAS_READING"] - self.gas_value
            self.gas_value = values._keys["GAS_READING"]

        for k, v in values._keys.items():
            LOG.info(k)
            LOG.info(v)
            LOG.info(v['prometheus'])

            if v['prometheus'] == "Info":
                self.weather_icon_name.info({ v['source']: v.value } )
            if v['prometheus'] == "Gauge":
                self.visibility_distance.set(v['value'])
        
        LOG.info("Update prometheus")

def main():
    """Main entry point"""    
    
    app_metrics = AppMetrics(
        PROMETHEUS_PREFIX=PROMETHEUS_PREFIX,
        pool_frequency=pool_frequency,
        device=device, 
        baudrate=baudrate
    )
    start_http_server(PROMETHEUS_PORT)
    LOG.info("start prometheus port: %s", PROMETHEUS_PORT)
    app_metrics.run_metrics_loop()
 
if __name__ == "__main__":
    main()