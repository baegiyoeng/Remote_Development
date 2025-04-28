#!/bin/bash

apt update
pip install pyserial
apt install linux-tools-$(uname -r)
apt install linux-modules-extra-$(uname -r)
apt install usbutils
apt install minicom