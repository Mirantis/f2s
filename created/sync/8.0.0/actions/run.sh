#!/bin/bash
set -xe

mkdir -p {{dst}}
rsync -c -r --delete {{src}} {{dst}}

# if dst is empty return an error
if [ `ls {{dst}} | wc -l` -eq 0 ]; then
        exit 1;
fi
