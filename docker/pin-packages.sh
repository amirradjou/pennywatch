#!/usr/bin/env bash
# Regenerates packages.txt: the exact Debian versions of everything the image installs, resolved
# from the snapshot named in the Dockerfile. Run it only when deliberately moving the instrument,
# since a new Valgrind or gcc can move every count.
set -euo pipefail
cd "$(dirname "$0")"

base=$(sed -n 's/^FROM //p' Dockerfile)
snapshot=$(grep -o 'https://snapshot.debian.org/archive/debian/[0-9TZ]*' Dockerfile)

# shellcheck disable=SC2016 # expanded by the container's shell
docker run --rm --cpus=1 -e snapshot="$snapshot" "$base" sh -c '
  printf "deb [check-valid-until=no] %s trixie main\n" "$snapshot" > /etc/apt/sources.list
  rm -f /etc/apt/sources.list.d/*
  apt-get -o Acquire::Retries=5 update >/dev/null 2>&1
  apt-get install -s --no-install-recommends valgrind gcc libc6-dev libc6-dbg 2>/dev/null \
    | sed -nE "s/^Inst ([^ ]+) (\[[^]]*\] )?\(([^ ]+) .*/\1=\3/p"
' | sort > packages.txt
wc -l packages.txt
