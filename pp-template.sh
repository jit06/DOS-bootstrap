#!/usr/bin/env bash
# Example optional staging post-processing script for package.sh --post-script.
# The working directory and STAGING_ROOT contain one directory per logical
# volume, such as C and D. Logging functions from functions.sh are available.
# This example has no effect until you uncomment and adapt the following lines:
# mkdir -p "$STAGING_ROOT/C/DOS"
# printf 'ECHO OFF\r\n' > "$STAGING_ROOT/C/AUTOEXEC.BAT"
