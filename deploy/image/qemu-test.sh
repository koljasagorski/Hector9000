#!/usr/bin/env bash
#
# Boots a finished Hector9000 image in QEMU (emulated Raspberry Pi 3B) with the image's OWN kernel,
# initramfs and fstab - three times: (1) first boot on a card bigger than the image (data partition
# grows, mounts, services), (2) with the read-only root on, (3) with it off again.
# It tests what boot-test.sh cannot: kernel, initramfs, partitions, fstab, hector-firstboot,
# hector-overlay. It still cannot test Raspberry Pi hardware (valves, scale, LEDs, display),
# so hector-server, hector-led and the kiosk fail in a loop - that is expected.
#
#   qemu-test.sh [IMAGE]       IMAGE: path INSIDE the work volume (default /work/out/hector9000-test.img)
#
# Needs Docker on an arm64 host and --privileged containers (loop devices). QEMU runs without KVM
# (software emulation): one boot takes a few minutes. The image is only copied, never changed.
# CARD_GIB (default 8) and PHASE_TIMEOUT (seconds, default 480) can be set in the environment.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOL="${HECTOR_IMG_VOLUME:-hector-img-work}"
BUILDER="${HECTOR_IMG_BUILDER:-hector-image-builder}"
IMAGE_TAG="${HECTOR_QEMU_IMAGE:-hector-qemu}"
SRC="${1:-/work/out/hector9000-test.img}"

if ! docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
    echo "==> Building the container image $IMAGE_TAG (builder + qemu-system-arm)"
    docker build -q -t "$IMAGE_TAG" - >/dev/null <<DOCKERFILE
FROM $BUILDER
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends qemu-system-arm qemu-utils \\
    && rm -rf /var/lib/apt/lists/*
DOCKERFILE
fi

# the guest script goes into the volume without a bind mount
COPYFILE_DISABLE=1 tar -C "$HERE" -cf - qemu-guest.sh \
    | docker run --rm -i -v "$VOL":/work alpine sh -c 'rm -rf /work/qemu-tools && mkdir -p /work/qemu-tools && tar -xf - -C /work/qemu-tools 2>/dev/null'

docker run --rm --privileged -v "$VOL":/work \
    -e SRC="$SRC" -e CARD_GIB="${CARD_GIB:-8}" -e PHASE_TIMEOUT="${PHASE_TIMEOUT:-480}" \
    "$IMAGE_TAG" bash -c "$(cat "$HERE/qemu-driver.sh")"
