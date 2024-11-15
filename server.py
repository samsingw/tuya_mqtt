#!/usr/bin/env python
"""
 TinyTuya - Poll devices in Devices.json and publish to mqtt using Homie convention

 Author: Louis Rossouw
"""

import tinytuya
import json
import time
import threading
import re
from pprint import pprint
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta

import logging
from config_defaults import *
from config import *
from MappedDevice import MappedDevice

# create logger
logger = logging.getLogger("tuya_mqtt")
logger.setLevel(LOGGING_LEVEL_CONSOLE)

# create formatter and add it to the handlers
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# create file handler which logs even debug messages
if LOGGING_FILE != None:
    fh = logging.FileHandler(LOGGING_FILE)
    fh.setLevel(LOGGING_LEVEL_FILE)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(LOGGING_LEVEL_CONSOLE)
ch.setFormatter(formatter)
logger.addHandler(ch)


def format_homie_id(s):
    return re.sub(r"[\W_]", "", s).lower()


class DeviceMonitor:
    def __init__(self, device_info):
        self.id = device_info["id"]
        self.homie_device_id = self.id
        self.key = device_info["key"]
        self.name = device_info["name"]
        self.label = device_info["name"]  # + "(" + device_info['id'] + ")"
        print(device_info)
        self.version = float(device_info["version"])
        self.device_info = device_info
        self.homie_device_id = format_homie_id(self.name)
        self.homie_device_info = []  # list of nodes and properties
        self.homie_init_time = datetime(1900, 1, 1)
        self.homie_publish_all_time = datetime(1900, 1, 1)
        self.tuya_last_data_time = datetime.now()

        logger.info("Initialising device instance for {}...".format(self.label))

        # mqtt client
        self.mqtt = mqtt.Client(
            client_id="{}-{}".format(MQTT_CLIENT_ID, self.homie_device_id)
        )
        self.mqtt.on_message = self.homie_message

        # MQTT Will
        topic = "{}/{}/{}".format(HOMIE_BASE_TOPIC, self.homie_device_id, "$state")
        self.mqtt.will_set(
            topic, payload="lost", qos=HOMIE_MQTT_QOS, retain=HOMIE_MQTT_RETAIN
        )
        # MQTT callback
        self.mqtt.on_connect = self.on_mqtt_connect
        self.mqtt.on_disconnect = self.on_mqtt_disconnect

        # Not connected
        self.tuya_connected = False

        # connect to MQTT
        self.mqtt_connect(
            host=MQTT_HOST,
            port=MQTT_PORT,
            username=MQTT_USERNAME,
            password=MQTT_PASSWORD,
        )

        # do homie init
        self.do_homie_init = True

    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("{} connected to MQTT...".format(self.label))
            self.mqtt.subscribe(
                "{}/{}/{}/{}/{}/{}".format(
                    HOMIE_BASE_TOPIC, self.homie_device_id, "+", "+", "set", "#"
                )
            )
            self.do_homie_init = True
        else:
            logger.info("Connectetion to MQTT failed return code of {}.".format(rc))

    def on_mqtt_disconnect(self, client, userdata, rc):
        logger.error(
            "MQTT was disconnected for {} with return code of {}".format(self.label, rc)
        )

    def mqtt_connect(
        self,
        host="localhost",
        port=1883,
        username=None,
        password=None,
        keepalive=60,
        bind_address="",
    ):
        logger.info("{} connecting to mqtt...".format(self.label))
        if username != None and password != None:
            self.mqtt.username_pw_set(username=username, password=password)
        error = True
        while error:
            try:
                self.mqtt.connect(host, port, keepalive, bind_address)
                error = False
            except Exception as e:
                logger.error(
                    "{} could not connect to mqtt due to {}.".format(self.label, e)
                )
                error = True
                time.sleep(5)
        self.mqtt.loop_start()

    def homie_message(self, client, userdata, message):
        m = str(message.payload.decode("utf-8"))
        logger.info(
            "Received MQTT message topic={}, message={}".format(message.topic, m)
        )
        topics = message.topic.split("/")
        node_topic = topics[2]
        property_topic = topics[3]
        if node_topic != "data":
            logger.error(
                "Invalid node topic {} in received MQTT message.".format(node_topic)
            )

        # get data node
        if "__nodes__" in self.homie_device_info:
            n = next(
                n
                for n in self.homie_device_info["__nodes__"]
                if n["__topic__"] == "data"
            )
        else:
            logger.error(
                "Message on topic {} ignored as we have no nodes for {} (Probably not connected).".format(
                    message.topic, self.label
                )
            )
            return

        properties = filter(
            lambda p: property_topic == p["__topic__"], n["__properties__"]
        )

        for p in properties:
            if p["$settable"] == "true" or p["$settable"] == True:
                v = None
                if p["$datatype"] == "boolean":
                    if m in ["true", "True", "TRUE"]:
                        v = True
                    elif m in ["false", "False", "FALSE"]:
                        v = False
                    else:
                        v = None
                        logger.error("Invalid message {} for boolean type.".format(m))
                elif p["$datatype"] in ("enum", "string"):
                    v = str(m)
                elif p["$datatype"] == "integer":
                    try:
                        v = int(m)
                    except:
                        v = None
                        logger.error("Invalid message {} for integer type.".format(m))
                elif p["$datatype"] == "float":
                    try:
                        v = float(m)
                    except:
                        v = None
                        logger.error("Invalid message {} for float type.".format(m))
                if v != None:
                    self.device.set_value(p["__tuya_code__"], v)
                    logger.info(
                        "Set tuya code {} to value {} for {}.".format(
                            p["__tuya_code__"], v, self.label
                        )
                    )
            else:
                logger.error(
                    "Property topic {} in received MQTT message settable state is {}.".format(
                        property_topic, p["$settable"]
                    )
                )

    def homie_publish(self, topic, message):
        self.mqtt.publish(
            topic=topic, payload=message, qos=HOMIE_MQTT_QOS, retain=HOMIE_MQTT_RETAIN
        )

    def create_device_info_nodes(self):
        nodes = [
            {
                "__topic__": "deviceinfo",
                "$name": "Device info",
                "$properties": "",
                "__properties__": [
                    {
                        "__topic__": "id",
                        "__tuya_code__": "id",
                        "$name": "Tuya Device ID",
                        "$datatype": "string",
                        "$retained": "true",
                        "$settable": "false",
                    },
                    {
                        "__topic__": "mac",
                        "__tuya_code__": "mac",
                        "$name": "Device MAC",
                        "$datatype": "string",
                        "$retained": "true",
                        "$settable": "false",
                    },
                    {
                        "__topic__": "uuid",
                        "__tuya_code__": "uuid",
                        "$name": "UUID",
                        "$datatype": "string",
                        "$retained": "true",
                        "$settable": "false",
                    },
                    {
                        "__topic__": "sn",
                        "__tuya_code__": "sn",
                        "$name": "Serial Number",
                        "$datatype": "string",
                        "$retained": "true",
                        "$settable": "false",
                    },
                    {
                        "__topic__": "sub",
                        "__tuya_code__": "sub",
                        "$name": "Sub Device",
                        "$datatype": "boolean",
                        "$retained": "true",
                        "$settable": "false",
                    },
                    {
                        "__topic__": "icon",
                        "__tuya_code__": "icon",
                        "$name": "Icon URL",
                        "$datatype": "string",
                        "$retained": "true",
                        "$settable": "false",
                    },
                    # this is ip at time devices file was created and may not be current
                    # {
                    #     "__topic__": "ip",
                    #     "__tuya_code__": "ip",
                    #     "$name": "Device IP",
                    #     "$datatype": "string",
                    #     "$retained" : "true",
                    #     "$settable": "false"
                    # },
                    {
                        "__topic__": "version",
                        "__tuya_code__": "version",
                        "$name": "Tuya Version",
                        "$datatype": "string",
                        "$retained": "true",
                        "$settable": "false",
                    },
                ],
            },
            {
                "__topic__": "productinfo",
                "$name": "Product info",
                "$properties": "",
                "__properties__": [
                    {
                        "__topic__": "category",
                        "__tuya_code__": "category",
                        "$name": "Category",
                        "$datatype": "string",
                        "$retained": "true",
                        "$settable": "false",
                    },
                    {
                        "__topic__": "productname",
                        "__tuya_code__": "product_name",
                        "$name": "Product Name",
                        "$datatype": "string",
                        "$retained": "true",
                        "$settable": "false",
                    },
                    {
                        "__topic__": "productid",
                        "__tuya_code__": "product_id",
                        "$name": "Product ID",
                        "$datatype": "string",
                        "$retained": "true",
                        "$settable": "false",
                    },
                    {
                        "__topic__": "biztype",
                        "__tuya_code__": "biz_type",
                        "$name": "Biz Type",
                        "$datatype": "integer",
                        "$retained": "true",
                        "$settable": "false",
                    },
                ],
            },
        ]
        return nodes

    def update_device_nodes_properties(self):
        self.homie_device_info["$nodes"] = ""
        for i, n in enumerate(self.homie_device_info["__nodes__"]):
            if self.homie_device_info["$nodes"] == "":
                self.homie_device_info["$nodes"] = n["__topic__"]
            else:
                self.homie_device_info["$nodes"] = (
                    self.homie_device_info["$nodes"] + "," + n["__topic__"]
                )
            self.homie_device_info["__nodes__"][i]["$properties"] == ""
            for p in n["__properties__"]:
                if self.homie_device_info["__nodes__"][i]["$properties"] == "":
                    self.homie_device_info["__nodes__"][i]["$properties"] = p[
                        "__topic__"
                    ]
                else:
                    self.homie_device_info["__nodes__"][i]["$properties"] = (
                        self.homie_device_info["__nodes__"][i]["$properties"]
                        + ","
                        + p["__topic__"]
                    )

    def create_data_node(self):
        data_node = {
            "__topic__": "data",
            "$name": "Data",
            "$properties": "",
            "__properties__": [],
        }
        for dp in self.status["dps_objects"]:
            if dp.value_type == "bitmap":
                for b in dp.bitmap:
                    p = {
                        "__topic__": format_homie_id(dp.name + b),
                        "__tuya_code__": dp.name,
                        "__tuya_bitmap_value__": b,
                        "$name": dp.name + " " + b,
                        "$settable": "false",  # cannot set bitmaps here
                        "$datatype": "boolean",
                    }
                    data_node["__properties__"].append(p)
            else:
                append_property = True
                print(dp)
                p = {
                    "__topic__": format_homie_id(dp.name),
                    "__tuya_code__": dp.name,
                    "$name": dp.name,
                    "$settable": str(dict(dp)["settable"]).lower(),
#                    "$settable": str(dp.settable).lower(),
                }
                if dp.value_type == "integer":
                    if dp.int_step < 1:
                        p["$datatype"] = "float"
                    else:
                        p["$datatype"] = "integer"
                    p["$format"] = "{}:{}".format(dp.int_min, dp.int_max)
                    if dp.unit:
                        if dp.unit == "Kw·h":
                            p["$unit"] = "kWh"
                        elif dp.unit == "hour":
                            p["$unit"] = "h"
                        else:
                            p["$unit"] = dp.unit
                elif dp.value_type == "enum":
                    p["$datatype"] = dp.value_type
                    p["$format"] = ""
                    for e in dp.enum_range:
                        if p["$format"] == "":
                            p["$format"] = "{}".format(e)
                        else:
                            p["$format"] = "{},{}".format(p["$format"], e)
                elif dp.value_type in ["string", "boolean"]:
                    p["$datatype"] = dp.value_type
                else:
                    append_property == False
                    logger.error("Unknown value type {}".format(dp.value_type))
                if append_property:
                    data_node["__properties__"].append(p)
        self.homie_device_info["__nodes__"].append(data_node)

    def create_homie_device_info(self):
        self.homie_device_info = {
            "$homie": HOMIE_DEVICE_VERSION,
            "$name": self.name,
            "$nodes": "",
            # "$extensions": "",
            "$implementation": HOMIE_IMPLEMENTATION,
            "__nodes__": [],
        }
        if HOMIE_PUBLISH_DEVICE_INFO:
            self.homie_device_info["__nodes__"] = self.create_device_info_nodes()

        self.create_data_node()
        self.update_device_nodes_properties()

    def get_hass_config_template(self):
        topic = "{}/{}/{}".format(HOMIE_BASE_TOPIC, self.homie_device_id, "$state")
        config_template = {
            "availability": {
                "topic": topic,
                "payload_available": "ready",
                "payload_not_available": "lost",
            },
            "availability_mode": "latest",
            "device": {
                "identifiers": [self.device_info["sn"], self.id, self.homie_device_id],
                "model": self.device_info["product_name"],
                "name": self.name,
                "sw_version": self.version,
                "via_device": MQTT_CLIENT_ID,
            },
        }
        return config_template

    def hass_publish_configs(self):
        for n in self.homie_device_info["__nodes__"]:
            for p in n["__properties__"]:
                component = "Unknown"
                unique_id = (
                    self.homie_device_id + "_" + n["__topic__"] + "_" + p["__topic__"]
                )
                config = self.get_hass_config_template()
                config["name"] = p["$name"]
                config["state_topic"] = "{}/{}/{}/{}".format(
                    HOMIE_BASE_TOPIC,
                    self.homie_device_id,
                    n["__topic__"],
                    p["__topic__"],
                )
                command_topic = "{}/{}/{}/{}/{}".format(
                    HOMIE_BASE_TOPIC,
                    self.homie_device_id,
                    n["__topic__"],
                    p["__topic__"],
                    "set",
                )

                config["unique_id"] = unique_id
                if p["$datatype"] == "boolean":
                    config["payload_off"] = "false"
                    config["payload_on"] = "true"
                    if p["$settable"] == "true":
                        component = "switch"
                        config["optimistic"] = False
                        config["command_topic"] = command_topic
                    else:
                        component = "binary_sensor"
                elif p["$datatype"] in ("float", "integer"):
                    if "$unit" in p:
                        config["unit_of_measurement"] = p["$unit"]
                    if p["$settable"] == "true":
                        component = "number"
                        config["optimistic"] = False
                        config["command_topic"] = command_topic
                        if "$format" in p:
                            config["min"], config["max"] = p["$format"].split(":")
                    else:
                        component = "sensor"
                elif p["$datatype"] == "string":
                    if p["$settable"] == "true":
                        component = "text"
                        config["optimistic"] = False
                        config["command_topic"] = command_topic
                    else:
                        component = "sensor"
                elif p["$datatype"] == "enum":
                    options = p["$format"].split(",")
                    if len(options) == 2 and "On" in options and "Off" in options:
                        config["payload_off"] = "Off"
                        config["payload_on"] = "On"
                        if p["$settable"] == "true":
                            component = "switch"
                            config["optimistic"] = False
                            config["command_topic"] = command_topic
                        else:
                            component = "binary_sensor"
                    else:
                        if p["$settable"] == "true":
                            component = "select"
                            config["optimistic"] = False
                            config["command_topic"] = command_topic
                            config["options"] = p["$format"].split(",")
                        else:
                            component = "sensor"
                else:
                    logger.error(
                        "Could not represent property {} of node {} for {}.".format(
                            p["__topic__"], n["__topic__"], self.label
                        )
                    )

                topic = "{}/{}/{}/{}".format(
                    HASS_BASE_TOPIC,
                    component,
                    unique_id,
                    "config",
                )
                config_serialised = json.dumps(config)
                if component != "Unknown":
                    # pprint(config_serialised)
                    self.homie_publish(topic, config_serialised)
                else:
                    pprint(p)

    def homie_publish_device_state(self, state):
        topic = "{}/{}/{}".format(HOMIE_BASE_TOPIC, self.homie_device_id, "$state")
        self.homie_publish(topic, state)

    def homie_init_device(self):
        for k in self.homie_device_info:
            if k != "__nodes__":
                topic = "{}/{}/{}".format(HOMIE_BASE_TOPIC, self.homie_device_id, k)
                self.homie_publish(topic, self.homie_device_info[k])
        for n in self.homie_device_info["__nodes__"]:
            for k in n:
                if k not in ["__topic__", "__properties__"]:
                    topic = "{}/{}/{}/{}".format(
                        HOMIE_BASE_TOPIC, self.homie_device_id, n["__topic__"], k
                    )
                    self.homie_publish(topic, n[k])
            for p in n["__properties__"]:
                for k in p:
                    if k not in ["__topic__", "__tuya_code__", "__tuya_bitmap_value__"]:
                        topic = "{}/{}/{}/{}/{}".format(
                            HOMIE_BASE_TOPIC,
                            self.homie_device_id,
                            n["__topic__"],
                            p["__topic__"],
                            k,
                        )
                        self.homie_publish(topic, p[k])

    def homie_publish_device_info(self):
        nodes = filter(
            lambda node: node["__topic__"] in ["deviceinfo", "productinfo"],
            self.homie_device_info["__nodes__"],
        )
        for n in nodes:
            for p in n["__properties__"]:
                if p["__tuya_code__"] in self.device_info:
                    topic = "{}/{}/{}/{}".format(
                        HOMIE_BASE_TOPIC,
                        self.homie_device_id,
                        n["__topic__"],
                        p["__topic__"],
                    )
                    if p["$datatype"] == "boolean":
                        self.homie_publish(
                            topic, str(self.device_info[p["__tuya_code__"]]).lower()
                        )
                    else:
                        self.homie_publish(topic, self.device_info[p["__tuya_code__"]])

    def homie_publish_dps_objects(self, dps_objects):
        n = next(
            n for n in self.homie_device_info["__nodes__"] if n["__topic__"] == "data"
        )
        for dp in dps_objects:
            if dp.value_type == "bitmap":
                for b in dp.bitmap:
                    properties = filter(
                        lambda p: dp.name == p["__tuya_code__"]
                        and b == p["__tuya_bitmap_value__"],
                        n["__properties__"],
                    )
                    for p in properties:
                        topic = "{}/{}/{}/{}".format(
                            HOMIE_BASE_TOPIC,
                            self.homie_device_id,
                            n["__topic__"],
                            p["__topic__"],
                        )
                    self.homie_publish(topic, ("{}".format(b in dp.value)).lower())
            else:
                properties = filter(
                    lambda p: dp.name == p["__tuya_code__"], n["__properties__"]
                )
                for p in properties:
                    topic = "{}/{}/{}/{}".format(
                        HOMIE_BASE_TOPIC,
                        self.homie_device_id,
                        n["__topic__"],
                        p["__topic__"],
                    )
                    if dp.value_type == "boolean":
                        self.homie_publish(topic, ("{}".format(dp.value)).lower())
                    else:
                        self.homie_publish(topic, "{}".format(dp.value))

    def homie_init(self, offline=True):
        logger.info("Intialising homie for {}...".format(self.label))
        # set device to init
        self.homie_publish_device_state("init")
        self.create_homie_device_info()
        self.homie_init_device()
