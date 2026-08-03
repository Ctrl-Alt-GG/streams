import os

bind = "0.0.0.0:8000"
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
threads = int(os.getenv("GUNICORN_THREADS", "2"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "30"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))
# Containers run read-only with no home directory, so the default
# $HOME/.gunicorn/gunicorn.ctl socket cannot be created.
control_socket_disable = True
accesslog = "-"
errorlog = "-"
capture_output = True
