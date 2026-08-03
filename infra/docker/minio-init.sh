#!/bin/sh
# Creates the private bucket and lifecycle rules used by PicGlot.
set -eu

BUCKET="${S3_BUCKET:-picglot}"

mc alias set local http://minio:9000 "${S3_ACCESS_KEY_ID}" "${S3_SECRET_ACCESS_KEY}"

if ! mc ls "local/${BUCKET}" >/dev/null 2>&1; then
  mc mb "local/${BUCKET}"
  echo "created bucket ${BUCKET}"
fi

# Never public: uploads are only reachable through signed URLs issued by the API.
mc anonymous set none "local/${BUCKET}"

# Belt and braces on top of the application lifecycle worker.
cat >/tmp/lifecycle.json <<'JSON'
{
  "Rules": [
    { "ID": "intermediate", "Status": "Enabled", "Filter": { "Prefix": "intermediate/" }, "Expiration": { "Days": 1 } },
    { "ID": "guest",        "Status": "Enabled", "Filter": { "Prefix": "guest/" },        "Expiration": { "Days": 2 } },
    { "ID": "abandoned",    "Status": "Enabled", "Filter": { "Prefix": "uploads/tmp/" },  "Expiration": { "Days": 1 } }
  ]
}
JSON
mc ilm import "local/${BUCKET}" </tmp/lifecycle.json || echo "lifecycle import skipped"

echo "minio initialised"
