# Phase 1 onboarding sandbox: a CLEAN machine, the way a new engineer's is.
#
# The whole point of this image is what it does NOT contain: no uv, no pipx, no
# global mdl, no project .venv, none of the PATH entries a developer's own shell
# profile accumulates. python:3.12-slim ships a system Python (a fresh machine has
# one) and a CA bundle, but no Python packaging tooling on PATH and no Modelith -
# so "fresh machine to `mdl --help`" is tested honestly, including the step where
# the engineer first installs uv.
#
# Base choice: python:3.12-slim, matching the toolchain the repo's own
# profiles/devcontainer already targets. We install NOTHING via apt on purpose -
# not only to keep the image a faithful clean machine, but because apt is not
# needed: uv is bootstrapped with the bundled pip (no curl), and the demo model
# ships inside the wheel (no git clone). Everything the acceptance bars measure -
# installing uv, `uv tool install modelith-dbt`, the demo loop, dbt build - happens
# inside run-phase1.sh at RUN time, where it is timed and asserted.
FROM python:3.12-slim

# MODELITH_VERSION pins exactly what a new engineer gets from the blessed path
# today. Override at build time to test a different release:
#   docker build --build-arg MODELITH_VERSION=0.3.2 ...
ARG MODELITH_VERSION=0.3.1
ENV MODELITH_VERSION=${MODELITH_VERSION}

# INSTALL_SOURCE selects what the harness installs:
#   pypi  (default) - `uv tool install modelith-dbt==$MODELITH_VERSION`, the real
#                     blessed path a new engineer follows.
#   wheel           - install a locally built wheel instead, to test an unreleased
#                     branch. Build the wheel first (`uv build --wheel`) and mount
#                     or copy dist/ to /tmp/wheels (see README).
ARG INSTALL_SOURCE=pypi
ENV INSTALL_SOURCE=${INSTALL_SOURCE}

# No apt installs (see header): the slim image already carries Python, pip, and a
# CA bundle - enough to bootstrap uv and reach PyPI over HTTPS.
#
# Run as a non-root user, like a real developer account. uv tool install targets
# ~/.local/bin for this user, exactly as it would on a laptop.
RUN useradd -m -s /bin/bash engineer
USER engineer
WORKDIR /home/engineer

# The harness is the only thing we copy in. It does the timed install + demo loop.
COPY --chown=engineer:engineer run-phase1.sh /home/engineer/run-phase1.sh

# Default: run the full Phase 1 acceptance harness. Override with `bash` to poke
# around interactively (docker run --rm -it modelith-onboarding bash).
ENTRYPOINT ["/bin/bash", "/home/engineer/run-phase1.sh"]
