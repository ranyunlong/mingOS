FROM rust:1.99.1@sha256:931c2c0531a8b143e044f0f3914dfe802f15bb3d1955f0b657e9359327ac2ffc
ENV DEBIAN_FRONTEND=noninteractive RUSTUP_AUTO_INSTALL=0
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 build-essential pkg-config ca-certificates \
      libwayland-dev libxkbcommon-dev libx11-dev libxcursor-dev libxi-dev libxrandr-dev \
      libegl1-mesa-dev libgl1-mesa-dri libgbm-dev libdrm-dev weston \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
