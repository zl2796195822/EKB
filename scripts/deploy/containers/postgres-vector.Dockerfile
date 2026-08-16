# Build this from the same PostgreSQL base image used in production.
# The pgvector Debian package is fetched and checksum-verified by the release
# process, then placed beside this Dockerfile as a build-only asset.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY postgresql-17-pgvector_0.8.6-1.pgdg12+1_amd64.deb /tmp/pgvector.deb
RUN dpkg -i /tmp/pgvector.deb && rm -f /tmp/pgvector.deb
