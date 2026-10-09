#!/usr/bin/env bash
set -e
python -m nuitka \
    --standalone \
    --onefile \
    --assume-yes-for-downloads \
    --output-filename=woc \
    --lto=yes \
    --remove-output \
    woc.py