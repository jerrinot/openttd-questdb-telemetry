# ---- build stage ----
FROM rust:1-bookworm AS build
WORKDIR /app

# Cache dependencies: build with a stub main first, then the real sources.
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs \
    && cargo build --release \
    && rm -rf src

COPY src ./src
# Touch so cargo notices the real main.rs is newer than the cached stub build.
RUN touch src/main.rs && cargo build --release

# ---- runtime stage ----
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/target/release/bridge /usr/local/bin/bridge
ENTRYPOINT ["/usr/local/bin/bridge"]
