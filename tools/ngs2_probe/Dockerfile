FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    ca-certificates wget gnupg git cmake ninja-build build-essential pkg-config python3 \
    gcc-14 g++-14 libx11-dev libxext-dev libwayland-dev libdecor-0-dev \
    libxkbcommon-dev libglfw3-dev libgles2-mesa-dev libfuse2 libasound2-dev \
    libpulse-dev libopenal-dev libudev-dev libxcursor-dev libxi-dev libxss-dev \
    libxtst-dev libxrandr-dev libxfixes-dev \
 && wget -q https://apt.llvm.org/llvm-snapshot.gpg.key -O /tmp/llvm.key \
 && gpg --dearmor -o /usr/share/keyrings/llvm19.gpg /tmp/llvm.key \
 && echo 'deb [signed-by=/usr/share/keyrings/llvm19.gpg] https://apt.llvm.org/noble/ llvm-toolchain-noble-19 main' > /etc/apt/sources.list.d/llvm19.list \
 && apt-get update -qq && apt-get install -y --no-install-recommends clang-19 mold \
 && rm -rf /var/lib/apt/lists/* /tmp/llvm.key
RUN git config --global --add safe.directory '*'
