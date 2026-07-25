#!/bin/sh
set -eu

: "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
: "${MINIO_STATIC_ACCESS_KEY:?MINIO_STATIC_ACCESS_KEY is required}"
: "${MINIO_STATIC_SECRET_KEY:?MINIO_STATIC_SECRET_KEY is required}"
: "${MINIO_STATIC_BUCKET:?MINIO_STATIC_BUCKET is required}"

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing "local/$MINIO_STATIC_BUCKET"
mc admin user add local "$MINIO_STATIC_ACCESS_KEY" "$MINIO_STATIC_SECRET_KEY"

printf '%s\n' \
  '{' \
  '  "Version": "2012-10-17",' \
  '  "Statement": [' \
  '    {' \
  '      "Effect": "Allow",' \
  '      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],' \
  "      \"Resource\": [\"arn:aws:s3:::$MINIO_STATIC_BUCKET\"]" \
  '    },' \
  '    {' \
  '      "Effect": "Allow",' \
  '      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],' \
  "      \"Resource\": [\"arn:aws:s3:::$MINIO_STATIC_BUCKET/*\"]" \
  '    }' \
  '  ]' \
  '}' > /tmp/static-writer-policy.json

mc admin policy create local streams-static-writer /tmp/static-writer-policy.json
mc admin policy attach local streams-static-writer --user "$MINIO_STATIC_ACCESS_KEY"
mc anonymous set download "local/$MINIO_STATIC_BUCKET"

mc stat "local/$MINIO_STATIC_BUCKET"