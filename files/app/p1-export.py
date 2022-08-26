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

PROMETHEUS_PREFIX = os.getenv("PROMETHEUS_PREFIX", "p1")
PROMETHEUS_PORT   = int(os.getenv("PROMETHEUS_PORT", "9003"))

pool_frequency = int(os.getenv("POOL_FREQUENCY", "60"))
device = os.getenv("P1_DEVICE", "/dev/ttyUSB0")
baudrate = int(os.getenv("P1_BAUDRATE", 115200))

LOGFORMAT = '%(asctime)-15s %(message)s'

logging.basicConfig(level=LOG_LEVEL, format=LOGFORMAT)
LOG = logging.getLogger("p1-export")

class P1Prometheus(object):
    PROMETHEUS_PREFIX = ''
    datadetails = {}
    gas_value = 0    
    _prometheus = {}
    _keys = {}

    def __init__(self, device, baudrate, PROMETHEUS_PREFIX, *args, **kwargs):
        self.PROMETHEUS_PREFIX = PROMETHEUS_PREFIX
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
            raise P1PrometheusError(e)
        else:
            self.serial.setRTS(False)
            self.port = self.serial.name

        f = open('p1.json', "r")
        self.datadetails = json.load(f)
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
                raise P1PrometheusError(e)

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

        LOG.info("validate")
        self.validate(datagram)       
        LOG.info("split")
        self.split( datagram)
        LOG.info("done")

    def validate(self, datagram):
        pattern = re.compile(b'\r\n(?=!)')
        for match in pattern.finditer(datagram):
            packet = datagram[:match.end() + 1]
            checksum = datagram[match.end() + 1:]

        if checksum.strip():
            given_checksum = int('0x' + checksum.decode('ascii').strip(), 16)
            calculated_checksum = crc16(packet)

            if given_checksum != calculated_checksum:
                raise P1PacketError('P1Packet with invalid checksum found')

    def split(self, datagram):
        LOG.info("split")
        LOG.info(datagram)
        self._keys = {}
        pattern = re.compile(b'(.*?)\\((.*?)\\)\r\n')        
        for match in pattern.findall(datagram):            
            key = match[0].decode("utf-8")
            LOG.info(key)
            if key in self.datadetails:
                LOG.info("found")
                if 'fieldname' in self.datadetails[key]:
                    LOG.info("found: " + key + " = " + match[1].decode("utf-8") + " : "+ self.datadetails[key]['description'])

                    fieldname = self.datadetails[key]['fieldname']
                    prometheus = self.datadetails[key]['prometheus']
                    source = self.datadetails[key]['source']
                    description = self.datadetails[key]['description']

                    value = match[1].decode("utf-8")
                    splitted = value.split("(")
                    if len(splitted) > 1:
                        value = splitted[1]

                    if 'unit' in self.datadetails[key]:
                        value = value.replace(self.datadetails[key]['unit'], "")

                    if 'type' in self.datadetails[fieldname]:
                        if self.datadetails[fieldname]['type'] == "float":
                            value = float(value)
                    if 'calculate' in self.datadetails[fieldname]:
                        for cal in self.datadetails[fieldname]["calculate"]:
                            if cal not in self._keys:
                                self._keys[cal] = 0

                            if self.datadetails[fieldname]["calculate"][cal] == "add":
                                self._keys[cal] = self._keys[cal] + value

                            if self.datadetails[fieldname]["calculate"][cal] == "minus":
                                self._keys[cal] = self._keys[cal] - value

                        LOG.info(self._keys[cal])
               
                    LOG.info(fieldname)                    
                    LOG.info(prometheus)
                    LOG.info(description)
                    LOG.info(value)
                    LOG.info(source)

                    if fieldname == ["GAS_READING"]:
                        if self.gas_value > 0:
                            if value > 0:
                                fieldname = "GAS_DELTA"
                                value = value - self.gas_value
                        self.gas_value = value

                    if not fieldname in prometheus:
                        if prometheus == "Info":
                            prometheus[fieldname] = Info(self.PROMETHEUS_PREFIX + fieldname, description)
                        if prometheus == "Gauge":
                            prometheus[fieldname] = Gauge(self.PROMETHEUS_PREFIX + fieldname, description)                           

                    if fieldname in prometheus:
                        if prometheus == "Info":
                            prometheus[fieldname].info(value)
                        if prometheus == "Gauge":
                            prometheus[fieldname].set(value)                    
            else:
                LOG.warn("not found: " + key + " = " + match[1].decode("utf-8"))

class P1PrometheusError(Exception):
    pass

class P1PacketError(Exception):
    pass

class AppMetrics:
    pool_frequency = 0
    """
    Representation of Prometheus metrics and loop to fetch and transform
    application metrics into Prometheus metrics.
    """

    def __init__(self, PROMETHEUS_PREFIX='', pool_frequency=60, device='/dev/ttyUSB0', baudrate=115200):
        self.pool_frequency = pool_frequency
        self.meter = P1Prometheus(device, baudrate, PROMETHEUS_PREFIX)
        
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

        self.meter.read_one_packet()
        
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