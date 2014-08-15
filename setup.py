#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys

if sys.version_info[:2] < (2, 7):
    sys.exit("ERROR: Python >= 2.7 required")

if sys.version_info[0] > 2:
    sys.exit("ERROR: Python < 3.0 required")

from distutils.core import setup

setup(
    name='txcaching',
    version="0.1",
    description="Library for easy caching of twisted functions.",
    url="https://github.com/alexgorin/txcaching",
    author="Alexander Gorin",
    author_email='saniagorin@gmail.com',
    license='MIT',
    packages=[
        'txcaching',
    ],
    install_requires=[
          'twisted',
    ],
    keywords=[
        'python', 'twisted', 'cache',
    ],
)