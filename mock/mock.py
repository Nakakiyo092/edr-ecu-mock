#!/usr/bin/env python3

import can
from udsoncan.connections import PythonIsoTpConnection
from udsoncan.client import Client
import udsoncan.configs
import isotp
from udsoncan import Request,Response
from udsoncan.services import ReadDataByIdentifier

# Isotp parameters
isotp_params = {
 'stmin': 0,
 'blocksize': 0,
 'tx_data_length': 8,
 'tx_data_min_length': 8,
 'tx_padding': 0,
 'rx_flowcontrol_timeout': 1000,
 'rx_consecutive_frame_timeout': 1000,
 'max_frame_size': 4095,
 'can_fd': False,
 'bitrate_switch': False,
}

uds_config = udsoncan.configs.default_client_config.copy()

try:
    #bus = can.Bus(interface='slcan', channel='/dev/ttyACM0', bitrate=500000)
    bus = can.Bus(interface='slcan', channel='COM9', bitrate=500000)
except Exception as err:
    print(err)
    exit()

notifier = can.Notifier(bus, [can.Printer()])                                       # Add a debug listener that print all messages
#tp_addr = isotp.Address(isotp.AddressingMode.NormalFixed_29bits, target_address=0xFF, source_address=0xF1)
tp_addr = isotp.Address(isotp.AddressingMode.NormalFixed_29bits, target_address=0xF1, source_address=0xFF)
stack = isotp.NotifierBasedCanStack(bus=bus, notifier=notifier, address=tp_addr, params=isotp_params)  # Network/Transport layer (IsoTP protocol). Register a new listenenr
conn = PythonIsoTpConnection(stack)                                                 # interface between Application and Transport layer

# 受信待ち
conn.open()

try:
    payload = conn.wait_frame(timeout=None)
except Exception as err:
    print(err)
    exit()

# リクエスト解析
request = Request.from_payload(payload)
if request.service and hasattr(request.service, "_sid"):
    sid = request.service._sid  # SID を取得

    # Reset要求にポジティブレスポンスを返す
    if sid == 0x22:
        response = Response(service=ReadDataByIdentifier, code=Response.Code.PositiveResponse, data=b'\x01')
        payload = response.get_payload()
        conn.send(payload)
    else:
        print('未対応サービスのリクエスト')
else:
    print('無効なリクエスト')

conn.close()

