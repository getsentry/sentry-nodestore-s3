#!/bin/env bash

set -eux

if [ "$(uname -s)" != "Linux" ]; then
    echo "Please use the GitHub Action."
    exit 1
fi

OLD_VERSION="${1}"
NEW_VERSION="${2}"

echo "Current version: $OLD_VERSION"
echo "Bumping version: $NEW_VERSION"

sed -E -i "s/version='\.+'/version='${NEW_VERSION}'/g" setup.py
