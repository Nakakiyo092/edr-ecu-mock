#!/usr/bin/env python3

import time
import can
import isotp
from udsoncan import Request, Response
from udsoncan.services import ReadDataByIdentifier

try:
    #bus = can.Bus(interface='slcan', channel='/dev/ttyACM0', bitrate=500000)
    bus = can.Bus(interface='slcan', channel='COM9', bitrate=500000)
    #bus = can.Bus(interface='vector', channel=0, bitrate=500000, app_name="Python-CAN")
    #bus = can.Bus('test', interface='virtual')
except Exception as err:
    print(err)
    exit()

# Isotp parameters
isotp_params = {
    # Will request the sender to wait 0ms between consecutive frame.
    # 0-127ms or 100-900ns with values from 0xF1-0xF9.
    'stmin': 0,
    # Request the sender to send all consecutives frames
    # without waiting a new flow control message.
    'blocksize': 0,
    # Number of wait frame allowed before triggering an error.
    'wftmax': 0,
    # Link layer (CAN layer) works with 8 byte payload (CAN 2.0).
    'tx_data_length': 8,
    # Minimum length of CAN messages. Messages are padded to meet this length.
    'tx_data_min_length': 8,
    # Will pad all transmitted CAN messages with byte 0x00.
    'tx_padding': 0,
    # Triggers a timeout if a flow control is awaited for more than 1000 milliseconds.
    'rx_flowcontrol_timeout': 1000,
    # Triggers a timeout if a consecutive frame is awaited for more than 1000 milliseconds.
    'rx_consecutive_frame_timeout': 1000,
    # When sending, respect the stmin requirement of the receiver.
    # Could be set to a float value in seconds.
    'override_receiver_stmin': None,
    # Limit the size of receive frame.
    'max_frame_size': 4095,
    # Does not set the can_fd flag on the output CAN messages.
    'can_fd': False,
    # Does not set the bitrate_switch flag on the output CAN messages.
    'bitrate_switch': False,
    # Disable the rate limiter.
    'rate_limit_enable': False,
    # Ignored when rate_limit_enable=False. Sets the max bitrate when rate_limit_enable=True.
    'rate_limit_max_bitrate': 1000000,
    # Ignored when rate_limit_enable=False.
    # Sets the averaging window size for bitrate calculation when rate_limit_enable=True.
    'rate_limit_window_size': 0.2,
    # Does not use the listen_mode which prevent transmission.
    'listen_mode': False,
}

notifier = can.Notifier(bus, [can.Printer()])                                       # Add a debug listener that print all messages
#rx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x700, rxid=0x7DF)
#tx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7FF, rxid=0x7F7)
#rx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7F9, rxid=0x7F1)
#tx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7F9, rxid=0x7F1)
rx_addr = isotp.Address(isotp.AddressingMode.NormalFixed_29bits, target_address=0xF1, source_address=0xFF)
tx_addr = isotp.Address(isotp.AddressingMode.NormalFixed_29bits, target_address=0xF1, source_address=0x77)
rx_stack = isotp.NotifierBasedCanStack(bus=bus, notifier=notifier, address=rx_addr, params=isotp_params)  # Network/Transport layer (IsoTP protocol). Register a new listenenr
tx_stack = isotp.NotifierBasedCanStack(bus=bus, notifier=notifier, address=tx_addr, params=isotp_params)  # Network/Transport layer (IsoTP protocol). Register a new listenenr

data_records = {}
for i in range(0xfa13, 0xfa16):
    data_records[i] = b''.join(bytes([j % 256]) for j in range(772))
    #data_records[i] = bytearray([i - 0xfa12] * 772)

requests = {}
responses = {}
for i in range(0xfa13, 0xfa16):
    requests[i] = ReadDataByIdentifier.make_request(didlist=[i], didconfig={'default':'s'})
    responses[i] = Response(service=ReadDataByIdentifier, code=Response.Code.PositiveResponse, data=bytes([(i>>8)&0xFF,i&0xFF])+data_records[i])
pend_response = Response(service=ReadDataByIdentifier, code=Response.Code.RequestCorrectlyReceived_ResponsePending)
nega_response = Response(service=ReadDataByIdentifier, code=Response.Code.RequestOutOfRange)

rx_stack.start()
tx_stack.start()

try:
    while True:
        payload = rx_stack.recv(block=True, timeout=0.01)
        for i in range(0xfa13, 0xfa16):
            if payload is not None:
                if payload == requests[i].get_payload():
                    if False:   # Pending
                        tx_stack.send(pend_response.get_payload())
                        time.sleep(3)

                    if False:   # Nega
                        tx_stack.send(nega_response.get_payload())

                    else:
                        tx_stack.send(responses[i].get_payload())

except Exception as err:
    print(err)
    exit()

rx_stack.stop()
tx_stack.stop()

bus.shutdown()
