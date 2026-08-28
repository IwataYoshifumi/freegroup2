#!/bin/sh
# cron コンテナのエントリポイント。
# docker-compose の env_file(.env) / environment で渡された環境変数は、
# 素の cron ジョブには自動継承されないため、/etc/environment に書き出して
# Debian cron の PAM (pam_env) 経由で各ジョブに引き継がせる。
set -e

printenv > /etc/environment

python manage.py collectstatic --noinput

cp /app/docker/cron/crontab /etc/cron.d/freegroup2-cron
chmod 0644 /etc/cron.d/freegroup2-cron

exec cron -f
