#!/usr/bin/env python3

"""
A Python script to simulate an ECU with EDR data.

License:
    MIT License.
    See the accompanying LICENSE file for full terms.
"""

import argparse
import random
import time
import can
import isotp
from udsoncan import Request, Response
from udsoncan.services import ReadDataByIdentifier


def get_argparser():
    """Get the command line argument parser."""

    parser = argparse.ArgumentParser(
        description="Simulate an ECU with EDR data. Press [CTRL] + 'c' to quit."
    )
    parser.add_argument(
        "devicename",
        type=str,
        help="device name like COM9 or /dev/ttyACM0 (required)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="enable verbose output"
    )
    parser.add_argument(
        "-i", "--id-type",
        choices=["11func", "11phys", "29bits"],
        default="29bits",
        help="CAN ID type: 11bits functional (11func), 11bits physical (11phys), or 29bits (default: 29bits)"
    )
    parser.add_argument(
        "-d", "--data",
        choices=["zeros", "step", "random"],
        default="zeros",
        help="data record values: zeros, step, or random pseudorandom (default: zeros)"
    )
    parser.add_argument(
        "-p", "--pending",
        action="store_true",
        help="enable pending response before positive/negative response"
    )
    parser.add_argument(
        "-n", "--negative",
        action="store_true",
        help="enable negative response instead of positive response"
    )
    return parser


def main():
    """Main process."""

    # Parse command line arguments
    argparser = get_argparser()
    args = argparser.parse_args()

    # Setup and start a CAN bus
    try:
        if args.devicename == "virtual":
            bus = can.Bus('test', interface='virtual')
        elif args.devicename == "vector":
            bus = can.Bus(interface='vector', channel=0, bitrate=500000, app_name="Python-CAN")
        else:
            bus = can.Bus(interface='slcan', channel=args.devicename, bitrate=500000)
    except can.CanInitializationError as err:
        print("Could not access CAN network.")
        print("The program is aborting.")
        print(err)
        if args.devicename not in ("virtual", "vector"):
            print("Possible causes:")
            print(f"  - Wrong device name: check '{args.devicename}' is correct")
            print( "  - Device not powered: check the device is powered on")
            print( "  - Device not connected: check the device is properly connected")
            print( "  - Wrong firmware: check the device has correct firmware")
            print( "  - Permission denied (Linux): try 'sudo usermod -aG dialout $USER' and re-login")
            print(f"    or 'sudo chmod 666 {args.devicename}'")
        return
    except Exception as err:
        print("Could not access CAN network.")
        print("The program is aborting.")
        print(err)
        return

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

    if args.verbose:
        # Setup a debug listener that print all messages
        notifier = can.Notifier(bus, [can.Printer()])
    else:
        notifier = can.Notifier(bus, [])

    if args.id_type == "11func":
        rx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x700, rxid=0x7DF)
        tx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7FF, rxid=0x7F7)
    elif args.id_type == "11phys":
        rx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7F9, rxid=0x7F1)
        tx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7F9, rxid=0x7F1)
    else:  # 29bits (default)
        rx_addr = isotp.Address(isotp.AddressingMode.NormalFixed_29bits, target_address=0xF1, source_address=0xFF)
        tx_addr = isotp.Address(isotp.AddressingMode.NormalFixed_29bits, target_address=0xF1, source_address=0x77)

    # Network/Transport layer (IsoTP protocol). Register a new listener
    rx_stack = isotp.NotifierBasedCanStack(bus=bus, notifier=notifier, address=rx_addr, params=isotp_params)
    tx_stack = isotp.NotifierBasedCanStack(bus=bus, notifier=notifier, address=tx_addr, params=isotp_params)

    # Fixed-seed RNG so random data is reproducible and differs per DID
    rng = random.Random(42)
    data_records = {}
    for i in range(0xfa13, 0xfa16):
        if args.data == "zeros":
            data_records[i] = bytes(772)
        elif args.data == "step":
            data_records[i] = b''.join(bytes([j % 256]) for j in range(772))
        else:  # random
            data_records[i] = bytes(rng.getrandbits(8) for _ in range(772))

    requests = {}
    responses = {}
    for i in range(0xfa13, 0xfa16):
        requests[i] = ReadDataByIdentifier.make_request(didlist=[i], didconfig={'default': 's'})
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
                        if args.pending:
                            tx_stack.send(pend_response.get_payload())
                            time.sleep(3)

                        if args.negative:
                            tx_stack.send(nega_response.get_payload())

                        else:
                            tx_stack.send(responses[i].get_payload())

    except Exception as err:
        print(err)

    rx_stack.stop()
    tx_stack.stop()

    bus.shutdown()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
