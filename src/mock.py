#!/usr/bin/env python3

"""
A Python script to simulate an ECU with EDR data.

This script acts as a UDS (Unified Diagnostic Services) server over CAN bus,
responding to ReadDataByIdentifier (service 0x22) requests for EDR data records
(DIDs 0xFA13, 0xFA14, 0xFA15). It uses ISO-TP (ISO 15765-2) as the transport
layer and supports 11-bit and 29-bit CAN addressing.

Supported CAN interfaces:
    slcan   Serial-line CAN adapter (e.g., COM9, /dev/ttyACM0), 500 kbit/s
    vector  Vector CAN interface, channel 0, 500 kbit/s
    virtual Virtual CAN bus for testing without hardware

Usage:
    Windows:
        python src\mock.py <devicename> [options]

    Linux / macOS:
        python3 src/mock.py <devicename> [options]

Arguments:
    devicename              Device name (e.g., COM9, /dev/ttyACM0, virtual, vector)

Options:
    -h, --help              Show this help message and exit
    -v, --verbose           Enable verbose output (print all CAN frames)
    -b N, --bg-frames N     Number of background CAN IDs to send (0-500, default: 0)
    -i TYPE, --id-type TYPE CAN ID type: 11func, 11phys, or 29bits (default: 29bits)
    -d TYPE, --data TYPE    Data record values: zeros, step, or random (default: zeros)
    -p, --pending           Send a pending response before the final response
    -n, --negative          Send a negative response instead of a positive response
    -s ADDR, --src-addr ADDR
                            Override the default source address

Examples:
    python3 src/mock.py /dev/ttyACM0
    python3 src/mock.py COM9 --verbose --bg-frames 100 --id-type 11func
    python3 src/mock.py virtual --data random --pending
    python3 src/mock.py vector --id-type 29bits --src-addr 0x11

Press [CTRL] + 'c' to quit.

License:
    MIT License.
    See the accompanying LICENSE file for full terms.
"""

import random
import threading
import time

import argparse
import can
import isotp
from udsoncan import Response
from udsoncan.services import ReadDataByIdentifier

_BG_FRAMES_RNG_SEED = 42
_BG_FRAMES_CYCLE_TIME_MS = 100

_ISOTP_PARAMS = {
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
        "-b", "--bg-frames",
        type=int,
        default=0,
        metavar="N",
        help="number of background CAN IDs to send (0-500, default 0)"
    )
    parser.add_argument(
        "-i", "--id-type",
        choices=["11func", "11phys", "29bits"],
        default="29bits",
        help=(
            "CAN ID type: 11bits functional (11func), 11bits physical (11phys),"
            " or 29bits (default: 29bits)"
        )
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
    parser.add_argument(
        "-s", "--src-addr",
        type=lambda x: int(x, 0),
        default=None,
        metavar="ADDR",
        help=(
            "set source address."
            " For 11func: range 0x08-0xFF, default 0xFF)."
            " For 29bits: range 0x00-0xFF, default 0x77)."
            " Ignored for 11phys."
        )
    )
    return parser


def generate_background_frames(count):
    """Return a list of (arbitration_id, is_extended_id, data) for background frames.

    Half of the IDs are standard 11-bit, the other half extended 29-bit.
    IDs and data are pseudorandom with a fixed seed for reproducibility.
    """
    rng = random.Random(_BG_FRAMES_RNG_SEED)

    n_std = count // 2
    n_ext = count - n_std

    frames = []
    for _ in range(n_std):
        arb_id = rng.randint(0, 0x6FF)
        data = bytes(rng.randint(0, 255) for _ in range(8))
        frames.append((arb_id, False, data))

    for _ in range(n_ext):
        arb_id = rng.randint(0, 0x17FFFFFF)
        data = bytes(rng.randint(0, 255) for _ in range(8))
        frames.append((arb_id, True, data))

    return frames


def background_sender(bus, frames, stop_event, cycle_time_s):
    """Send background CAN frames repeatedly every cycle_time_s seconds."""
    while not stop_event.is_set():
        cycle_start = time.monotonic()
        for arb_id, is_extended, data in frames:
            if stop_event.is_set():
                return
            msg = can.Message(arbitration_id=arb_id, is_extended_id=is_extended, data=data)
            try:
                bus.send(msg)
            except can.CanError:
                pass
        elapsed = time.monotonic() - cycle_start
        remaining = cycle_time_s - elapsed
        if remaining > 0:
            stop_event.wait(remaining)


def _create_bus(args):
    """Create and return a CAN bus, or None if initialization fails."""
    try:
        if args.devicename == "virtual":
            return can.Bus('test', interface='virtual')
        if args.devicename == "vector":
            return can.Bus(interface='vector', channel=0, bitrate=500000, app_name="Python-CAN")
        else:
            return can.Bus(interface='slcan', channel=args.devicename, bitrate=500000)
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
        return None
    except Exception as err:
        print("Could not access CAN network.")
        print("The program is aborting.")
        print(err)
        return None


def _create_isotp_addresses(args):
    """Return (rx_addr, tx_addr) based on the id_type argument."""
    if args.id_type == "11func":
        src_addr = args.src_addr if args.src_addr is not None else 0xFF
        txid = 0x700 | src_addr
        rxid = txid - 8
        rx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x700, rxid=0x7DF)
        tx_addr = isotp.Address(
            isotp.AddressingMode.Normal_11bits, txid=txid, rxid=rxid
        )
    elif args.id_type == "11phys":
        rx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7F9, rxid=0x7F1)
        tx_addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7F9, rxid=0x7F1)
    else:  # 29bits (default)
        src_addr = args.src_addr if args.src_addr is not None else 0x77
        rx_addr = isotp.Address(
            isotp.AddressingMode.NormalFixed_29bits,
            target_address=0xF1,
            source_address=0xFF,
        )
        tx_addr = isotp.Address(
            isotp.AddressingMode.NormalFixed_29bits,
            target_address=0xF1,
            source_address=src_addr,
        )
    return rx_addr, tx_addr


