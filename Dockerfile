FROM python:3.13-slim

# Show-local times need the IANA database, which slim images do not ship.
# It arrives via the tzdata pip dependency, so no apt layer is needed.

RUN useradd --create-home --uid 10001 paxbot

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY paxbot ./paxbot
COPY shows.toml ./

RUN pip install --no-cache-dir .

# The SQLite file lives on a mounted volume, never inside an image layer.
ENV PAXBOT_DB=/data/paxbot.db
RUN mkdir -p /data && chown paxbot:paxbot /data
VOLUME ["/data"]

USER paxbot
ENTRYPOINT ["python", "-m", "paxbot"]
CMD ["--help"]
