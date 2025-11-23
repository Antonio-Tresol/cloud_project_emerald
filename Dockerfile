FROM python:3.12-slim

# 1. Install uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# 2. Set up the Virtual Environment
# We set VIRTUAL_ENV so 'uv' and python usage automatically use it
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Create the venv
RUN uv venv $VIRTUAL_ENV

# 3. Install Dependencies
COPY pyproject.toml .

# FIX: Use 'uv pip install .' to install dependencies from pyproject.toml
# We verify the pyproject.toml exists, then install.
# We explicitly tell it not to require a 'src' layout since we just have a script.
RUN uv pip install .

# 4. Copy Application Code
COPY main.py .

# 5. Run
EXPOSE 80
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]