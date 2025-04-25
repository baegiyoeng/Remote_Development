#!/bin/bash

apt update
apt install linux-tools-$(uname -r)
apt isntall linux-modules-extra-$(uname -r)
apt install usbutils
apt install minicom