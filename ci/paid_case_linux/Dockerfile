ARG PYTHON_IMAGE=python:3.13.4-slim-bookworm
FROM ${PYTHON_IMAGE}
# Build network is for public packages only. Runtime is --network none.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod 755 /usr/sbin/policy-rc.d \
 && apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && mkdir -p /usr/share/postgresql-common/pgdg \
 && curl --fail --silent --show-error https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && printf 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main\n' > /etc/apt/sources.list.d/pgdg.list \
 && mkdir -p /etc/postgresql-common \
 && printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-18 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home runner
COPY source /opt/rtm/source
COPY harness /opt/rtm/harness
RUN python -m pip install --no-cache-dir --only-binary=:all: --index-url https://pypi.org/simple \
      --report /opt/rtm/install-report.json -r /opt/rtm/source/requirements.txt \
      -c /opt/rtm/harness/constraints.txt \
 && python -m pip check
WORKDIR /opt/rtm/source
USER 10001:10001
ENTRYPOINT ["python", "-B", "/opt/rtm/harness/full_app_linux.py"]
