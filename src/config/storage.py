from storages.backends.s3 import S3ManifestStaticStorage


class HashedS3ManifestStaticStorage(S3ManifestStaticStorage):
    def url(self, name: str, force: bool = False) -> str:
        return super().url(name, force=True)