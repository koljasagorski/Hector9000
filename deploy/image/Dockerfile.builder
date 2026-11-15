# Build environment for deploy/image/build-image.sh and check-image.sh (arm64, needs a privileged
# container for loop devices). The tools here only handle disk images; everything inside the
# image is installed by the image's own apt.
FROM debian:bookworm-slim
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        ca-certificates util-linux fdisk e2fsprogs dosfstools git xz-utils rsync procps kmod \
        initramfs-tools-core zstd cpio sudo \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
