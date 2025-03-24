#!/bin/sh

# Set permission for USB port
sudo chmod 666 /dev/ttyACM0

# Activate mock ecu
python3 mock/mock.py
