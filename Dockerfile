FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Taipei
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 checkin \
    && useradd --uid 10001 --gid 10001 --no-create-home checkin \
    && mkdir -p /data /app/instance \
    && chown -R checkin:checkin /data /app/instance
COPY --chown=checkin:checkin checkin ./checkin
COPY --chown=checkin:checkin examples ./examples
COPY --chown=checkin:checkin wsgi.py gunicorn.conf.py ./
USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
