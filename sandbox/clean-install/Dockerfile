# Clean-install verification: the CORE-ONLY install a user gets from
#   pip install modelith-dbt        (NOT 'modelith-dbt[ontology]')
#
# This is the environment our dev workspace hides: `uv run` always has every extra
# (pyoxigraph included), so a command that imports the optional RDF stack passes
# locally and crashes on a real core-only install. That is exactly the class of bug
# that shipped — `mdl import erwin` into an empty folder pulled mdl_ontology → the
# pyoxigraph backend, which a core install does not have, and died with
# ModuleNotFoundError: No module named 'pyoxigraph'.
#
# The image is a faithful clean machine: python:3.12-slim, a non-root user, pip only.
# It installs a LOCALLY BUILT wheel (mounted at /tmp/wheels) so an unreleased branch
# is tested, and it installs NO extras — the point is to prove the core CLI's logical
# flows run without them. run-clean.sh does the asserts and exits non-zero on failure,
# so it doubles as a CI gate.
FROM python:3.12-slim

RUN useradd -m -s /bin/bash user
USER user
WORKDIR /home/user

COPY --chown=user:user run-clean.sh /home/user/run-clean.sh

ENTRYPOINT ["/bin/bash", "/home/user/run-clean.sh"]
