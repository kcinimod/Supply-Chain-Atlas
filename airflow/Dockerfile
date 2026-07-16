# Custom Airflow image: base + everything the Atlas task code imports.
# The project source itself is NOT baked in — the repo is bind-mounted at
# /opt/atlas and put on PYTHONPATH, so code edits never require a rebuild;
# only dependency changes do.
#
# Two quirks solved here:
# * pip's resolver hits resolution-too-deep on this dependency set (airflow +
#   mlflow + dbt + sklearn is a big graph) — uv resolves it in seconds.
#   apache-airflow itself is pinned inside requirements.txt so the resolution
#   can never break the base install.
# * The image's ACTIVE interpreter is the airflow user's venv at
#   /home/airflow/.local (not /usr/local) — uv must target that python or the
#   packages land somewhere the scheduler never looks.
FROM apache/airflow:3.3.0-python3.12

COPY airflow/requirements.txt /requirements.txt
RUN pip install --no-cache-dir uv \
    && uv pip install --python /home/airflow/.local/bin/python --no-cache -r /requirements.txt
