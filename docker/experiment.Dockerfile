# Sandbox image for Stage B experiments (build: `mise run build-image`).
# Preinstalls the common scientific stack so the council can run real-ish experiments OFFLINE
# (the sandbox runs with --network none, so nothing can be pip-installed at run time).
FROM python:3.12-slim

# Keep this lean but useful; pin major libs for reproducibility.
RUN pip install --no-cache-dir \
    numpy==2.* \
    pandas==2.* \
    scipy==1.* \
    scikit-learn==1.* \
    matplotlib==3.*

# Non-root, no home writes needed; /work is mounted read-only at run time.
WORKDIR /work