def _create_responses(args):
    """Return a response context dict for DID handling."""
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
        did_bytes = bytes([(i >> 8) & 0xFF, i & 0xFF])
        responses[i] = Response(
            service=ReadDataByIdentifier,
            code=Response.Code.PositiveResponse,
            data=did_bytes + data_records[i],
        )
    pend_response = Response(
        service=ReadDataByIdentifier,
        code=Response.Code.RequestCorrectlyReceived_ResponsePending,
    )
    nega_response = Response(
        service=ReadDataByIdentifier,
        code=Response.Code.RequestOutOfRange,
    )
    return {
        'requests': requests,
        'pos': responses,
        'pend': pend_response,
        'nega': nega_response,
    }


def _start_background_sender(bus, args, stop_event):
    """Start background sender daemon thread if bg_frames > 0."""
    if args.bg_frames <= 0:
        return
    frames = generate_background_frames(args.bg_frames)
    thread = threading.Thread(
        target=background_sender,
        args=(bus, frames, stop_event, _BG_FRAMES_CYCLE_TIME_MS / 1000),
        daemon=True,
    )
    thread.start()


def _handle_payload(payload, resp_ctx, tx_stack, args):
    """Send the appropriate response for a received payload."""
    if payload is None:
        return
    for i in range(0xfa13, 0xfa16):
        if payload == resp_ctx['requests'][i].get_payload():
            print(f"Request received (DID: 0x{i:04X}).")
            if args.pending:
                tx_stack.send(resp_ctx['pend'].get_payload())
                print("Reply sent (pending response).")
                time.sleep(3)
            if args.negative:
                tx_stack.send(resp_ctx['nega'].get_payload())
                print("Reply sent (negative response).")
            else:
                tx_stack.send(resp_ctx['pos'][i].get_payload())
                print("Reply sent (positive response).")


def main():
    """Main process."""

    # Parse command line arguments
    argparser = get_argparser()
    args = argparser.parse_args()

    if not 0 <= args.bg_frames <= 500:
        argparser.error("--bg-frames must be between 0 and 500")

    if args.src_addr is not None:
        if args.id_type == "11func" and not 0x08 <= args.src_addr <= 0xFF:
            argparser.error(f"--src-addr must be between 0x08 and 0xFF for {args.id_type}")
        elif args.id_type == "29bits" and not 0x00 <= args.src_addr <= 0xFF:
            argparser.error(f"--src-addr must be between 0x00 and 0xFF for {args.id_type}")

    # Setup and start a CAN bus
    bus = _create_bus(args)
    if bus is None:
        return

    if args.verbose:
        # Setup a debug listener that print all CAN frames
        notifier = can.Notifier(bus, [can.Printer()])
    else:
        notifier = can.Notifier(bus, [])

    rx_addr, tx_addr = _create_isotp_addresses(args)

    rx_stack = isotp.NotifierBasedCanStack(
        bus=bus, notifier=notifier, address=rx_addr, params=_ISOTP_PARAMS
    )
    tx_stack = isotp.NotifierBasedCanStack(
        bus=bus, notifier=notifier, address=tx_addr, params=_ISOTP_PARAMS
    )

    resp_ctx = _create_responses(args)

    rx_stack.start()
    tx_stack.start()

    stop_event = threading.Event()
    _start_background_sender(bus, args, stop_event)

    try:
        while True:
            payload = rx_stack.recv(block=True, timeout=0.01)
            _handle_payload(payload, resp_ctx, tx_stack, args)
    except Exception as err:
        print(err)
    finally:
        stop_event.set()
        rx_stack.stop()
        tx_stack.stop()
        notifier.stop()
        bus.shutdown()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
