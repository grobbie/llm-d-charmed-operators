#!/bin/bash
set -e

echo "========================================"
echo "LLM-D Charmed solution builder"
echo "========================================"

echo "Installing build dependencies..."
# Update package lists
sudo apt-get update -y || echo "Warning: apt-get update failed, skipping."

# Install required snaps
sudo snap install lxd || echo "LXD already installed or snap unavailable."
sudo snap install charmcraft --classic || echo "Charmcraft already installed or snap unavailable."

# Make sure LXD is initialized
if command -v lxc &> /dev/null; then
    if ! sudo lxc profile show default > /dev/null 2>&1; then
        echo "Initializing LXD..."
        sudo lxd waitready
        sudo lxd init --auto
    fi
fi

# Make sure the current user is in the lxd group
if ! groups | grep -q "\blxd\b"; then
    echo "Adding $USER to the lxd group..."
    sudo usermod -aG lxd $USER
    echo "NOTE: You may need to log out and back in natively for group application, or run 'newgrp lxd' before executing."
fi

CHARMS=(
    "llm-d-prefill-k8s"
    "llm-d-decode-k8s"
    "llm-d-kv-cache-k8s"
    "llm-d-inference-scheduler-k8s"
)

echo ""
echo "Packaging LLM-D Charms..."

for charm in "${CHARMS[@]}"; do
    echo "========================================"
    echo "Building $charm..."
    echo "========================================"
    if [ -d "$charm" ]; then
        cd "$charm"
        charmcraft pack
        cd ..
    else
        echo "WARNING: Directory $charm not found! Skipping..."
    fi
done

echo "========================================"
echo "All LLM-D charms successfully packaged!"
echo "========================================"
