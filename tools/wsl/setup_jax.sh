#!/usr/bin/env bash
# JAX with CUDA for trainers that run in WSL2 (docs/runner-design.md, "JAX-only methods").
# Idempotent. Creates ~/noita-rl-jax/.venv; the games stay on Windows.
set -euo pipefail

VENV_DIR="$HOME/noita-rl-jax"
export PATH="$HOME/.local/bin:$PATH"

command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
mkdir -p "$VENV_DIR"
cd "$VENV_DIR"
[ -d .venv ] || uv venv --python 3.12
uv pip install --python .venv/bin/python "jax[cuda12]" numpy gymnasium

# JAX grabs 75 % of GPU memory by default; the Windows-side trainers share the GPU.
XLA_PYTHON_CLIENT_PREALLOCATE=false .venv/bin/python - <<'EOF'
import jax, jax.numpy as jnp
print("jax", jax.__version__, jax.devices())
x = jnp.ones((4096, 4096))
print("matmul ok:", float((x @ x).block_until_ready()[0, 0]))
EOF
