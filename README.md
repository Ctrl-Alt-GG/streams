# Streams
Private Django directory for streams served by an external MediaMTX deployment.

## Static assets

Static assets use the same MinIO-backed manifest storage in local and production
deployments. The `setup` service runs `collectstatic` before `web` starts, and
uploads both original and content-hashed assets to `MINIO_STATIC_BUCKET` through
the private `MINIO_ENDPOINT_URL`.

`MINIO_STATIC_PUBLIC_URL` is the browser-visible URL for the root of that bucket
and must end with `/`. In production, the ingress must map it to the MinIO bucket.
For the example environment, requests are routed as follows:

```text
https://streams.example.com/static/<object>
	-> http://127.0.0.1:9000/streams-static/<object>
```

The bucket is download-only for anonymous clients. Its writer credentials are
used only by Django's `S3ManifestStaticStorage`; do not expose the MinIO console
or writer credentials through the public ingress.
