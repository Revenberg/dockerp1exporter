import datetime
import os
import binascii
import sys
import decimal
import re
import crcmod.predefined
import serial
import json
import time
import random

mqttclientid = f'python-mqtt-{random.randint(0, 1000)}'
crc16 = crcmod.predefined.mkPredefinedCrcFun('crc16')

do_raw_log = os.getenv("LOGGING", "false").lower() == 'true'

pool_frequency = int(os.getenv("POOL_FREQUENCY", "60"))

device = os.getenv("P1_DEVICE", "/dev/ttyUSB0")
baudrate = int(os.getenv("P1_BAUDRATE", "115200"))

mqttBroker = os.getenv("MQTT_ADDRESS", "localhost")
mqttPort = int(os.getenv("MQTT_PORT", "1883"))
mqttTopic = os.getenv("MQTT_TOPIC", "reading/p1")

values = dict()

gas_value = 0

class SmartMeter(object):

    def __init__(self, port, *args, **kwargs):
        try:
            self.serial = serial.Serial(
                port,
                kwargs.get('baudrate', 115200),
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

        return P1Packet(datagram)

class SmartMeterError(Exception):
    pass

class P1PacketError(Exception):
    pass

class P1Packet(object):
    _datagram = ''
    _datadetails = None
    _keys = {}

    def __init__(self, datagram):

        f = open('p1.json', "r")
        self._datadetails = json.load(f)
        f.close()

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
                    if do_raw_log:
                        print("found: " + key + " = " + match[1].decode("utf-8") + " : "+ self._datadetails[key]['value'])

                    fieldname = self._datadetails[key]['key']

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

                        if do_raw_log:
                            print(self._keys[cal])

                    if do_raw_log:
                        print(fieldname)
                        print(value)
                    self._keys[fieldname] = value
            else:
                if do_raw_log:
                    print("not found: " + key + " = " + match[1].decode("utf-8"))
                print("not found: " + key + " = " + match[1].decode("utf-8"))

    def __str__(self):
        return self._datagram.decode('ascii')

def getData(client, mqttTopic, device, baudrate, pool_frequency):

    meter = SmartMeter(device, baudrate)

    while True:
        values = meter.read_one_packet()

        if values._keys["GAS_READING"]:
            global gas_value
            
            if gas_value > 0:
                if values._keys["GAS_READING"] > 0:
                    values._keys["GAS_DELTA"] = values._keys["GAS_READING"] - gas_value
            gas_value = values._keys["GAS_READING"]
        json_body = { k: v for k, v in values._keys.items() }                     

        if do_raw_log:
            print(f"Send topic `{mqttTopic}`")
            print(f"Send topic `{json_body}`")

        result = client.publish(mqttTopic, json.dumps(json_body))
        # result: [0, 1]
        status = result[0]

        if status == 0:
            if do_raw_log:
                print(f"Send topic `{mqttTopic}`")
        else:
            print(f"Failed to send message to topic {mqttTopic} ")

        time.sleep(pool_frequency)

def connect_mqtt(mqttclientid, mqttBroker, mqttPort ):
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT Broker!")
        else:
            print("Failed to connect, return code %d\n", rc)
    # Set Connecting Client ID
    client = mqtt.Client(mqttclientid)
#    client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.connect(mqttBroker, mqttPort)
    return client

client=connect_mqtt(mqttclientid, mqttBroker, mqttPort )
client.loop_start()
getData(client, mqttTopic, device, baudrate, pool_frequency)
