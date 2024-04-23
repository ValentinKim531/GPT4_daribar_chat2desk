FROM python:3.10.2

WORKDIR /app

COPY . /app

RUN python -m venv /app/venv
RUN . /app/venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

CMD . /app/venv/bin/activate && uvicorn app:app --host=0.0.0.0 --port=${PORT:-8000}



