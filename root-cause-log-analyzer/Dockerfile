# ---- build stage: produce a wheel ----
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY rca ./rca
RUN pip wheel --no-deps -w /wheels .

# ---- runtime stage: tiny, non-root, zero third-party deps ----
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home app && mkdir /data && chown app /data
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
USER app
WORKDIR /data
VOLUME ["/data"]
ENTRYPOINT ["rca"]
CMD ["--help"]