#        self.hass_publish_configs()
        self.homie_publish_device_info()
        self.homie_init_time = datetime.now()

        # device ready
        self.homie_publish_device_state("ready")
        logger.info("Intialised homie for {}.".format(self.label))
        self.do_homie_init = False

    def tuya_connect(self):
        self.tuya_connected = False
        while not self.tuya_connected:
            try:
                logger.info("Connecting to {}...".format(self.label))
                self.device = MappedDevice(
                    dev_id=self.id,
                    local_key=self.key,
                    persist=True,
                    # expand_bitmaps=False,
                )
                self.device.set_version(self.version)
                self.status = self.device.status()
                logger.info("Fetched status of {}...".format(self.label))
                self.tuya_connected = True
                logger.info("Connected to {}...".format(self.label))
            except Exception as e:
                self.tuya_connected = False
                logger.error(f"Could not connect to (self.label): {e}")
                time.sleep(DEVICE_RECONNECT_SECONDS)

    def loop(self):
        while True:
            # try:
            if not self.tuya_connected:
                self.tuya_connect()
            if self.do_homie_init or datetime.now() > self.homie_init_time + timedelta(
                seconds=HOMIE_INIT_SECONDS
            ):
                self.status = self.device.status()
                logger.info("Fetched status of {}...".format(self.label))
                if "dps_objects" in self.status:
                    self.homie_init()
                else:
                    logger.error(
                        "No dps_objects in status. {} is probably disconnected.".format(
                            self.label
                        )
                    )
                    self.tuya_connected = False
            if datetime.now() > self.homie_publish_all_time + timedelta(
                seconds=HOMIE_PUBLISH_ALL_SECONDS
            ):
                data = self.device.status()
                logger.info("Fetched status of {}...".format(self.label))
                self.status = data
                self.homie_publish_all_time = datetime.now()
            else:
                # See if any data is available
                logger.debug("Receiving data from {}...".format(self.label))
                data = self.device.receive()

            if data != None:
                if "dps_printable" in data:
                    logger.info(
                        "Received Payload from {}: {}".format(
                            self.label, data["dps_printable"]
                        )
                    )
                if "dps_objects" in data:
                    self.tuya_last_data_time = datetime.now()
                    self.homie_publish_dps_objects(data["dps_objects"])
            elif datetime.now() > self.tuya_last_data_time + timedelta(
                seconds=DEVICE_ASSUME_DEAD_SECONDS
            ):
                logger.error("No recent data from {}".format(self.label))
                self.tuya_connected = False

            # Send keyalive heartbeat
            logger.debug(" > Send Heartbeat Ping to {} < ".format(self.label))
            payload = self.device.generate_payload(tinytuya.HEART_BEAT)
            self.device.send(payload)
        # except:
        #    logger.error("Error in loop for device {}".format(self.label))
        #    self.tuya_connected = False
        #    time.sleep(DEVICE_RECONNECT_SECONDS)


def start_device_monitor(device_info):
    dm = DeviceMonitor(device_info)
    dm.loop()


if __name__ == "__main__":
    logger.info("Starting tuya_mqtt...")

    devices_info = []

    # Read Devices.json
    try:
        # Load defaults
        logger.debug("Loading device file {}...".format(DEVICE_FILE))
        with open(DEVICE_FILE) as f:
            devices_info = json.load(f)
    except:
        # No Device info
        logger.error("Device file not found.")
        exit()

    # create threads
    logger.info("Creating device threads...")

    threads = []
    for di in devices_info:
        threads.append(threading.Thread(target=start_device_monitor, args=(di,)))

    logger.info("Starting device threads...")
    for t in threads:
        t.start()
        time.sleep(DEVICE_THREAD_START_GAP_SECONDS)
