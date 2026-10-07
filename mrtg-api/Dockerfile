FROM python:3.12

WORKDIR /app

COPY ./mrtg-api/requirements.txt requirements.txt

RUN pip install --no-cache-dir --upgrade -r requirements.txt \
 && useradd mrtg-api -u 225 -r -M

COPY ./mrtg-api .

USER mrtg-api

CMD ["/usr/local/bin/gunicorn", "--bind", "0.0.0.0:8081", "-m", "007", "--workers", "3", "--threads", "3", "wsgi:app"]
