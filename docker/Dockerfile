# The instrument. Everything that can move an instruction count is pinned here, so a change in
# the numbers comes from the packages being measured and not from this image.
FROM python:3.12-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

COPY packages.txt /tmp/packages.txt

# Exact versions of the whole install. Debian's mirror comes first because the snapshot service is
# slow and often times out; once the mirror moves past a pinned version, apt gets it from the
# snapshot, and either way it checks the same hashes.
RUN printf '%s\n' \
      'deb http://deb.debian.org/debian trixie main' \
      'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261101T000000Z trixie main' \
      > /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/* \
    && apt-get -o Acquire::Retries=5 update \
    && xargs -a /tmp/packages.txt apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* /tmp/packages.txt

RUN pip install --no-cache-dir --root-user-action=ignore uv==0.12.15
