FROM python:alpine3.7

EXPOSE 9005

ENV  PROMETHEUS_PREFIX p1
ENV  PROMETHEUS_PORT 9005

ENV  POOL_FREQUENCY 600
ENV P1_DEVICE /dev/ttyUSB0
ENV P1_BAUDRATE 115200

RUN pip install --upgrade pip && pip uninstall serial

COPY files/requirements.txt /app/

WORKDIR /app
RUN pip install -r requirements.txt

RUN mkdir -p /data/backup

COPY config/* /app/
COPY files/app* /app/

CMD python ./p1-export.py