#!/usr/bin/env bash
set -e
python -m nuitka \
    --standalone \
    --onefile \
    --assume-yes-for-downloads \
    --output-filename=helpme \
    --lto=yes \
    --remove-output \
    woc.py