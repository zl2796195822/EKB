# S3 path-style gateway. The public endpoint is the existing EKB gateway,
# while MinIO remains loopback-only. The /ekb-s3 bucket prefix is preserved so
# AWS Signature V4 canonical paths are not rewritten by nginx.
location ^~ /ekb-s3/ {
  proxy_http_version 1.1;
  proxy_set_header Host $http_host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_request_buffering off;
  proxy_buffering off;
  proxy_read_timeout 3600s;
  proxy_send_timeout 3600s;
  proxy_pass http://127.0.0.1:9000;
}
