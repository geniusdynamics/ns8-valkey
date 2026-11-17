#!/bin/bash

#
# Copyright (C) 2023 Nethesis S.r.l.
# Copyright (C) 2026 Genius Dynamics
# SPDX-License-Identifier: GPL-3.0-or-later
#

# Terminate on error
set -e

# Prepare variables for later use
images=()
# The image will be pushed to GitHub container registry
repobase="${REPOBASE:-ghcr.io/geniusdynamics}"
# Configure the image name
reponame="valkey"
# Node.js 25+ no longer ships Corepack, install it from npm
# renovate: datasource=npm packageName=corepack
corepack_version=0.36.0

# Create a new empty container image
container=$(buildah from scratch)

# Reuse existing nodebuilder-valkey container, to speed up builds
if ! buildah containers --format "{{.ContainerName}}" | grep -q nodebuilder-valkey; then
    echo "Pulling NodeJS runtime..."
    buildah from --name nodebuilder-valkey -v "${PWD}:/usr/src:Z" docker.io/library/node:26.13.0-slim
    buildah run nodebuilder-valkey sh -c "npm install -g corepack@${corepack_version} && corepack enable"
fi

echo "Build static UI files with node..."
buildah run \
    --workingdir=/usr/src/ui \
    --env="NODE_OPTIONS=--openssl-legacy-provider" \
    nodebuilder-valkey \
    sh -c "yarn install && yarn build"

# Add imageroot directory to the container image
buildah add "${container}" imageroot /imageroot
buildah add "${container}" ui/dist /ui
# - valkey@any:valkeypeer: an instance joining a cluster asks a member for
#   the cluster secrets (admit-member) and for a topology repair
#   (reconcile-cluster). Both actions are in the valkeypeer role of every
#   instance (create-module/20roles), not open to readers.
# - four TCP ports: client and cluster bus ports of the main process, client
#   and cluster bus ports of the replica process (cluster mode). They are
#   bound to the node cluster VPN address and loopback only; the cluster
#   network is in the trusted firewall zone, so no public port is opened.
# - rootless module, host network (Valkey Cluster announces real addresses)
# - pinned Valkey image: Renovate tracks the org.nethserver.images label.
#   Valkey 9.0+ is required (CLUSTER MIGRATESLOTS, CLUSTER REPLICATE NO ONE).
buildah config --entrypoint=/ \
    --label="org.nethserver.authorizations=valkey@any:valkeypeer" \
    --label="org.nethserver.tcp-ports-demand=4" \
    --label="org.nethserver.rootfull=0" \
    --label="org.nethserver.min-core=3.13.0" \
    --label="org.nethserver.images=docker.io/valkey/valkey:9.1.3-alpine" \
    "${container}"
# Commit the image
buildah commit "${container}" "${repobase}/${reponame}"

# Append the image URL to the images array
images+=("${repobase}/${reponame}")

#
# NOTICE:
#
# It is possible to build and publish multiple images.
#
# 1. create another buildah container
# 2. add things to it and commit it
# 3. append the image url to the images array
#

#
# Setup CI when pushing to Github. 
# Warning! docker::// protocol expects lowercase letters (,,)
if [[ -n "${CI}" ]]; then
    # Set output value for Github Actions
    printf "images=%s\n" "${images[*],,}" >> "${GITHUB_OUTPUT}"
else
    # Just print info for manual push
    printf "Publish the images with:\n\n"
    for image in "${images[@],,}"; do printf "  buildah push %s docker://%s:%s\n" "${image}" "${image}" "${IMAGETAG:-latest}" ; done
    printf "\n"
fi
