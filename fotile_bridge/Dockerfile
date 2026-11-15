FROM python:3.12-alpine

RUN apk add --no-cache mosquitto tzdata

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/app/
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
