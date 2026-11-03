#!/bin/bash
#
# (C) Copyright 2014- ECMWF.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
#
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.

# Change entries in a Fortran namelist file
# Usage: change_namelist.sh infile.nam outfile.nam key1=value1 key2=value2 ...

INFILE=$1
OUTFILE=$2
shift
shift

SEDARGS=()
while [ "$#" -gt 0 ]
do
    if [[ "$1" != *"="* ]]
    then
        echo "Error in $0: argument '$1' not of the form key=value"
        exit 1
    fi
    KEY="${1%%=*}"
    VALUE="${1#*=}"
    if ! grep -q "^[[:space:]!]*${KEY}[[:space:]]*=" "$INFILE"
    then
        echo "Error: $KEY not found in $INFILE"
        exit 1
    fi

    # An argument array preserves spaces in values such as "McICA ACC".
    # Match an assignment, not similarly prefixed keys such as do_sw_direct.
    VALUE=${VALUE//\\/\\\\}
    VALUE=${VALUE//&/\\&}
    VALUE=${VALUE//|/\\|}
    SEDARGS+=(-e "s|^[[:space:]!]*${KEY}[[:space:]]*=.*|${KEY}=${VALUE},|")
    shift
done

sed "${SEDARGS[@]}" "$INFILE" > "$OUTFILE"
