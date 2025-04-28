#!/bin/bash

apt update
pip install pyserial
apt install linux-tools-$(uname -r)
apt install linux-modules-extra-$(uname -r)
apt install usbutils
mkdir -p /usr/share/hwdata
wget -O /usr/share/hwdata/usb.ids https://raw.githubusercontent.com/usbutils/usbutils/master/data/usb.ids
apt install minicom