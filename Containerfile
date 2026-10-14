FROM docker.io/library/python:3.14.8-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS build
WORKDIR /src
COPY pyproject.toml requirements.lock.txt LICENSE THIRD_PARTY_NOTICES.md ./
COPY talkabout/ ./talkabout/
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.lock.txt && /opt/venv/bin/pip install --no-cache-dir --no-deps .

FROM docker.io/library/python:3.14.8-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83
LABEL org.opencontainers.image.title="TalkAbout" org.opencontainers.image.licenses="MIT" io.talkabout.project="talkabout"
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 talkabout && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp talkabout
USER 10001:10001
WORKDIR /tmp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 CMD ["python", "-m", "talkabout.container", "healthcheck"]
ENTRYPOINT ["python", "-m", "talkabout.container"]
CMD ["serve"]
